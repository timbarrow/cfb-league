"""
cfb_sync.py — College Football Data -> Supabase pipeline.

Responsibilities
  1. Work out the *upcoming* game week (season, season_type, week).
  2. Pull /games, DraftKings-first /lines and /rankings for that week.
  3. Also refresh the PREVIOUS week so final scores land and bets can settle.
  4. Upsert everything into public.games without ever clobbering good data
     with nulls.

Run:  python cfb_sync.py            (daily board refresh)
      python cfb_sync.py --live     (Tier 1 live/final scoreboard refresh)
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

# DraftKings remains the source of truth whenever it has the requested market.
# Bovada fills only a missing spread or total so the opening-week board does not
# disappear while DraftKings is still posting its early lines.
LINES_PROVIDER = os.getenv("CFBD_PROVIDER", "DraftKings").strip() or "DraftKings"
LINES_FALLBACK_PROVIDER = (
    os.getenv("CFBD_FALLBACK_PROVIDER", "Bovada").strip() or "Bovada"
)

RANKING_POLL = os.getenv("CFBD_POLL", "AP Top 25")


def ensure_sync_schema() -> None:
    """Keep scheduled jobs safe when a deployment adds sync metadata columns."""
    with tx() as conn:
        exec_(conn, "alter table public.games add column if not exists lines_updated_at timestamptz")
        exec_(conn, "alter table public.games add column if not exists scores_updated_at timestamptz")
        exec_(conn, "alter table public.games add column if not exists classification text")
        exec_(conn, "alter table public.games add column if not exists game_period integer")
        exec_(conn, "alter table public.games add column if not exists game_clock text")
        exec_(conn, "alter table public.games add column if not exists game_possession text")
        exec_(conn, "alter table public.games add column if not exists game_situation text")
        exec_(conn, "alter table public.games add column if not exists home_win_probability numeric(5,4)")
        exec_(conn, "alter table public.games add column if not exists away_win_probability numeric(5,4)")


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


def _book_by_name(books: Iterable[dict], name: str) -> dict:
    wanted = name.casefold()
    return next(
        (
            book
            for book in books
            if str(pick(book, "provider", default="") or "").strip().casefold()
            == wanted
        ),
        {},
    )


def _provider_label(spread_source: str | None, total_source: str | None) -> str | None:
    if not spread_source and not total_source:
        return None
    if spread_source == total_source:
        suffix = " backup" if spread_source == LINES_FALLBACK_PROVIDER else ""
        return f"{spread_source}{suffix}"
    pieces = []
    if spread_source:
        pieces.append(f"Spread: {spread_source}")
    if total_source:
        pieces.append(f"Total: {total_source}")
    return " · ".join(pieces)


def fetch_lines(season: int, season_type: str, week: int) -> dict[int, dict]:
    """Return one board per game, preferring DraftKings market by market."""
    try:
        payload = api_get("lines", year=season, week=week, seasonType=season_type)
    except RuntimeError as exc:
        # A failed provider request must never erase yesterday's valid board.
        raise RuntimeError(f"Odds refresh failed; existing lines preserved: {exc}") from exc

    out: dict[int, dict] = {}
    coverage = {"primary_only": 0, "mixed": 0, "fallback_only": 0}
    for row in payload:
        gid = to_int(pick(row, "id", "gameId", "game_id"))
        if gid is None:
            continue
        books = pick(row, "lines", default=[]) or []
        primary = _book_by_name(books, LINES_PROVIDER)
        fallback = _book_by_name(books, LINES_FALLBACK_PROVIDER)

        primary_spread = to_float(pick(primary, "spread"))
        primary_total = to_float(
            pick(primary, "overUnder", "over_under", "overunder")
        )
        fallback_spread = to_float(pick(fallback, "spread"))
        fallback_total = to_float(
            pick(fallback, "overUnder", "over_under", "overunder")
        )

        spread = primary_spread if primary_spread is not None else fallback_spread
        total = primary_total if primary_total is not None else fallback_total
        if spread is None and total is None:
            continue

        spread_source = (
            LINES_PROVIDER if primary_spread is not None else LINES_FALLBACK_PROVIDER
        ) if spread is not None else None
        total_source = (
            LINES_PROVIDER if primary_total is not None else LINES_FALLBACK_PROVIDER
        ) if total is not None else None
        sources = {source for source in (spread_source, total_source) if source}
        if sources == {LINES_PROVIDER}:
            coverage["primary_only"] += 1
        elif sources == {LINES_FALLBACK_PROVIDER}:
            coverage["fallback_only"] += 1
        else:
            coverage["mixed"] += 1

        out[gid] = {
            "spread_home": spread,
            "total_line": total,
            "provider": _provider_label(spread_source, total_source),
        }
    log.info(
        "Loaded odds for %d games: %d %s-only, %d mixed, %d %s-only.",
        len(out),
        coverage["primary_only"],
        LINES_PROVIDER,
        coverage["mixed"],
        coverage["fallback_only"],
        LINES_FALLBACK_PROVIDER,
    )
    return out


def fetch_games(season: int, season_type: str, week: int) -> list[dict]:
    return api_get(
        "games",
        year=season,
        week=week,
        seasonType=season_type,
        classification="fbs",
    )


def shape_rows(
    raw_games: Iterable[dict],
    lines: dict[int, dict],
    ranks: dict[str, int],
    now: datetime,
    *,
    replace_odds: bool = False,
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

        # The daily /games feed has no authoritative live state. Kickoff time
        # alone is not proof that a game started (TBD times, postponements and
        # delayed workflow runs all occur), so only the live scoreboard may
        # move a non-final game to in_progress.
        status = (
            "completed"
            if completed and home_score is not None and away_score is not None
            else "scheduled"
        )

        odds = lines.get(gid, {})
        rows.append(
            {
                "id": gid,
                "season": to_int(pick(g, "season")) or 0,
                "week": to_int(pick(g, "week")) or 0,
                "season_type": (pick(g, "seasonType", "season_type", default="regular") or "regular").lower(),
                "classification": "fbs",
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
                "replace_odds": replace_odds,
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
    classification,
    home_team, away_team, home_conference, away_conference,
    home_rank, away_rank, spread_home, total_line, lines_provider,
    status, home_score, away_score, lines_updated_at, scores_updated_at, updated_at
) values (
    :id, :season, :week, :season_type, :start_date, :neutral_site,
    :classification,
    :home_team, :away_team, :home_conference, :away_conference,
    :home_rank, :away_rank, :spread_home, :total_line, :lines_provider,
    :status, :home_score, :away_score,
    case when :replace_odds then now() else null end,
    case when :status <> 'scheduled' then now() else null end,
    now()
)
on conflict (id) do update set
    season          = excluded.season,
    week            = excluded.week,
    season_type     = excluded.season_type,
    classification  = excluded.classification,
    start_date      = excluded.start_date,
    neutral_site    = excluded.neutral_site,
    home_team       = excluded.home_team,
    away_team       = excluded.away_team,
    home_conference = coalesce(excluded.home_conference, public.games.home_conference),
    away_conference = coalesce(excluded.away_conference, public.games.away_conference),
    -- ranks are intentionally overwritable with NULL (teams drop out of the poll)
    home_rank       = excluded.home_rank,
    away_rank       = excluded.away_rank,
    -- A daily book refresh replaces the market, including clearing stale
    -- stale lines. Score-only refreshes preserve the last daily board.
    spread_home     = case when :replace_odds then excluded.spread_home
                           else coalesce(excluded.spread_home, public.games.spread_home) end,
    total_line      = case when :replace_odds then excluded.total_line
                           else coalesce(excluded.total_line, public.games.total_line) end,
    lines_provider  = case when :replace_odds then excluded.lines_provider
                           else coalesce(excluded.lines_provider, public.games.lines_provider) end,
    lines_updated_at = case when :replace_odds then now()
                            else public.games.lines_updated_at end,
    home_score      = coalesce(excluded.home_score, public.games.home_score),
    away_score      = coalesce(excluded.away_score, public.games.away_score),
    scores_updated_at = case when excluded.status <> 'scheduled' then now()
                             else public.games.scores_updated_at end,
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
        # Treat the filtered /games response as the authoritative FBS set for
        # each synced week. This also repairs rows tagged by older syncs that
        # loaded every NCAA classification.
        scopes = {
            (row["season"], row["season_type"], row["week"])
            for row in rows
        }
        for season, season_type, week in scopes:
            exec_(
                conn,
                """update public.games
                      set classification = null
                    where season = :season
                      and season_type = :season_type
                      and week = :week
                      and classification = 'fbs'""",
                season=season,
                season_type=season_type,
                week=week,
            )
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
    rows = shape_rows(raw, lines, ranks, now, replace_odds=with_odds)
    n = upsert_games(rows)
    log.info("Upserted %d games for %s %s week %s.", n, season, season_type, week)
    return n


def _live_status(game: dict) -> str:
    """Normalize the scoreboard's shifting status vocabulary."""
    raw = str(pick(game, "status", default="") or "").strip().casefold()
    if "final" in raw or "complete" in raw or raw == "post":
        return "completed"
    if any(word in raw for word in ("progress", "live", "halftime", "delay")):
        return "in_progress"
    period = to_int(pick(game, "period", default=0)) or 0
    return "in_progress" if period > 0 else "scheduled"


def shape_live_rows(payload: Iterable[dict]) -> list[dict]:
    """Shape Tier 1 /scoreboard rows for score-only updates."""
    rows: list[dict] = []
    for game in payload:
        gid = to_int(pick(game, "id", "gameId", "game_id"))
        home = pick(game, "homeTeam", "home_team", default={}) or {}
        away = pick(game, "awayTeam", "away_team", default={}) or {}
        home_score = to_int(pick(home, "points", "score"))
        away_score = to_int(pick(away, "points", "score"))
        game_period = to_int(pick(game, "period"))
        raw_clock = pick(game, "clock")
        game_clock = str(raw_clock).strip() if raw_clock is not None else None
        possession = str(pick(game, "possession", default="") or "").strip().lower() or None
        situation = str(pick(game, "situation", "gameSituation", default="") or "").strip() or None
        home_win_probability = pick(home, "winProbability", "win_probability")
        away_win_probability = pick(away, "winProbability", "win_probability")
        status = _live_status(game)
        if gid is None:
            continue
        if status == "completed" and (home_score is None or away_score is None):
            status = "in_progress"
        rows.append(
            {
                "id": gid,
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
                "game_period": game_period,
                "game_clock": game_clock,
                "game_possession": possession,
                "game_situation": situation,
                "home_win_probability": home_win_probability,
                "away_win_probability": away_win_probability,
            }
        )
    return rows


LIVE_UPDATE_SQL = """
update public.games
   set home_score = coalesce(:home_score, home_score),
       away_score = coalesce(:away_score, away_score),
       game_period = coalesce(:game_period, game_period),
       game_clock = coalesce(:game_clock, game_clock),
       game_possession = coalesce(:game_possession, game_possession),
       game_situation = :game_situation,
       home_win_probability = coalesce(:home_win_probability, home_win_probability),
       away_win_probability = coalesce(:away_win_probability, away_win_probability),
       status = case
           when status = 'completed' then 'completed'
           when :status = 'completed' then 'completed'
           when :status = 'in_progress' then 'in_progress'
           else status
       end,
       scores_updated_at = now(),
       updated_at = now()
 where id = :id
   and classification = 'fbs'
"""


def sync_live_scoreboard() -> int:
    """Refresh live/final scores in one Tier 1 API call."""
    log.info("Refreshing the CFBD live scoreboard ...")
    rows = shape_live_rows(api_get("scoreboard", classification="fbs"))
    written = 0
    with tx() as conn:
        if not try_advisory_lock(conn, LOCK_SYNC):
            log.warning("Another sync holds the lock; exiting cleanly.")
            return 0
        for row in rows:
            result = exec_(conn, LIVE_UPDATE_SQL, **row)
            written += max(result.rowcount or 0, 0)
    log.info("Updated %d loaded games from the live scoreboard.", written)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync CFBD games/lines into Supabase.")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--season-type", choices=["regular", "postseason"])
    ap.add_argument("--no-prev", action="store_true", help="Skip refreshing the prior week.")
    ap.add_argument(
        "--live",
        action="store_true",
        help="Use the Tier 1 scoreboard for a one-call live/final score refresh.",
    )
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    ensure_sync_schema()

    if args.live:
        sync_live_scoreboard()
        return 0

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
