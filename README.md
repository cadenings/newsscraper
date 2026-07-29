# Finance News Scraper

A terminal financial-news scraper. It pulls headlines from a curated set of
**reputable, macro-indicative** RSS/Atom feeds, keeps only items published in the
**last 24 hours**, de-duplicates syndicated copies, **ranks everything by
recency**, and stores it in a shared **Supabase (Postgres) database** — so the
data lives in the cloud, not just on one machine, and a GitHub Actions job can
keep it updated once a day even when this PC is off. No GUI — everything runs
from the terminal.

## How it works

```
RSS/Atom feeds ─► fetch (parallel) ─► drop opinion ─► keep last 24h ─► dedup (title hash)
                                                                            │
                                                                            ▼
                                        Supabase / Postgres (articles table, ranked by time)
```

- **Sources** live in [`sources.py`](sources.py) — a plain list of
  `(name, category, url)` tuples, curated toward the outlets and official bodies
  that actually move macro sentiment:
  - **Central banks:** Federal Reserve, ECB, Bank of England, Bank of Japan
  - **Official statistics:** BLS (jobs/CPI), BEA (GDP/PCE), EIA (energy)
  - **Top-tier press:** FT, The Economist, CNBC, MarketWatch, Yahoo Finance
  - **Reputable desks:** NYT (Business + Economy), Guardian, BBC, CNA
  - **Wire coverage via Google News** (`when:1d`), targeted at macro drivers —
    Fed & rates, inflation, jobs & growth, bonds & yields, trade & tariffs,
    oil & energy, Asia & China. Reuters and AP no longer publish public RSS, so
    their macro reporting is pulled through these searches.

  Add or remove feeds by editing that file. Central-bank and statistics feeds
  are low-frequency by design — they show up empty on quiet days and light up on
  a rate decision, a CPI print, or a GDP release, which is exactly when they
  carry the highest-signal macro news.
- **24h window**: only items with a parseable publish time inside the window are
  stored. Undated items are dropped (we can't prove they're recent).
- **Dedup**: `sha256` of the normalised headline (lower-cased, punctuation
  stripped, trailing `" - Publisher"` removed) — so the same wire story carried
  by six outlets is stored once. Re-running `scrape` never creates duplicates.
- **Ranking**: newest first, via an indexed `published_ts` (unix epoch) column.

## Setup

Requires Python 3.9+.

```bash
pip install -r requirements.txt
```

### 1. Create a free Supabase project

1. Go to [supabase.com](https://supabase.com), sign up/in, and create a new
   project (pick any name/region; the free tier is plenty for this — see
   [Storage & retention](#storage--retention) below).
2. Once it's created, open **SQL Editor** → **New query**, paste the entire
   contents of [`schema.sql`](schema.sql), and run it. This creates the
   `articles` table, its indexes, and a small helper function `stats` uses.
3. Open **Settings → API**. You need two values:
   - **Project URL**
   - **service_role** key (not the `anon`/`public` one — this is a trusted
     backend script, not a browser app, and the service_role key is what lets
     it write without extra permission policies. Keep it secret.)

### 2. Configure credentials locally

```bash
copy .env.example .env
```

Open `.env` and fill in the two values from step 1:

```
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your-service-role-key-here
```

`.env` is git-ignored — it never gets committed. Every command now reads/writes
Supabase instead of a local file, so you'll need internet access to run any of
them, including `top`.

```bash
python finance_news.py scrape
python finance_news.py top --limit 20
```

If credentials are missing or wrong, the tool fails fast with a clear message
telling you what to fix — no confusing stack trace.

### 3. Automate the daily run with GitHub Actions

This makes the data refresh once a day even when your PC is off.

1. Create a GitHub repo (public or private both work) and push this folder to
   it:
   ```bash
   git init
   git add .
   git commit -m "Finance news scraper"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
   (`.env` won't be included — it's git-ignored, exactly as intended.)
2. On GitHub, open the repo → **Settings → Secrets and variables → Actions →
   New repository secret**, and add two secrets:
   - `SUPABASE_URL` — same value as in your `.env`
   - `SUPABASE_KEY` — same value as in your `.env`
3. That's it — [`.github/workflows/daily-scrape.yml`](.github/workflows/daily-scrape.yml)
   is already in the repo. It runs `scrape` then `prune --hours 2160` (90 days)
   every day at 12:00 UTC, and you can also trigger it manually from the
   **Actions** tab (`workflow_dispatch`) to verify it works right away instead
   of waiting for the schedule.

From then on: the cloud keeps the data fresh automatically, and you run `top`
/ `report` locally (or from anywhere with the same `.env`) whenever you want
to read it.

### Making sure it keeps running

Two things guarantee the daily run stays reliable long-term, not just on day one:

- **GitHub auto-disables scheduled workflows after 60 days of repo
  inactivity.** Since `scrape`/`prune` only write to Supabase, the repo itself
  would otherwise go quiet and GitHub would silently turn the schedule off.
  The workflow's last step commits a small `STATUS.md` back to the repo every
  run, which keeps the repo active indefinitely — no action needed from you.
- **To verify it's actually running:** open `STATUS.md` in the repo on
  GitHub.com any time — it shows the timestamp and article counts from the
  most recent successful run, updated daily. For the full history, the repo's
  **Actions** tab lists every run (success/failure) with logs. GitHub can also
  email you automatically if a scheduled run fails — that's on by default for
  your own repos (check **Settings → Notifications → Actions** if you want to
  confirm or change it).

Note that GitHub's cron scheduler fires *within a window* around the set time
during high load, not to the exact second — normal, and irrelevant at a daily
cadence.

## Storage & retention

At the current source list (~500 articles/day, ~1.5 KB each), that's roughly
750 KB/day. The included workflow prunes anything older than 90 days
(`prune --hours 2160`), keeping the database around **~65–70 MB** — comfortably
inside Supabase's free-tier 500 MB storage limit indefinitely. Adjust the
`--hours` value in `daily-scrape.yml` (and run `prune` locally the same way) if
you want a longer or shorter history.

## Quick start on Windows

Double-click **`finance_news.bat`** for a simple menu (update news, open the
printable report, check sources, etc.). The same file also works as a shortcut
from a terminal — anything after it is passed straight to the tool:

```bat
finance_news.bat scrape
finance_news.bat report --open
finance_news.bat top --limit 40
```

## Usage

```bash
# Fetch every feed and store the last 24h of headlines
python finance_news.py scrape

# Show the 30 most recent stored articles (newest first)
python finance_news.py top

# Filter and drill in
python finance_news.py top --limit 50 --source CNBC
python finance_news.py top --keyword "fed" --links
python finance_news.py top --category economy

# Print the whole day's news — a clean, grouped digest you can print
python finance_news.py report --open            # HTML, opens in your browser -> Ctrl+P
python finance_news.py report --format txt       # plain-text version
python finance_news.py report --category central-bank   # just the policy releases

# What's in the database?
python finance_news.py stats

# List / test the configured feeds
python finance_news.py sources
python finance_news.py sources --check

# Export the whole database
python finance_news.py export --format json --out news.json
python finance_news.py export --format csv  --out news.csv

# Delete anything older than 24h (keeps the DB lean)
python finance_news.py prune
```

A typical loop: `scrape` (e.g. on a schedule or whenever you want fresh data),
then `top` to read, or `report` to print.

## Printing the day's news

`report` builds a complete, printable digest of everything from the last 24h
(not just the top N) — **full headlines** (no truncation), each with its source,
publisher, Eastern-time stamp, age, and a short summary, **grouped by category**
(Central Banks → Official Data → Economy → Markets → Wires → Global → Business).

```bash
python finance_news.py report --open
```

- `--format html` (default) writes `news_report_<date>.html`; `--open` launches
  it in your browser, where **Ctrl+P** prints it. The layout uses a clean
  print stylesheet (white page, serif type, page breaks that don't split a story).
- `--format txt` writes a plain-text version for e-mailing or piping.
- The same `--source`, `--category`, `--keyword`, and `--hours` filters as `top`
  apply, so you can print, say, only the day's central-bank/data releases.

### Keeping it fresh automatically

`scrape` is idempotent (re-running it never creates duplicates), so it's safe
to run on any schedule. The included [GitHub Actions workflow](#3-automate-the-daily-run-with-github-actions)
does this once a day in the cloud — no PC required. If you'd rather run it
locally on a timer instead (e.g. Windows Task Scheduler or cron), that works
too: `finance_news.bat scrape` or `python finance_news.py scrape --quiet`.

## The data is queryable anywhere

The `articles` table lives in your Supabase project — open it directly in the
Supabase **Table Editor**, query it with SQL in the **SQL Editor**, connect
`pandas` via any Postgres driver, or use the `export` command below for a
portable CSV/JSON snapshot. Schema (see [`schema.sql`](schema.sql) for the
authoritative definition):

| column         | meaning                                   |
|----------------|-------------------------------------------|
| `title`        | headline                                  |
| `summary`      | short description (HTML stripped)         |
| `link`         | article URL                               |
| `source`       | feed label from `sources.py`              |
| `publisher`    | underlying outlet (e.g. via Google News)  |
| `category`     | markets / economy / policy / earnings / … |
| `published_at` | ISO 8601 UTC                              |
| `published_ts` | unix epoch (used for recency ranking)     |
| `fetched_at`   | when the scraper saw it                   |
| `content_hash` | dedup key (unique)                        |

## Options

- `scrape --hours N` — change the recency window (default 24).
- `scrape --workers N` — parallel fetch workers (default 12).
- `scrape --quiet` — only print the final summary line.
- `scrape --keep-opinion` — include opinion/editorial pieces (see below).

## Opinion filtering

Op-eds, columns, and editorials are viewpoints rather than reported facts, so
they're **excluded by default** — detected by title label (`Opinion:`, `Op-Ed`,
`COLUMN-`, `... | Editorial`, etc.) or an `/opinion/` URL path. Each scrape
reports how many it dropped (`opinion filtered=N`). Pass `scrape --keep-opinion`
if you'd rather keep them.

## Notes

- Feeds that are temporarily unreachable are skipped with a warning; a broken
  feed never stops a run. Run `sources --check` to see what's currently live.
- A few notable feeds are intentionally disabled in `sources.py` (with reasons):
  WSJ's public feeds are frozen at Jan 2025, Nikkei Asia publishes no
  timestamps, and IMF/BIS/US-Treasury feeds bot-block or 404. Their coverage is
  largely picked up by the Google News wire searches.
- Items without a parseable publish time are dropped — the 24h guarantee only
  holds for items we can actually timestamp.
- Headlines only (titles + links + timestamps) are stored, which keeps this on
  the right side of most feeds' terms; check redistribution terms before
  republishing anything.
- `finance_news.db` from earlier versions of this tool is no longer read or
  written — the data now lives in Supabase. That file is left on disk
  untouched (nothing deletes it); it's just historical and safe to remove
  once you've confirmed Supabase is working.
