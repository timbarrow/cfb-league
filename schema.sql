-- =====================================================================
-- CFB PRIVATE BETTING LEAGUE — Supabase / PostgreSQL schema
-- Run this once in the Supabase SQL Editor.
-- Idempotent: safe to re-run.
-- =====================================================================

-- gen_random_uuid() is built into PostgreSQL 13+ (Supabase runs 15/17), so no
-- extension is required. Uncomment the line below only on PostgreSQL 12 or older.
-- create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
create table if not exists public.users (
    id            uuid primary key default gen_random_uuid(),
    username      text        not null,
    password_hash text        not null,
    balance       numeric(14,2) not null default 10000.00,
    is_admin      boolean     not null default false,
    created_at    timestamptz not null default now(),
    constraint users_balance_nonneg check (balance >= 0),
    constraint users_username_len   check (char_length(trim(username)) between 2 and 24)
);

-- Case-insensitive unique username (prevents "Dave" vs "dave" collisions).
create unique index if not exists users_username_lower_key
    on public.users (lower(username));

-- ---------------------------------------------------------------------
-- games   (id = College Football Data API game id)
-- ---------------------------------------------------------------------
create table if not exists public.games (
    id              bigint      primary key,          -- CFBD game id
    season          integer     not null,
    week            integer     not null,
    season_type     text        not null default 'regular',   -- regular | postseason
    classification  text,                              -- fbs rows power the league board/health
    start_date      timestamptz not null,             -- kickoff (UTC) — drives bet locking
    neutral_site    boolean     not null default false,
    home_team       text        not null,
    away_team       text        not null,
    home_conference text,
    away_conference text,
    home_rank       integer,                          -- AP Top 25 rank, null if unranked
    away_rank       integer,
    spread_home     numeric(5,1),                     -- home-team spread, negative = home favored
    total_line      numeric(5,1),
    lines_provider  text,
    lines_updated_at timestamptz,
    status          text        not null default 'scheduled',  -- scheduled | in_progress | completed
    home_score      integer,
    away_score      integer,
    game_period     integer,
    game_clock      text,
    scores_updated_at timestamptz,
    updated_at      timestamptz not null default now(),
    constraint games_status_chk      check (status in ('scheduled','in_progress','completed')),
    constraint games_season_type_chk check (season_type in ('regular','postseason')),
    constraint games_rank_chk        check (
        (home_rank is null or home_rank between 1 and 25) and
        (away_rank is null or away_rank between 1 and 25)
    ),
    -- A completed game must have both scores; prevents grading against half-loaded rows.
    constraint games_completed_has_scores check (
        status <> 'completed' or (home_score is not null and away_score is not null)
    )
);

-- Idempotent upgrades for leagues created with an earlier schema.
alter table public.games add column if not exists lines_updated_at timestamptz;
alter table public.games add column if not exists scores_updated_at timestamptz;
alter table public.games add column if not exists classification text;
alter table public.games add column if not exists game_period integer;
alter table public.games add column if not exists game_clock text;

create index if not exists games_week_idx       on public.games (season, season_type, week);
create index if not exists games_start_date_idx on public.games (start_date);
create index if not exists games_status_idx     on public.games (status) where status <> 'completed';

-- ---------------------------------------------------------------------
-- bets
-- ---------------------------------------------------------------------
create table if not exists public.bets (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid   not null references public.users (id) on delete cascade,
    game_id           bigint not null references public.games (id) on delete restrict,
    bet_type          text   not null,
    -- The line AS TAKEN by the bettor. Immutable once placed, so later line
    -- movement in `games` never re-prices an existing ticket.
    line_placed       numeric(5,1)  not null,
    wager_amount      numeric(14,2) not null,
    payout_multiplier numeric(6,4)  not null default 2.00,   -- no-vig: stake + equal profit
    status            text          not null default 'pending',
    payout_amount     numeric(14,2),                          -- credited back on settlement
    created_at        timestamptz   not null default now(),
    settled_at        timestamptz,
    constraint bets_type_chk   check (bet_type in ('spread_home','spread_away','over','under')),
    constraint bets_status_chk check (status  in ('pending','won','lost','push')),
    constraint bets_wager_chk  check (wager_amount >= 1 and wager_amount <= 1000000),
    constraint bets_mult_chk   check (payout_multiplier > 1),
    constraint bets_settled_consistency check (
        (status = 'pending' and settled_at is null and payout_amount is null) or
        (status <> 'pending' and settled_at is not null and payout_amount is not null)
    )
);

create index if not exists bets_user_idx    on public.bets (user_id, created_at desc);
create index if not exists bets_game_idx    on public.bets (game_id);
create index if not exists bets_pending_idx on public.bets (status) where status = 'pending';

-- Players may add to the same side or market as often as they choose. Each
-- insert captures a separate ticket at the line shown at that moment.
drop index if exists public.bets_one_open_per_market;

-- ---------------------------------------------------------------------
-- persistent browser sessions (the browser stores only a random token)
-- ---------------------------------------------------------------------
create table if not exists public.auth_sessions (
    token_hash   char(64) primary key,
    user_id      uuid not null references public.users (id) on delete cascade,
    created_at   timestamptz not null default now(),
    expires_at   timestamptz not null,
    last_seen_at timestamptz not null default now()
);

create index if not exists auth_sessions_expires_idx
    on public.auth_sessions (expires_at);

-- ---------------------------------------------------------------------
-- Convenience view: leaderboard by net worth
--   net worth = cash balance + capital tied up in open tickets
-- ---------------------------------------------------------------------
create or replace view public.leaderboard as
select
    u.id                                                              as user_id,
    u.username,
    u.balance                                                         as cash,
    coalesce(sum(b.wager_amount) filter (where b.status = 'pending'), 0)::numeric(14,2) as open_stake,
    (u.balance + coalesce(sum(b.wager_amount) filter (where b.status = 'pending'), 0))::numeric(14,2) as net_worth,
    coalesce(sum(b.payout_amount - b.wager_amount) filter (where b.status <> 'pending'), 0)::numeric(14,2) as settled_pl,
    count(*) filter (where b.status = 'pending')                      as open_bets,
    count(*) filter (where b.status = 'won')                          as wins,
    count(*) filter (where b.status = 'lost')                         as losses,
    count(*) filter (where b.status = 'push')                         as pushes
from public.users u
left join public.bets b on b.user_id = u.id
group by u.id, u.username, u.balance;

-- ---------------------------------------------------------------------
-- Security note
-- ---------------------------------------------------------------------
-- This app connects with the Postgres role directly (server-side from
-- Streamlit), never from the browser, so PostgREST RLS is not in the
-- request path. Keep RLS ENABLED with no permissive policies so that a
-- leaked anon key cannot read the tables over the REST API.
alter table public.users disable row level security;
alter table public.bets  disable row level security;
alter table public.games disable row level security;
-- ^ If you also expose these tables via Supabase's REST API, flip these to
--   `enable row level security;` and add policies. With direct Postgres
--   connections (this app) RLS is bypassed by the table owner anyway.
