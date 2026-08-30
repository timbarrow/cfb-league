"""Experience-layer checks for standings, recap, sharing and time grouping."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO

from PIL import Image

import app


def test_weekly_recap_includes_every_player_and_awards() -> None:
    standings = [
        {"user_id": "a", "username": "Alex", "season": 2026, "week": 0, "net_worth": 10000},
        {"user_id": "b", "username": "Blair", "season": 2026, "week": 0, "net_worth": 10000},
        {"user_id": "a", "username": "Alex", "season": 2026, "week": 1, "net_worth": 10200},
        {"user_id": "b", "username": "Blair", "season": 2026, "week": 1, "net_worth": 9900},
    ]
    tickets = [
        {
            "id": "1", "user_id": "a", "username": "Alex", "game_id": 10,
            "season": 2026, "week": 1, "bet_type": "spread_home", "line_placed": -3.5,
            "wager_amount": 200, "payout_amount": 400, "status": "won",
            "home_team": "Georgia", "away_team": "Clemson",
        },
        {
            "id": "2", "user_id": "b", "username": "Blair", "game_id": 10,
            "season": 2026, "week": 1, "bet_type": "spread_away", "line_placed": 3.5,
            "wager_amount": 100, "payout_amount": 0, "status": "lost",
            "home_team": "Georgia", "away_team": "Clemson",
        },
    ]

    recap = app.build_weekly_recap(tickets, standings)

    assert recap is not None
    assert recap["week"] == 1
    assert recap["winner"]["username"] == "Alex"
    assert recap["winner"]["week_change"] == Decimal("200.00")
    assert [row["username"] for row in recap["standings"]] == ["Alex", "Blair"]
    assert recap["biggest"]["username"] == "Alex"
    assert recap["bad_beat"]["username"] == "Blair"


def test_standings_snapshot_surfaces_current_week_money_and_user_rank() -> None:
    board = [
        {"user_id": "a", "username": "Alex", "net_worth": 10200, "open_stake": 0,
         "wins": 1, "losses": 0, "pushes": 0},
        {"user_id": "b", "username": "Blair", "net_worth": 9750, "open_stake": 100,
         "wins": 0, "losses": 1, "pushes": 0},
    ]
    tickets = [
        {"user_id": "a", "season": 2026, "week": 1, "status": "won",
         "wager_amount": 200, "payout_amount": 400},
        {"user_id": "b", "season": 2026, "week": 1, "status": "lost",
         "wager_amount": 250, "payout_amount": 0},
        {"user_id": "b", "season": 2026, "week": 2, "status": "pending",
         "wager_amount": 100, "payout_amount": None},
    ]

    snapshot = app.build_standings_snapshot(board, tickets, "b")

    assert snapshot["week"] == 2
    assert snapshot["settled_count"] == 0
    assert snapshot["weekly_winner"] is None
    assert snapshot["weekly_loser"] is None
    assert snapshot["you"]["rank"] == 2
    assert snapshot["you"]["week_change"] == Decimal("0.00")


def test_standings_snapshot_calls_out_weekly_winner_and_loser() -> None:
    board = [
        {"user_id": "a", "username": "Alex", "net_worth": 10200},
        {"user_id": "b", "username": "Blair", "net_worth": 9750},
    ]
    tickets = [
        {"user_id": "a", "season": 2026, "week": 1, "status": "won",
         "wager_amount": 200, "payout_amount": 400},
        {"user_id": "b", "season": 2026, "week": 1, "status": "lost",
         "wager_amount": 250, "payout_amount": 0},
    ]

    snapshot = app.build_standings_snapshot(board, tickets, "a")

    assert snapshot["weekly_winner"]["username"] == "Alex"
    assert snapshot["weekly_winner"]["week_change"] == Decimal("200.00")
    assert snapshot["weekly_loser"]["username"] == "Blair"
    assert snapshot["weekly_loser"]["week_change"] == Decimal("-250.00")
    assert snapshot["rows"][0]["week_wins"] == 1
    assert snapshot["rows"][1]["week_losses"] == 1


def test_share_card_is_a_portrait_png() -> None:
    recap = {
        "season": 2026,
        "week": 1,
        "winner": {"username": "Alex", "week_change": Decimal("200"), "net_worth": 10200},
        "standings": [
            {"recap_rank": 1, "username": "Alex", "net_worth": 10200},
            {"recap_rank": 2, "username": "Blair", "net_worth": 9900},
        ],
        "ticket_count": 2,
        "biggest": None,
        "bad_beat": None,
        "boldest": None,
        "popular": None,
        "popular_count": 0,
        "most_active": None,
        "most_active_count": 0,
    }

    card = app.build_share_card(recap)
    image = Image.open(BytesIO(card))

    assert image.format == "PNG"
    assert image.size == (1080, 1350)


def test_kickoff_windows_and_countdown_are_simple() -> None:
    saturday_early = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)  # 11 AM Central
    assert app.kickoff_window(saturday_early)[0] == "Saturday · Early window"
    assert app._kickoff_countdown(
        saturday_early,
        now=datetime(2026, 8, 29, 14, 30, tzinfo=timezone.utc),
    ) == "1h 30m"


def test_live_score_value_preserves_zero_and_marks_missing() -> None:
    assert app._score_value(0) == "0"
    assert app._score_value(21) == "21"
    assert app._score_value(None) == "—"


def test_game_progress_formats_quarters_halftime_and_overtime() -> None:
    assert app.game_progress(3, "08:42") == "Q3 · 8:42"
    assert app.game_progress(2, "00:00") == "Halftime"
    assert app.game_progress(5, "12:10") == "OT · 12:10"
    assert app.game_progress(6, None) == "2OT"
    assert app.game_progress(None, None) == "Live"
    assert app.game_progress(4, "00:00", "completed") == "Final"


def test_win_probabilities_normalize_and_total_one_hundred() -> None:
    assert app.win_probabilities(
        {"away_win_probability": Decimal("0.394"), "home_win_probability": Decimal("0.606")}
    ) == (39, 61)
    assert app.win_probabilities(
        {"away_win_probability": 2, "home_win_probability": 3}
    ) == (40, 60)
    assert app.win_probabilities({}) is None


def test_ticket_delete_window_closes_at_first_kickoff() -> None:
    kickoff = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    ticket = {"status": "pending", "season_first_start": kickoff}

    assert app.ticket_delete_is_open(ticket, kickoff - timedelta(seconds=1))
    assert not app.ticket_delete_is_open(ticket, kickoff)
    assert not app.ticket_delete_is_open(ticket, kickoff + timedelta(seconds=1))
    assert not app.ticket_delete_is_open(
        {"status": "won", "season_first_start": kickoff},
        kickoff - timedelta(days=1),
    )
