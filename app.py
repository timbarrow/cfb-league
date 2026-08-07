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

    LOCAL_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    LOCAL_TZ = timezone.utc

CENT = Decimal("0.01")
STARTING_BANKROLL = Decimal("10000.00")
DEFAULT_MULTIPLIER = Decimal("1.91")  # -110

# =====================================================================
# Page config + mobile-first CSS
# =====================================================================
st.set_page_config(
    page_title="CFB League",
    page_icon="🏈",
    layout="centered",           # narrower default = better on phones
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
/* ---- Layout: tight gutters, room for thumbs at the bottom ---- */
.block-container { padding: 0.6rem 0.7rem 5rem !important; max-width: 780px; }
#MainMenu, footer { visibility: hidden; }

/* ---- Touch targets: 44px minimum (Apple HIG / WCAG 2.5.5) ---- */
.stButton > button,
.stFormSubmitButton > button,
div[data-baseweb="select"] > div { min-height: 44px; }
.stButton > button, .stFormSubmitButton > button {
    border-radius: 10px; font-weight: 600; width: 100%;
}

/* 16px inputs stop iOS Safari auto-zooming on focus */
input, textarea, select { font-size: 16px !important; }

/* ---- Radios as chunky, tappable rows ---- */
div[role="radiogroup"] > label {
    display: flex; align-items: center;
    padding: 10px 12px; margin-bottom: 6px;
    border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
    min-height: 44px; width: 100%;
}
div[role="radiogroup"] > label:hover { border-color: rgba(120,170,255,.9); }

/* ---- Tabs: scrollable, thumb-sized ---- */
.stTabs [data-baseweb="tab-list"] { gap: 2px; overflow-x: auto; }
.stTabs [data-baseweb="tab"] { min-height: 44px; padding: 0 12px; font-size: .95rem; }

/* ---- Columns wrap instead of squashing on narrow screens ---- */
@media (max-width: 640px) {
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .4rem; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        min-width: calc(50% - 0.4rem) !important; flex: 1 1 calc(50% - 0.4rem) !important;
    }
    div[data-testid="stMetricValue"] { font-size: 1.25rem; }
    div[data-testid="stMetricLabel"] { font-size: .75rem; }
    h1 { font-size: 1.4rem !important; }
    h2 { font-size: 1.15rem !important; }
}

/* ---- Game card chrome ---- */
.rank-badge {
    display:inline-block; background:#b91c1c; color:#fff; font-size:.68rem;
    font-weight:700; padding:1px 5px; border-radius:5px; margin-right:4px;
    vertical-align:middle;
}
.matchup { font-size:1.02rem; font-weight:650; line-height:1.45; }
.meta { font-size:.76rem; opacity:.72; margin-top:2px; }
.pill {
    display:inline-block; padding:1px 7px; border-radius:999px;
    border:1px solid rgba(128,128,128,.4); font-size:.7rem; margin-right:4px;
}
.stDataFrame { font-size: .82rem; }
</style>
""",
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
    return f"{local:%a %b} {local.day} · {hour}:{local:%M} {ampm} ET"


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

    return True, "Account created — you're staked with $10,000. Sign in below."


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
    st.title("🏈 The League")
    st.caption("Private college football pick'em. $10,000 fake bankroll. Standard −110.")

    tab_in, tab_up = st.tabs(["Sign in", "Create account"])

    with tab_in:
        with st.form("login_form"):
            u = st.text_input("Username", key="li_user", autocomplete="username")
            p = st.text_input("Password", type="password", key="li_pass",
                              autocomplete="current-password")
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
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
            code = st.text_input("League invite code", key="su_code")
            if st.form_submit_button("Create account", width="stretch"):
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


def tab_place_bets(user: dict, balance: Decimal) -> None:
    games = load_upcoming_games()

    if not games:
        st.info(
            "No upcoming games are loaded yet. Run `cfb_sync.py` (or wait for the "
            "scheduled GitHub Action) to pull this week's board."
        )
        return

    wk = games[0]
    st.markdown(
        f"### Week {wk['week']} board"
        f"<span class='meta'> — {wk['season']} {wk['season_type']}</span>",
        unsafe_allow_html=True,
    )

    conferences = sorted(
        {c for g in games for c in (g["home_conference"], g["away_conference"]) if c}
    )

    with st.expander("🔎 Filters", expanded=False):
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

    st.caption(f"{len(visible)} of {len(games)} games · Bankroll {fmt_money(balance)} · −110 (1.91×)")

    if not visible:
        st.warning("No games match those filters. Loosen them in the Filters panel above.")
        return

    for g in visible:
        opts = market_options(g, bet_filter)
        separator = "vs" if g["neutral_site"] else "@"
        neutral_note = " · neutral site" if g["neutral_site"] else ""
        pills = (
            f"<span class='pill'>{g['away_conference'] or 'IND'}</span>"
            f"<span class='pill'>{g['home_conference'] or 'IND'}</span>"
        )
        if g["lines_provider"]:
            pills += f"<span class='pill'>{g['lines_provider']}</span>"

        with st.container(border=True):
            st.markdown(
                f"<div class='matchup'>{rank_html(g['away_rank'])}{g['away_team']}"
                f"<span style='opacity:.55'> {separator} </span>"
                f"{rank_html(g['home_rank'])}{g['home_team']}</div>"
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
                        "Wager ($)",
                        min_value=1.0,
                        max_value=float(max(balance, Decimal("1.00"))),
                        value=100.0 if balance >= 100 else float(max(balance, Decimal("1.00"))),
                        step=25.0,
                        key=f"amt_{g['id']}",
                    )
                with c2:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    submitted = st.form_submit_button(
                        "Place bet", type="primary", width="stretch"
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
    st.caption(
        f"Record {wins}-{losses}-{pushes}"
        + (f" · {wins / graded:.1%} win rate" if graded else "")
    )

    df_open, df_settled = bets_to_frames(rows)

    st.markdown("#### Open tickets")
    if df_open.empty:
        st.info("No open tickets. Head to **Place Bets** to get on the board.")
    else:
        st.dataframe(
            df_open, width="stretch", hide_index=True,
            column_config={k: v for k, v in MONEY_COLS.items() if k in df_open.columns},
        )

    st.markdown("#### Settled tickets")
    if df_settled.empty:
        st.info("Nothing settled yet.")
    else:
        st.dataframe(
            df_settled, width="stretch", hide_index=True,
            column_config={k: v for k, v in MONEY_COLS.items() if k in df_settled.columns},
        )


# =====================================================================
# Tab 3 — Leaderboard & Rival Tracker
# =====================================================================
def tab_leaderboard(user: dict) -> None:
    board = load_leaderboard()
    if not board:
        st.info("No players yet.")
        return

    df = pd.DataFrame(
        [
            {
                "#": i + 1,
                "Player": r["username"] + (" (you)" if str(r["user_id"]) == user["id"] else ""),
                "Net Worth": float(money(r["net_worth"])),
                "Cash": float(money(r["cash"])),
                "At Risk": float(money(r["open_stake"])),
                "Record": f"{r['wins']}-{r['losses']}-{r['pushes']}",
                "P/L": float(money(r["settled_pl"])),
            }
            for i, r in enumerate(board)
        ]
    )
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_config={k: v for k, v in MONEY_COLS.items() if k in df.columns},
    )

    st.markdown("#### 🔍 Rival tracker")
    st.caption("Every ticket in the league is public. Tap a player to inspect their slip.")

    all_bets = load_all_bets()
    by_user: dict[str, list[dict]] = {}
    for b in all_bets:
        by_user.setdefault(str(b["user_id"]), []).append(b)

    for r in board:
        uid = str(r["user_id"])
        label = (
            f"{r['username']} — {fmt_money(r['net_worth'])} "
            f"({r['wins']}-{r['losses']}-{r['pushes']})"
        )
        with st.expander(label, expanded=False):
            rows = by_user.get(uid, [])
            if not rows:
                st.caption("No bets placed yet.")
                continue
            df_open, df_settled = bets_to_frames(rows)
            st.markdown("**Open**")
            if df_open.empty:
                st.caption("No open tickets.")
            else:
                st.dataframe(
                    df_open.drop(columns=["Kickoff"], errors="ignore"),
                    width="stretch", hide_index=True,
                    column_config={k: v for k, v in MONEY_COLS.items() if k in df_open.columns},
                )
            st.markdown("**Settled**")
            if df_settled.empty:
                st.caption("Nothing settled.")
            else:
                st.dataframe(
                    df_settled, width="stretch", hide_index=True,
                    column_config={k: v for k, v in MONEY_COLS.items() if k in df_settled.columns},
                )


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

    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.markdown(
            f"<div class='matchup'>🏈 {user['username']}</div>"
            f"<div class='meta'>Cash {fmt_money(balance)}</div>",
            unsafe_allow_html=True,
        )
    with head_r:
        if st.button("Sign out", width="stretch"):
            st.session_state.pop("user", None)
            refresh_data()
            st.rerun()

    t1, t2, t3 = st.tabs(["🎯 Place Bets", "🎟️ My Tickets", "🏆 Leaderboard"])
    with t1:
        tab_place_bets(user, balance)
    with t2:
        tab_my_tickets(user, balance)
    with t3:
        tab_leaderboard(user)

    st.divider()
    if st.button("↻ Refresh data", width="stretch"):
        refresh_data()
        st.rerun()
    st.caption("Play money only. Lines via collegefootballdata.com. Settlement runs hourly.")


if __name__ == "__main__":
    main()
