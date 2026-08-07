"""
Shared database layer for the CFB betting league.

Used by app.py (Streamlit), cfb_sync.py (GitHub Actions) and settle_bets.py.
Reads DATABASE_URL from the environment first, then from Streamlit secrets.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Connection

_ENGINE: Engine | None = None

# Advisory-lock keys (arbitrary but stable) used to serialise batch jobs.
LOCK_SETTLE = 918_273_641
LOCK_SYNC = 918_273_642


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")

    if not url:
        # Streamlit Community Cloud path: st.secrets["DATABASE_URL"]
        try:
            import streamlit as st  # noqa: WPS433 (local import on purpose)

            url = st.secrets.get("DATABASE_URL")  # type: ignore[assignment]
        except Exception:  # streamlit missing, or no secrets file
            url = None

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to the environment (GitHub Actions "
            "secret) or to .streamlit/secrets.toml for Streamlit Cloud."
        )

    url = url.strip()

    # Normalise the driver prefix.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]

    # Supabase requires TLS. Add it if the caller forgot.
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    return url


def get_engine() -> Engine:
    """Process-wide engine. Small pool: Streamlit Cloud is single-container."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            _resolve_database_url(),
            pool_size=3,
            max_overflow=2,
            pool_pre_ping=True,      # survives Supabase pooler dropping idle conns
            pool_recycle=280,
            future=True,
            connect_args={
                "connect_timeout": 10,
                # Never let a stuck query pin a worker forever.
                "options": "-c statement_timeout=20000",
                "application_name": "cfb-league",
            },
        )
    return _ENGINE


@contextmanager
def tx() -> Iterator[Connection]:
    """Transactional connection. Commits on success, rolls back on exception."""
    with get_engine().begin() as conn:
        yield conn


@contextmanager
def ro() -> Iterator[Connection]:
    """Read-only connection (auto-closed, no explicit transaction)."""
    with get_engine().connect() as conn:
        yield conn


def q(conn: Connection, sql: str, **params: Any) -> list[dict]:
    """Run a query, return list of dicts."""
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def q1(conn: Connection, sql: str, **params: Any) -> dict | None:
    rows = q(conn, sql, **params)
    return rows[0] if rows else None


def exec_(conn: Connection, sql: str, **params: Any):
    return conn.execute(text(sql), params)


def try_advisory_lock(conn: Connection, key: int) -> bool:
    """Non-blocking session lock so two cron runs can't settle concurrently."""
    return bool(conn.execute(text("select pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar())


# ---------------------------------------------------------------------------
# Password hashing — stdlib only (no bcrypt wheel needed on Streamlit Cloud)
# ---------------------------------------------------------------------------
import hashlib
import hmac
import secrets

_PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,24}$")


def valid_username(name: str) -> bool:
    return bool(USERNAME_RE.match((name or "").strip()))
