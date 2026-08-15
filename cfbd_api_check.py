#!/usr/bin/env python3
"""Safely diagnose CollegeFootballData API credentials and endpoint access.

Examples:
    python cfbd_api_check.py
    python cfbd_api_check.py --check info
    CFBD_API_KEY=... python cfbd_api_check.py --check scoreboard

The key is read from CFBD_API_KEY, or from .env when the environment variable
is absent. The key itself is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.collegefootballdata.com"
KEY_HELP_URL = "https://collegefootballdata.com/key"


def _dotenv_value(path: Path, name: str) -> str | None:
    """Read one simple KEY=value entry without adding a dotenv dependency."""
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def load_key(env_file: Path) -> tuple[str, str]:
    raw = os.getenv("CFBD_API_KEY")
    source = "CFBD_API_KEY environment variable"
    if raw is None:
        raw = _dotenv_value(env_file, "CFBD_API_KEY")
        source = str(env_file)
    if raw is None or not raw.strip():
        raise ValueError(
            f"CFBD_API_KEY is missing from the environment and {env_file}. "
            f"Request a key at {KEY_HELP_URL}."
        )

    key = raw.strip()
    if key.casefold().startswith("bearer "):
        raise ValueError(
            f"{source} starts with 'Bearer '. Store only the raw API key; "
            "the script adds the Bearer prefix."
        )
    if key[:1] in "\"'" or key[-1:] in "\"'":
        raise ValueError(
            f"{source} contains a quote at the edge. Remove surrounding quotes "
            "from the GitHub Actions secret."
        )
    if any(character.isspace() for character in key):
        raise ValueError(f"{source} contains embedded whitespace; paste the raw key again.")
    return key, source


def _safe_error(body: bytes, key: str) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        message = text[:300]
    else:
        if isinstance(payload, dict):
            message = next(
                (
                    str(payload[field])
                    for field in ("message", "error", "detail", "title")
                    if payload.get(field)
                ),
                json.dumps(payload)[:300],
            )
        else:
            message = str(payload)[:300]
    return message.replace(key, "[REDACTED]") if message else "no response message"


def request_json(
    key: str,
    base_url: str,
    path: str,
    params: dict[str, str] | None = None,
    opener: Callable[..., Any] = urlopen,
) -> tuple[Any, Any]:
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{base_url.rstrip('/')}/{path.lstrip('/')}{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "cfb-league-api-check/1.0",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            return json.load(response), response.headers
    except HTTPError as exc:
        message = _safe_error(exc.read(), key)
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("the API returned a successful but non-JSON response") from exc


def _print_failure(path: str, error: RuntimeError) -> int:
    detail = str(error)
    print(f"FAIL  /{path}: {detail}")
    if detail.startswith("HTTP 401"):
        print(
            "      CFBD rejected the credential. Generate or confirm the key at\n"
            f"      {KEY_HELP_URL}, then replace GitHub's CFBD_API_KEY secret with\n"
            "      the raw key only (no quotes and no 'Bearer ' prefix)."
        )
        return 3
    if detail.startswith("HTTP 403"):
        print("      The key is recognized, but this account cannot use the endpoint.")
        return 4
    if detail.startswith("HTTP 429"):
        print("      The account's API call allowance is exhausted or rate-limited.")
        return 5
    return 6


def run_checks(
    key: str,
    base_url: str,
    checks: list[str],
    opener: Callable[..., Any] = urlopen,
) -> int:
    print(f"Credential loaded ({len(key)} characters); value will not be displayed.")

    for check in checks:
        params = {"classification": "fbs"} if check == "scoreboard" else None
        try:
            payload, _headers = request_json(key, base_url, check, params, opener)
        except RuntimeError as exc:
            return _print_failure(check, exc)

        if check == "info":
            if not isinstance(payload, dict):
                print("FAIL  /info: expected a JSON object.")
                return 7
            tier = payload.get("tierName", "unknown")
            used = payload.get("usedCalls", "unknown")
            limit = payload.get("monthlyLimit", "unknown")
            remaining = payload.get("remainingCalls", "unknown")
            reset = payload.get("resetAt", "unknown")
            print(
                f"PASS  /info: key accepted; tier={tier}; usage={used}/{limit}; "
                f"remaining={remaining}; resets={reset}"
            )
        else:
            if not isinstance(payload, list):
                print("FAIL  /scoreboard: expected a JSON array.")
                return 7
            print(f"PASS  /scoreboard?classification=fbs: returned {len(payload)} games.")

    print("CFBD API diagnostic passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        choices=("both", "info", "scoreboard"),
        default="both",
        help="Which endpoint to test (default: both).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Fallback env file when CFBD_API_KEY is not exported (default: .env).",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CFBD_API_BASE", DEFAULT_BASE_URL),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        key, source = load_key(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Using credential from {source}.")
    checks = ["info", "scoreboard"] if args.check == "both" else [args.check]
    return run_checks(key, args.base_url, checks)


if __name__ == "__main__":
    raise SystemExit(main())
