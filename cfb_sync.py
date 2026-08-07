"""
cfb_sync.py — College Football Data -> Supabase pipeline.

Responsibilities
  1. Work out the *upcoming* game week (season, season_type, week).
  2. Pull /games, /lines and /rankings for that week.
  3. Also refresh the PREVIOUS week so final scores land and bets can settle.
  4. Upsert everything into public.games without ever clobbering good data
     with nulls.

Run:  python cfb_sync.py            (env: CFBD_API_KEY, DATABASE_URL)
      python cfb_sync.py --week 6 --season 2026 --season-type regular
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import LOCK_SYNC, exec_, tx, try_advisory_lock

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("cfb_sync")

API_BASE = os.getenv("CFBD_API_BASE", "https://api.collegefootballdata.com").rstrip("/")
API_KEY = os.getenv("CFBD_API_KEY", "").strip()

# Preference order for sportsbook lines. First provider present wins.
PROVIDER_PRIORITY = [
    p.strip().lower()
    for p in os.getenv(
        "CFBD_PROVIDERS", "DraftKings,Bovada,ESPN Bet,consensus,teamrankings,numberfire"
    ).split(",")
    if p.strip()
]

RANKING_POLL = os.getenv("CFBD_POLL", "AP Top 25")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    if not API_KEY:
        raise SystemExit(
            "CFBD_API_KEY is missing. Get a free key at "
            "https://collegefootballdata.com/key and export it."
        )
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
            "User-Agent": "cfb-league-sync/1.0",
        }
    )
    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


SESSION: requests.Session | None = None


def api_get(path: str, **params: Any) -> list[dict]:
    global SESSION
    if SESSION is None:
        SESSION = make_session()
    url = f"{API_BASE}/{path.lstrip('/')}"
    clean = {k: v for k, v in params.items() if v is not None}
    try:
        r = SESSION.get(url, params=clean, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"CFBD request failed: {url} {clean}: {exc}") from exc

    if r.status_code == 401:
        raise RuntimeError("CFBD returned 401 — bad or expired CFBD_API_KEY.")
    if r.status_code == 429:
        raise RuntimeError("CFBD rate limit hit (429). Reduce sync frequency.")
    if not r.ok:
        raise RuntimeError(f"CFBD {r.status_code} for {url} {clean}: {r.text[:300]}")

    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError(f"CFBD returned non-JSON for {url}: {r.text[:200]}") from exc

    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    return data


# ---------------------------------------------------------------------------
# Field access — CFBD has shipped both camelCase and snake_case over the years.
# ---------------------------------------------------------------------------
def pick(d: dict, *names: str, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    # last-ditch: case/underscore-insensitive match
    norm = {k.replace("_", "").lower(): v for k, v in d.items()}
    for n in names:
        v = norm.get(n.replace("_", "").lower())
        if v is not None:
            return v
    return default


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Week resolution
# ---------------------------------------------------------------------------
def default_season(now: datetime) -> int:
    # CFB season Y runs Aug Y -> Jan Y+1. Jan–Jun belongs to the prior season.
    return now.year if now.month >= 7 else now.year - 1


def resolve_upcoming_week(now: datetime) -> tuple[int, str, int]:
    """
    Return (season, season_type, week) for the next week that still has
    un-played games. Uses /calendar; falls back to scanning /games.
    """
    for season in (default_season(now), default_season(now) + 1):
        try:
            cal = api_get("calendar", year=season)
        except RuntimeError as exc:
            log.warning("calendar fetch failed for %s: %s", season, exc)
            cal = []

        entries = []
        for c in cal:
            wk = to_int(pick(c, "week"))
            last = parse_dt(pick(c, "lastGameStart", "last_game_start", "endDate", "end_date"))
            first = parse_dt(pick(c, "firstGameStart", "first_game_start", "startDate", "start_date"))
            st = (pick(c, "seasonType", "season_type", default="regular") or "regular").lower()
            if wk is None or st not in ("regular", "postseason"):
                continue
            entries.append((first, last, st, wk))

        entries.sort(key=lambda e: (e[0] or datetime.max.replace(tzinfo=timezone.utc)))
        for first, last, st, wk in entries:
            # A week is "upcoming" until its final scheduled kickoff has passed.
            boundary = last or first
            if boundary and boundary + timedelta(hours=6) > now:
                log.info("Upcoming week from calendar: %s %s week %s", season, st, wk)
                return season, st, wk

    # Fallback: earliest future game in the API.
    season = default_season(now)
    for st in ("regular", "postseason"):
        games = api_get("games", year=season, seasonType=st)
        future = []
        for g in games:
            dt = parse_dt(pick(g, "startDate", "start_date"))
            wk = to_int(pick(g, "week"))
            if dt and wk and dt > now:
                future.append((dt, wk))
        if future:
            future.sort()
            log.info("Upcoming week from /games fallback: %s %s week %s", season, st, future[0][1])
            return season, st, future[0][1]

    raise RuntimeError("Could not determine an upcoming week — season may be over.")


def previous_week(season: int, season_type: str, week: int) -> tuple[int, str, int] | None:
    if week > 1:
        return season, season_type, week - 1
    if season_type == "postseason":
        return season, "regular", 15  # bowl season follows the regular season
    return None


# ---------------------------------------------------------------------------
# Fetch + shape
# ---------------------------------------------------------------------------
def fetch_rankings(season: int, season_type: str, week: int) -> dict[str, int]:
    """team name -> AP rank. Falls back to the most recent poll available."""
    ranks: dict[str, int] = {}
    for wk in [week, max(week - 1, 1)]:
        try:
            payload = api_get("rankings", year=season, week=wk, seasonType=season_type)
        except RuntimeError as exc:
            log.warning("rankings fetch failed (week %s): %s", wk, exc)
            continue
        for entry in payload:
            for poll in pick(entry, "polls", default=[]) or []:
                name = (pick(poll, "poll", default="") or "").strip()
                if name.lower() != RANKING_POLL.lower():
                    continue
                for row in pick(poll, "ranks", default=[]) or []:
                    school = pick(row, "school", "team")
                    rank = to_int(pick(row, "rank"))
                    if school and rank and 1 <= rank <= 25:
                        ranks.setdefault(school.strip(), rank)
        if ranks:
            break
    log.info("Loaded %d ranked teams (%s).", len(ranks), RANKING_POLL)
    return ranks


def fetch_lines(season: int, season_type: str, week: int) -> dict[int, dict]:
    """game_id -> {'spread_home': float|None, 'total_line': float|None, 'provider': str}"""
    try:
        payload = api_get("lines", year=season, week=week, seasonType=season_type)
    except RuntimeError as exc:
        log.warning("lines fetch failed: %s — games will sync without odds.", exc)
        return {}

    out: dict[int, dict] = {}
    for row in payload:
        gid = to_int(pick(row, "id", "gameId", "game_id"))
        if gid is None:
            continue
        books = pick(row, "lines", default=[]) or []
        best: tuple[int, dict] | None = None
        for book in books:
            provider = (pick(book, "provider", default="") or "").strip()
            spread = to_float(pick(book, "spread"))
            total = to_float(pick(book, "overUnder", "over_under", "overunder"))
            if spread is None and total is None:
                continue
            try:
                score = PROVIDER_PRIORITY.index(provider.lower())
            except ValueError:
                score = len(PROVIDER_PRIORITY) + 1
            # Prefer a book that quotes BOTH markets.
            if spread is None or total is None:
                score += 100
            cand = {"spread_home": spread, "total_line": total, "provider": provider}
            if best is None or score < best[0]:
                best = (score, cand)
        if best:
            out[gid] = best[1]
    log.info("Loaded odds for %d games.", len(out))
    return out


def fetch_games(season: int, season_type: str, week: int) -> list[dict]:
    return api_get("games", year=season, week=week, seasonType=season_type)


def shape_rows(
    raw_games: Iterable[dict],
    lines: dict[int, dict],
    ranks: dict[str, int],
    now: datetime,
) -> list[dict]:
    rows: list[dict] = []
    for g in raw_games:
        gid = to_int(pick(g, "id"))
        home = pick(g, "homeTeam", "home_team")
        away = pick(g, "awayTeam", "away_team")
        start = parse_dt(pick(g, "startDate", "start_date"))
        if gid is None or not home or not away or start is None:
            log.debug("Skipping malformed game row: %s", str(g)[:160])
            continue

        home_score = to_int(pick(g, "homePoints", "home_points", "homeScore", "home_score"))
        away_score = to_int(pick(g, "awayPoints", "away_points", "awayScore", "away_score"))
        completed = bool(pick(g, "completed", default=False))

        if completed and home_score is not None and away_score is not None:
            status = "completed"
        elif start <= now:
            status = "in_progress"
        else:
            status = "scheduled"

        odds = lines.get(gid, {})
        rows.append(
            {
                "id": gid,
                "season": to_int(pick(g, "season")) or 0,
                "week": to_int(pick(g, "week")) or 0,
                "season_type": (pick(g, "seasonType", "season_type", default="regular") or "regular").lower(),
                "start_date": start,
                "neutral_site": bool(pick(g, "neutralSite", "neutral_site", default=False)),
                "home_team": home,
                "away_team": away,
                "home_conference": pick(g, "homeConference", "home_conference"),
                "away_conference": pick(g, "awayConference", "away_conference"),
                "home_rank": ranks.get(str(home).strip()),
                "away_rank": ranks.get(str(away).strip()),
                "spread_home": odds.get("spread_home"),
                "total_line": odds.get("total_line"),
                "lines_provider": odds.get("provider"),
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
UPSERT_SQL = """
insert into public.games (
    id, season, week, season_type, start_date, neutral_site,
    home_team, away_team, home_conference, away_conference,
    home_rank, away_rank, spread_home, total_line, lines_provider,
    status, home_score, away_score, updated_at
) values (
    :id, :season, :week, :season_type, :start_date, :neutral_site,
    :home_team, :away_team, :home_conference, :away_conference,
    :home_rank, :away_rank, :spread_home, :total_line, :lines_provider,
    :status, :home_score, :away_score, now()
)
on conflict (id) do update set
    season          = excluded.season,
    week            = excluded.week,
    season_type     = excluded.season_type,
    start_date      = excluded.start_date,
    neutral_site    = excluded.neutral_site,
    home_team       = excluded.home_team,
    away_team       = excluded.away_team,
    home_conference = coalesce(excluded.home_conference, public.games.home_conference),
    away_conference = coalesce(excluded.away_conference, public.games.away_conference),
    -- ranks are intentionally overwritable with NULL (teams drop out of the poll)
    home_rank       = excluded.home_rank,
    away_rank       = excluded.away_rank,
    -- never blank an existing line just because a book pulled it
    spread_home     = coalesce(excluded.spread_home, public.games.spread_home),
    total_line      = coalesce(excluded.total_line,  public.games.total_line),
    lines_provider  = coalesce(excluded.lines_provider, public.games.lines_provider),
    home_score      = coalesce(excluded.home_score, public.games.home_score),
    away_score      = coalesce(excluded.away_score, public.games.away_score),
    -- status only moves forward: scheduled -> in_progress -> completed
    status = case
        when public.games.status = 'completed' then 'completed'
        when excluded.status = 'completed' then 'completed'
        when public.games.status = 'in_progress' and excluded.status = 'scheduled' then 'in_progress'
        else excluded.status
    end,
    updated_at = now()
"""


def upsert_games(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    with tx() as conn:
        if not try_advisory_lock(conn, LOCK_SYNC):
            log.warning("Another sync holds the lock; exiting cleanly.")
            return 0
        for row in rows:
            # Guard the CHECK constraint: a 'completed' row must carry scores.
            if row["status"] == "completed" and (
                row["home_score"] is None or row["away_score"] is None
            ):
                row["status"] = "in_progress"
            exec_(conn, UPSERT_SQL, **row)
            written += 1
    return written


def sync_week(season: int, season_type: str, week: int, now: datetime, with_odds: bool = True) -> int:
    log.info("Syncing %s %s week %s ...", season, season_type, week)
    raw = fetch_games(season, season_type, week)
    if not raw:
        log.warning("No games returned for %s %s week %s.", season, season_type, week)
        return 0
    lines = fetch_lines(season, season_type, week) if with_odds else {}
    ranks = fetch_rankings(season, season_type, week)
    rows = shape_rows(raw, lines, ranks, now)
    n = upsert_games(rows)
    log.info("Upserted %d games for %s %s week %s.", n, season, season_type, week)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync CFBD games/lines into Supabase.")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--season-type", choices=["regular", "postseason"])
    ap.add_argument("--no-prev", action="store_true", help="Skip refreshing the prior week.")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    if args.week and args.season:
        target = (args.season, args.season_type or "regular", args.week)
    else:
        target = resolve_upcoming_week(now)

    total = sync_week(*target, now=now)

    if not args.no_prev:
        prev = previous_week(*target)
        if prev:
            try:
                # Prior week: we only care about final scores, odds are moot.
                total += sync_week(*prev, now=now, with_odds=False)
            except RuntimeError as exc:
                log.warning("Previous-week refresh failed (non-fatal): %s", exc)

    log.info("Done. %d rows written.", total)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — top-level guard for cron visibility
        log.error("SYNC FAILED: %s", exc, exc_info=True)
        sys.exit(1)
