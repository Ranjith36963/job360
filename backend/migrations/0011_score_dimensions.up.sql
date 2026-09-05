-- 0011_score_dimensions: per-dimension score columns on jobs.
--
-- Step-1.5 mini-batch S1.1 (the dim-field bombshell fix).
-- JobResponse already exposes 9 per-dim score slots (role, skill,
-- seniority_score, experience, credentials, location_score, recency,
-- semantic, penalty) defaulted to 0 because Step 1 never persisted the
-- breakdown the (since-deleted) scorer returned. This migration
-- adds the missing columns; the writer side (insert_job + the pipeline)
-- and the read side (_JOBS_ENRICHMENT_JOIN_COLS + _row_to_job_response) land
-- in the same Step-1.5 batch.
--
-- All columns are additive INTEGER DEFAULT 0 so legacy rows stay valid
-- (zero is the Pillar-2-Batch-2.9 "this dimension was not scored" sentinel).
-- Per CLAUDE.md rule #1 the jobs UNIQUE constraint is untouched; per rule
-- #10 no per-user column is added — score breakdown is part of the shared
-- catalog.
--
-- Self-bootstrapping schema guard
-- -------------------------------
-- Some test fixtures (test_auth_routes::temp_db, test_channels_routes,
-- test_feed_service, test_tenancy_isolation) call `runner.up()` WITHOUT
-- first calling `JobDatabase.init_db()`, so the `jobs` table may not exist
-- at all. Production always creates the table via init_db() before
-- migrations run, but the migration must work from every starting state.
-- The CREATE TABLE IF NOT EXISTS declares the full post-0011 jobs shape;
-- the ALTER statements that follow remain the forward path for DBs that
-- already had the legacy jobs table. When both surfaces produce the same
-- column, the migration runner swallows the resulting "duplicate column
-- name" errors (see runner._apply_up_sql).

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    salary_min REAL,
    salary_max REAL,
    description TEXT DEFAULT '',
    apply_url TEXT NOT NULL,
    source TEXT NOT NULL,
    date_found TEXT NOT NULL,
    match_score INTEGER DEFAULT 0,
    visa_flag INTEGER DEFAULT 0,
    experience_level TEXT DEFAULT '',
    normalized_company TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    posted_at TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_updated_at TEXT,
    date_confidence TEXT DEFAULT 'low',
    date_posted_raw TEXT,
    consecutive_misses INTEGER DEFAULT 0,
    staleness_state TEXT DEFAULT 'active',
    role INTEGER DEFAULT 0,
    skill INTEGER DEFAULT 0,
    seniority_score INTEGER DEFAULT 0,
    experience INTEGER DEFAULT 0,
    credentials INTEGER DEFAULT 0,
    location_score INTEGER DEFAULT 0,
    recency INTEGER DEFAULT 0,
    semantic INTEGER DEFAULT 0,
    penalty INTEGER DEFAULT 0,
    UNIQUE(normalized_company, normalized_title)
);

ALTER TABLE jobs ADD COLUMN role INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN skill INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN seniority_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN experience INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN credentials INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN location_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN recency INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN semantic INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN penalty INTEGER DEFAULT 0;
