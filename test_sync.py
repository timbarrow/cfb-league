"""Provider and Tier 1 scoreboard shaping checks — no database required."""

from __future__ import annotations

import cfb_sync
import pytest


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
                "homeTeam": {"points": 21},
                "awayTeam": {"points": 17},
            },
            {
                "game_id": 102,
                "status": "Final",
                "home_team": {"score": 31},
                "away_team": {"score": 24},
            },
        ]
    )

    assert rows == [
        {"id": 101, "status": "in_progress", "home_score": 21, "away_score": 17},
        {"id": 102, "status": "completed", "home_score": 31, "away_score": 24},
    ]


def test_failed_line_pull_preserves_existing_board(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cfb_sync, "api_get", fail)

    with pytest.raises(RuntimeError, match="existing lines preserved"):
        cfb_sync.fetch_lines(2026, "regular", 1)
