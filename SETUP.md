# Setup checklist — CFB League

Written for: **Windows**, **personal GitHub account**, **public repo**.
Total time: about 30 minutes. Everything below is free.

Work top to bottom. Each step says what "done" looks like, so you know whether
to move on.

---

## Step 0 — Accounts you'll need

Open these in tabs now and sign up if you don't have them. All free, no card.

| Account | URL |
|---|---|
| GitHub | <https://github.com/signup> |
| Supabase | <https://supabase.com/dashboard> (sign in with GitHub) |
| College Football Data | <https://collegefootballdata.com/key> |
| Streamlit Community Cloud | <https://share.streamlit.io> (sign in with GitHub) |

Signing into Supabase and Streamlit **with your GitHub account** saves you two
passwords and makes the later steps click-through.

---

## Step 1 — Move the project somewhere permanent

The folder is currently buried in the Claude session directory. Move it:

1. Open File Explorer.
2. Copy the whole **`cfb-league`** folder.
3. Paste it at **`C:\Users\timba\Documents\cfb-league`**.

Work from the Documents copy from here on.

### Then delete two leftover folders

I tried to initialize git for you earlier and the workspace blocked me from
cleaning up afterward, so there's a broken `.git` folder in there. **It will
break your first commit if you leave it.**

Open PowerShell (Start → type "PowerShell" → Enter) and run:

```powershell
cd C:\Users\timba\Documents\cfb-league
Remove-Item -Recurse -Force .git -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
dir
```

**Done when:** `dir` lists `app.py`, `schema.sql`, `README.md` and friends, and
no `.git` folder. (`.github` and `.streamlit` should still be there — those are
different folders and you need them. Run `dir -Force` to see them.)

---

## Step 2 — Get your College Football Data API key

1. Go to <https://collegefootballdata.com/key>.
2. Enter your email, submit.
3. Check your inbox — the key arrives in a minute or two. It's a long random string.
4. Paste it into a scratch note. You'll need it twice.

**Done when:** you have the key text saved somewhere you can copy from.

---

## Step 3 — Create the Supabase database

1. <https://supabase.com/dashboard> → **New project**.
2. Fill in:
   - **Name:** `cfb-league`
   - **Database Password:** click Generate, then **⚠️ save it somewhere safe**.
     If you type your own, **use only letters and numbers** — symbols like
     `@ : / ? #` break the connection string later.
   - **Region:** whichever is closest to you (e.g. East US).
3. **Create new project.** It takes ~2 minutes to provision.

### Load the schema

4. Left sidebar → **SQL Editor** → **New query**.
5. Open `schema.sql` from your project folder in Notepad, select all, copy.
6. Paste into the SQL editor → click **Run** (or Ctrl+Enter).

**Done when:** you see "Success. No rows returned." Then go to **Table Editor**
in the sidebar — you should see three tables: `users`, `games`, `bets`.

### Grab the connection string

7. Sidebar → **Project Settings** (gear) → **Database**.
8. Scroll to **Connection string** → select the **Session pooler** tab.
9. Copy the URI. It looks like:

   ```
   postgresql://postgres.abcdefghijk:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```

10. Replace `[YOUR-PASSWORD]` (including the square brackets) with the database
    password from step 3.2, and add `?sslmode=require` on the end:

   ```
   postgresql://postgres.abcdefghijk:YourPassword123@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require
   ```

11. Save that finished string in your scratch note. This is your **`DATABASE_URL`**.

**Done when:** you have a `DATABASE_URL` with no square brackets left in it.

---

## Step 4 — Put the code on GitHub

### 4a. Create the empty repo

1. <https://github.com/new>
2. **Repository name:** `cfb-league`
3. **Public** ← leave this selected. This is what makes Actions free.
4. Do **not** tick "Add a README" or add a .gitignore — you already have both.
5. **Create repository.**
6. Leave the page open. You need the URL from it.

### 4b. Push the files

Check whether git is installed — in PowerShell:

```powershell
git --version
```

**If you get a version number,** run these, replacing `YOURNAME` with your
GitHub username:

```powershell
cd C:\Users\timba\Documents\cfb-league
git init -b main
git add .
git commit -m "CFB betting league"
git remote add origin https://github.com/YOURNAME/cfb-league.git
git push -u origin main
```

A browser window will pop up to sign you into GitHub. Approve it.

**If git is not installed,** install GitHub Desktop instead —
<https://desktop.github.com> — then: **File → Add local repository** → browse to
`C:\Users\timba\Documents\cfb-league` → it offers to create a repository → do
that → **Publish repository** → untick "Keep this code private" → Publish.

**Done when:** refreshing your GitHub repo page shows `app.py`, `schema.sql`,
and a `.github` folder. **Check that `.github/workflows/cfb-pipeline.yml` is
there** — if that folder is missing, the automation won't run.

---

## Step 5 — Add the secrets to GitHub and run the pipeline

1. On your repo → **Settings** tab → left sidebar **Secrets and variables** →
   **Actions**.
2. **New repository secret**, twice:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the connection string from step 3.11 |
   | `CFBD_API_KEY` | the key from step 2 |

   Names must match exactly — all caps, underscores.

3. Go to the **Actions** tab. If it asks you to enable workflows, click
   **I understand my workflows, go ahead and enable them.**
4. Left sidebar → **CFB pipeline (sync odds + settle bets)** → **Run workflow**
   → **Run workflow**.
5. Wait ~1 minute, refresh, click into the run.

**Done when:** all steps have green checkmarks. Click "Sync games, lines and
rankings" to expand the log — you want to see something like
`Upserted 63 games for 2026 regular week 1.`

**If it fails,** open the failed step and read the last few lines:

| Log says | Fix |
|---|---|
| `CFBD returned 401` | `CFBD_API_KEY` secret is wrong — re-copy it, no spaces |
| `password authentication failed` | Wrong DB password in `DATABASE_URL` |
| `could not translate host name` | You copied the wrong Supabase tab — use **Session pooler** |
| `DATABASE_URL is not set` | Secret name is misspelled |

> **Heads up for early August:** sportsbooks don't post lines for most games
> until a week or two before kickoff. It's normal for the sync to report games
> with few or no odds right now. Re-run it closer to the season and the board
> fills in.

---

## Step 6 — Deploy the app

1. <https://share.streamlit.io> → sign in with GitHub → **Create app**.
2. Choose **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `YOURNAME/cfb-league`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** pick something like `your-cfb-league` — this is the link you
     text everyone, so make it memorable.
4. Click **Advanced settings** → **Secrets** box → paste this, substituting your
   own connection string:

   ```toml
   DATABASE_URL = "postgresql://postgres.abcdefghijk:YourPassword123@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
   LEAGUE_INVITE_CODE = "pick-any-phrase"
   ```

   Keep the quotes. `LEAGUE_INVITE_CODE` is what stops strangers signing up —
   choose anything and share it only with your ten people.

5. **Deploy.** First build takes 2–4 minutes while it installs dependencies.

**Done when:** the app loads and you see the 🏈 The League sign-in screen.

---

## Step 7 — Create accounts and invite the league

1. On your app → **Create account** tab.
2. Username, password (6+ characters), and your `LEAGUE_INVITE_CODE`.
3. **Create account** → then sign in on the first tab.
4. You should see **Cash $10,000.00**.
5. Text everyone the app URL + the invite code.

Tell them to add it to their phone home screen — iPhone: Share → Add to Home
Screen. It then opens full-screen like a real app.

---

## Step 8 — Confirm the whole loop works

- **Place Bets tab** shows only the upcoming week, and games disappear from it
  once they kick off.
- Place a small test bet — your Cash drops immediately and the ticket shows in
  **My Tickets**.
- After that game finishes, the hourly Action grades it within the hour and your
  balance updates. **Leaderboard** re-ranks everyone by net worth.

If you want to force it rather than wait: GitHub → Actions → CFB pipeline →
**Run workflow**.

---

## Ongoing

You don't have to do anything. The Action runs every 6 hours during the season
and hourly on Fri/Sat/Sun, and it goes quiet February through July.

Two things worth knowing:

- **Supabase pauses free projects after ~1 week of no activity.** During the
  season the pipeline keeps it awake. In the offseason it will pause — just hit
  **Restore** in the Supabase dashboard when the season starts. No data is lost.
- **Streamlit sleeps inactive apps.** The first person to open it after a quiet
  spell waits ~30 seconds for it to wake. Normal, not broken.

---

## When something goes wrong

| Symptom | Likely cause |
|---|---|
| App says "No upcoming games are loaded yet" | Pipeline hasn't run, or no lines posted yet. Run the workflow manually. |
| App shows "Database unavailable" | Supabase project is paused — Restore it in the dashboard. |
| "The spread moved before your bet landed" | Working as intended — the line changed. Refresh and re-bet. |
| "You already have an open ticket on that market" | One open bet per market per game. Intentional. |
| Bets not settling | Check the Actions tab for a failed run; expand "Settle completed bets". |
| Someone can't sign up | They have the invite code wrong, or the username is taken. |

**If you get stuck:** paste me the error text — from the GitHub Actions log, or
from the red box in the app — and tell me which step number you were on. That's
usually enough for me to pinpoint it.
