"""Unit tests for the CFBD diagnostic; no live API calls are made."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import cfbd_api_check


class FakeResponse(io.BytesIO):
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_checks_info_then_scoreboard(capsys) -> None:
    responses = iter(
        [
            {"tierName": "Tier 1", "usedCalls": 5, "monthlyLimit": 5000,
             "remainingCalls": 4995, "resetAt": "2026-09-01"},
            [{"id": 1}],
        ]
    )

    def opener(_request, timeout):
        assert timeout == 30
        return FakeResponse(json.dumps(next(responses)).encode())

    assert cfbd_api_check.run_checks("secret", "https://example.test", ["info", "scoreboard"], opener) == 0
    output = capsys.readouterr().out
    assert "PASS  /info" in output
    assert "returned 1 games" in output
    assert "secret" not in output


def test_401_explains_how_to_replace_secret(capsys) -> None:
    def opener(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"message":"Unauthorized"}'))

    key = "super-private-value"
    assert cfbd_api_check.run_checks(key, "https://example.test", ["info"], opener) == 3
    output = capsys.readouterr().out
    assert "CFBD rejected the credential" in output
    assert "no quotes" in output
    assert key not in output


def test_bearer_prefix_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CFBD_API_KEY", "Bearer abc123")
    try:
        cfbd_api_check.load_key(tmp_path / ".env")
    except ValueError as exc:
        assert "raw API key" in str(exc)
    else:
        raise AssertionError("expected a malformed key to be rejected")
