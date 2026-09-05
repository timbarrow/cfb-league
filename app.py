"""
app.py — Mobile-first Streamlit front end for a 10-person private CFB
betting league. Runs free on Streamlit Community Cloud against Supabase.

Tabs
  1. Place Bets  — upcoming week only, filtered by conference / Top 25 / market
  2. My Tickets  — cash, open stakes, settled P/L, open + settled tables
  3. Leaderboard — net worth ranking, fully transparent ticket inspection
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections import Counter, defaultdict
from io import BytesIO
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

import altair as alt
import extra_streamlit_components as stx
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageFont
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
AUTH_COOKIE = "fourth_down_session"
AUTH_SESSION_DAYS = 30

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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Barlow+Condensed:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=Manrope:wght@400;500;600;700&family=Roboto+Condensed:wght@400;500;600;700&display=swap" rel="stylesheet">

<style>
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

html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }
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


def fmt_money_change(v) -> str:
    d = money(v)
    return f"+{fmt_money(d)}" if d > 0 else fmt_money(d)


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


def _as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def _freshness_label(dt: datetime | None) -> str:
    local = _as_local(dt)
    if local is None:
        return "Pending first refresh"
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a %b} {local.day} · {hour}:{local:%M} {ampm} CT"


def _compact_update(dt: datetime | None) -> str:
    local = _as_local(dt)
    if local is None:
        return "Daily line"
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"Updated {hour}:{local:%M} {ampm} CT"


def _kickoff_countdown(dt: datetime | None, now: datetime | None = None) -> str:
    if dt is None:
        return "Board pending"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    remaining = target - current
    if remaining.total_seconds() <= 0:
        return "Kicking now"
    minutes = int(remaining.total_seconds() // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def game_progress(period, clock, status: str = "in_progress") -> str:
    """Broadcast-style game position such as Q3 · 8:42 or 2OT."""
    if status == "completed":
        return "Final"
    try:
        number = int(period) if period is not None else 0
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return "Live"
    label = f"Q{number}" if number <= 4 else ("OT" if number == 5 else f"{number - 4}OT")
    clock_text = str(clock or "").strip()
    if number == 2 and clock_text in {"0:00", "00:00"}:
        return "Halftime"
    if clock_text:
        parts = clock_text.split(":", 1)
        if len(parts) == 2 and parts[0].isdigit():
            clock_text = f"{int(parts[0])}:{parts[1]}"
        return f"{label} · {clock_text}"
    return label


def win_probabilities(game: dict) -> tuple[int, int] | None:
    """Return display-ready away/home percentages that always total 100."""
    try:
        away = float(game.get("away_win_probability"))
        home = float(game.get("home_win_probability"))
    except (TypeError, ValueError):
        return None
    total = away + home
    if total <= 0:
        return None
    away_pct = round(max(0.0, min(1.0, away / total)) * 100)
    return away_pct, 100 - away_pct


def kickoff_window(dt: datetime) -> tuple[str, str]:
    local = _as_local(dt)
    if local is None:
        return "Kickoff TBD", "Time to be announced"
    day = local.strftime("%A")
    if local.weekday() == 5:
        if local.hour < 14:
            return f"{day} · Early window", "Kickoffs before 2 PM CT"
        if local.hour < 18:
            return f"{day} · Afternoon", "Kickoffs from 2–5:59 PM CT"
        return f"{day} · Primetime", "Kickoffs at 6 PM CT or later"
    if local.hour >= 18:
        return f"{day} · Night", "Weeknight showcase"
    return day, "Special kickoff window"


def _minutes_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    value = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - value).total_seconds() / 60, 0)


def render_data_health(status: dict) -> None:
    lines_at = status.get("lines_updated_at")
    scores_at = status.get("scores_updated_at")
    live_games = int(status.get("live_games") or 0)
    line_age = _minutes_since(lines_at)
    score_age = _minutes_since(scores_at)
    line_state = "warning" if line_age is None or line_age > 36 * 60 else "good"
    score_state = "warning" if live_games and (score_age is None or score_age > 20) else "good"
    score_copy = (
        f"{live_games} live · checked {_freshness_label(scores_at)}"
        if live_games
        else "No games live · automatic checks scheduled"
    )
    st.markdown(
        f"""
        <div class="fd-data-health">
            <span class="{line_state}"><i></i>Odds board · {_freshness_label(lines_at)}</span>
            <span class="{score_state}"><i></i>{escape(score_copy)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if line_state == "warning" and lines_at is not None:
        st.warning("The daily odds board is older than expected. Existing tickets are safe; new bets should wait for the next refresh.")
    if score_state == "warning":
        st.warning("A live score check is overdue. Tickets will remain open until an official final is posted.")


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
           g.lines_provider, g.lines_updated_at, g.status
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


@st.cache_data(ttl=15, show_spinner=False)
def load_live_games() -> list[dict]:
    """All FBS games currently marked live, newest score refresh first."""
    sql = """
    select id, season, week, season_type, start_date, neutral_site,
           home_team, away_team, home_conference, away_conference,
           home_rank, away_rank, home_score, away_score, status,
           game_period, game_clock, game_possession, game_situation,
           home_win_probability, away_win_probability, scores_updated_at
      from public.games
     where status = 'in_progress'
       and classification = 'fbs'
       and home_score is not null
       and away_score is not null
     order by start_date, home_team
    """
    with ro() as conn:
        return q(conn, sql)


@st.cache_data(ttl=15, show_spinner=False)
def load_user_bets(user_id: str) -> list[dict]:
    sql = """
    select b.id, b.bet_type, b.line_placed, b.wager_amount, b.payout_multiplier,
           b.status, b.payout_amount, b.created_at, b.settled_at,
           g.home_team, g.away_team, g.season, g.week, g.start_date,
           g.home_score, g.away_score, g.status as game_status,
           g.game_period, g.game_clock, g.game_possession, g.game_situation,
           g.scores_updated_at,
           (select min(first_game.start_date)
              from public.games first_game
             where first_game.season = g.season) as season_first_start
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
def load_completed_games() -> list[dict]:
    """Completed FBS games for the latest season in the league database."""
    sql = """
    select id, season, week, season_type, start_date, neutral_site,
           home_team, away_team, home_conference, away_conference,
           home_rank, away_rank, home_score, away_score, scores_updated_at
      from public.games
     where status = 'completed'
       and classification = 'fbs'
       and season = (select max(season) from public.games where classification = 'fbs')
     order by week desc, start_date desc, home_team
    """
    with ro() as conn:
        return q(conn, sql)


@st.cache_data(ttl=30, show_spinner=False)
def load_weekly_standings() -> list[dict]:
    """Weekly league value, including the active week after its first settled ticket."""
    sql = """
    with current_season as (
        select coalesce(max(season), extract(year from now())::integer) as season
          from public.games
    ),
    standings_weeks as (
        select season, 0 as week from current_season
        union
        select g.season, g.week
          from public.games g
          join current_season cs on cs.season = g.season
         group by g.season, g.week
        having max(g.start_date) + interval '6 hours' < now()
           and count(*) filter (where g.status = 'completed') > 0
           and count(*) filter (where g.status = 'in_progress') = 0
        union
        select distinct g.season, g.week
          from public.games g
          join public.bets b on b.game_id = g.id
          join current_season cs on cs.season = g.season
         where b.status in ('won', 'lost', 'push')
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
      cross join standings_weeks w
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
           g.id as game_id, g.season, g.home_team, g.away_team, g.week, g.start_date,
           g.home_score, g.away_score, g.status as game_status,
           g.game_period, g.game_clock, g.game_possession, g.game_situation,
           g.scores_updated_at
      from public.bets b
      join public.games g on g.id = b.game_id
     order by b.created_at desc
    """
    with ro() as conn:
        return q(conn, sql)


@st.cache_data(ttl=30, show_spinner=False)
def load_data_status() -> dict:
    with ro() as conn:
        row = q1(
            conn,
            """select max(lines_updated_at) as lines_updated_at,
                      max(scores_updated_at) as scores_updated_at,
                      count(*) filter (where status = 'in_progress') as live_games
                 from public.games
                where classification = 'fbs'""",
        )
    return row or {"lines_updated_at": None, "scores_updated_at": None, "live_games": 0}


@st.cache_data(ttl=45, show_spinner=False)
def load_weekly_recap_bets() -> list[dict]:
    """Settled tickets from the latest completed week, with enough context for recap awards."""
    sql = """
    with latest as (
        select season, week
          from public.games
         group by season, week
        having max(start_date) + interval '6 hours' < now()
           and count(*) filter (where status = 'completed') > 0
           and count(*) filter (where status = 'in_progress') = 0
         order by season desc, week desc
         limit 1
    )
    select b.id, b.user_id, u.username, b.bet_type, b.line_placed,
           b.wager_amount, b.payout_amount, b.status, b.created_at,
           g.id as game_id, g.season, g.week, g.home_team, g.away_team,
           g.home_score, g.away_score
      from public.bets b
      join public.users u on u.id = b.user_id
      join public.games g on g.id = b.game_id
      join latest w on w.season = g.season and w.week = g.week
     where b.status in ('won', 'lost', 'push')
     order by b.created_at
    """
    with ro() as conn:
        return q(conn, sql)


def refresh_data() -> None:
    load_upcoming_games.clear()
    load_live_games.clear()
    load_user_bets.clear()
    load_leaderboard.clear()
    load_completed_games.clear()
    load_weekly_standings.clear()
    load_all_bets.clear()
    load_data_status.clear()
    load_weekly_recap_bets.clear()


def get_balance(user_id: str) -> Decimal:
    with ro() as conn:
        row = q1(conn, "select balance from public.users where id = :id", id=user_id)
    return money(row["balance"]) if row else Decimal("0.00")


# =====================================================================
# Auth
# =====================================================================
@st.cache_resource(show_spinner=False)
def ensure_runtime_schema() -> None:
    """Apply small, idempotent migrations needed by the deployed app."""
    with tx() as conn:
        exec_(conn, "drop index if exists public.bets_one_open_per_market")
        exec_(conn, "alter table public.games add column if not exists lines_updated_at timestamptz")
        exec_(conn, "alter table public.games add column if not exists scores_updated_at timestamptz")
        exec_(conn, "alter table public.games add column if not exists classification text")
        exec_(conn, "alter table public.games add column if not exists game_period integer")
        exec_(conn, "alter table public.games add column if not exists game_clock text")
        exec_(conn, "alter table public.games add column if not exists game_possession text")
        exec_(conn, "alter table public.games add column if not exists game_situation text")
        exec_(conn, "alter table public.games add column if not exists home_win_probability numeric(5,4)")
        exec_(conn, "alter table public.games add column if not exists away_win_probability numeric(5,4)")
        exec_(
            conn,
            """
            create table if not exists public.auth_sessions (
                token_hash   char(64) primary key,
                user_id      uuid not null references public.users(id) on delete cascade,
                created_at   timestamptz not null default now(),
                expires_at   timestamptz not null,
                last_seen_at timestamptz not null default now()
            )
            """,
        )
        exec_(
            conn,
            """create index if not exists auth_sessions_expires_idx
                   on public.auth_sessions (expires_at)""",
        )


def _cookie_manager():
    return stx.CookieManager(key="fourth_down_cookie_manager")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_auth_session(user_id: str) -> str:
    """Store only a hash server-side; the random raw token stays in the browser."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=AUTH_SESSION_DAYS)
    with tx() as conn:
        exec_(conn, "delete from public.auth_sessions where expires_at <= now()")
        exec_(
            conn,
            """insert into public.auth_sessions (token_hash, user_id, expires_at)
               values (:token_hash, :user_id, :expires_at)""",
            token_hash=_token_hash(token),
            user_id=user_id,
            expires_at=expires,
        )
    return token


def restore_auth_session(token: str) -> dict | None:
    if not token:
        return None
    with tx() as conn:
        row = q1(
            conn,
            """select u.id, u.username, u.is_admin
                 from public.auth_sessions s
                 join public.users u on u.id = s.user_id
                where s.token_hash = :token_hash
                  and s.expires_at > now()""",
            token_hash=_token_hash(token),
        )
        if row:
            exec_(
                conn,
                """update public.auth_sessions set last_seen_at = now()
                    where token_hash = :token_hash""",
                token_hash=_token_hash(token),
            )
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
    }


def revoke_auth_session(token: str | None) -> None:
    if not token:
        return
    with tx() as conn:
        exec_(
            conn,
            "delete from public.auth_sessions where token_hash = :token_hash",
            token_hash=_token_hash(token),
        )


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
    token = create_auth_session(str(row["id"]))
    st.session_state.auth_token = token
    st.session_state.auth_cookie_to_set = token
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
    st.caption("Sign in once on this device; Fourth Down remembers you securely for 30 days.")

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
                    if ok:
                        login_ok, login_msg = login_user(u, p)
                        if login_ok:
                            st.rerun()
                        else:
                            st.error(login_msg)
                    else:
                        st.error(msg)


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
        if "users_balance_nonneg" in msg:
            return False, "That wager would overdraw your bankroll."
        return False, "Bet rejected by the database."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not place bet: {exc}"

    refresh_data()
    to_win = money(wager * DEFAULT_MULTIPLIER) - wager
    return True, f"Ticket placed: {fmt_money(wager)} to win {fmt_money(to_win)}."


def ticket_delete_is_open(bet: dict, now: datetime | None = None) -> bool:
    """Tickets are refundable only until the season's first kickoff."""
    if str(bet.get("status")) != "pending":
        return False
    cutoff = bet.get("season_first_start")
    if cutoff is None:
        return False
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current < cutoff


def delete_bet(user_id: str, bet_id: str) -> tuple[bool, str]:
    """Delete one owned pending ticket and refund it before season kickoff."""
    try:
        with tx() as conn:
            bet = q1(
                conn,
                """select b.id, b.wager_amount, b.status, g.season
                     from public.bets b
                     join public.games g on g.id = b.game_id
                    where b.id = :bid and b.user_id = :uid
                    for update of b""",
                bid=bet_id,
                uid=user_id,
            )
            if not bet:
                return False, "Ticket not found."
            if bet["status"] != "pending":
                return False, "Settled tickets cannot be deleted."

            window = q1(
                conn,
                """select min(start_date) as season_first_start,
                          clock_timestamp() as checked_at
                     from public.games
                    where season = :season""",
                season=bet["season"],
            )
            cutoff = window["season_first_start"] if window else None
            checked_at = window["checked_at"] if window else datetime.now(timezone.utc)
            if cutoff is None or checked_at >= cutoff:
                return False, "The season has started. Tickets are now final."

            deleted = exec_(
                conn,
                """delete from public.bets
                    where id = :bid and user_id = :uid and status = 'pending'""",
                bid=bet_id,
                uid=user_id,
            )
            if deleted.rowcount != 1:
                return False, "Ticket could not be deleted."
            exec_(
                conn,
                "update public.users set balance = balance + :refund where id = :uid",
                refund=money(bet["wager_amount"]),
                uid=user_id,
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not delete ticket: {exc}"

    refresh_data()
    return True, f"Ticket deleted. {fmt_money(bet['wager_amount'])} returned to cash."


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
    provider = escape(str(game["lines_provider"] or "DraftKings"))
    line_stamp = escape(_compact_update(game.get("lines_updated_at")))
    active = st.session_state.get("bet_selection", {})

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="fd-game-top">
                <div class="fd-kickoff"><span>{escape(day)}</span><strong>{escape(clock)}</strong><span>{escape(zone)}</span></div>
                <div class="fd-game-info">
                    <div class="fd-game-meta"><span>{escape(site)}</span><span>{provider} · {line_stamp}</span></div>
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


def _score_value(score) -> str:
    """Keep a missing early-game score honest instead of displaying zero."""
    return "—" if score is None else str(int(score))


def _render_live_scoreboard(games: list[dict]) -> None:
    latest_update = max(
        (game.get("scores_updated_at") for game in games if game.get("scores_updated_at")),
        default=None,
    )
    st.markdown(
        f"""
        <div class="fd-live-head">
            <div><span class="fd-live-dot"></span><strong>Live now</strong></div>
            <span>{len(games)} game{'s' if len(games) != 1 else ''} · {_freshness_label(latest_update)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    for game in games:
        separator = "VS" if game.get("neutral_site") else "AT"
        progress = game_progress(game.get("game_period"), game.get("game_clock"))
        situation = str(game.get("game_situation") or "").strip()
        situation_html = (
            f'<div class="fd-live-situation">{escape(situation)}</div>' if situation else ""
        )
        possession = str(game.get("game_possession") or "").lower()
        away_ball = '<span class="fd-possession" title="Possession" aria-label="Possession">🏈</span>' if possession == "away" else ""
        home_ball = '<span class="fd-possession" title="Possession" aria-label="Possession">🏈</span>' if possession == "home" else ""
        probabilities = win_probabilities(game)
        probability_html = ""
        if probabilities:
            away_pct, home_pct = probabilities
            probability_html = (
                f'<div class="fd-win-prob" aria-label="Win probability: away {away_pct} '
                f'percent, home {home_pct} percent"><div><span>Away {away_pct}%</span>'
                f'<span>Home {home_pct}%</span></div><span class="fd-win-track">'
                f'<i style="width:{away_pct}%"></i></span></div>'
            )
        st.markdown(
            f"""
            <div class="fd-live-game">
                <div class="fd-live-team">
                    <span>{rank_html(game.get('away_rank'))}{escape(str(game['away_team']))}{away_ball}</span>
                    <strong>{_score_value(game.get('away_score'))}</strong>
                </div>
                <div class="fd-live-team">
                    <span>{rank_html(game.get('home_rank'))}{escape(str(game['home_team']))}{home_ball}</span>
                    <strong>{_score_value(game.get('home_score'))}</strong>
                </div>
                {probability_html}
                {situation_html}
                <div class="fd-live-meta"><strong>{escape(progress)}</strong> · {separator} · Week {game['week']} · Scores refresh automatically</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


@st.fragment(run_every="30s")
def _render_live_section() -> None:
    """Refresh the first-screen scoreboard without rerunning the whole app."""
    games = load_live_games()
    show_live = st.toggle(
        f"Show live games ({len(games)})",
        value=True,
        key="f_show_live",
        help="Hide or restore the live scoreboard without changing the betting board.",
    )
    if show_live and games:
        _render_live_scoreboard(games)
    elif show_live:
        st.markdown(
            "<div class='fd-live-empty'>No games are in progress right now.</div>",
            unsafe_allow_html=True,
        )


def _render_bet_slip(user: dict, balance: Decimal, *, key_prefix: str = "") -> None:
    flash = st.session_state.pop("bet_flash", None)
    if flash:
        st.toast(flash, icon="🎟️")
        st.markdown(
            "<div class='fd-ticket-locked'>Ticket placed · Your receipt is now in My tickets</div>",
            unsafe_allow_html=True,
        )

    selection = st.session_state.get("bet_selection")
    if not selection:
        st.markdown(
            """
            <div class="fd-slip-card">
                <div class="fd-slip-pick">Your slip is open</div>
                <div class="fd-slip-empty">Choose any spread or total from this week’s board. Your pick and potential return will appear here.</div>
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

    st.caption(
        "Pending tickets can be deleted for a full refund until the season's first kickoff; "
        "after that, every ticket is final. You can add another bet on this game before its kickoff."
    )
    st.markdown("<div class='fd-slip-footer'></div>", unsafe_allow_html=True)


@st.dialog("Your bet ticket", width="small")
def _bet_dialog(user: dict, balance: Decimal) -> None:
    _render_bet_slip(user, balance, key_prefix="dialog_")


def tab_place_bets(user: dict, balance: Decimal) -> None:
    games = load_upcoming_games()
    live_games = load_live_games()

    wk = games[0] if games else (live_games[0] if live_games else None)
    st.markdown(
        f"""
        <div class="fd-board-head">
            <h2>{f"Week {wk['week']} board" if wk else "The board"}</h2>
            <div class="fd-board-meta">{f"{wk['season']} {escape(str(wk['season_type']))}" if wk else "Season schedule"}<br>All times Central · Lines lock at kickoff</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_live_section()

    if not games:
        st.info(
            "No upcoming games are loaded. The next sync will add the betting card "
            "as soon as lines are available."
        )
        return

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
    latest_line_update = max(
        (game.get("lines_updated_at") for game in games if game.get("lines_updated_at")),
        default=None,
    )
    st.markdown(
        f"""
        <div class="fd-trust-strip">
            <span><b>Books</b> DraftKings first · Bovada backup</span>
            <span><b>Board set</b> {escape(_freshness_label(latest_line_update))}</span>
            <span><b>Rules</b> 2.00× · Final after season kickoff</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not visible:
        st.warning("No games match those filters. Open Tune the card and broaden the board.")
        return

    board_col, slip_col = st.columns([2.25, 1])
    with board_col:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for game in visible:
            grouped.setdefault(kickoff_window(game["start_date"]), []).append(game)
        for window_index, ((window_name, window_note), window_games) in enumerate(
            grouped.items(), start=1
        ):
            st.markdown(
                f"""
                <div class="fd-window-head">
                    <span class="fd-window-number">{window_index:02d}</span>
                    <div><strong>{escape(window_name)}</strong><small>{escape(window_note)} · {len(window_games)} games</small></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            for game in window_games:
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


def render_ticket_cards(
    rows: list[dict],
    *,
    settled: bool,
    delete_user_id: str | None = None,
) -> None:
    """Render tickets as responsive slips instead of a horizontally scrolling table."""
    for bet in rows:
        wager = money(bet["wager_amount"])
        matchup = f"{bet['away_team']} @ {bet['home_team']}"
        matchup_html = escape(matchup)
        if not settled:
            potential = money(wager * Decimal(str(bet["payout_multiplier"])))
            game_status = str(bet.get("game_status") or "scheduled")
            if game_status == "in_progress":
                progress = game_progress(bet.get("game_period"), bet.get("game_clock"))
                situation = str(bet.get("game_situation") or "").strip()
                possession = str(bet.get("game_possession") or "").lower()
                away_ball = '<span class="fd-possession" title="Possession" aria-label="Possession">🏈</span>' if possession == "away" else ""
                home_ball = '<span class="fd-possession" title="Possession" aria-label="Possession">🏈</span>' if possession == "home" else ""
                matchup_html = (
                    f"{escape(str(bet['away_team']))}{away_ball} @ "
                    f"{escape(str(bet['home_team']))}{home_ball}"
                )
                score = (
                    f"{bet['away_team']} {bet['away_score']}–{bet['home_score']} {bet['home_team']}"
                    if bet.get("home_score") is not None and bet.get("away_score") is not None
                    else "Score pending"
                )
                detail = f"{progress} · {situation} · {score}" if situation else f"{progress} · {score}"
                card_class = "ticket-live"
                result = f"<span class='ticket-result live'><i></i>{escape(progress)}</span>"
            elif game_status == "completed":
                score = (
                    f"{bet['away_score']}–{bet['home_score']} final"
                    if bet.get("home_score") is not None
                    else "Final posted"
                )
                detail = f"{score} · Grading ticket"
                card_class = "ticket-grading"
                result = "<span class='ticket-result grading'>FINAL · GRADING</span>"
            else:
                detail = f"Week {bet['week']} · {_safe_kickoff(bet['start_date'])}"
                card_class = ""
                result = ""
            money_label = "Possible return"
            money_value = fmt_money(potential)
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
                    <div class="ticket-game">{matchup_html}</div>
                </div>
                <div class="ticket-money"><strong>{money_value}</strong><span>{money_label}</span></div>
                <div class="ticket-meta">{escape(detail)} · Staked {fmt_money(wager)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if not settled and delete_user_id and ticket_delete_is_open(bet):
            if st.button(
                f"Delete bet · refund {fmt_money(wager)}",
                key=f"delete_bet_{bet['id']}",
                width="stretch",
                help="Available only until the season's first game kicks off.",
            ):
                ok, message = delete_bet(delete_user_id, str(bet["id"]))
                st.session_state.ticket_delete_flash = (ok, message)
                st.rerun()


MONEY_COLS = {
    "Wager": st.column_config.NumberColumn("Wager", format="$%.2f", width="small"),
    "To Win": st.column_config.NumberColumn("To Win", format="$%.2f", width="small"),
    "P/L": st.column_config.NumberColumn("P/L", format="$%.2f", width="small"),
    "Net Worth": st.column_config.NumberColumn("Net Worth", format="$%.2f"),
    "Cash": st.column_config.NumberColumn("Cash", format="$%.2f"),
    "At Risk": st.column_config.NumberColumn("At Risk", format="$%.2f"),
}


@st.fragment(run_every=15)
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
    flash = st.session_state.pop("ticket_delete_flash", None)
    if flash:
        ok, message = flash
        if ok:
            st.success(message)
        else:
            st.error(message)

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
    st.caption("Scores and results refresh automatically while this page is open.")
    if not open_rows:
        st.info("Your slip is clean. Head to the board and make your first call.")
    else:
        deletable = [bet for bet in open_rows if ticket_delete_is_open(bet)]
        if deletable:
            cutoff = min(bet["season_first_start"] for bet in deletable)
            st.caption(
                "Use the delete button under an individual ticket for a full refund until "
                f"the season's first kickoff: {_safe_kickoff(cutoff)}."
            )
        render_ticket_cards(open_rows, settled=False, delete_user_id=user["id"])

    st.markdown(
        f"<div class='section-head'><h3>Settled</h3><span>Record {record}{win_rate}</span></div>",
        unsafe_allow_html=True,
    )
    if not settled_rows:
        st.info("Results will land here after the final whistle.")
    else:
        render_ticket_cards(settled_rows, settled=True)


# =====================================================================
# Tab 3 — Final scores
# =====================================================================
def _current_results_week(completed_games: list[dict]) -> int | None:
    """
    The week Results should default to: the active board week once any of
    its games have kicked off (live or final), otherwise the most recently
    completed week.
    """
    upcoming = load_upcoming_games()
    board_week = int(upcoming[0]["week"]) if upcoming else None
    if board_week is not None:
        live_weeks = {int(g["week"]) for g in load_live_games()}
        completed_weeks = {int(g["week"]) for g in completed_games}
        if board_week in live_weeks or board_week in completed_weeks:
            return board_week
    completed_weeks = {int(g["week"]) for g in completed_games}
    return max(completed_weeks) if completed_weeks else None


@st.fragment(run_every=30)
def tab_results() -> None:
    games = load_completed_games()
    st.markdown(
        "<div class='fd-view-head'><h2>Results</h2><p>Every completed FBS game on the league board</p></div>",
        unsafe_allow_html=True,
    )
    if not games:
        st.info("Final scores will appear here as games finish.")
        return

    weeks = sorted({int(game["week"]) for game in games}, reverse=True)
    default_week = _current_results_week(games)
    if default_week is not None and default_week not in weeks:
        weeks = sorted({*weeks, default_week}, reverse=True)

    options = ["All weeks", *[f"Week {week}" for week in weeks]]
    default_choice = f"Week {default_week}" if default_week is not None else "All weeks"
    default_index = options.index(default_choice) if default_choice in options else 0

    filter_col, search_col = st.columns([1, 2])
    with filter_col:
        week_pick = st.selectbox(
            "Week",
            options,
            index=default_index,
            key="results_week",
        )
    with search_col:
        team_search = st.text_input(
            "Find a team",
            key="results_team",
            placeholder="e.g. TCU",
        )

    visible = games
    if week_pick != "All weeks":
        selected_week = int(str(week_pick).split()[-1])
        visible = [game for game in visible if int(game["week"]) == selected_week]
    if team_search.strip():
        needle = team_search.strip().casefold()
        visible = [
            game for game in visible
            if needle in str(game["away_team"]).casefold()
            or needle in str(game["home_team"]).casefold()
        ]

    st.markdown(
        f"<div class='fd-board-summary'>{len(visible)} final{'s' if len(visible) != 1 else ''} shown</div>",
        unsafe_allow_html=True,
    )
    if not visible:
        st.warning("No final scores match those filters.")
        return

    for game in visible:
        if game["away_score"] is None or game["home_score"] is None:
            continue
        away_score = int(game["away_score"])
        home_score = int(game["home_score"])
        away_class = " winner" if away_score > home_score else ""
        home_class = " winner" if home_score > away_score else ""
        st.markdown(
            f"""
            <div class="fd-result-card">
                <div class="fd-result-meta">Final · Week {game['week']} · {_safe_kickoff(game['start_date'])}</div>
                <div class="fd-result-team{away_class}"><span>{rank_html(game.get('away_rank'))}{escape(str(game['away_team']))}</span><strong>{away_score}</strong></div>
                <div class="fd-result-team{home_class}"><span>{rank_html(game.get('home_rank'))}{escape(str(game['home_team']))}</span><strong>{home_score}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =====================================================================
# Tab 4 — Leaderboard & Rival Tracker
# =====================================================================
def build_weekly_recap(ticket_rows: list[dict], weekly_rows: list[dict]) -> dict | None:
    """Create deterministic recap awards from public league data."""
    completed_periods = [
        (int(row["season"]), int(row["week"]))
        for row in ticket_rows
        if row.get("season") is not None and int(row.get("week") or 0) > 0
    ]
    if not completed_periods:
        return None
    season, week = max(completed_periods)
    current = [
        row for row in weekly_rows
        if int(row["season"]) == season and int(row["week"]) == week
    ]
    earlier = sorted(
        {int(row["week"]) for row in weekly_rows if int(row["season"]) == season and int(row["week"]) < week}
    )
    previous_week = earlier[-1] if earlier else 0
    previous = {
        str(row["user_id"]): money(row["net_worth"])
        for row in weekly_rows
        if int(row["season"]) == season and int(row["week"]) == previous_week
    }
    standings = [
        dict(row)
        for row in sorted(
            current,
            key=lambda row: (-money(row["net_worth"]), str(row["username"]).casefold()),
        )
    ]
    for index, row in enumerate(standings, start=1):
        row["recap_rank"] = index
        row["week_change"] = money(row["net_worth"]) - previous.get(
            str(row["user_id"]), STARTING_BANKROLL
        )

    winner = sorted(
        standings,
        key=lambda row: (-money(row["week_change"]), str(row["username"]).casefold()),
    )[0]
    bets = [row for row in ticket_rows if int(row.get("week") or 0) == week]
    winners = [row for row in bets if row["status"] == "won"]
    losses = [row for row in bets if row["status"] == "lost"]
    biggest = max(
        winners,
        key=lambda row: money(row["payout_amount"]) - money(row["wager_amount"]),
        default=None,
    )
    bad_beat = max(losses, key=lambda row: money(row["wager_amount"]), default=None)
    boldest = max(bets, key=lambda row: money(row["wager_amount"]), default=None)

    pick_counts: Counter = Counter()
    pick_sample: dict[tuple, dict] = {}
    activity: defaultdict[str, int] = defaultdict(int)
    for row in bets:
        pick_key = (int(row["game_id"]), row["bet_type"], str(row["line_placed"]))
        pick_counts[pick_key] += 1
        pick_sample.setdefault(pick_key, row)
        activity[str(row["username"])] += 1
    popular_key = max(pick_counts, key=lambda key: (pick_counts[key], str(key)), default=None)
    popular = pick_sample.get(popular_key) if popular_key else None
    most_active = max(activity, key=lambda name: (activity[name], name.casefold()), default=None)

    return {
        "season": season,
        "week": week,
        "winner": winner,
        "standings": standings,
        "ticket_count": len(bets),
        "biggest": biggest,
        "bad_beat": bad_beat,
        "boldest": boldest,
        "popular": popular,
        "popular_count": pick_counts.get(popular_key, 0) if popular_key else 0,
        "most_active": most_active,
        "most_active_count": activity.get(most_active, 0) if most_active else 0,
    }


def build_standings_snapshot(
    board: list[dict], ticket_rows: list[dict], user_id: str
) -> dict:
    """Combine season standings with the latest week's settled money movement."""
    ticket_weeks = [
        (int(row["season"]), int(row["week"]))
        for row in ticket_rows
        if row.get("season") is not None and row.get("week") is not None
    ]
    active_period = max(ticket_weeks, default=None)
    week_rows = [
        row
        for row in ticket_rows
        if active_period is not None
        and row.get("season") is not None
        and row.get("week") is not None
        and (int(row["season"]), int(row["week"])) == active_period
        and row.get("status") in {"won", "lost", "push"}
    ]

    weekly_pl: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    weekly_record: defaultdict[str, Counter] = defaultdict(Counter)
    for ticket in week_rows:
        uid = str(ticket["user_id"])
        weekly_pl[uid] += money(ticket.get("payout_amount")) - money(ticket.get("wager_amount"))
        weekly_record[uid][str(ticket["status"])] += 1

    rows: list[dict] = []
    for rank, standing in enumerate(board, start=1):
        row = dict(standing)
        uid = str(row["user_id"])
        row["rank"] = rank
        row["week_change"] = money(weekly_pl[uid])
        row["week_wins"] = weekly_record[uid]["won"]
        row["week_losses"] = weekly_record[uid]["lost"]
        row["week_pushes"] = weekly_record[uid]["push"]
        rows.append(row)

    active = [
        row
        for row in rows
        if weekly_record[str(row["user_id"])] or row["week_change"]
    ]
    weekly_winner = min(
        active,
        key=lambda row: (-row["week_change"], str(row["username"]).casefold()),
        default=None,
    )
    weekly_loser = min(
        active,
        key=lambda row: (row["week_change"], str(row["username"]).casefold()),
        default=None,
    )
    if weekly_loser and weekly_loser["week_change"] >= 0:
        weekly_loser = None

    return {
        "season": active_period[0] if active_period else None,
        "week": active_period[1] if active_period else None,
        "rows": rows,
        "leader": rows[0] if rows else None,
        "weekly_winner": weekly_winner,
        "weekly_loser": weekly_loser,
        "you": next((row for row in rows if str(row["user_id"]) == str(user_id)), None),
        "settled_count": len(week_rows),
    }


def _recap_awards(recap: dict) -> list[tuple[str, str, str]]:
    biggest = recap.get("biggest")
    bad_beat = recap.get("bad_beat")
    boldest = recap.get("boldest")
    popular = recap.get("popular")
    awards: list[tuple[str, str, str]] = []
    if biggest:
        profit = money(biggest["payout_amount"]) - money(biggest["wager_amount"])
        awards.append(("Biggest hit", str(biggest["username"]), f"{describe_bet(biggest)} · +{fmt_money(profit)}"))
    if bad_beat:
        awards.append(("Bad beat", str(bad_beat["username"]), f"{describe_bet(bad_beat)} · {fmt_money(bad_beat['wager_amount'])}"))
    if popular:
        awards.append(("The room was on", describe_bet(popular), f"{recap['popular_count']} tickets · {popular['away_team']} @ {popular['home_team']}"))
    if boldest:
        awards.append(("Biggest call", str(boldest["username"]), f"{fmt_money(boldest['wager_amount'])} on {describe_bet(boldest)}"))
    if recap.get("most_active"):
        awards.append(("Most active", str(recap["most_active"]), f"{recap['most_active_count']} tickets placed"))
    return awards[:4]


def _share_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_share_card(recap: dict) -> bytes:
    """Render a portrait PNG that feels like a final-score broadcast card."""
    width, height = 1080, 1350
    ink, field, blue = "#07110e", "#183426", "#174485"
    yellow, paper, red, muted = "#f2c84b", "#f7f3e8", "#db4a40", "#b9c7bd"
    image = Image.new("RGB", (width, height), ink)
    draw = ImageDraw.Draw(image)
    display = _share_font(76, bold=True)
    headline = _share_font(54, bold=True)
    body = _share_font(32, bold=True)
    utility = _share_font(24)
    small = _share_font(20, bold=True)

    draw.rectangle((0, 0, width, 170), fill=blue)
    draw.rectangle((0, 162, width, 170), fill=yellow)
    draw.text((60, 38), "FOURTH DOWN", font=headline, fill=paper)
    draw.text((60, 108), f"WEEK {recap['week']:02d}  /  {recap['season']} FINAL", font=utility, fill=yellow)
    draw.text((60, 218), "THIS WEEK'S WINNER", font=small, fill=yellow)
    winner = recap["winner"]
    draw.text((55, 258), str(winner["username"]).upper(), font=display, fill=paper)
    change = money(winner["week_change"])
    change_label = f"+{fmt_money(change)}" if change > 0 else (fmt_money(change) if change < 0 else "EVEN")
    draw.text((60, 350), f"{change_label} THIS WEEK", font=body, fill=yellow)

    draw.rectangle((50, 430, width - 50, 900), fill=field, outline="#667a6c", width=3)
    draw.text((80, 466), "LEAGUE TABLE", font=small, fill=muted)
    y = 520
    for row in recap["standings"][:6]:
        draw.line((80, y + 58, width - 80, y + 58), fill="#52675a", width=2)
        draw.text((80, y), f"{int(row['recap_rank']):02d}", font=body, fill=red)
        draw.text((160, y + 4), str(row["username"]).upper()[:22], font=body, fill=paper)
        worth = fmt_money(row["net_worth"])
        box = draw.textbbox((0, 0), worth, font=body)
        draw.text((width - 80 - (box[2] - box[0]), y + 4), worth, font=body, fill=paper)
        y += 64

    awards = _recap_awards(recap)
    draw.text((60, 950), "WEEKLY RECEIPTS", font=small, fill=yellow)
    y = 995
    for label, name, detail in awards[:3]:
        draw.text((60, y), label.upper(), font=small, fill=muted)
        draw.text((250, y - 5), name.upper()[:28], font=body, fill=paper)
        draw.text((250, y + 32), detail[:55], font=utility, fill=muted)
        y += 88

    draw.rectangle((0, height - 62, width, height), fill=yellow)
    draw.text((60, height - 47), f"{recap['ticket_count']} TICKETS  ·  EVEN MONEY  ·  NO HOUSE VIG", font=small, fill=ink)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_share_controls(card: bytes, recap: dict) -> None:
    filename = f"fourth-down-week-{recap['week']}-final.png"
    encoded = base64.b64encode(card).decode("ascii")
    winner = str(recap["winner"]["username"])
    share_text = json.dumps(
        f"Fourth Down Week {recap['week']} final: {winner} wins the week."
    )
    components.html(
        f"""
        <button id="share-week" style="width:100%;height:46px;border:1px solid #07110e;background:#f2c84b;color:#07110e;font:700 12px monospace;text-transform:uppercase;cursor:pointer">Share Week {recap['week']} final</button>
        <script>
        const button = document.getElementById('share-week');
        button.addEventListener('click', async () => {{
          const raw = atob('{encoded}');
          const bytes = new Uint8Array(raw.length);
          for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
          const file = new File([bytes], '{filename}', {{type:'image/png'}});
          try {{
            if (navigator.canShare && navigator.canShare({{files:[file]}})) {{
              await navigator.share({{title:'Fourth Down Week {recap['week']}', text:{share_text}, files:[file]}});
            }} else {{
              await navigator.clipboard.writeText({share_text});
              button.textContent = 'Recap copied';
            }}
          }} catch (error) {{ if (error.name !== 'AbortError') button.textContent = 'Use download below'; }}
        }});
        </script>
        """,
        height=54,
    )
    st.download_button(
        "Download share card",
        data=card,
        file_name=filename,
        mime="image/png",
        width="stretch",
    )


def render_weekly_recap(ticket_rows: list[dict], weekly_rows: list[dict]) -> None:
    recap = build_weekly_recap(ticket_rows, weekly_rows)
    if recap is None:
        return

    winner = recap["winner"]
    change = money(winner["week_change"])
    with st.expander(
        f"Week {recap['week']} final · {winner['username']} won {fmt_money(change)}",
        expanded=False,
    ):
        awards = _recap_awards(recap)
        st.markdown(
            "<div class='fd-recap-grid'>" + "".join(
                f"<div class='fd-recap-item'><small>{escape(label)}</small><strong>{escape(name)}</strong><span>{escape(detail)}</span></div>"
                for label, name, detail in awards
            ) + "</div>",
            unsafe_allow_html=True,
        )
        card = build_share_card(recap)
        render_share_controls(card, recap)


def render_weekly_dashboard(rows: list[dict], user: dict) -> None:
    if not rows:
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
    history = history[(history["season"] == latest_season) & (history["week"] > 0)].copy()
    if history["week"].nunique() < 1:
        return
    history["rank"] = (
        history.groupby("week")["net_worth"]
        .rank(method="min", ascending=False)
        .astype(int)
    )
    history["week_label"] = "W" + history["week"].astype(str)
    history["value_label"] = history["net_worth"].map(lambda value: f"${value:,.2f}")

    week_count = int(history["week"].nunique())
    if week_count == 1:
        latest_week = int(history["week"].iloc[0])
        history["season_pl"] = history["net_worth"] - float(STARTING_BANKROLL)
        history["pl_label"] = history["season_pl"].map(fmt_money_change)
        history["result"] = history["season_pl"].map(
            lambda value: "Gain" if value > 0 else ("Loss" if value < 0 else "Even")
        )
        y = alt.Y(
            "player:N",
            title=None,
            sort=alt.SortField(field="rank", order="ascending"),
            axis=alt.Axis(labelLimit=115),
        )
        x = alt.X(
            "season_pl:Q",
            title=f"WEEK {latest_week} PROFIT / LOSS",
            scale=alt.Scale(zero=True),
            axis=alt.Axis(format="$,.0f"),
        )
        color = alt.Color(
            "result:N",
            title=None,
            scale=alt.Scale(
                domain=["Gain", "Even", "Loss"],
                range=["#d6a800", "#667a6c", "#c8433b"],
            ),
            legend=None,
        )
        tooltip = [
            alt.Tooltip("player:N", title="Player"),
            alt.Tooltip("rank:Q", title="Place"),
            alt.Tooltip("pl_label:N", title=f"Week {latest_week} P/L"),
            alt.Tooltip("value_label:N", title="Net worth"),
        ]
        bars = alt.Chart(history).mark_bar(size=14).encode(
            x=x, y=y, color=color, tooltip=tooltip
        )
        points = alt.Chart(history).mark_point(filled=True, size=65).encode(
            x=x, y=y, color=color, tooltip=tooltip
        )
        labels = alt.Chart(history).mark_text(
            align="left", baseline="middle", dx=7, color="#07110e", fontSize=11
        ).encode(x=x, y=y, text="pl_label:N")
        chart = (
            (bars + points + labels)
            .properties(height=max(220, len(history) * 28))
            .configure(background="transparent")
            .configure_view(stroke=None)
            .configure_axis(
                domainColor="#52675a",
                gridColor="#a9b99f",
                labelColor="#07110e",
                titleColor="#07110e",
                titleFont="IBM Plex Mono",
                labelFont="IBM Plex Mono",
            )
        )
        with st.expander(
            f"Week {latest_week} money chart · profit / loss",
            expanded=False,
        ):
            st.altair_chart(chart, width="stretch")
        return

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
                scale=alt.Scale(domain=[max_rank, 1]),
                axis=alt.Axis(tickMinStep=1, tickCount=max_rank),
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
            gridColor="#a9b99f",
            labelColor="#07110e",
            titleColor="#07110e",
            titleFont="IBM Plex Mono",
            labelFont="IBM Plex Mono",
        )
        .configure_legend(
            labelColor="#07110e",
            labelFont="IBM Plex Mono",
            symbolStrokeWidth=4,
        )
    )
    with st.expander("Season history · week-by-week rank", expanded=False):
        st.altair_chart(chart, width="stretch")


@st.fragment(run_every=60)
def tab_leaderboard(user: dict) -> None:
    board = load_leaderboard()
    if not board:
        st.info("No players yet.")
        return

    all_bets = load_all_bets()
    snapshot = build_standings_snapshot(board, all_bets, str(user["id"]))
    leader = snapshot["leader"]
    week_label = f"Week {snapshot['week']}" if snapshot["week"] is not None else "This week"
    st.markdown(
        f"""
        <div class="fd-view-head">
            <h2>Standings</h2>
            <p>{escape(week_label)} so far · {snapshot['settled_count']} tickets settled</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    weekly_winner = snapshot["weekly_winner"]
    weekly_loser = snapshot["weekly_loser"]
    your_row = snapshot["you"]
    pulse = [
        ("Season leader", str(leader["username"]), fmt_money(leader["net_worth"])),
        (
            f"{week_label} leader",
            str(weekly_winner["username"]) if weekly_winner else "No action",
            fmt_money_change(weekly_winner["week_change"]) if weekly_winner else "$0.00",
        ),
        (
            "Biggest loss",
            str(weekly_loser["username"]) if weekly_loser else "None yet",
            fmt_money_change(weekly_loser["week_change"]) if weekly_loser else "$0.00",
        ),
        (
            "Your position",
            f"#{your_row['rank']} of {len(board)}" if your_row else "Unranked",
            f"{fmt_money_change(your_row['week_change'])} this week" if your_row else "No tickets",
        ),
    ]
    st.markdown(
        "<div class='fd-standings-pulse'>"
        + "".join(
            f"<div><small>{escape(label)}</small><strong>{escape(name)}</strong><span>{escape(value)}</span></div>"
            for label, name, value in pulse
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f"<div class='fd-table-head'><span>Rank / player</span><span>{escape(week_label)}</span><span>Total</span></div>",
        unsafe_allow_html=True,
    )
    for row in snapshot["rows"]:
        is_you = str(row["user_id"]) == str(user["id"])
        you_label = " · You" if is_you else ""
        week_change = money(row["week_change"])
        change_class = "up" if week_change > 0 else ("down" if week_change < 0 else "flat")
        st.markdown(
            f"""
            <div class="leader-row {'is-you' if is_you else ''}">
                <div class="leader-rank">{row['rank']:02d}</div>
                <div>
                    <div class="leader-name">{escape(str(row['username']))}{you_label}</div>
                    <div class="leader-record">Season {row['wins']}-{row['losses']}-{row['pushes']} · {fmt_money(row['open_stake'])} at risk</div>
                </div>
                <div class="leader-week {change_class}"><strong>{fmt_money_change(week_change)}</strong><small>{row['week_wins']}-{row['week_losses']}-{row['week_pushes']}</small></div>
                <div class="leader-worth">{fmt_money(row['net_worth'])}<small>Net worth</small></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    weekly_rows = load_weekly_standings()
    render_weekly_dashboard(weekly_rows, user)
    render_weekly_recap(load_weekly_recap_bets(), weekly_rows)

    st.markdown(
        "<div class='section-head'><h3>Scout the league</h3><span>Every ticket is public</span></div>",
        unsafe_allow_html=True,
    )
    st.caption("Choose one player to see exactly where their money is riding.")

    by_user: dict[str, list[dict]] = {}
    for b in all_bets:
        by_user.setdefault(str(b["user_id"]), []).append(b)

    player_ids = [str(row["user_id"]) for row in board]
    player_by_id = {str(row["user_id"]): row for row in board}
    selected_id = st.selectbox(
        "Player",
        player_ids,
        format_func=lambda uid: (
            f"{player_by_id[uid]['username']} · {fmt_money(player_by_id[uid]['net_worth'])}"
        ),
        key="scout_player",
    )
    rows = by_user.get(selected_id, [])
    if not rows:
        st.caption("No bets placed yet.")
        return
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
    try:
        ensure_runtime_schema()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Database setup failed: {exc}")
        st.stop()
        return

    cookie_manager = _cookie_manager()
    pending_cookie = st.session_state.pop("auth_cookie_to_set", None)
    if pending_cookie:
        cookie_manager.set(
            AUTH_COOKIE,
            pending_cookie,
            key=f"set_auth_{_token_hash(pending_cookie)[:12]}",
            expires_at=datetime.now(timezone.utc) + timedelta(days=AUTH_SESSION_DAYS),
            secure=True,
            same_site="strict",
        )

    if "user" not in st.session_state:
        browser_token = cookie_manager.get(AUTH_COOKIE)
        if browser_token:
            try:
                restored = restore_auth_session(browser_token)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not restore your session: {exc}")
                restored = None
            if restored:
                st.session_state.user = restored
                st.session_state.auth_token = browser_token
                refresh_data()
            else:
                try:
                    cookie_manager.delete(
                        AUTH_COOKIE,
                        key=f"delete_stale_{_token_hash(browser_token)[:12]}",
                    )
                except Exception:
                    pass

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
        kickoff_countdown = _kickoff_countdown(header_games[0]["start_date"]) if header_games else "—"
    except Exception:  # the tabs will show the actionable database error if needed
        header_week = "—"
        next_kickoff = "Board pending"
        kickoff_countdown = "—"

    st.markdown(
        f"""
        <div class="fd-scorebar">
            <div class="fd-brand"><span class="fd-ball">4D</span><span>Fourth Down</span></div>
            <div class="fd-readout fd-week"><span>Week</span><strong>{escape(header_week)}</strong></div>
            <div class="fd-readout fd-kickoff-readout"><span>Next · {escape(next_kickoff)}</span><strong>{escape(kickoff_countdown)}</strong></div>
            <div class="fd-readout"><span>{escape(str(user['username']))}</span><strong>{fmt_money(balance)}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        render_data_health(load_data_status())
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Automation status is temporarily unavailable: {exc}")

    t1, t2, t3, t4 = st.tabs(["The board", "My tickets", "Results", "Standings"])
    with t1:
        tab_place_bets(user, balance)
    with t2:
        tab_my_tickets(user, balance)
    with t3:
        tab_results()
    with t4:
        tab_leaderboard(user)

    refresh_col, signout_col = st.columns([1, 1])
    with refresh_col:
        if st.button("↻ Refresh lines", width="stretch"):
            refresh_data()
            st.rerun()
    with signout_col:
        if st.button("Sign out", width="stretch"):
            token = st.session_state.pop("auth_token", None) or cookie_manager.get(AUTH_COOKIE)
            try:
                revoke_auth_session(token)
            finally:
                if token:
                    cookie_manager.delete(
                        AUTH_COOKIE,
                        key=f"delete_auth_{_token_hash(token)[:12]}",
                    )
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
