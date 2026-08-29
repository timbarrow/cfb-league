# 🏈 CFB League — private college football betting app

A mobile-first, fake-money betting league for ~10 people. Everyone starts with
**$10,000**, bets spreads and totals at **even money (2.00× return, no vig)**, and the
leaderboard ranks players by net worth. Every ticket in the league is public.

The app infrastructure runs for **$0/month** on Streamlit Community Cloud,
Supabase's free tier and public-repository GitHub Actions. Live scores require
a College Football Data **Tier 1** membership.

---

## Files

| File | Purpose |
|---|---|
| `schema.sql` | PostgreSQL schema (`users`, `games`, `bets`, `leaderboard` view) |
| `db.py` | Engine, transaction helpers, password hashing (stdlib PBKDF2) |
| `cfb_sync.py` | Pulls games / lines / AP rankings from CFBD into Supabase |
| `settle_bets.py` | Grades completed games and credits winnings |
| `app.py` | The Streamlit UI (3 tabs) |
| `test_settlement.py` | Grading + payout math tests (no DB needed) |
| `test_experience.py` | Weekly recap, share-card and kickoff-window tests |
| `integration_check.py` | End-to-end test against a scratch Postgres |
| `.github/workflows/cfb-pipeline.yml` | Daily Central-time games, rankings and odds schedule |
| `.github/workflows/live-scores.yml` | Isolated live-score and settlement schedule |

---

## Setup (about 15 minutes)

### 1. Supabase

1. Create a free project at [supabase.com](https://supabase.com).
2. SQL Editor → paste all of `schema.sql` → **Run**.
3. Project Settings → Database → **Connection string → URI**. Copy the
   **Session pooler** URI (port `5432`) and swap in your database password.

> Free Supabase projects pause after ~1 week of inactivity. The hourly GitHub
> Action keeps it awake during the season.

### 2. College Football Data API key

Free key: <https://collegefootballdata.com/key>. Arrives by email.

### 3. GitHub

Push this folder to a repo, then add two **Actions secrets**
(Settings → Secrets and variables → Actions):

- `DATABASE_URL`
- `CFBD_API_KEY`

Run the workflow once manually (Actions → *CFB pipeline* → **Run workflow**) to
populate the board.

### 4. Streamlit Community Cloud

1. [share.streamlit.io](https://share.streamlit.io) → **New app** → point at your
   repo, main file `app.py`.
2. Advanced settings → **Secrets** → paste:

   ```toml
   DATABASE_URL = "postgresql://postgres.xxxx:PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
   LEAGUE_INVITE_CODE = "pick-something"
   ```

3. Deploy. Share the URL; each member signs up with the invite code and is
   staked $10,000 automatically.

---

## How it works

### Next-week locking

`cfb_sync.py` asks CFBD's `/calendar` which week still has unplayed games, and
syncs that one (plus the previous week, to pick up final scores). The app then
derives the board from the earliest future kickoff in the table and shows
**only that week**, and only games that haven't started:

```sql
where g.start_date > now() and g.status = 'scheduled'
```

Kickoff is re-checked inside the bet transaction, so a game can't be bet after
it starts even if the page was left open.

### Lines and live results

- The board refreshes once daily at **8:07 AM Central**. Each market uses
  **DraftKings first**, then fills only a missing spread or total from
  **Bovada**. The game card labels the source whenever the books are mixed.
- Mon–Wed results refresh at **8 PM, 10 PM and 11:59 PM Central**.
- Thu–Fri live results refresh about every seven minutes from **8 PM through
  midnight**.
- Saturday live results refresh about every seven minutes from **11 AM through
  Sunday at 2 AM**, followed by a **Sunday 6 AM** sweep.
- Live runs use CFBD's Tier 1 `/scoreboard` endpoint in one API call, then grade
  completed tickets immediately.

GitHub's scheduler can start a job a few minutes late. Also, `*/7` produces
`:00, :07, … :56`, followed by the next hour's `:00`, so the hour boundary is a
four-minute interval.

### Tickets, live results and sign-in

Pending tickets can be deleted from **My tickets** for a full refund until the
season's earliest kickoff. At that instant the delete controls disappear and
the transaction rejects any stale deletion request; tickets are final for the
rest of the season. Deletion is per ticket—there is no bulk-delete action—and
the refunded ticket is removed as though it had never been placed. Players can
place any number of separate tickets on the same game and market, whether the
line moved or remained unchanged.

After a successful sign-in (or new-account signup), the browser receives a
random 30-day session token. The database stores only its SHA-256 hash—not the
password or raw token. Signing out revokes the session immediately.

Open tickets refresh automatically while the app is open. When CFBD posts a
score they change to **LIVE**, then **FINAL · GRADING**, and finally the settled
win/loss/push receipt. The board and header also show when odds and
scores were last refreshed.

### Weekly league experience

- The standings dashboard starts at **Week 0** with every player at $10,000.
- A season-tape chart tracks every player's weekly rank and net worth.
- Each closed week publishes a recap with the weekly winner, biggest hit, bad
  beat, most popular pick and biggest wager.
- The recap generates a portrait PNG that can be shared from a phone or
  downloaded for the group chat.

### Betting math

`spread_home` is the home team's line (negative = home favored); `spread_away`
is stored as its negation. A bet wins when
`(your side's points − opponent's points) + your line > 0`; exactly `0` is a
push. Totals compare `home + away` against the line.

The stake is debited when the ticket is placed. On settlement:

| Result | Credited back |
|---|---|
| Won | `wager × 2.00` (stake + equal profit) |
| Push | `wager` |
| Lost | `0` |

All money math uses `Decimal`, never floats.

### Concurrency and correctness

- **Bet placement** locks the bettor's `users` row (`SELECT … FOR UPDATE`),
  re-reads the game inside the same transaction, rejects the bet if the line
  moved between render and submit, and writes the ticket + debit atomically.
- **Concurrent submissions** are serialized against the player's bankroll;
  intentional repeat tickets on the same market remain allowed.
- **Settlement** takes a transaction-level advisory lock, selects gradable bets
  `FOR UPDATE`, and guards every write with `status = 'pending'` — so running it
  twice credits nothing twice. Balances are updated once per user, not per bet.
- **Overdrafts** are impossible: the check happens under the row lock *and*
  a `balance >= 0` CHECK constraint backstops it.
- **Line movement** never re-prices an open ticket — `line_placed` is frozen
  on the bet row.

### Mobile-first UI

- 44px minimum touch targets on every button, radio row and select.
- 16px inputs (stops iOS Safari from zooming on focus).
- Filters collapsed into a single top-level expander.
- Columns wrap instead of squashing below 640px.
- Selections are chunky tappable rows, not tiny dots.
- Betting is one form per game card: pick a side → enter a wager → one button.

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # fill in DATABASE_URL + CFBD_API_KEY
export $(grep -v '^#' .env | xargs)

python cfb_sync.py            # populate this week's board
streamlit run app.py
```

## Tests

```bash
python test_settlement.py     # grading + payout edge cases, no DB
python -m pytest -q test_sync.py  # DraftKings + live-score shaping, no DB
python -m pytest -q test_experience.py  # recap + share-card experience

# End-to-end against a THROWAWAY database (it drops and recreates the tables):
DATABASE_URL=postgresql://... python integration_check.py
```

`integration_check.py` covers signup, the kickoff lock, the balance guard,
stale-line rejection, repeat tickets, win/loss/push settlement,
re-settlement idempotency, and the leaderboard totals.

## Manual operations

```bash
python cfb_sync.py --season 2026 --week 7 --season-type regular
python cfb_sync.py --live            # Tier 1 score-only refresh
python settle_bets.py --dry-run     # grade everything, write nothing
```

## Cost

| Service | Plan | This app's usage |
|---|---|---|
| Streamlit Community Cloud | unlimited public apps | 1 app, ~10 users |
| Supabase | 500 MB DB | a few MB per season |
| CFBD API | Tier 1 (5,000 calls/month) | ~1,100 calls in a busy month |
| GitHub Actions (**public repo**) | unlimited minutes | **$0** |
| GitHub Actions (private repo) | 2,000 min/mo on Free | see below |

**On a public repo this is genuinely $0** — Actions minutes are unlimited and no
allowance is consumed. Keep the repo public unless you have a reason not to;
none of the secrets live in the code.

On a **private** repo, minutes are billed and GitHub rounds every job up to the
nearest minute. This live schedule produces roughly 5,000–6,000 short runs over
an August–January season. The included public repository therefore matters:
its GitHub Actions cost remains **$0**.

## Notes

- Play money only. This is a private league scoreboard, not a sportsbook.
- Passwords use PBKDF2-SHA256 (240k rounds, per-user salt) from the standard
  library — no native wheels needed on Streamlit Cloud.
- DraftKings is the primary odds provider. `CFBD_PROVIDER` can override it and
  `CFBD_FALLBACK_PROVIDER` controls the market-by-market backup (Bovada by
  default).
