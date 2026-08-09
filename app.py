"""
app.py — Mobile-first Streamlit front end for a 10-person private CFB
betting league. Runs free on Streamlit Community Cloud against Supabase.

Tabs
  1. Place Bets  — upcoming week only, filtered by conference / Top 25 / market
  2. My Tickets  — cash, open stakes, settled P/L, open + settled tables
  3. Leaderboard — net worth ranking, fully transparent ticket inspection
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError

from db import (
    exec_,
    hash_password,
    q,
    q1,
    ro,
    tx,
    valid_username,
    verify_password,
)

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - tzdata missing
    LOCAL_TZ = timezone.utc

CENT = Decimal("0.01")
STARTING_BANKROLL = Decimal("10000.00")
DEFAULT_MULTIPLIER = Decimal("2.00")  # no-vig league: stake + equal profit

# =====================================================================
# Page config + mobile-first CSS
# =====================================================================
st.set_page_config(
    page_title="Fourth Down | CFB League",
    page_icon="🏈",
    layout="centered",           # narrower default = better on phones
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=IBM+Plex+Mono:wght@500;600&family=Manrope:wght@400;500;600;700&display=swap');

:root {
    --ink: #07110e;
    --panel: #0c1b16;
    --panel-2: #11261e;
    --line: rgba(221, 239, 229, .14);
    --chalk: #f4f2e8;
    --muted: #9db1a9;
    --field: #27d17f;
    --field-dark: #0d6b45;
    --signal: #ffb547;
    --danger: #ff6b5f;
}

html { scroll-behavior: smooth; }
body, [class*="css"] { font-family: "Manrope", sans-serif; }
[data-testid="stAppViewContainer"] {
    color: var(--chalk);
    background:
        radial-gradient(circle at 78% -12%, rgba(39, 209, 127, .19), transparent 34rem),
        radial-gradient(circle at -10% 32%, rgba(255, 181, 71, .08), transparent 26rem),
        var(--ink);
}
[data-testid="stHeader"] { background: transparent; }
.block-container { padding: 1rem 1.25rem 5rem !important; max-width: 1100px; }
#MainMenu, footer { visibility: hidden; }

h1, h2, h3, h4, .matchup, .display-type {
    font-family: "Barlow Condensed", sans-serif !important;
    letter-spacing: -.025em;
}
p, label, [data-testid="stCaptionContainer"] { color: var(--muted); }

/* Brand / hero */
.brand-lockup { display:flex; align-items:center; gap:.75rem; }
.brand-mark {
    display:grid; place-items:center; width:42px; height:42px; border-radius:11px;
    background:var(--field); color:var(--ink); font:800 1.35rem "Barlow Condensed";
    box-shadow:0 0 28px rgba(39,209,127,.22); transform:rotate(-3deg);
}
.brand-name { color:var(--chalk); font:800 1.3rem/1 "Barlow Condensed"; text-transform:uppercase; letter-spacing:.03em; }
.brand-sub { color:var(--muted); font:500 .62rem/1.5 "IBM Plex Mono"; letter-spacing:.12em; text-transform:uppercase; }
.auth-hero {
    position:relative; overflow:hidden; margin:.75rem 0 1.25rem; min-height:330px;
    padding:clamp(2rem,7vw,4.6rem); border:1px solid var(--line); border-radius:24px;
    background:
      linear-gradient(90deg, rgba(7,17,14,.98) 0%, rgba(7,17,14,.84) 52%, rgba(7,17,14,.22) 100%),
      repeating-linear-gradient(90deg, transparent 0 9.7%, rgba(244,242,232,.07) 9.7% 10%),
      linear-gradient(135deg, #126943, #0a3728);
    box-shadow:0 28px 80px rgba(0,0,0,.28);
}
.auth-hero:after {
    content:"50"; position:absolute; right:2%; bottom:-26%; color:rgba(244,242,232,.08);
    font:800 17rem/1 "Barlow Condensed"; letter-spacing:-.1em;
}
.eyebrow { color:var(--field); font:600 .7rem "IBM Plex Mono"; letter-spacing:.16em; text-transform:uppercase; }
.auth-title { max-width:650px; margin:.85rem 0 1rem; color:var(--chalk); font:800 clamp(3.35rem,10vw,6.8rem)/.78 "Barlow Condensed"; text-transform:uppercase; letter-spacing:-.045em; }
.auth-copy { max-width:500px; color:#bfd0c9; font-size:1rem; line-height:1.65; }
.hero-proof { display:flex; flex-wrap:wrap; gap:.6rem 1.4rem; margin-top:2rem; color:var(--chalk); font:600 .69rem "IBM Plex Mono"; text-transform:uppercase; letter-spacing:.08em; }
.hero-proof span:before { content:""; display:inline-block; width:7px; height:7px; margin-right:.5rem; border-radius:50%; background:var(--signal); }
div[data-testid="stMainBlockContainer"]:has(.auth-hero) { max-width:900px; }

/* Controls */
input, textarea, select { font-size:16px !important; }
[data-baseweb="input"] > div, [data-baseweb="base-input"], [data-baseweb="select"] > div {
    min-height:48px; border-color:var(--line) !important; background:#0a1713 !important; border-radius:10px !important;
}
.stButton > button, .stFormSubmitButton > button {
    min-height:48px; width:100%; border-radius:10px; border:1px solid var(--line);
    font:700 .76rem "IBM Plex Mono"; letter-spacing:.05em; text-transform:uppercase;
    transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover { transform:translateY(-1px); border-color:var(--field); }
.stButton > button[kind^="primary"], .stFormSubmitButton > button[kind^="primary"] {
    background:var(--field); color:var(--ink); border-color:var(--field); box-shadow:0 10px 26px rgba(39,209,127,.15);
}
.stButton > button[kind^="primary"] p, .stFormSubmitButton > button[kind^="primary"] p { color:var(--ink) !important; }
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible { outline:3px solid var(--signal); outline-offset:3px; }

/* Navigation */
.stTabs [data-baseweb="tab-list"] {
    gap:4px; padding:5px; overflow-x:auto; border:1px solid var(--line); border-radius:14px;
    background:rgba(12,27,22,.82); backdrop-filter:blur(16px);
}
.stTabs [data-baseweb="tab"] {
    flex:1 0 auto; min-height:46px; padding:0 18px; border-radius:9px;
    color:var(--muted); font:700 .74rem "IBM Plex Mono"; letter-spacing:.03em; text-transform:uppercase;
}
.stTabs [aria-selected="true"] { color:var(--ink) !important; background:var(--chalk) !important; }
.stTabs [aria-selected="true"] p { color:var(--ink) !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }

/* Dashboard grammar */
.topbar { padding:.3rem 0 .95rem; }
.user-chip { text-align:right; }
.user-chip strong { display:block; color:var(--chalk); font:700 1.25rem "Barlow Condensed"; }
.user-chip span { color:var(--field); font:600 .68rem "IBM Plex Mono"; letter-spacing:.06em; text-transform:uppercase; }
.board-hero { display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; margin:2rem 0 1rem; }
.board-title { margin:0; color:var(--chalk); font:800 clamp(2.5rem,7vw,4.8rem)/.88 "Barlow Condensed"; text-transform:uppercase; }
.board-count { color:var(--signal); font:600 .68rem "IBM Plex Mono"; letter-spacing:.09em; text-transform:uppercase; }
.process-rail { display:grid; grid-template-columns:repeat(3,1fr); margin:0 0 1.2rem; border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.process-step { padding:.8rem 1rem; color:var(--muted); background:rgba(12,27,22,.62); font:600 .66rem "IBM Plex Mono"; letter-spacing:.07em; text-transform:uppercase; }
.process-step + .process-step { border-left:1px solid var(--line); }
.process-step b { color:var(--field); margin-right:.45rem; }
.board-summary { margin:.85rem 0 1.2rem; color:var(--muted); font:500 .72rem "IBM Plex Mono"; }

div[data-testid="stExpander"] { border:1px solid var(--line) !important; border-radius:12px !important; background:rgba(12,27,22,.58); }
div[data-testid="stExpander"] summary { min-height:48px; font:700 .73rem "IBM Plex Mono"; letter-spacing:.03em; text-transform:uppercase; }
div[data-testid="stVerticalBlockBorderWrapper"] {
    margin-bottom:.35rem; border:1px solid var(--line) !important; border-radius:16px !important;
    background:linear-gradient(145deg, rgba(17,38,30,.96), rgba(10,24,19,.96));
    box-shadow:0 14px 34px rgba(0,0,0,.16); transition:border-color .2s ease, transform .2s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover { border-color:rgba(39,209,127,.42) !important; transform:translateY(-1px); }
.game-kicker { display:flex; justify-content:space-between; gap:1rem; margin-bottom:.45rem; color:var(--muted); font:500 .64rem "IBM Plex Mono"; letter-spacing:.06em; text-transform:uppercase; }
.game-number { color:var(--signal); }
.matchup { color:var(--chalk); font-size:clamp(1.45rem,4vw,2rem); font-weight:700; line-height:1.08; }
.versus { color:var(--muted); padding:0 .3rem; font-size:.75em; }
.meta { color:var(--muted); font:500 .7rem/1.55 "IBM Plex Mono"; margin-top:.38rem; }
.rank-badge { display:inline-grid; place-items:center; min-width:24px; height:20px; margin-right:5px; padding:0 5px; border:1px solid rgba(255,181,71,.5); border-radius:5px; color:var(--signal); font:600 .62rem "IBM Plex Mono"; vertical-align:middle; }
.pill { display:inline-block; margin:.35rem .28rem 0 0; padding:3px 8px; border:1px solid var(--line); border-radius:999px; color:#b5c7c0; font:500 .58rem "IBM Plex Mono"; letter-spacing:.04em; text-transform:uppercase; }

div[role="radiogroup"] { gap:7px; }
div[role="radiogroup"] > label {
    display:flex; align-items:center; min-height:48px; width:100%; margin:0; padding:10px 13px;
    border:1px solid var(--line); border-radius:10px; background:#091612; transition:.16s ease;
}
div[role="radiogroup"] > label:hover { border-color:var(--field); background:#0c2119; }
div[role="radiogroup"] > label:has(input:checked) { border-color:var(--field); background:rgba(39,209,127,.11); box-shadow:inset 3px 0 0 var(--field); }
div[role="radiogroup"] > label p { color:var(--chalk); font-weight:650; }

/* Stats, tickets, leaderboard */
[data-testid="stMetric"] { min-height:116px; padding:1rem; border:1px solid var(--line); border-radius:13px; background:rgba(12,27,22,.75); }
[data-testid="stMetricLabel"] p { color:var(--muted); font:600 .65rem "IBM Plex Mono"; letter-spacing:.08em; text-transform:uppercase; }
[data-testid="stMetricValue"] { color:var(--chalk); font:700 1.8rem "Barlow Condensed"; }
.section-head { display:flex; align-items:baseline; justify-content:space-between; gap:1rem; margin:2rem 0 .8rem; border-bottom:1px solid var(--line); }
.section-head h3 { margin:0 0 .65rem; color:var(--chalk); font-size:1.55rem; text-transform:uppercase; }
.section-head span { color:var(--muted); font:500 .62rem "IBM Plex Mono"; text-transform:uppercase; }
.ticket-card { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:.5rem 1.2rem; padding:1rem 1.1rem; margin:.55rem 0; border:1px solid var(--line); border-left:3px solid var(--signal); border-radius:11px; background:rgba(12,27,22,.7); }
.ticket-card.settled-won { border-left-color:var(--field); }
.ticket-card.settled-lost { border-left-color:var(--danger); }
.ticket-pick { color:var(--chalk); font:700 1.2rem "Barlow Condensed"; }
.ticket-game, .ticket-meta { color:var(--muted); font:500 .63rem/1.55 "IBM Plex Mono"; text-transform:uppercase; }
.ticket-money { grid-row:1 / span 2; grid-column:2; text-align:right; }
.ticket-money strong { display:block; color:var(--chalk); font:700 1.25rem "Barlow Condensed"; }
.ticket-money span { color:var(--muted); font:500 .58rem "IBM Plex Mono"; text-transform:uppercase; }
.ticket-result { display:inline-block; margin-left:.4rem; color:var(--field); }
.ticket-result.lost { color:var(--danger); }
.ticket-result.push { color:var(--signal); }
.leader-row { display:grid; grid-template-columns:42px minmax(0,1fr) auto; align-items:center; gap:.7rem; padding:.85rem 1rem; margin:.42rem 0; border:1px solid var(--line); border-radius:11px; background:rgba(12,27,22,.68); }
.leader-row.is-you { border-color:rgba(39,209,127,.55); background:rgba(39,209,127,.08); }
.leader-rank { color:var(--signal); font:700 1.25rem "Barlow Condensed"; }
.leader-name { color:var(--chalk); font:700 1rem "Manrope"; }
.leader-record { color:var(--muted); font:500 .6rem "IBM Plex Mono"; text-transform:uppercase; }
.leader-worth { text-align:right; color:var(--chalk); font:700 1.35rem "Barlow Condensed"; }
.leader-worth small { display:block; color:var(--muted); font:500 .55rem "IBM Plex Mono"; text-transform:uppercase; }
.footer-note { margin-top:2rem; padding-top:1rem; border-top:1px solid var(--line); color:var(--muted); font:500 .6rem/1.6 "IBM Plex Mono"; text-align:center; text-transform:uppercase; letter-spacing:.05em; }

[data-testid="stAlert"] { border-radius:11px; border:1px solid var(--line); }
.stDataFrame { font-size:.82rem; border:1px solid var(--line); border-radius:10px; overflow:hidden; }

@media (max-width: 640px) {
    .block-container { padding:.55rem .75rem 6rem !important; }
    div[data-testid="stHorizontalBlock"] { flex-wrap:wrap; gap:.5rem; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] { min-width:calc(50% - .5rem) !important; flex:1 1 calc(50% - .5rem) !important; }
    div[data-testid="stHorizontalBlock"]:has(.brand-lockup) { flex-wrap:nowrap; align-items:center; gap:.4rem; }
    div[data-testid="stHorizontalBlock"]:has(.brand-lockup) > div[data-testid="column"] { min-width:0 !important; }
    div[data-testid="stHorizontalBlock"]:has(.brand-lockup) > div[data-testid="column"]:nth-child(1) { flex:1 1 auto !important; }
    div[data-testid="stHorizontalBlock"]:has(.brand-lockup) > div[data-testid="column"]:nth-child(2) { flex:0 0 104px !important; }
    div[data-testid="stHorizontalBlock"]:has(.brand-lockup) > div[data-testid="column"]:nth-child(3) { flex:0 0 76px !important; }
    .brand-mark { width:36px; height:36px; }
    .brand-sub { display:none; }
    .brand-name { font-size:1.05rem; }
    .auth-hero { min-height:350px; padding:2rem 1.35rem; border-radius:18px; }
    .auth-hero:after { right:-8%; font-size:12rem; }
    .auth-title { font-size:3.65rem; }
    .board-hero { align-items:flex-start; flex-direction:column; }
    .process-step { padding:.72rem .5rem; font-size:.56rem; text-align:center; }
    .process-step b { display:block; margin:0 0 .25rem; }
    .stTabs [data-baseweb="tab"] { padding:0 11px; font-size:.62rem; }
    .user-chip { text-align:left; }
    .matchup { font-size:1.55rem; }
    [data-testid="stMetric"] { min-height:102px; padding:.8rem; }
    [data-testid="stMetricValue"] { font-size:1.45rem; }
    .ticket-card { grid-template-columns:minmax(0,1fr) auto; padding:.9rem; }
    .leader-row { grid-template-columns:30px minmax(0,1fr) auto; padding:.75rem; }
}

@media (prefers-reduced-motion: reduce) {
    *, *:before, *:after { scroll-behavior:auto !important; transition:none !important; }
}
</style>
""",
    unsafe_allow_html=True,
)

# The approved Saturday Desk visual system lives separately so the interface
# can evolve without obscuring the betting and settlement logic below.
st.markdown(
    f"<style>{Path(__file__).with_name('saturday_desk.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)


# =====================================================================
# Formatting helpers
# =====================================================================
def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(v) -> str:
    d = money(v)
    return f"-${abs(d):,.2f}" if d < 0 else f"${d:,.2f}"


def fmt_line(v) -> str:
    """+3.5 / -7 / PK"""
    d = Decimal(str(v))
    if d == 0:
        return "PK"
    s = f"{d.normalize():f}"
    return f"+{s}" if d > 0 else s


def fmt_total(v) -> str:
    return f"{Decimal(str(v)).normalize():f}"


def rank_html(rank) -> str:
    return f'<span class="rank-badge">#{int(rank)}</span>' if rank else ""


def _safe_kickoff(dt: datetime) -> str:
    """Portable kickoff formatting (%-d / %-I are not available on Windows)."""
    if dt is None:
        return "TBD"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a %b} {local.day} · {hour}:{local:%M} {ampm} CT"


BET_LABEL = {
    "spread_home": "Spread (Home)",
    "spread_away": "Spread (Away)",
    "over": "Over",
    "under": "Under",
}


def describe_bet(row) -> str:
    bt = row["bet_type"]
    line = row["line_placed"]
    if bt == "spread_home":
        return f"{row['home_team']} {fmt_line(line)}"
    if bt == "spread_away":
        return f"{row['away_team']} {fmt_line(line)}"
    if bt == "over":
        return f"Over {fmt_total(line)}"
    return f"Under {fmt_total(line)}"


# =====================================================================
# Data access (cached — cleared whenever a bet is written)
# =====================================================================
@st.cache_data(ttl=45, show_spinner=False)
def load_upcoming_games() -> list[dict]:
    """
    Games for the NEXT game week only, and only those that have not kicked off.
    The 'next week' is derived from the earliest future kickoff in the table.
    """
    sql = """
    with nxt as (
        select season, season_type, week
          from public.games
         where start_date > now() and status = 'scheduled'
         order by start_date
         limit 1
    )
    select g.id, g.season, g.week, g.season_type, g.start_date, g.neutral_site,
           g.home_team, g.away_team, g.home_conference, g.away_conference,
           g.home_rank, g.away_rank, g.spread_home, g.total_line,
           g.lines_provider, g.status
      from public.games g
      join nxt on g.season = nxt.season
                and g.season_type = nxt.season_type
                and g.week = nxt.week
     where g.start_date > now()
       and g.status = 'scheduled'
       and (g.spread_home is not null or g.total_line is not null)
     order by g.start_date, g.home_team
    """
    with ro() as conn:
        return q(conn, sql)


@st.cache_data(ttl=30, show_spinner=False)
def load_user_bets(user_id: str) -> list[dict]:
    sql = """
    select b.id, b.bet_type, b.line_placed, b.wager_amount, b.payout_multiplier,
           b.status, b.payout_amount, b.created_at, b.settled_at,
           g.home_team, g.away_team, g.week, g.start_date,
           g.home_score, g.away_score, g.status as game_status
      from public.bets b
      join public.games g on g.id = b.game_id
     where b.user_id = :uid
     order by b.created_at desc
    """
    with ro() as conn:
        return q(conn, sql, uid=user_id)


@st.cache_data(ttl=30, show_spinner=False)
def load_leaderboard() -> list[dict]:
    with ro() as conn:
        return q(conn, "select * from public.leaderboard order by net_worth desc, username")


@st.cache_data(ttl=30, show_spinner=False)
def load_weekly_standings() -> list[dict]:
    """End-of-week league value, derived from settled tickets in the latest season."""
    sql = """
    with current_season as (
        select max(season) as season from public.games
    ),
    completed_weeks as (
        select distinct g.season, g.week
          from public.games g
          join current_season cs on cs.season = g.season
         where g.status = 'completed'
    )
    select u.id as user_id, u.username, w.season, w.week,
           cast(
               :starting + coalesce((
                   select sum(b.payout_amount - b.wager_amount)
                     from public.bets b
                     join public.games g on g.id = b.game_id
                    where b.user_id = u.id
                      and b.status in ('won', 'lost', 'push')
                      and g.season = w.season
                      and g.week <= w.week
               ), 0)
               as numeric(14,2)
           ) as net_worth
      from public.users u
      cross join completed_weeks w
     order by w.week, net_worth desc, u.username
    """
    with ro() as conn:
        return q(conn, sql, starting=STARTING_BANKROLL)


@st.cache_data(ttl=30, show_spinner=False)
def load_all_bets() -> list[dict]:
    """Every ticket in the league — the transparency requirement."""
    sql = """
    select b.user_id, b.id, b.bet_type, b.line_placed, b.wager_amount,
           b.payout_multiplier, b.status, b.payout_amount, b.created_at,
           g.home_team, g.away_team, g.week, g.start_date,
           g.home_score, g.away_score
      from public.bets b
      join public.games g on g.id = b.game_id
     order by b.created_at desc
    """
    with ro() as conn:
        return q(conn, sql)


def refresh_data() -> None:
    load_upcoming_games.clear()
    load_user_bets.clear()
    load_leaderboard.clear()
    load_weekly_standings.clear()
    load_all_bets.clear()


def get_balance(user_id: str) -> Decimal:
    with ro() as conn:
        row = q1(conn, "select balance from public.users where id = :id", id=user_id)
    return money(row["balance"]) if row else Decimal("0.00")


# =====================================================================
# Auth
# =====================================================================
def _secret(name: str, default: str = "") -> str:
    """st.secrets raises when no secrets.toml exists — never let that crash the app."""
    import os

    try:
        val = st.secrets.get(name)
        if val is not None:
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default)


def register_user(username: str, password: str, invite: str) -> tuple[bool, str]:
    required = _secret("LEAGUE_INVITE_CODE").strip()
    if required and (invite or "").strip() != required:
        return False, "Invalid league invite code."
    if not valid_username(username):
        return False, "Username must be 2–24 characters (letters, numbers, . _ -)."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."

    try:
        with tx() as conn:
            existing = q1(
                conn,
                "select 1 from public.users where lower(username) = lower(:u)",
                u=username.strip(),
            )
            if existing:
                return False, "That username is taken."
            exec_(
                conn,
                """insert into public.users (username, password_hash, balance)
                   values (:u, :p, :b)""",
                u=username.strip(),
                p=hash_password(password),
                b=STARTING_BANKROLL,
            )
    except IntegrityError:
        return False, "That username is taken."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not create account: {exc}"

    return True, "Account created — you're staked with $10,000. Use the first tab to enter."


def login_user(username: str, password: str) -> tuple[bool, str]:
    try:
        with ro() as conn:
            row = q1(
                conn,
                """select id, username, password_hash, is_admin
                     from public.users where lower(username) = lower(:u)""",
                u=(username or "").strip(),
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"Database unavailable: {exc}"

    if not row or not verify_password(password or "", row["password_hash"]):
        return False, "Incorrect username or password."

    st.session_state.user = {
        "id": str(row["id"]),
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
    }
    refresh_data()
    return True, ""


def auth_screen() -> None:
    st.markdown(
        """
        <div class="fd-scorebar">
            <div class="fd-brand"><span class="fd-ball">4D</span><span>Fourth Down</span></div>
            <div class="fd-readout fd-week"><span>League</span><strong>Private</strong></div>
            <div class="fd-readout fd-kickoff-readout"><span>Season</span><strong>2026</strong></div>
            <div class="fd-readout"><span>Starting stack</span><strong>$10,000</strong></div>
        </div>
        <section class="fd-auth-card">
            <div class="fd-auth-copy">
                <small>Saturday credentials</small>
                <h1>Call your shots.</h1>
                <p>One bankroll. Every weekly line. A full season of receipts and bragging rights.</p>
            </div>
            <div class="fd-auth-pass"><span>League access</span><strong>WEEK 01</strong></div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    tab_in, tab_up = st.tabs(["Enter the league", "Join with a code"])

    with tab_in:
        with st.form("login_form"):
            u = st.text_input("Username", key="li_user", autocomplete="username")
            p = st.text_input("Password", type="password", key="li_pass",
                              autocomplete="current-password")
            if st.form_submit_button("Enter the league", type="primary", width="stretch"):
                ok, msg = login_user(u, p)
                if ok:
                    st.rerun()
                else:
                    st.error(msg)

    with tab_up:
        with st.form("signup_form"):
            u = st.text_input("Choose a username", key="su_user")
            p = st.text_input("Choose a password (6+ chars)", type="password", key="su_pass")
            p2 = st.text_input("Confirm password", type="password", key="su_pass2")
            code = st.text_input("League code", key="su_code")
            if st.form_submit_button("Claim my $10K stack", width="stretch"):
                if p != p2:
                    st.error("Passwords don't match.")
                else:
                    ok, msg = register_user(u, p, code)
                    (st.success if ok else st.error)(msg)


# =====================================================================
# Bet placement — race-safe
# =====================================================================
def place_bet(
    user_id: str,
    game_id: int,
    bet_type: str,
    line_shown: Decimal,
    wager: Decimal,
) -> tuple[bool, str]:
    wager = money(wager)
    if wager < 1:
        return False, "Minimum wager is $1."

    try:
        with tx() as conn:
            # 1) Lock the bankroll row first — serialises concurrent submissions
            #    from the same account (two phones, double-tap, etc.).
            user = q1(
                conn,
                "select balance from public.users where id = :id for update",
                id=user_id,
            )
            if not user:
                return False, "Account not found."
            balance = money(user["balance"])
            if wager > balance:
                return False, f"Insufficient funds. Available: {fmt_money(balance)}."

            # 2) Re-read the game inside the transaction: enforces the kickoff
            #    lock and catches line movement between render and submit.
            game = q1(
                conn,
                """select spread_home, total_line, status, start_date,
                          home_team, away_team
                     from public.games where id = :g""",
                g=game_id,
            )
            if not game:
                return False, "Game not found."
            if game["status"] != "scheduled":
                return False, "This game has already started. Betting is closed."
            if game["start_date"] <= datetime.now(timezone.utc):
                return False, "Kickoff has passed. Betting is closed."

            if bet_type in ("spread_home", "spread_away"):
                if game["spread_home"] is None:
                    return False, "No spread posted for this game."
                current = Decimal(str(game["spread_home"]))
                if bet_type == "spread_away":
                    current = -current
                market = "spread"
            else:
                if game["total_line"] is None:
                    return False, "No total posted for this game."
                current = Decimal(str(game["total_line"]))
                market = "total"

            if current != Decimal(str(line_shown)):
                return False, (
                    f"The {market} moved to {fmt_line(current) if market == 'spread' else fmt_total(current)}"
                    " before your bet landed. Refresh and re-submit."
                )

            # 3) Debit + write the ticket atomically.
            exec_(
                conn,
                """insert into public.bets
                       (user_id, game_id, bet_type, line_placed, wager_amount, payout_multiplier)
                   values (:u, :g, :t, :l, :w, :m)""",
                u=user_id,
                g=game_id,
                t=bet_type,
                l=current,
                w=wager,
                m=DEFAULT_MULTIPLIER,
            )
            exec_(
                conn,
                "update public.users set balance = balance - :w where id = :u",
                w=wager,
                u=user_id,
            )
    except IntegrityError as exc:
        msg = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
        if "bets_one_open_per_market" in msg:
            return False, "You already have an open ticket on that market."
        if "users_balance_nonneg" in msg:
            return False, "That wager would overdraw your bankroll."
        return False, "Bet rejected by the database."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not place bet: {exc}"

    refresh_data()
    to_win = money(wager * DEFAULT_MULTIPLIER) - wager
    return True, f"Ticket placed: {fmt_money(wager)} to win {fmt_money(to_win)}."


# =====================================================================
# Tab 1 — Place Bets
# =====================================================================
def market_options(game: dict, bet_filter: str) -> list[tuple[str, Decimal, str]]:
    """(bet_type, line, label) tuples for the selection radio."""
    opts: list[tuple[str, Decimal, str]] = []
    if bet_filter in ("All markets", "Spread (ATS)") and game["spread_home"] is not None:
        sh = Decimal(str(game["spread_home"]))
        opts.append(("spread_away", -sh, f"{game['away_team']}  {fmt_line(-sh)}"))
        opts.append(("spread_home", sh, f"{game['home_team']}  {fmt_line(sh)}"))
    if bet_filter in ("All markets", "Over / Under") and game["total_line"] is not None:
        tl = Decimal(str(game["total_line"]))
        opts.append(("over", tl, f"Over  {fmt_total(tl)}"))
        opts.append(("under", tl, f"Under  {fmt_total(tl)}"))
    return opts


def _legacy_tab_place_bets(user: dict, balance: Decimal) -> None:
    games = load_upcoming_games()

    if not games:
        st.info(
            "No upcoming games are loaded yet. Run `cfb_sync.py` (or wait for the "
            "scheduled GitHub Action) to pull this week's board."
        )
        return

    wk = games[0]
    st.markdown(
        f"""
        <div class="board-hero">
            <div>
                <div class="eyebrow">{escape(str(wk['season_type']))} season · {wk['season']}</div>
                <div class="board-title">Week {wk['week']} board</div>
            </div>
            <div class="board-count">Lines lock at kickoff</div>
        </div>
        <div class="process-rail" aria-label="How to place a bet">
            <div class="process-step"><b>01</b> Pick a game</div>
            <div class="process-step"><b>02</b> Choose a side</div>
            <div class="process-step"><b>03</b> Set your stake</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    conferences = sorted(
        {c for g in games for c in (g["home_conference"], g["away_conference"]) if c}
    )

    with st.expander("Tune the board", expanded=False):
        bet_filter = st.radio(
            "Market",
            ["All markets", "Spread (ATS)", "Over / Under"],
            horizontal=False,
            key="f_market",
        )
        conf_pick = st.multiselect(
            "Conference", conferences, default=[], key="f_conf",
            placeholder="All conferences",
        )
        top25_only = st.toggle("Top 25 teams only", value=False, key="f_top25")
        hide_bet = st.toggle("Hide games I've already bet", value=False, key="f_hide")
        team_search = st.text_input("Search team", key="f_search", placeholder="e.g. Georgia")

    my_bets = load_user_bets(user["id"])
    bet_matchups = {
        (b["home_team"], b["away_team"]) for b in my_bets if b["status"] == "pending"
    }

    visible: list[dict] = []
    for g in games:
        if conf_pick and not (
            g["home_conference"] in conf_pick or g["away_conference"] in conf_pick
        ):
            continue
        if top25_only and not (g["home_rank"] or g["away_rank"]):
            continue
        if team_search:
            needle = team_search.strip().lower()
            if needle not in g["home_team"].lower() and needle not in g["away_team"].lower():
                continue
        if hide_bet and (g["home_team"], g["away_team"]) in bet_matchups:
            continue
        if not market_options(g, bet_filter):
            continue
        visible.append(g)

    st.markdown(
        f"<div class='board-summary'>{len(visible)} of {len(games)} games showing &nbsp;·&nbsp; "
        f"Available {fmt_money(balance)} &nbsp;·&nbsp; Even money · No vig</div>",
        unsafe_allow_html=True,
    )

    if not visible:
        st.warning("No games match those filters. Loosen them in the Filters panel above.")
        return

    for game_index, g in enumerate(visible, start=1):
        opts = market_options(g, bet_filter)
        separator = "vs" if g["neutral_site"] else "@"
        neutral_note = " · neutral site" if g["neutral_site"] else ""
        pills = (
            f"<span class='pill'>{escape(str(g['away_conference'] or 'IND'))}</span>"
            f"<span class='pill'>{escape(str(g['home_conference'] or 'IND'))}</span>"
        )
        if g["lines_provider"]:
            pills += f"<span class='pill'>{escape(str(g['lines_provider']))}</span>"

        with st.container(border=True):
            st.markdown(
                f"<div class='game-kicker'><span class='game-number'>Game {game_index:02d}</span>"
                f"<span>{_safe_kickoff(g['start_date'])}</span></div>"
                f"<div class='matchup'>{rank_html(g['away_rank'])}{escape(g['away_team'])}"
                f"<span class='versus'> {separator} </span>"
                f"{rank_html(g['home_rank'])}{escape(g['home_team'])}</div>"
                f"<div class='meta'>{_safe_kickoff(g['start_date'])}{neutral_note}</div>"
                f"<div class='meta' style='margin-top:5px'>{pills}</div>",
                unsafe_allow_html=True,
            )

            with st.form(key=f"bet_{g['id']}", clear_on_submit=True, border=False):
                labels = [o[2] for o in opts]
                choice = st.radio(
                    "Selection",
                    labels,
                    index=None,
                    key=f"sel_{g['id']}",
                    label_visibility="collapsed",
                )
                c1, c2 = st.columns([1, 1])
                with c1:
                    wager = st.number_input(
                        "Your stake ($)",
                        min_value=1.0,
                        max_value=float(max(balance, Decimal("1.00"))),
                        value=100.0 if balance >= 100 else float(max(balance, Decimal("1.00"))),
                        step=25.0,
                        key=f"amt_{g['id']}",
                    )
                with c2:
                    possible_return = money(Decimal(str(wager)) * DEFAULT_MULTIPLIER)
                    st.markdown(
                        f"<div class='meta' style='margin-top:8px'>Possible return</div>"
                        f"<div class='matchup'>{fmt_money(possible_return)}</div>",
                        unsafe_allow_html=True,
                    )
                submitted = st.form_submit_button(
                    "Lock in this pick", type="primary", width="stretch"
                )

                if submitted:
                    if choice is None:
                        st.warning("Pick a side first.")
                    else:
                        bet_type, line, _ = opts[labels.index(choice)]
                        ok, msg = place_bet(
                            user["id"], g["id"], bet_type, line, Decimal(str(wager))
                        )
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)


def _kickoff_tape(dt: datetime) -> tuple[str, str, str]:
    """Compact day, time, zone pieces for the yellow kickoff tape."""
    if dt is None:
        return "TBD", "—", "CT"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a} {local.day}", f"{hour}:{local:%M}", f"{ampm} CT"


def _choose_market(game: dict, option: tuple[str, Decimal, str]) -> None:
    bet_type, line, label = option
    separator = "vs" if game["neutral_site"] else "at"
    st.session_state.bet_selection = {
        "game_id": int(game["id"]),
        "bet_type": bet_type,
        "line": str(line),
        "label": label,
        "matchup": f"{game['away_team']} {separator} {game['home_team']}",
        "kickoff": _safe_kickoff(game["start_date"]),
    }
    st.session_state.open_bet_dialog = True


def _set_slip_wager(amount: int, wager_key: str) -> None:
    st.session_state[wager_key] = float(amount)


def _render_game_card(game: dict, options: list[tuple[str, Decimal, str]]) -> None:
    day, clock, zone = _kickoff_tape(game["start_date"])
    separator = "VS" if game["neutral_site"] else "AT"
    site = "Neutral site" if game["neutral_site"] else (
        f"{game['away_conference'] or 'IND'} at {game['home_conference'] or 'IND'}"
    )
    provider = escape(str(game["lines_provider"] or "Consensus"))
    active = st.session_state.get("bet_selection", {})

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="fd-game-top">
                <div class="fd-kickoff"><span>{escape(day)}</span><strong>{escape(clock)}</strong><span>{escape(zone)}</span></div>
                <div class="fd-game-info">
                    <div class="fd-game-meta"><span>{escape(site)}</span><span>{provider} · latest line</span></div>
                    <div class="matchup">
                        <span>{rank_html(game['away_rank'])}{escape(game['away_team'])}</span>
                        <span class="versus">{separator}</span>
                        <span>{rank_html(game['home_rank'])}{escape(game['home_team'])}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        market_cols = st.columns(len(options))
        for col, option in zip(market_cols, options, strict=True):
            bet_type, _line, label = option
            is_active = (
                int(active.get("game_id", -1)) == int(game["id"])
                and active.get("bet_type") == bet_type
            )
            with col:
                st.button(
                    label,
                    key=f"market_{game['id']}_{bet_type}",
                    type="primary" if is_active else "secondary",
                    width="stretch",
                    on_click=_choose_market,
                    args=(game, option),
                )


def _render_bet_slip(user: dict, balance: Decimal, *, key_prefix: str = "") -> None:
    flash = st.session_state.pop("bet_flash", None)
    if flash:
        st.success(flash)

    selection = st.session_state.get("bet_selection")
    if not selection:
        st.markdown(
            """
            <div class="fd-slip-card">
                <div class="fd-slip-pick">Your slip is open</div>
                <div class="fd-slip-empty">Choose any spread or total from the Saturday card. Your pick and potential return will appear here.</div>
            </div>
            <div class="fd-slip-footer"></div>
            """,
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f"""
        <div class="fd-slip-card">
            <div class="fd-slip-pick">{escape(str(selection['label']))}</div>
            <div class="fd-slip-game">{escape(str(selection['matchup']))} · {escape(str(selection['kickoff']))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    max_wager = float(max(balance, Decimal("1.00")))
    default_wager = 100.0 if balance >= 100 else max_wager
    wager_key = f"{key_prefix}slip_wager"
    current = float(st.session_state.get(wager_key, default_wager))
    if wager_key not in st.session_state or current > max_wager or current < 1:
        st.session_state[wager_key] = default_wager

    wager = st.number_input(
        "Stake ($)",
        min_value=1.0,
        max_value=max_wager,
        step=25.0,
        key=wager_key,
    )

    quick_cols = st.columns(3)
    for col, amount in zip(quick_cols, (50, 100, 250), strict=True):
        with col:
            st.button(
                f"${amount}",
                key=f"{key_prefix}quick_stake_{amount}",
                disabled=balance < amount,
                width="stretch",
                on_click=_set_slip_wager,
                args=(amount, wager_key),
            )

    stake = Decimal(str(wager))
    total_return = money(stake * DEFAULT_MULTIPLIER)
    to_win = total_return - money(stake)
    st.markdown(
        f"""
        <div class="fd-slip-return">
            <span>To win</span><strong>{fmt_money(to_win)}</strong>
            <span>Total return</span><strong>{fmt_money(total_return)}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        f"Place {selection['label']}",
        key=f"{key_prefix}place_selected_bet",
        type="primary",
        width="stretch",
    ):
        ok, msg = place_bet(
            user["id"],
            int(selection["game_id"]),
            str(selection["bet_type"]),
            Decimal(str(selection["line"])),
            stake,
        )
        if ok:
            st.session_state.pop("bet_selection", None)
            st.session_state.bet_flash = msg
            st.rerun()
        else:
            st.error(msg)

    st.markdown("<div class='fd-slip-footer'></div>", unsafe_allow_html=True)


@st.dialog("Your bet ticket", width="small")
def _bet_dialog(user: dict, balance: Decimal) -> None:
    _render_bet_slip(user, balance, key_prefix="dialog_")


def tab_place_bets(user: dict, balance: Decimal) -> None:
    games = load_upcoming_games()
    if not games:
        st.info(
            "No upcoming games are loaded. The next sync will add the weekly card "
            "as soon as lines are available."
        )
        return

    wk = games[0]
    st.markdown(
        f"""
        <div class="fd-board-head">
            <h2>Saturday card</h2>
            <div class="fd-board-meta">Week {wk['week']} · {wk['season']} {escape(str(wk['season_type']))}<br>All times Central · Lines lock at kickoff</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    conferences = sorted(
        {c for game in games for c in (game["home_conference"], game["away_conference"]) if c}
    )
    with st.expander("Tune the card", expanded=False):
        bet_filter = st.radio(
            "Market",
            ["All markets", "Spread (ATS)", "Over / Under"],
            key="f_market",
        )
        conf_pick = st.multiselect(
            "Conference", conferences, default=[], key="f_conf", placeholder="All conferences"
        )
        top25_only = st.toggle("Top 25 teams only", value=False, key="f_top25")
        hide_bet = st.toggle("Hide games I've already bet", value=False, key="f_hide")
        team_search = st.text_input("Search team", key="f_search", placeholder="e.g. Georgia")

    my_bets = load_user_bets(user["id"])
    bet_matchups = {
        (bet["home_team"], bet["away_team"])
        for bet in my_bets
        if bet["status"] == "pending"
    }
    visible: list[dict] = []
    for game in games:
        if conf_pick and not (
            game["home_conference"] in conf_pick or game["away_conference"] in conf_pick
        ):
            continue
        if top25_only and not (game["home_rank"] or game["away_rank"]):
            continue
        if team_search:
            needle = team_search.strip().lower()
            if needle not in game["home_team"].lower() and needle not in game["away_team"].lower():
                continue
        if hide_bet and (game["home_team"], game["away_team"]) in bet_matchups:
            continue
        if not market_options(game, bet_filter):
            continue
        visible.append(game)

    st.markdown(
        f"<div class='fd-board-summary'>{len(visible)} of {len(games)} games · "
        f"{fmt_money(balance)} available · Even money · No house vig</div>",
        unsafe_allow_html=True,
    )

    if not visible:
        st.warning("No games match those filters. Open Tune the card and broaden the board.")
        return

    board_col, slip_col = st.columns([2.25, 1])
    with board_col:
        for game in visible:
            _render_game_card(game, market_options(game, bet_filter))
    with slip_col:
        _render_bet_slip(user, balance)

    if st.session_state.pop("open_bet_dialog", False):
        _bet_dialog(user, balance)


# =====================================================================
# Tab 2 — My Tickets
# =====================================================================
def bets_to_frames(rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    open_rows, settled_rows = [], []
    for b in rows:
        wager = money(b["wager_amount"])
        mult = Decimal(str(b["payout_multiplier"]))
        matchup = f"{b['away_team']} @ {b['home_team']}"
        if b["status"] == "pending":
            open_rows.append(
                {
                    "Pick": describe_bet(b),
                    "Game": matchup,
                    "Wk": b["week"],
                    "Wager": float(wager),
                    "To Win": float(money(wager * mult) - wager),
                    "Kickoff": _safe_kickoff(b["start_date"]),
                }
            )
        else:
            payout = money(b["payout_amount"])
            settled_rows.append(
                {
                    "Pick": describe_bet(b),
                    "Game": matchup,
                    "Wk": b["week"],
                    "Score": (
                        f"{b['away_score']}–{b['home_score']}"
                        if b["home_score"] is not None
                        else "—"
                    ),
                    "Wager": float(wager),
                    "Result": b["status"].upper(),
                    "P/L": float(payout - wager),
                }
            )
    return pd.DataFrame(open_rows), pd.DataFrame(settled_rows)


def render_ticket_cards(rows: list[dict], *, settled: bool) -> None:
    """Render tickets as responsive slips instead of a horizontally scrolling table."""
    for bet in rows:
        wager = money(bet["wager_amount"])
        matchup = f"{bet['away_team']} @ {bet['home_team']}"
        if not settled:
            potential = money(wager * Decimal(str(bet["payout_multiplier"])))
            detail = f"Week {bet['week']} · {_safe_kickoff(bet['start_date'])}"
            money_label = "Possible return"
            money_value = fmt_money(potential)
            card_class = ""
            result = ""
        else:
            status = str(bet["status"])
            profit = money(bet["payout_amount"]) - wager
            score = (
                f"{bet['away_score']}–{bet['home_score']} final"
                if bet["home_score"] is not None
                else "Final"
            )
            detail = f"Week {bet['week']} · {score}"
            money_label = "Profit / loss"
            money_value = fmt_money(profit)
            card_class = f"settled-{status}"
            result = f"<span class='ticket-result {status}'>{escape(status.upper())}</span>"

        st.markdown(
            f"""
            <div class="ticket-card {card_class}">
                <div>
                    <div class="ticket-pick">{escape(describe_bet(bet))}{result}</div>
                    <div class="ticket-game">{escape(matchup)}</div>
                </div>
                <div class="ticket-money"><strong>{money_value}</strong><span>{money_label}</span></div>
                <div class="ticket-meta">{escape(detail)} · Staked {fmt_money(wager)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


MONEY_COLS = {
    "Wager": st.column_config.NumberColumn("Wager", format="$%.2f", width="small"),
    "To Win": st.column_config.NumberColumn("To Win", format="$%.2f", width="small"),
    "P/L": st.column_config.NumberColumn("P/L", format="$%.2f", width="small"),
    "Net Worth": st.column_config.NumberColumn("Net Worth", format="$%.2f"),
    "Cash": st.column_config.NumberColumn("Cash", format="$%.2f"),
    "At Risk": st.column_config.NumberColumn("At Risk", format="$%.2f"),
}


def tab_my_tickets(user: dict, balance: Decimal) -> None:
    rows = load_user_bets(user["id"])
    open_stake = sum((money(b["wager_amount"]) for b in rows if b["status"] == "pending"), Decimal("0"))
    settled_pl = sum(
        (money(b["payout_amount"]) - money(b["wager_amount"]) for b in rows if b["status"] != "pending"),
        Decimal("0"),
    )
    net_worth = balance + open_stake

    st.markdown(
        "<div class='fd-view-head'><h2>My tickets</h2><p>Open positions and final receipts</p></div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    c1.metric("Cash", fmt_money(balance))
    c2.metric("At risk", fmt_money(open_stake))
    c3, c4 = st.columns(2)
    c3.metric("Settled P/L", fmt_money(settled_pl), delta=f"{float(settled_pl):+,.2f}")
    c4.metric("Net worth", fmt_money(net_worth),
              delta=f"{float(net_worth - STARTING_BANKROLL):+,.2f}")

    wins = sum(1 for b in rows if b["status"] == "won")
    losses = sum(1 for b in rows if b["status"] == "lost")
    pushes = sum(1 for b in rows if b["status"] == "push")
    graded = wins + losses
    record = f"{wins}-{losses}-{pushes}"
    win_rate = f" · {wins / graded:.1%} win rate" if graded else ""
    open_rows = [b for b in rows if b["status"] == "pending"]
    settled_rows = [b for b in rows if b["status"] != "pending"]

    st.markdown(
        f"<div class='section-head'><h3>Open tickets</h3><span>{len(open_rows)} live · {fmt_money(open_stake)} at risk</span></div>",
        unsafe_allow_html=True,
    )
    if not open_rows:
        st.info("Your slip is clean. Head to the board and make your first call.")
    else:
        render_ticket_cards(open_rows, settled=False)

    st.markdown(
        f"<div class='section-head'><h3>Settled</h3><span>Record {record}{win_rate}</span></div>",
        unsafe_allow_html=True,
    )
    if not settled_rows:
        st.info("Results will land here after the final whistle.")
    else:
        render_ticket_cards(settled_rows, settled=True)


# =====================================================================
# Tab 3 — Leaderboard & Rival Tracker
# =====================================================================
def render_weekly_dashboard(rows: list[dict], user: dict) -> None:
    st.markdown(
        "<div class='section-head'><h3>Race through the season</h3>"
        "<span>End-of-week rank · settled tickets only</span></div>",
        unsafe_allow_html=True,
    )
    if not rows:
        st.caption("The movement chart starts after the first week has a final score.")
        return

    history = pd.DataFrame(
        {
            "user_id": [str(row["user_id"]) for row in rows],
            "player": [str(row["username"]) for row in rows],
            "season": [int(row["season"]) for row in rows],
            "week": [int(row["week"]) for row in rows],
            "net_worth": [float(row["net_worth"]) for row in rows],
        }
    )
    latest_season = int(history["season"].max())
    history = history[history["season"] == latest_season].copy()
    history["rank"] = (
        history.groupby("week")["net_worth"]
        .rank(method="min", ascending=False)
        .astype(int)
    )
    history["week_label"] = "W" + history["week"].astype(str)
    history["value_label"] = history["net_worth"].map(lambda value: f"${value:,.2f}")

    max_rank = max(int(history["rank"].max()), 2)
    palette = [
        "#f2c84b",
        "#8ecae6",
        "#ef8279",
        "#ddebcb",
        "#c5a3ff",
        "#ff9f5a",
        "#63d1a8",
        "#f7f3e8",
        "#de9dcc",
        "#9fb2ff",
    ]
    chart = (
        alt.Chart(history)
        .mark_line(point=alt.OverlayMarkDef(filled=True, size=78), strokeWidth=3)
        .encode(
            x=alt.X(
                "week:O",
                title="WEEK",
                axis=alt.Axis(labelAngle=0, tickSize=0),
            ),
            y=alt.Y(
                "rank:Q",
                title="PLACE",
                scale=alt.Scale(domain=[1, max_rank], reverse=True),
                axis=alt.Axis(tickMinStep=1),
            ),
            color=alt.Color(
                "player:N",
                title=None,
                scale=alt.Scale(range=palette),
                legend=alt.Legend(orient="bottom", columns=2),
            ),
            detail="user_id:N",
            tooltip=[
                alt.Tooltip("player:N", title="Player"),
                alt.Tooltip("week:O", title="Week"),
                alt.Tooltip("rank:Q", title="Place"),
                alt.Tooltip("value_label:N", title="Net worth"),
            ],
        )
        .properties(height=285)
        .configure(background="transparent")
        .configure_view(stroke="#87978c")
        .configure_axis(
            gridColor="#42574b",
            labelColor="#f7f3e8",
            titleColor="#ddebcb",
            titleFont="IBM Plex Mono",
            labelFont="IBM Plex Mono",
        )
        .configure_legend(
            labelColor="#f7f3e8",
            labelFont="IBM Plex Mono",
            symbolStrokeWidth=4,
        )
    )
    st.altair_chart(chart, width="stretch")

    weeks = sorted(history["week"].unique())
    latest_week = int(weeks[-1])
    previous_week = int(weeks[-2]) if len(weeks) > 1 else None
    current = history[history["week"] == latest_week].sort_values(["rank", "player"])
    previous_ranks = (
        history[history["week"] == previous_week].set_index("user_id")["rank"].to_dict()
        if previous_week is not None
        else {}
    )

    st.markdown(
        f"<div class='fd-movement-head'><span>Week {latest_week} movement</span>"
        f"<span>{latest_season} season</span></div>",
        unsafe_allow_html=True,
    )
    for row in current.itertuples(index=False):
        previous_rank = previous_ranks.get(row.user_id)
        movement = int(previous_rank) - int(row.rank) if previous_rank is not None else None
        if movement is None:
            move_label, move_class = "NEW", "flat"
        elif movement > 0:
            move_label, move_class = f"▲ {movement}", "up"
        elif movement < 0:
            move_label, move_class = f"▼ {abs(movement)}", "down"
        else:
            move_label, move_class = "—", "flat"
        you_class = " is-you" if row.user_id == str(user["id"]) else ""
        you_label = " · You" if you_class else ""
        st.markdown(
            f"""
            <div class="fd-movement-row{you_class}">
                <div class="fd-movement-rank">{int(row.rank):02d}</div>
                <div class="fd-movement-player">{escape(row.player)}{you_label}</div>
                <div class="fd-movement-worth">{fmt_money(row.net_worth)}</div>
                <div class="fd-movement-change {move_class}">{move_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def tab_leaderboard(user: dict) -> None:
    board = load_leaderboard()
    if not board:
        st.info("No players yet.")
        return

    leader = board[0]
    st.markdown(
        f"""
        <div class="fd-view-head">
            <h2>League table</h2>
            <p>Leader · {escape(str(leader['username']))} · {fmt_money(leader['net_worth'])}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_weekly_dashboard(load_weekly_standings(), user)

    st.markdown(
        "<div class='section-head'><h3>Current table</h3>"
        "<span>Cash + open stake</span></div>",
        unsafe_allow_html=True,
    )

    for index, row in enumerate(board, start=1):
        is_you = str(row["user_id"]) == user["id"]
        you_label = " · You" if is_you else ""
        st.markdown(
            f"""
            <div class="leader-row {'is-you' if is_you else ''}">
                <div class="leader-rank">{index:02d}</div>
                <div>
                    <div class="leader-name">{escape(str(row['username']))}{you_label}</div>
                    <div class="leader-record">{row['wins']}-{row['losses']}-{row['pushes']} · {fmt_money(row['open_stake'])} at risk</div>
                </div>
                <div class="leader-worth">{fmt_money(row['net_worth'])}<small>Net worth</small></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        "<div class='section-head'><h3>Scout the league</h3><span>Every ticket is public</span></div>",
        unsafe_allow_html=True,
    )
    st.caption("Open a player to see exactly where their money is riding.")

    all_bets = load_all_bets()
    by_user: dict[str, list[dict]] = {}
    for b in all_bets:
        by_user.setdefault(str(b["user_id"]), []).append(b)

    for r in board:
        uid = str(r["user_id"])
        label = (
            f"{r['username']} · {fmt_money(r['net_worth'])} · "
            f"{r['wins']}-{r['losses']}-{r['pushes']}"
        )
        with st.expander(label, expanded=False):
            rows = by_user.get(uid, [])
            if not rows:
                st.caption("No bets placed yet.")
                continue
            open_rows = [b for b in rows if b["status"] == "pending"]
            settled_rows = [b for b in rows if b["status"] != "pending"]
            st.markdown("**Open tickets**")
            if not open_rows:
                st.caption("No open tickets.")
            else:
                render_ticket_cards(open_rows, settled=False)
            st.markdown("**Settled tickets**")
            if not settled_rows:
                st.caption("Nothing settled.")
            else:
                render_ticket_cards(settled_rows, settled=True)


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    if "user" not in st.session_state:
        auth_screen()
        return

    user = st.session_state.user

    try:
        balance = get_balance(user["id"])
    except Exception as exc:  # noqa: BLE001
        st.error(f"Database unavailable: {exc}")
        st.stop()
        return

    try:
        header_games = load_upcoming_games()
        header_week = str(header_games[0]["week"]) if header_games else "—"
        next_kickoff = _safe_kickoff(header_games[0]["start_date"]) if header_games else "Board pending"
    except Exception:  # the tabs will show the actionable database error if needed
        header_week = "—"
        next_kickoff = "Board pending"

    st.markdown(
        f"""
        <div class="fd-scorebar">
            <div class="fd-brand"><span class="fd-ball">4D</span><span>Fourth Down</span></div>
            <div class="fd-readout fd-week"><span>Week</span><strong>{escape(header_week)}</strong></div>
            <div class="fd-readout fd-kickoff-readout"><span>Next kickoff</span><strong>{escape(next_kickoff)}</strong></div>
            <div class="fd-readout"><span>{escape(str(user['username']))}</span><strong>{fmt_money(balance)}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    t1, t2, t3 = st.tabs(["The board", "My tickets", "Standings"])
    with t1:
        tab_place_bets(user, balance)
    with t2:
        tab_my_tickets(user, balance)
    with t3:
        tab_leaderboard(user)

    refresh_col, signout_col = st.columns([1, 1])
    with refresh_col:
        if st.button("↻ Refresh lines", width="stretch"):
            refresh_data()
            st.rerun()
    with signout_col:
        if st.button("Sign out", width="stretch"):
            st.session_state.pop("user", None)
            st.session_state.pop("bet_selection", None)
            refresh_data()
            st.rerun()
    st.markdown(
        "<div class='footer-note'>Play money only · Lines via College Football Data · "
        "Tickets settle automatically after the final</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
