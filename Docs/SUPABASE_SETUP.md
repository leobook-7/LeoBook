# Supabase Setup Guide

> **Version**: 9.3 · **Last Updated**: 2026-03-18
> **One-stop reference** — everything needed to provision a fresh Supabase database for LeoBook from scratch.

---

## Overview

LeoBook uses Supabase (PostgreSQL) as its cloud data layer. The local SQLite database (`leobook.db`) is the source of truth for all prediction and enrichment work. Supabase is the sync target — it powers the Flutter app's real-time data, enables recovery bootstrapping, and provides the cloud backup of all predictions, schedules, odds, and audit logs.

**Sync direction**: Local SQLite → Supabase (push-only on startup, watermark-based delta on pipeline milestones).

---

## Part 1 — Create a Supabase Project

1. Go to **[https://supabase.com](https://supabase.com)** and sign in (GitHub recommended).
2. Click **New Project**.
3. Fill in:
   - **Name**: `leobook-production` (or any name you prefer)
   - **Database Password**: generate a strong password and save it
   - **Region**: choose the closest to your location (e.g. `West EU (Frankfurt)` for Nigeria)
   - **Pricing Plan**: Free tier is sufficient to start
4. Wait ~2 minutes for provisioning to complete.

---

## Part 2 — Get API Credentials

1. Go to **Project Settings** → **API**.
2. Copy the following and add them to your `.env` file:

```env
SUPABASE_URL=https://xxxxxxxxxxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

> **Security rules:**
> - The **Service Role Key** has full admin access. Use it ONLY in the Python backend (`.env`). Never commit it to git or expose it in the Flutter app.
> - The **Anon Key** is for the Flutter frontend (read-only public access).
> - Never commit `.env` to the repo. It is in `.gitignore`.

---

## Part 3 — Bootstrap the Database (MANDATORY, one-time)

This is the most important step. A fresh Supabase project has no tables and no `exec_sql` function. Without running this script first, `python Leo.py --sync` will silently fail with `404 Not Found` on every table — and appear to succeed.

### How to run

1. Go to your Supabase dashboard → **SQL Editor** (left sidebar).
2. Click **New Query**.
3. Paste the entire SQL block below.
4. Click **Run** (or press `Cmd/Ctrl + Enter`).

The script is safe to re-run at any time — all statements use `CREATE TABLE IF NOT EXISTS` and `CREATE OR REPLACE`.

---

```sql
-- =============================================================================
-- LEOBOOK SUPABASE BOOTSTRAP v9.2
-- Run this ONCE on a fresh Supabase project via SQL Editor.
-- Safe to re-run — all statements are idempotent.
--
-- AUTHORITATIVE SOURCE: Data/Access/sync_schema.py (TABLE_CONFIG, _COL_REMAP)
-- This file must stay in sync with sync_schema.py.
-- Column names, types, and PRIMARY KEY definitions here must exactly match
-- the DDL strings in that dict — they control what _ALLOWED_COLS accepts.
-- v9.3 additions: fb_matches simplified, computed_standings uses real season,
--   STEP 9 matching engine REMOVED (now inline Python).
-- =============================================================================

-- =============================================================================
-- STEP 1: exec_sql RPC function
-- Required by sync_manager._ensure_remote_table() for auto-provisioning.
-- Without this, the auto-create fallback silently fails (PGRST202).
-- =============================================================================

CREATE OR REPLACE FUNCTION public.exec_sql(query TEXT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  EXECUTE query;
END;
$$;

GRANT EXECUTE ON FUNCTION public.exec_sql(TEXT) TO service_role;

-- =============================================================================
-- STEP 2: Extensions
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- STEP 3: Core data tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.leagues (
    league_id TEXT PRIMARY KEY,
    fs_league_id TEXT,
    country_code TEXT,
    continent TEXT,
    name TEXT NOT NULL,
    crest TEXT,
    current_season TEXT,
    url TEXT,
    region_flag TEXT,
    other_names TEXT,
    abbreviations TEXT,
    search_terms TEXT,
    level TEXT,
    season_format TEXT,
    date_updated TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.teams (
    team_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    league_ids      JSONB,         -- stored as JSON array e.g. ["1_1_8bP2bXmH"]
    crest           TEXT,
    country_code    TEXT,
    url             TEXT,
    city            TEXT,
    stadium         TEXT,
    other_names     TEXT,
    abbreviations   TEXT,
    search_terms    TEXT,
    last_updated    TIMESTAMPTZ DEFAULT now()
);

-- NOTE: home_team / away_team are the display names.
-- They are mapped from home_team_name / away_team_name in SQLite via _COL_REMAP
-- in sync_manager.py. Do NOT rename these columns in Supabase.
-- home_score / away_score are INTEGER — cast errors will silently NULL the field.
-- extra JSONB stores match tags: AET, PEN, Postp, Canc, Abn.
CREATE TABLE IF NOT EXISTS public.schedules (
    fixture_id      TEXT PRIMARY KEY,
    date            TEXT,
    match_time      TEXT,
    league_id       TEXT,
    home_team_id    TEXT,
    home_team       TEXT,
    away_team_id    TEXT,
    away_team       TEXT,
    home_score      INTEGER,
    away_score      INTEGER,
    extra           JSONB,
    league_stage    TEXT,
    match_status    TEXT,
    season          TEXT,
    home_crest      TEXT,
    away_crest      TEXT,
    match_link      TEXT,
    country_league   TEXT,
    last_updated    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.predictions (
    fixture_id               TEXT PRIMARY KEY,
    date                     TEXT,
    match_time               TEXT,
    country_league            TEXT,
    home_team                TEXT,
    away_team                TEXT,
    home_team_id             TEXT,
    away_team_id             TEXT,
    prediction               TEXT,
    confidence               TEXT,
    reason                   TEXT,
    xg_home                  REAL,
    xg_away                  REAL,
    btts                     TEXT,
    over_2_5                 TEXT,
    best_score               TEXT,
    top_scores               TEXT,
    home_form_n              INTEGER,
    away_form_n              INTEGER,
    home_tags                TEXT,
    away_tags                TEXT,
    h2h_tags                 TEXT,
    standings_tags           TEXT,
    h2h_count                INTEGER,
    actual_score             TEXT,
    outcome_correct          TEXT,
    status                   TEXT DEFAULT 'pending',
    match_link               TEXT,
    odds                     TEXT,
    market_reliability_score REAL,
    home_crest_url           TEXT,
    away_crest_url           TEXT,
    recommendation_score     REAL,
    h2h_fixture_ids          JSONB,
    form_fixture_ids         JSONB,
    standings_snapshot       JSONB,
    league_stage             TEXT,
    generated_at             TEXT,
    home_score               TEXT,
    away_score               TEXT,
    last_updated             TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.fb_matches (
    site_match_id TEXT PRIMARY KEY,
    league_id TEXT,
    date TEXT,
    match_time TEXT,
    home_team TEXT,
    away_team TEXT,
    url TEXT,
    fixture_id TEXT,
    matched TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.match_odds (
    fixture_id TEXT NOT NULL,
    site_match_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    base_market TEXT NOT NULL,
    category TEXT,
    exact_outcome TEXT NOT NULL,
    line TEXT,
    odds_value REAL,
    likelihood_pct INTEGER,
    rank_in_list INTEGER,
    extracted_at TEXT,
    last_updated TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (fixture_id, market_id, exact_outcome, line)
);

CREATE TABLE IF NOT EXISTS public.live_scores (
    fixture_id TEXT PRIMARY KEY,
    home_team TEXT,
    away_team TEXT,
    home_score TEXT,
    away_score TEXT,
    minute TEXT,
    status TEXT,
    country_league TEXT,
    match_link TEXT,
    timestamp TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.countries (
    code TEXT PRIMARY KEY,
    name TEXT,
    continent TEXT,
    capital TEXT,
    flag_1x1 TEXT,
    flag_4x3 TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

-- =============================================================================
-- STEP 4: Reporting and audit tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT,
    event_type TEXT,
    description TEXT,
    balance_before REAL,
    balance_after REAL,
    stake REAL,
    status TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.accuracy_reports (
    report_id TEXT PRIMARY KEY,
    timestamp TEXT,
    volume INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    return_pct REAL DEFAULT 0,
    period TEXT DEFAULT 'last_24h',
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.paper_trades (
    id                  SERIAL PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    league_id INTEGER,
    match_date TEXT,
    market_key TEXT NOT NULL,
    market_name TEXT NOT NULL,
    recommended_outcome TEXT NOT NULL,
    live_odds REAL,
    synthetic_odds REAL,
    model_prob REAL NOT NULL,
    ev REAL,
    gated INTEGER NOT NULL,
    stairway_step INTEGER,
    simulated_stake REAL,
    simulated_payout REAL,
    home_score INTEGER,
    away_score INTEGER,
    outcome_correct INTEGER,
    simulated_pl REAL,
    reviewed_at TEXT,
    rule_pick TEXT,
    rl_pick TEXT,
    ensemble_pick TEXT,
    rl_confidence REAL,
    rule_confidence REAL,
    last_updated TIMESTAMPTZ DEFAULT now(),
    UNIQUE (fixture_id, market_key)
);

-- =============================================================================
-- STEP 5: User and rule engine tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.profiles (
    id TEXT PRIMARY KEY,
    email TEXT,
    username TEXT,
    full_name TEXT,
    avatar_url TEXT,
    tier TEXT DEFAULT 'free',
    credits REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.custom_rules (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    name TEXT,
    description TEXT,
    is_active INTEGER,
    logic TEXT,
    priority INTEGER,
    created_at TEXT,
    updated_at TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.rule_executions (
    id TEXT PRIMARY KEY,
    rule_id TEXT,
    fixture_id TEXT,
    user_id TEXT,
    result TEXT,
    executed_at TEXT,
    last_updated TIMESTAMPTZ DEFAULT now()
);

-- =============================================================================
-- STEP 6: Computed Standings VIEW
-- Standings are never stored as a table — they are always computed on-the-fly
-- from the schedules table. Zero storage, always accurate.
-- =============================================================================

CREATE OR REPLACE VIEW public.computed_standings AS
WITH all_matches AS (
    SELECT
        league_id, season,
        home_team_id AS team_id, home_team AS team_name,
        home_score AS gf, away_score AS ga
    FROM public.schedules
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
      AND match_status = 'finished'

    UNION ALL

    SELECT
        league_id, season,
        away_team_id AS team_id, away_team AS team_name,
        away_score AS gf, home_score AS ga
    FROM public.schedules
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
      AND match_status = 'finished'
),
standings AS (
    SELECT
        league_id,
        season,
        team_id,
        MAX(team_name) AS team_name,
        COUNT(*) AS played,
        SUM(CASE WHEN gf > ga THEN 1 ELSE 0 END) AS won,
        SUM(CASE WHEN gf = ga THEN 1 ELSE 0 END) AS drawn,
        SUM(CASE WHEN gf < ga THEN 1 ELSE 0 END) AS lost,
        SUM(gf) AS goals_for,
        SUM(ga) AS goals_against,
        SUM(gf) - SUM(ga) AS goal_difference,
        SUM(CASE WHEN gf > ga THEN 3 WHEN gf = ga THEN 1 ELSE 0 END) AS points
    FROM all_matches
    GROUP BY league_id, season, team_id
)
SELECT
    *,
    ROW_NUMBER() OVER (
        PARTITION BY league_id, season
        ORDER BY points DESC, goal_difference DESC, goals_for DESC, team_name
    )::INTEGER AS position
FROM standings;

GRANT SELECT ON public.computed_standings TO anon, authenticated, service_role;

-- =============================================================================
-- STEP 7: Row Level Security — service_role full access, anon read-only
-- =============================================================================

DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'leagues', 'teams', 'schedules', 'predictions', 'fb_matches',
        'match_odds', 'live_scores', 'countries', 'audit_log',
        'accuracy_reports', 'paper_trades', 'profiles',
        'custom_rules', 'rule_executions'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('
            DROP POLICY IF EXISTS "service_role_all_%1$s" ON public.%1$I;
            CREATE POLICY "service_role_all_%1$s" ON public.%1$I
            FOR ALL TO service_role USING (true) WITH CHECK (true);
        ', t);
        EXECUTE format('
            DROP POLICY IF EXISTS "anon_read_%1$s" ON public.%1$I;
            CREATE POLICY "anon_read_%1$s" ON public.%1$I
            FOR SELECT TO anon, authenticated USING (true);
        ', t);
    END LOOP;
END $$;

-- =============================================================================
-- STEP 8: Auto-update last_updated trigger on every table
-- =============================================================================

CREATE OR REPLACE FUNCTION update_last_updated_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'leagues', 'teams', 'schedules', 'predictions', 'fb_matches',
        'match_odds', 'live_scores', 'countries', 'audit_log',
        'accuracy_reports', 'paper_trades', 'profiles',
        'custom_rules', 'rule_executions'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS trg_last_updated_%1$s ON public.%1$I;
            CREATE TRIGGER trg_last_updated_%1$s
            BEFORE UPDATE ON public.%1$I
            FOR EACH ROW EXECUTE FUNCTION update_last_updated_column();
        ', t);
    END LOOP;
END $$;

-- =============================================================================
-- STEP 9: Utilities (v9.3)
-- normalize_team_name() kept for search/display use.
-- fb_match_candidates VIEW, match_fb_to_schedule() RPC, auto_match_fb_matches()
-- RPC, and trg_auto_match_fb trigger have been REMOVED.
-- Match resolution is now inline Python: Modules/FootballCom/match_resolver.py v2.0
-- Logic: league_id + date + normalized home + away (exact match against schedules).
-- =============================================================================

DROP FUNCTION IF EXISTS public.normalize_team_name(TEXT);
CREATE OR REPLACE FUNCTION public.normalize_team_name(raw TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE STRICT
AS $$
  SELECT TRIM(
           REGEXP_REPLACE(
             REGEXP_REPLACE(
               LOWER(COALESCE(raw, '')),
               '\m(fc|cf|sc|ac|bk|sk|fk|if|afc|bfc|sfc|united|city|town|rovers|wanderers|athletic|albion|county)\M',
               '', 'gi'
             ),
             '[^a-z0-9]+', ' ', 'g'
           )
         )
$$;

GRANT EXECUTE ON FUNCTION public.normalize_team_name(TEXT) TO service_role, anon, authenticated;

CREATE INDEX IF NOT EXISTS idx_schedules_league_date ON public.schedules (league_id, date);
CREATE INDEX IF NOT EXISTS idx_schedules_date ON public.schedules (date);

-- =============================================================================
-- Bootstrap complete (v9.3).
-- Run: python Leo.py --sync
-- Match resolution: Python-side (match_resolver.py v2.0 — exact matching)
-- =============================================================================
```

---

## Part 3B — v9.1 Schema ALTERs (upgrade from v8.x)

If you have an existing database from before v9.1, run these ALTERs in the SQL Editor. On a fresh install (Part 3 above), these are already included and can be skipped.

```sql
-- Required ALTERs for v9.1 (run in Supabase SQL Editor if upgrading)

-- teams.league_ids must be JSONB (may be TEXT in older installs)
ALTER TABLE public.teams
  ALTER COLUMN league_ids TYPE JSONB USING league_ids::JSONB;

-- schedules: add extra column if missing (stores match tags: AET, PEN, etc.)
ALTER TABLE public.schedules
  ADD COLUMN IF NOT EXISTS extra JSONB;

-- Ensure integer scores (not TEXT)
ALTER TABLE public.schedules
  ALTER COLUMN home_score TYPE INTEGER USING home_score::INTEGER;
ALTER TABLE public.schedules
  ALTER COLUMN away_score TYPE INTEGER USING away_score::INTEGER;

-- Recreate computed_standings VIEW with correct types + no team_name duplicate bug
CREATE OR REPLACE VIEW public.computed_standings AS
WITH all_matches AS (
    SELECT league_id, season,
           home_team_id AS team_id, home_team AS team_name,
           home_score AS gf, away_score AS ga
    FROM public.schedules
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
      AND match_status = 'finished'
    UNION ALL
    SELECT league_id, season,
           away_team_id AS team_id, away_team AS team_name,
           away_score AS gf, home_score AS ga
    FROM public.schedules
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
      AND match_status = 'finished'
),
standings AS (
    SELECT league_id, season, team_id,
        MAX(team_name) AS team_name,
        COUNT(*)                                                       AS played,
        SUM(CASE WHEN gf > ga THEN 1 ELSE 0 END)                      AS won,
        SUM(CASE WHEN gf = ga THEN 1 ELSE 0 END)                      AS drawn,
        SUM(CASE WHEN gf < ga THEN 1 ELSE 0 END)                      AS lost,
        SUM(gf)                                                        AS goals_for,
        SUM(ga)                                                        AS goals_against,
        SUM(gf) - SUM(ga)                                             AS goal_difference,
        SUM(CASE WHEN gf > ga THEN 3 WHEN gf = ga THEN 1 ELSE 0 END) AS points
    FROM all_matches
    GROUP BY league_id, season, team_id
)
SELECT *,
    ROW_NUMBER() OVER (
        PARTITION BY league_id, season
        ORDER BY points DESC, goal_difference DESC, goals_for DESC, team_name
    )::INTEGER AS position
FROM standings;

GRANT SELECT ON public.computed_standings TO anon, authenticated, service_role;
```

---

## Part 4 — Verify the Bootstrap

After running the SQL above, verify in **Table Editor** (left sidebar) that the following tables are visible:

| Table | Purpose |
|---|---|
| `leagues` | League metadata — `country_code`, `region_flag`, `crest`, `current_season` |
| `teams` | Team metadata — `league_ids` (JSONB), `crest`, `country_code` |
| `schedules` | All fixtures — backbone of the system. `home_team`/`away_team` mapped from `home_team_name`/`away_team_name` via `_COL_REMAP` |
| `predictions` | Rule Engine + RL ensemble predictions |
| `fb_matches` | Football.com match objects (odds harvesting) |
| `match_odds` | Per-market odds extracted from Football.com |
| `live_scores` | Live score updates from the streamer |
| `countries` | Country codes and flag URLs |
| `audit_log` | Every system event (bets, syncs, errors) |
| `accuracy_reports` | Periodic prediction accuracy snapshots |
| `paper_trades` | Simulated bet log (RL training signal) |
| `profiles` | App user accounts |
| `custom_rules` | User-defined rule engine entries |
| `rule_executions` | Log of each custom rule execution |

> **Note**: `region_league` CSV is deprecated. All league data lives in the `leagues` table. `teams.league_ids` is JSONB in Supabase.

Also verify in **Database** → **Functions** that `exec_sql` appears in the `public` schema.

---

## Part 5 — First Sync

With the schema in place, run the first sync from your codespace:

```bash
python Leo.py --sync
```

**Expected output (healthy):**
```
[schedules]  Pushing X,XXX rows to Supabase...
[leagues]    Pushing X,XXX rows to Supabase...
[teams]      Pushing X,XXX rows to Supabase...
[SUCCESS] Sync complete.
```

**Failure symptom to watch for:**
```
[AUTO] Table 'X' not found — creating...
[!] exec_sql RPC failed ...
✓ Both local and remote empty     ← this is WRONG if local has data
```
If you see this, the bootstrap SQL was not run or did not complete. Return to Part 3.

---

## Part 6 — Sync Architecture Reference

### How sync works

```
Leo.py startup
  └── SyncManager.sync_on_startup()
        └── For each table in TABLE_CONFIG:
              ├── Local empty?  → bootstrap (pull from Supabase)
              ├── First sync?   → push all rows
              └── Subsequent?  → push only rows newer than watermark
                                 (watermark stored in _sync_watermarks table)
```

### Sync triggers

| Trigger | Command | Tables |
|---|---|---|
| Startup | `python Leo.py` (automatic) | All 14 tables |
| Manual full push | `python Leo.py --sync` | All 14 tables |
| Post-prediction | Chapter 1 P3 (automatic) | predictions, schedules |
| Live scores | Live streamer (automatic, 60s) | live_scores |
| Recovery pull | `python Leo.py --pull` | All 14 tables |

### Batch sizes (tuned for Supabase's 8s statement timeout)

| Table | Batch size | Reason |
|---|---|---|
| `schedules` | 500 rows | Large JSONB + complex conflict resolution |
| `match_odds` | 1,000 rows | Composite primary key |
| All others | 2,000 rows | Standard |

### Computed Standings
`standings` is **not** a synced table — it is a PostgreSQL `VIEW` computed on-the-fly from `schedules`. The Flutter app and Python backend query `computed_standings` directly. This means standings are always accurate without any sync overhead. 
> [!NOTE]
> `computed_standings` VIEW is now actively used by Flutter (v9.3) via a reactive `watchSchedules` trigger mechanism.

### Storage Buckets

LeoBook uses 3 Supabase Storage buckets for binary assets. These are NOT managed by `--sync` — they are managed by `--assets`.

| Bucket | Content | Files | Managed by |
|---|---|---|---|
| `flags` | SVG flag icons | 171 SVGs (all countries) | `sync_region_flags()` |
| `team-crests` | Team logo PNGs | Uploaded during enrichment | `fs_league_enricher` + `sync_team_assets()` |
| `league-crests` | League logo PNGs | Uploaded during enrichment | `fs_league_enricher` + `sync_league_assets()` |

All 3 buckets are created with `public: True`. To sync all buckets:
```bash
python Leo.py --assets          # sync all three buckets
python -m Modules.Assets.asset_manager --flags  # flags only
```

Current state (2026-03-15): 1,234 leagues have `region_flag` URLs in SQLite + Supabase. 171 distinct flag SVGs in the `flags` bucket.

---

## Part 7 — Troubleshooting

### "exec_sql RPC failed (PGRST202)"
The `exec_sql` function was not created. Re-run Part 3 (Step 1 of the SQL).

### "Could not find the table 'public.X' (PGRST205)"
A table is missing. Re-run Part 3 in full.

### "Both local and remote empty" but local SQLite has data
The upsert silently failed because the table didn't exist at upsert time. Re-run Part 3, then `python Leo.py --sync`.

### Sync succeeds but Flutter app shows no data
Check Row Level Security. The `anon_read_*` policies (Part 3 Step 7) must exist. Verify in Supabase dashboard → **Authentication** → **Policies**.

### `python Leo.py --pull` — recovery from Supabase
If local SQLite is lost or corrupted, this pulls all tables from Supabase back to local:
```bash
python Leo.py --pull
```

---

## Part 8 — Environment Variables Reference

| Variable | Where | Purpose |
|---|---|---|
| `SUPABASE_URL` | `.env` | Project URL from Settings → API |
| `SUPABASE_SERVICE_KEY` | `.env` | Service Role Key (full access, backend only) |

The Flutter app uses the **Anon Key** configured separately in `leobookapp/lib/config/`.

---

*Last updated: 2026-03-24 (v9.2 — region_league→country_league rename, prediction save in --train-rl)*
*Previous: v9.1 — v9.1 ALTERs section, Storage Buckets section, sync_schema.py canonical source reference, tables list updated (2026-03-15)*
*LeoBook Engineering Team — Materialless LLC*

---

## v9.2 Migration: Rename `region_league` → `country_league`

> **Run once** in the Supabase SQL Editor for existing deployments.
> New deployments using the updated CREATE TABLE statements above do NOT need this.

```sql
-- v9.2: Rename region_league → country_league
-- sync_schema.py sends country_league; column must match.
ALTER TABLE public.schedules RENAME COLUMN region_league TO country_league;
ALTER TABLE public.predictions RENAME COLUMN region_league TO country_league;
ALTER TABLE public.live_scores RENAME COLUMN region_league TO country_league;
ALTER TABLE public.standings RENAME COLUMN region_league TO country_league;
```