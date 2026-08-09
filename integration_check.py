"""
integration_check.py — end-to-end smoke test against a REAL PostgreSQL.

It applies schema.sql, then exercises: signup, bet placement, the kickoff lock,
the balance guard, line-movement rejection, duplicate-ticket protection,
settlement (win / loss / push), idempotent re-settlement, and the leaderboard.

    DATABASE_URL=postgresql://... python integration_check.py

WARNING: it DROPS and recreates public.users / games / bets. Point it at a
scratch database, never at your live league.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

from sqlalchemy import text  # noqa: E402

from db import exec_, q, q1, ro, tx  # noqa: E402

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  ✗ {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {name}")


def reset_schema() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    with tx() as c:
        c.execute(text("drop view if exists public.leaderboard cascade"))
        for t in ("bets", "games", "users"):
            c.execute(text(f"drop table if exists public.{t} cascade"))
    with tx() as c:
        c.exec_driver_sql(open(os.path.join(here, "schema.sql"), encoding="utf-8").read())
    print("schema applied")


def seed_games() -> None:
    with tx() as c:
        exec_(
            c,
            """insert into public.games
               (id, season, week, season_type, start_date, home_team, away_team,
                home_conference, away_conference, home_rank, away_rank,
                spread_home, total_line, lines_provider, status)
               values
               (101, 2026, 5, 'regular', now() + interval '3 days', 'Georgia', 'Alabama',
                'SEC', 'SEC', 1, 4, -3.5, 52.5, 'DraftKings', 'scheduled'),
               (102, 2026, 5, 'regular', now() + interval '3 days', 'Ohio State', 'Michigan',
                'Big Ten', 'Big Ten', 2, null, -7.0, 45.0, 'DraftKings', 'scheduled'),
               (103, 2026, 5, 'regular', now() + interval '4 days', 'Texas', 'Baylor',
                'SEC', 'Big 12', null, null, 10.5, 60.5, 'DraftKings', 'scheduled'),
               (104, 2026, 5, 'regular', now() - interval '1 hour', 'Utah', 'BYU',
                'Big 12', 'Big 12', null, null, -2.5, 44.5, 'DraftKings', 'in_progress')""",
        )


def main() -> int:
    import app  # imports Streamlit in bare mode; prints harmless warnings

    reset_schema()
    seed_games()

    print("\n[auth]")
    ok, msg = app.register_user("Dude95", "hunter22", "")
    check("register succeeds", ok, True)
    ok2, _ = app.register_user("dude95", "hunter22", "")
    check("duplicate username (case-insensitive) blocked", ok2, False)
    ok3, _ = app.register_user("Rival", "hunter22", "")
    check("second player registers", ok3, True)
    check("bad password rejected", app.register_user("x!", "abc", "")[0], False)

    with ro() as c:
        me = q1(c, "select id, balance from public.users where username = 'Dude95'")
        rival = q1(c, "select id from public.users where username = 'Rival'")
    uid, rid = str(me["id"]), str(rival["id"])
    check("starting bankroll", Decimal(str(me["balance"])), Decimal("10000.00"))

    print("\n[bet placement]")
    ok, msg = app.place_bet(uid, 101, "spread_home", Decimal("-3.5"), Decimal("500"))
    check("place home spread", (ok, "500.00" in msg), (True, True))
    check("balance debited", app.get_balance(uid), Decimal("9500.00"))

    ok, msg = app.place_bet(uid, 101, "spread_home", Decimal("-3.5"), Decimal("100"))
    check("duplicate open ticket blocked", ok, False)

    ok, msg = app.place_bet(uid, 101, "over", Decimal("52.5"), Decimal("1000000"))
    check("overdraw blocked", (ok, "Insufficient" in msg), (False, True))

    ok, msg = app.place_bet(uid, 101, "over", Decimal("49.5"), Decimal("100"))
    check("stale line rejected", (ok, "moved" in msg), (False, True))

    ok, msg = app.place_bet(uid, 104, "spread_home", Decimal("-2.5"), Decimal("100"))
    check("kicked-off game locked", (ok, "closed" in msg), (False, True))

    ok, _ = app.place_bet(uid, 102, "spread_away", Decimal("7.0"), Decimal("300"))
    check("place away spread (line auto-flipped)", ok, True)
    ok, _ = app.place_bet(uid, 103, "over", Decimal("60.5"), Decimal("200"))
    check("place over", ok, True)
    ok, _ = app.place_bet(rid, 101, "spread_away", Decimal("3.5"), Decimal("2000"))
    check("rival places bet", ok, True)

    check("balance after 3 bets", app.get_balance(uid), Decimal("9000.00"))

    print("\n[upcoming-week isolation]")
    with tx() as c:
        exec_(
            c,
            """insert into public.games
               (id, season, week, season_type, start_date, home_team, away_team,
                spread_home, total_line, status)
               values (201, 2026, 6, 'regular', now() + interval '10 days',
                       'LSU', 'Florida', -6.5, 50.5, 'scheduled')""",
        )
    app.refresh_data()
    weeks = {g["week"] for g in app.load_upcoming_games()}
    check("only the next week is on the board", weeks, {5})
    ids = {g["id"] for g in app.load_upcoming_games()}
    check("started game excluded from board", 104 in ids, False)

    print("\n[settlement]")
    with tx() as c:
        # 101: Georgia 31 - Alabama 20 -> home -3.5 WINS, total 51 UNDER 52.5
        # 102: Ohio State 21 - Michigan 14 -> away +7.0 PUSH
        # 103: Texas 20 - Baylor 17 -> total 37, over 60.5 LOSES
        exec_(c, """update public.games set status='completed', home_score=31, away_score=20
                     where id=101""")
        exec_(c, """update public.games set status='completed', home_score=21, away_score=14
                     where id=102""")
        exec_(c, """update public.games set status='completed', home_score=20, away_score=17
                     where id=103""")

    from settle_bets import settle_bets

    s = settle_bets()
    # 4 tickets: my Georgia -3.5 (win), my OSU +7 push, my Texas over (loss),
    # rival's Alabama +3.5 (loss — Georgia won by 11).
    check("bets graded", s["graded"], 4)
    check("winners", s["won"], 1)
    check("losses", s["lost"], 2)
    check("pushes", s["push"], 1)
    check("total credited", s["credited"], Decimal("1300.00"))

    with ro() as c:
        rows = q(c, """select bet_type, status, payout_amount from public.bets
                        where user_id = :u order by created_at""", u=uid)
    got = [(r["bet_type"], r["status"], str(Decimal(str(r["payout_amount"])))) for r in rows]
    check(
        "my tickets settled correctly",
        got,
        [
            ("spread_home", "won", "1000.00"),  # 500 * 2.00, no vig
            ("spread_away", "push", "300.00"),  # stake returned
            ("over", "lost", "0.00"),
        ],
    )
    # 9000 cash + 1000 + 300 = 10300
    check("balance after settlement", app.get_balance(uid), Decimal("10300.00"))
    check("rival lost the stake", app.get_balance(rid), Decimal("8000.00"))

    print("\n[idempotency]")
    s2 = settle_bets()
    check("re-running settles nothing", s2["graded"], 0)
    check("balance unchanged", app.get_balance(uid), Decimal("10300.00"))

    print("\n[leaderboard]")
    app.refresh_data()
    board = app.load_leaderboard()
    check("ranked by net worth", [r["username"] for r in board], ["Dude95", "Rival"])
    me_row = board[0]
    check("net worth = cash + open stake", Decimal(str(me_row["net_worth"])), Decimal("10300.00"))
    check("settled P/L", Decimal(str(me_row["settled_pl"])), Decimal("300.00"))
    check("record", (me_row["wins"], me_row["losses"], me_row["pushes"]), (1, 1, 1))
    check("all bets visible for transparency", len(app.load_all_bets()), 4)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} check(s).")
        return 1
    print("ALL INTEGRATION CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
