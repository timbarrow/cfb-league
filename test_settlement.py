"""
Settlement math tests — no database required.

    python test_settlement.py     (or: pytest test_settlement.py)
"""

from decimal import Decimal

from settle_bets import grade_bet, payout_for

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


# --- Spread: home favourite -----------------------------------------------
# Alabama (home) -7.5 vs Auburn. Final 31-20 -> margin 11, covers.
check("home -7.5 covers", grade_bet("spread_home", -7.5, 31, 20), "won")
check("away +7.5 loses", grade_bet("spread_away", 7.5, 31, 20), "lost")

# Final 24-20 -> margin 4, favourite fails to cover.
check("home -7.5 fails", grade_bet("spread_home", -7.5, 24, 20), "lost")
check("away +7.5 covers", grade_bet("spread_away", 7.5, 24, 20), "won")

# --- Spread: exact push on a whole number ---------------------------------
# Home -7, wins by exactly 7.
check("home -7 push", grade_bet("spread_home", -7, 27, 20), "push")
check("away +7 push", grade_bet("spread_away", 7, 27, 20), "push")

# --- Spread: home underdog -------------------------------------------------
# Home +3.5, loses by 3 -> dog covers.
check("home +3.5 covers", grade_bet("spread_home", 3.5, 20, 23), "won")
check("away -3.5 fails", grade_bet("spread_away", -3.5, 20, 23), "lost")

# Home +3.5, loses by 4 -> dog does not cover.
check("home +3.5 fails", grade_bet("spread_home", 3.5, 20, 24), "lost")
check("away -3.5 covers", grade_bet("spread_away", -3.5, 20, 24), "won")

# --- Pick'em ---------------------------------------------------------------
check("PK home wins", grade_bet("spread_home", 0, 21, 17), "won")
check("PK away wins", grade_bet("spread_away", 0, 21, 17), "lost")
check("PK tie is a push", grade_bet("spread_home", 0, 21, 21), "push")

# --- Totals ----------------------------------------------------------------
check("over 52.5 hits", grade_bet("over", 52.5, 31, 24), "won")     # 55
check("under 52.5 misses", grade_bet("under", 52.5, 31, 24), "lost")
check("over 52.5 misses", grade_bet("over", 52.5, 24, 21), "lost")  # 45
check("under 52.5 hits", grade_bet("under", 52.5, 24, 21), "won")
check("over 55 push", grade_bet("over", 55, 31, 24), "push")        # exactly 55
check("under 55 push", grade_bet("under", 55, 31, 24), "push")

# --- Shutouts / zeros ------------------------------------------------------
check("0-0 under 40", grade_bet("under", 40, 0, 0), "won")
check("0-0 home -3", grade_bet("spread_home", -3, 0, 0), "lost")

# --- Float precision traps (52.5 + 0.1 style) ------------------------------
check("half-point precision", grade_bet("over", 47.5, 24, 23), "lost")   # 47 < 47.5
check("half-point precision 2", grade_bet("over", 46.5, 24, 23), "won")  # 47 > 46.5
check("decimal push not float", grade_bet("under", 47, 24, 23), "push")

# --- Bad input -------------------------------------------------------------
try:
    grade_bet("parlay", 1, 1, 1)
    FAILS.append("bad bet_type: expected ValueError")
except ValueError:
    pass

# --- Payouts ---------------------------------------------------------------
check("win pays 1.91x", payout_for("won", 100, Decimal("1.91")), Decimal("191.00"))
check("no-vig win pays 2x", payout_for("won", 100, Decimal("2.00")), Decimal("200.00"))
check("loss pays 0", payout_for("lost", 100, Decimal("1.91")), Decimal("0.00"))
check("push refunds stake", payout_for("push", 100, Decimal("1.91")), Decimal("100.00"))
# Rounding: 33.33 * 1.91 = 63.6603 -> 63.66
check("rounds to cents", payout_for("won", Decimal("33.33"), Decimal("1.91")), Decimal("63.66"))
# Half-cent rounds up, not banker's-rounding down.
check("half cent rounds up", payout_for("won", Decimal("5.00"), Decimal("1.911")), Decimal("9.56"))

# --- Net P/L identity: a $100 winner nets +$91 -----------------------------
check(
    "net profit on win",
    payout_for("won", 100, Decimal("1.91")) - Decimal("100"),
    Decimal("91.00"),
)

if FAILS:
    print(f"FAILED {len(FAILS)} check(s):")
    for f in FAILS:
        print("  -", f)
    raise SystemExit(1)

print("All settlement checks passed.")
