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
