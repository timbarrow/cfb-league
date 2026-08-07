"""
settle_bets.py — automated bet settlement engine.

Grades every `pending` bet whose game is `completed`, then credits winnings
back to the bettor's balance.

Money model (stake is debited at placement time):
    won   -> credit wager * payout_multiplier   (1.91 on a -110 line: stake + 0.91 profit)
    push  -> credit wager                        (stake back)
    lost  -> credit 0

Safety properties:
  * One transaction per batch, wrapped in a transaction-level advisory lock, so
    two concurrent cron runs can never double-credit.
  * Bets are re-checked with `SELECT ... FOR UPDATE` and the UPDATE is guarded
    by `status = 'pending'`, making the whole thing idempotent.
  * Decimal arithmetic end-to-end — no float drift on money.

Run:  python settle_bets.py            (env: DATABASE_URL)
      python settle_bets.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

from db import LOCK_SETTLE, exec_, q, tx, try_advisory_lock

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("settle_bets")

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Grading — pure function, unit-testable, no DB
# ---------------------------------------------------------------------------
def grade_bet(bet_type: str, line_placed, home_score: int, away_score: int) -> str:
    """
    Return 'won' | 'lost' | 'push'.

    Convention: `line_placed` is the number the bettor took.
      spread_home : home spread   (e.g. -7.5 laying the favourite)
      spread_away : away spread   (e.g. +7.5 taking the dog) = -spread_home
      over / under: the total

    A spread bet wins when (points scored by your side - opponent) + your line > 0.
    """
    line = Decimal(str(line_placed))
    hs, aws = Decimal(int(home_score)), Decimal(int(away_score))

    if bet_type == "spread_home":
        margin = (hs - aws) + line
    elif bet_type == "spread_away":
        margin = (aws - hs) + line
    elif bet_type == "over":
        margin = (hs + aws) - line
    elif bet_type == "under":
        margin = line - (hs + aws)
    else:
        raise ValueError(f"Unknown bet_type: {bet_type!r}")

    if margin == 0:
        return "push"
    return "won" if margin > 0 else "lost"


def payout_for(status: str, wager, multiplier) -> Decimal:
    """Amount credited back to the user's balance."""
    w = money(wager)
    if status == "won":
        return money(w * Decimal(str(multiplier)))
    if status == "push":
        return w
    return Decimal("0.00")


# ---------------------------------------------------------------------------
# Batch settlement
# ---------------------------------------------------------------------------
SELECT_GRADABLE = """
select
    b.id, b.user_id, b.game_id, b.bet_type, b.line_placed,
    b.wager_amount, b.payout_multiplier,
    g.home_score, g.away_score, g.home_team, g.away_team
from public.bets b
join public.games g on g.id = b.game_id
where b.status = 'pending'
  and g.status = 'completed'
  and g.home_score is not null
  and g.away_score is not null
order by b.created_at
for update of b
"""

UPDATE_BET = """
update public.bets
   set status = :status,
       payout_amount = :payout,
       settled_at = now()
 where id = :id
   and status = 'pending'
"""

CREDIT_USER = """
update public.users
   set balance = balance + :amount
 where id = :user_id
"""


def settle_bets(dry_run: bool = False) -> dict:
    """Settle every gradable pending bet. Returns a summary dict."""
    summary = {
        "graded": 0,
        "won": 0,
        "lost": 0,
        "push": 0,
        "skipped": 0,
        "credited": Decimal("0.00"),
    }

    with tx() as conn:
        if not try_advisory_lock(conn, LOCK_SETTLE):
            log.warning("Another settlement run is in progress; exiting.")
            summary["skipped"] = -1
            return summary

        rows = q(conn, SELECT_GRADABLE)
        log.info("Found %d gradable pending bets.", len(rows))

        credits: dict[str, Decimal] = {}

        for r in rows:
            try:
                status = grade_bet(
                    r["bet_type"], r["line_placed"], r["home_score"], r["away_score"]
                )
            except ValueError as exc:
                log.error("Skipping bet %s: %s", r["id"], exc)
                summary["skipped"] += 1
                continue

            payout = payout_for(status, r["wager_amount"], r["payout_multiplier"])

            log.info(
                "%s %s %s (%s) %s-%s -> %s (credit %s)",
                str(r["id"])[:8],
                r["bet_type"],
                r["line_placed"],
                f"{r['away_team']} @ {r['home_team']}",
                r["away_score"],
                r["home_score"],
                status.upper(),
                payout,
            )

            if dry_run:
                summary["graded"] += 1
                summary[status] += 1
                summary["credited"] += payout
                continue

            res = exec_(conn, UPDATE_BET, id=r["id"], status=status, payout=payout)
            if res.rowcount != 1:
                # Someone else settled it between SELECT and UPDATE — skip the credit.
                log.warning("Bet %s already settled; not crediting.", r["id"])
                summary["skipped"] += 1
                continue

            summary["graded"] += 1
            summary[status] += 1
            if payout > 0:
                uid = str(r["user_id"])
                credits[uid] = credits.get(uid, Decimal("0.00")) + payout
                summary["credited"] += payout

        # One balance write per user rather than per bet.
        for user_id, amount in credits.items():
            exec_(conn, CREDIT_USER, user_id=user_id, amount=amount)

        if dry_run:
            # Roll the transaction back.
            raise _DryRunRollback(summary)

    return summary


class _DryRunRollback(Exception):
    def __init__(self, summary: dict):
        super().__init__("dry run")
        self.summary = summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Settle completed CFB bets.")
    ap.add_argument("--dry-run", action="store_true", help="Grade but roll back.")
    args = ap.parse_args(argv)

    try:
        summary = settle_bets(dry_run=args.dry_run)
    except _DryRunRollback as rb:
        summary = rb.summary
        log.info("DRY RUN — transaction rolled back.")

    log.info(
        "Settled %d bets | won=%d lost=%d push=%d skipped=%d | credited $%s",
        summary["graded"],
        summary["won"],
        summary["lost"],
        summary["push"],
        summary["skipped"],
        summary["credited"],
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log.error("SETTLEMENT FAILED: %s", exc, exc_info=True)
        sys.exit(1)
