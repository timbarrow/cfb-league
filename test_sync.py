"""Provider and Tier 1 scoreboard shaping checks — no database required."""

from __future__ import annotations

import cfb_sync


def test_draftkings_is_the_only_line_source(monkeypatch) -> None:
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
        101: {"spread_home": -3.5, "total_line": 50.5, "provider": "DraftKings"}
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
