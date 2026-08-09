# 🏈 CFB League — private college football betting app

A mobile-first, fake-money betting league for ~10 people. Everyone starts with
**$10,000**, bets spreads and totals at **even money (2.00× return, no vig)**, and the
leaderboard ranks players by net worth. Every ticket in the league is public.

Runs for **$0/month**: Streamlit Community Cloud + Supabase free tier +
College Football Data API + GitHub Actions.

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
| `integration_check.py` | End-to-end test against a scratch Postgres |
| `.github/workflows/cfb-pipeline.yml` | Hourly sync + settle cron |

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
- **Double-submit** on a flaky phone connection is blocked by a partial unique
  index (`one open ticket per user per market per game`).
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

# End-to-end against a THROWAWAY database (it drops and recreates the tables):
DATABASE_URL=postgresql://... python integration_check.py
```

`integration_check.py` covers signup, the kickoff lock, the balance guard,
stale-line rejection, duplicate-ticket protection, win/loss/push settlement,
re-settlement idempotency, and the leaderboard totals.

## Manual operations

```bash
python cfb_sync.py --season 2026 --week 7 --season-type regular
python settle_bets.py --dry-run     # grade everything, write nothing
```

## Cost

| Service | Free tier | This app's usage |
|---|---|---|
| Streamlit Community Cloud | unlimited public apps | 1 app, ~10 users |
| Supabase | 500 MB DB | a few MB per season |
| CFBD API | free key | ~4 calls per run |
| GitHub Actions (**public repo**) | unlimited minutes | **$0** |
| GitHub Actions (private repo) | 2,000 min/mo on Free | see below |

**On a public repo this is genuinely $0** — Actions minutes are unlimited and no
allowance is consumed. Keep the repo public unless you have a reason not to;
none of the secrets live in the code.

On a **private** repo, minutes are billed and GitHub rounds every job up to the
nearest minute. The season-aware schedule in `cfb-pipeline.yml` fires roughly
2,200 times per season (~150 runs/month averaged over a year), which is about
**2,200–3,300 minutes per season** — comfortably inside a 2,000 min/month
allowance, and about $13–20/season at the $0.006/min Linux overage rate if you
had no allowance left at all.

To cut it further, delete the hourly weekend `cron` line and keep only the
every-6-hours one (~600 runs/season).

## Notes

- Play money only. This is a private league scoreboard, not a sportsbook.
- Passwords use PBKDF2-SHA256 (240k rounds, per-user salt) from the standard
  library — no native wheels needed on Streamlit Cloud.
- Odds provider preference is configurable via `CFBD_PROVIDERS`; a book that
  quotes both a spread and a total is always preferred.
- Want to allow adding to an open position? Drop the
  `bets_one_open_per_market` index.
