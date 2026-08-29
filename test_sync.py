"""Provider and Tier 1 scoreboard shaping checks — no database required."""

from __future__ import annotations

from contextlib import nullcontext

import cfb_sync
import pytest


def test_games_feed_is_scoped_to_fbs(monkeypatch) -> None:
    call = {}

    def fake_get(path, **params):
        call.update(path=path, **params)
        return []

    monkeypatch.setattr(cfb_sync, "api_get", fake_get)

    assert cfb_sync.fetch_games(2026, "regular", 1) == []
    assert call == {
        "path": "games",
        "year": 2026,
        "week": 1,
        "seasonType": "regular",
        "classification": "fbs",
    }


def test_daily_feed_does_not_infer_live_status_from_kickoff_time() -> None:
    rows = cfb_sync.shape_rows(
        [
            {
                "id": 101,
                "season": 2026,
                "week": 1,
                "seasonType": "regular",
                "startDate": "2026-08-27T12:00:00Z",
                "homeTeam": "Home",
                "awayTeam": "Away",
                "completed": False,
            }
        ],
        {},
        {},
        cfb_sync.datetime(2026, 8, 27, 23, 0, tzinfo=cfb_sync.timezone.utc),
    )

    assert rows[0]["status"] == "scheduled"
    assert rows[0]["classification"] == "fbs"


def test_week_upsert_reconciles_stale_fbs_tags(monkeypatch) -> None:
    calls = []
    conn = object()
    row = {
        "id": 101,
        "season": 2026,
        "season_type": "regular",
        "week": 1,
        "classification": "fbs",
        "status": "scheduled",
        "home_score": None,
        "away_score": None,
    }

    monkeypatch.setattr(cfb_sync, "tx", lambda: nullcontext(conn))
    monkeypatch.setattr(cfb_sync, "try_advisory_lock", lambda *_args: True)
    monkeypatch.setattr(
        cfb_sync,
        "exec_",
        lambda _conn, sql, **params: calls.append((sql, params)),
    )

    assert cfb_sync.upsert_games([row]) == 1
    assert "set classification = null" in calls[0][0]
    assert calls[0][1] == {
        "season": 2026,
        "season_type": "regular",
        "week": 1,
    }
    assert calls[1] == (cfb_sync.UPSERT_SQL, row)


def test_draftkings_wins_when_it_has_both_markets(monkeypatch) -> None:
    monkeypatch.setattr(
        cfb_sync,
        "api_get",
        lambda *_args, **_kwargs: [
            {
                "id": 101,
                "lines": [
                    {"provider": "Bovada", "spread": -4, "overUnder": 49},
                    {"provider": "DraftKings", "spread": -3.5, "overUnder": 50.5},
                ],
            },
            {
                "id": 102,
                "lines": [{"provider": "Bovada", "spread": -7, "overUnder": 42}],
            },
        ],
    )

    lines = cfb_sync.fetch_lines(2026, "regular", 1)

    assert lines == {
        101: {"spread_home": -3.5, "total_line": 50.5, "provider": "DraftKings"},
        102: {"spread_home": -7.0, "total_line": 42.0, "provider": "Bovada backup"},
    }


def test_bovada_fills_only_the_missing_draftkings_market(monkeypatch) -> None:
    monkeypatch.setattr(
        cfb_sync,
        "api_get",
        lambda *_args, **_kwargs: [
            {
                "id": 201,
                "lines": [
                    {"provider": "DraftKings", "spread": -2.5, "overUnder": None},
                    {"provider": "Bovada", "spread": -3, "overUnder": 48.5},
                ],
            },
            {
                "id": 202,
                "lines": [
                    {"provider": "DraftKings", "spread": None, "overUnder": 55},
                    {"provider": "Bovada", "spread": 4.5, "overUnder": 54.5},
                ],
            },
        ],
    )

    assert cfb_sync.fetch_lines(2026, "regular", 1) == {
        201: {
            "spread_home": -2.5,
            "total_line": 48.5,
            "provider": "Spread: DraftKings · Total: Bovada",
        },
        202: {
            "spread_home": 4.5,
            "total_line": 55.0,
            "provider": "Spread: Bovada · Total: DraftKings",
        },
    }


def test_live_scoreboard_shapes_camel_and_snake_case() -> None:
    rows = cfb_sync.shape_live_rows(
        [
            {
                "id": 101,
                "status": "In Progress",
                "period": 3,
                "clock": "08:42",
                "homeTeam": {"points": 21},
                "awayTeam": {"points": 17},
            },
            {
                "game_id": 102,
                "status": "Final",
                "period": 4,
                "clock": "00:00",
                "home_team": {"score": 31},
                "away_team": {"score": 24},
            },
        ]
    )

    assert rows == [
        {
            "id": 101, "status": "in_progress", "home_score": 21, "away_score": 17,
            "game_period": 3, "game_clock": "08:42",
        },
        {
            "id": 102, "status": "completed", "home_score": 31, "away_score": 24,
            "game_period": 4, "game_clock": "00:00",
        },
    ]


def test_failed_line_pull_preserves_existing_board(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cfb_sync, "api_get", fail)

    with pytest.raises(RuntimeError, match="existing lines preserved"):
        cfb_sync.fetch_lines(2026, "regular", 1)
