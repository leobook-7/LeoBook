# league_db_schema.py: SQLite DDL, migration definitions, and CSV import map.
# Part of LeoBook Data — Access Layer
# Imported by league_db.py only. All callers should use league_db.py.

# ── SQLite schema ─────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS leagues (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id           TEXT UNIQUE NOT NULL,
        fs_league_id        TEXT,
        country_code        TEXT,
        continent           TEXT,
        name                TEXT NOT NULL,
        crest               TEXT,
        current_season      TEXT,
        url                 TEXT,
        processed           INTEGER DEFAULT 0,
        region              TEXT,
        region_flag         TEXT,
        region_url          TEXT,
        other_names         TEXT,
        abbreviations       TEXT,
        search_terms        TEXT,
        date_updated        TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS teams (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id             TEXT UNIQUE,
        name                TEXT NOT NULL,
        league_ids          JSON,
        crest               TEXT,
        country_code        TEXT,
        url                 TEXT,
        hq_crest            INTEGER DEFAULT 0,
        country             TEXT,
        city                TEXT,
        stadium             TEXT,
        other_names         TEXT,
        abbreviations       TEXT,
        search_terms        TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS schedules (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id          TEXT UNIQUE,
        date                TEXT,
        time                TEXT,
        league_id           TEXT,
        home_team_id        TEXT,
        home_team_name      TEXT,
        away_team_id        TEXT,
        away_team_name      TEXT,
        home_score          INTEGER,
        away_score          INTEGER,
        extra               JSON,
        league_stage        TEXT,
        match_status        TEXT,
        season              TEXT,
        home_crest          TEXT,
        away_crest          TEXT,
        url                 TEXT,
        region_league       TEXT,
        match_link          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS predictions (
        fixture_id          TEXT PRIMARY KEY,
        date                TEXT,
        match_time          TEXT,
        region_league       TEXT,
        home_team           TEXT,
        away_team           TEXT,
        home_team_id        TEXT,
        away_team_id        TEXT,
        prediction          TEXT,
        confidence          TEXT,
        reason              TEXT,
        xg_home             REAL,
        xg_away             REAL,
        btts                TEXT,
        over_2_5            TEXT,
        best_score          TEXT,
        top_scores          TEXT,
        home_form_n         INTEGER,
        away_form_n         INTEGER,
        home_tags           TEXT,
        away_tags           TEXT,
        h2h_tags            TEXT,
        standings_tags      TEXT,
        h2h_count           INTEGER,
        actual_score        TEXT,
        outcome_correct     TEXT,
        status              TEXT DEFAULT 'pending',
        match_link          TEXT,
        odds                TEXT,
        market_reliability_score REAL,
        home_crest_url      TEXT,
        away_crest_url      TEXT,
        recommendation_score REAL,
        h2h_fixture_ids     JSON,
        form_fixture_ids    JSON,
        standings_snapshot  JSON,
        league_stage        TEXT,
        generated_at        TEXT,
        home_score          TEXT,
        away_score          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    -- standings: REMOVED in v7.0 — computed on-the-fly from schedules table.
    -- See computed_standings() function and Supabase computed_standings VIEW.

    CREATE TABLE IF NOT EXISTS audit_log (
        id                  TEXT PRIMARY KEY,
        timestamp           TEXT,
        event_type          TEXT,
        description         TEXT,
        balance_before      REAL,
        balance_after       REAL,
        stake               REAL,
        status              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fb_matches (
        site_match_id       TEXT PRIMARY KEY,
        date                TEXT,
        time                TEXT,
        home_team           TEXT,
        away_team           TEXT,
        league              TEXT,
        url                 TEXT,
        last_extracted      TEXT,
        fixture_id          TEXT,
        matched             TEXT,
        odds                TEXT,
        booking_status      TEXT,
        booking_details     TEXT,
        booking_code        TEXT,
        booking_url         TEXT,
        status              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS live_scores (
        fixture_id          TEXT PRIMARY KEY,
        home_team           TEXT,
        away_team           TEXT,
        home_score          TEXT,
        away_score          TEXT,
        minute              TEXT,
        status              TEXT,
        region_league       TEXT,
        match_link          TEXT,
        timestamp           TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS accuracy_reports (
        report_id           TEXT PRIMARY KEY,
        timestamp           TEXT,
        volume              INTEGER,
        win_rate            REAL,
        return_pct          REAL,
        period              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS countries (
        code                TEXT PRIMARY KEY,
        name                TEXT,
        continent           TEXT,
        capital             TEXT,
        flag_1x1            TEXT,
        flag_4x3            TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS profiles (
        id                  TEXT PRIMARY KEY,
        email               TEXT,
        username            TEXT,
        full_name           TEXT,
        avatar_url          TEXT,
        tier                TEXT,
        credits             REAL,
        created_at          TEXT,
        updated_at          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS custom_rules (
        id                  TEXT PRIMARY KEY,
        user_id             TEXT,
        name                TEXT,
        description         TEXT,
        is_active           INTEGER,
        logic               TEXT,
        priority            INTEGER,
        created_at          TEXT,
        updated_at          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rule_executions (
        id                  TEXT PRIMARY KEY,
        rule_id             TEXT,
        fixture_id          TEXT,
        user_id             TEXT,
        result              TEXT,
        executed_at         TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS match_odds (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id      TEXT    NOT NULL,
        site_match_id   TEXT    NOT NULL,
        market_id       TEXT    NOT NULL,
        base_market     TEXT    NOT NULL,
        category        TEXT    NOT NULL DEFAULT '',
        exact_outcome   TEXT    NOT NULL,
        line            TEXT    DEFAULT '',
        odds_value      REAL    NOT NULL,
        likelihood_pct  INTEGER NOT NULL DEFAULT 0,
        rank_in_list    INTEGER NOT NULL DEFAULT 0,
        extracted_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(fixture_id, market_id, exact_outcome, line)
    );

    CREATE TABLE IF NOT EXISTS paper_trades (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id        TEXT NOT NULL,
        trade_date        TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        home_team         TEXT NOT NULL,
        away_team         TEXT NOT NULL,
        league_id         INTEGER,
        match_date        TEXT,
        market_key        TEXT NOT NULL,
        market_name       TEXT NOT NULL,
        recommended_outcome TEXT NOT NULL,
        live_odds         REAL,
        synthetic_odds    REAL,
        model_prob        REAL NOT NULL,
        ev                REAL,
        gated             INTEGER NOT NULL,
        stairway_step     INTEGER,
        simulated_stake   REAL,
        simulated_payout  REAL,
        home_score        INTEGER,
        away_score        INTEGER,
        outcome_correct   INTEGER,
        simulated_pl      REAL,
        reviewed_at       TEXT,
        rule_pick         TEXT,
        rl_pick           TEXT,
        ensemble_pick     TEXT,
        rl_confidence     REAL,
        rule_confidence   REAL,
        UNIQUE(fixture_id, market_key)
    );

    CREATE TABLE IF NOT EXISTS log_segments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        path        TEXT NOT NULL UNIQUE,
        category    TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        closed_at   TEXT,
        size_bytes  INTEGER DEFAULT 0,
        uploaded    INTEGER DEFAULT 0,
        remote_path TEXT
    );

    -- Indexes for hot-path queries
    CREATE INDEX IF NOT EXISTS idx_schedules_league ON schedules(league_id);
    CREATE INDEX IF NOT EXISTS idx_schedules_date ON schedules(date);
    CREATE INDEX IF NOT EXISTS idx_schedules_fixture_id ON schedules(fixture_id);
    CREATE INDEX IF NOT EXISTS idx_leagues_league_id ON leagues(league_id);
    CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(date);
    CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
    CREATE INDEX IF NOT EXISTS idx_match_odds_fixture ON match_odds(fixture_id);
    CREATE INDEX IF NOT EXISTS idx_match_odds_market ON match_odds(market_id, extracted_at);
    CREATE INDEX IF NOT EXISTS idx_match_odds_site ON match_odds(site_match_id);
"""

# ── ALTER TABLE migrations (idempotent) ───────────────────────────────────────

_ALTER_MIGRATIONS = [
    ("leagues", "region", "TEXT"),
    ("leagues", "region_flag", "TEXT"),
    ("leagues", "region_url", "TEXT"),
    ("leagues", "other_names", "TEXT"),
    ("leagues", "abbreviations", "TEXT"),
    ("leagues", "search_terms", "TEXT"),
    ("leagues", "date_updated", "TEXT"),
    ("leagues", "fs_league_id", "TEXT"),
    ("teams", "team_id", "TEXT"),
    ("teams", "city", "TEXT"),
    ("teams", "stadium", "TEXT"),
    ("teams", "other_names", "TEXT"),
    ("teams", "abbreviations", "TEXT"),
    ("teams", "search_terms", "TEXT"),
    ("teams", "hq_crest", "INTEGER DEFAULT 0"),
    ("schedules", "region_league", "TEXT"),
    ("schedules", "match_link", "TEXT"),
]

# ── CSV → SQLite import map ───────────────────────────────────────────────────

_CSV_TABLE_MAP = {
    "schedules.csv": ("schedules", "fixture_id", {
        "match_time": "time",
        "match_link": "url",
        "home_team": "home_team_name",
        "away_team": "away_team_name",
    }),
    "teams.csv": ("teams", "team_id", {
        "team_name": "name",
        "team_crest": "crest",
        "team_url": "url",
    }),
    "region_league.csv": ("leagues", "league_id", {
        "league": "name",
        "league_crest": "crest",
        "league_url": "url",
    }),
    "predictions.csv": ("predictions", "fixture_id", {
        "over_2.5": "over_2_5",
    }),
    "audit_log.csv": ("audit_log", "id", {}),
    "fb_matches.csv": ("fb_matches", "site_match_id", {}),
    "live_scores.csv": ("live_scores", "fixture_id", {}),
    "accuracy_reports.csv": ("accuracy_reports", "report_id", {}),
    "countries.csv": ("countries", "code", {}),
    "profiles.csv": ("profiles", "id", {}),
    "custom_rules.csv": ("custom_rules", "id", {}),
    "rule_executions.csv": ("rule_executions", "id", {}),
}

# ── Computed standings SQL (v7.0) ─────────────────────────────────────────────

_COMPUTED_STANDINGS_SQL = """
    WITH match_results AS (
        SELECT
            s.league_id,
            s.home_team_id AS team_id,
            COALESCE(s.home_team_name, h.name) AS team_name,
            s.season,
            s.date,
            CASE WHEN s.home_score > s.away_score THEN 3 WHEN s.home_score = s.away_score THEN 1 ELSE 0 END AS points,
            CASE WHEN s.home_score > s.away_score THEN 1 ELSE 0 END AS wins,
            CASE WHEN s.home_score = s.away_score THEN 1 ELSE 0 END AS draws,
            CASE WHEN s.home_score < s.away_score THEN 1 ELSE 0 END AS losses,
            s.home_score AS goals_for,
            s.away_score AS goals_against
        FROM schedules s
        LEFT JOIN teams h ON s.home_team_id = h.team_id
        WHERE s.match_status = 'finished'
          AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
          AND (TYPEOF(s.home_score) != 'text' OR CAST(s.home_score AS INTEGER) = s.home_score)

        UNION ALL

        SELECT
            s.league_id,
            s.away_team_id AS team_id,
            COALESCE(s.away_team_name, a.name) AS team_name,
            s.season,
            s.date,
            CASE WHEN s.away_score > s.home_score THEN 3 WHEN s.away_score = s.home_score THEN 1 ELSE 0 END,
            CASE WHEN s.away_score > s.home_score THEN 1 ELSE 0 END,
            CASE WHEN s.away_score = s.home_score THEN 1 ELSE 0 END,
            CASE WHEN s.away_score < s.home_score THEN 1 ELSE 0 END,
            s.away_score,
            s.home_score
        FROM schedules s
        LEFT JOIN teams a ON s.away_team_id = a.team_id
        WHERE s.match_status = 'finished'
          AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
          AND (TYPEOF(s.home_score) != 'text' OR CAST(s.home_score AS INTEGER) = s.home_score)
    )
    SELECT
        league_id, team_id, team_name, season,
        COUNT(*) AS played,
        SUM(wins) AS wins,
        SUM(draws) AS draws,
        SUM(losses) AS losses,
        SUM(goals_for) AS goals_for,
        SUM(goals_against) AS goals_against,
        SUM(goals_for) - SUM(goals_against) AS goal_difference,
        SUM(points) AS points
    FROM match_results
    WHERE 1=1 {filters}
    GROUP BY league_id, team_id, team_name, season
    ORDER BY league_id, season, points DESC, goal_difference DESC, goals_for DESC
"""

__all__ = [
    "_SCHEMA_SQL",
    "_ALTER_MIGRATIONS",
    "_CSV_TABLE_MAP",
    "_COMPUTED_STANDINGS_SQL",
]
