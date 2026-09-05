-- Reverse of 0038 — recreates run_log, job_enrichment and job_embeddings.
--
-- READ THIS BEFORE RELYING ON IT: IT BRINGS BACK THE SCHEMA, NOT THE DATA.
-- The rows are gone; a forward migration's reverse restores the shape so the
-- runner's up -> down -> up cycle is clean and an operator can roll a deploy
-- back without a schema mismatch. If you need the ROWS, restore from the daily
-- backup (`.github/workflows/db-backup.yml`).
--
-- The column lists below are the union of each table's creating migration and
-- every additive migration that followed it, so a rolled-back schema matches
-- what the migration before this one left behind:
--   run_log        — 0000 baseline + 0010 (observability) + 0033
--                    (enrichment_stats) + matcher_stats, which only ever came
--                    from the init_db() column mirror in
--                    src/repositories/database.py, never from a migration file.
--   job_enrichment — 0008, unchanged since.
--   job_embeddings — 0009 + 0027 (the pgvector column, re-added under the same
--                    tolerant guard 0027 uses: no pgvector, no column, no
--                    boot failure).
--
-- No code reads any of this. Nothing in `backend/src` will start writing to
-- these tables again just because they exist.

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_found INTEGER DEFAULT 0,
    new_jobs INTEGER DEFAULT 0,
    sources_queried INTEGER DEFAULT 0,
    per_source TEXT DEFAULT '{}',
    run_uuid TEXT,
    per_source_errors TEXT DEFAULT '{}',
    per_source_duration TEXT DEFAULT '{}',
    total_duration REAL,
    user_id TEXT,
    matcher_stats TEXT DEFAULT '{}',
    enrichment_stats TEXT DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_run_log_run_uuid
    ON run_log(run_uuid) WHERE run_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS job_enrichment (
    job_id INTEGER PRIMARY KEY
        REFERENCES jobs(id) ON DELETE CASCADE,
    title_canonical TEXT NOT NULL,
    category TEXT NOT NULL,
    employment_type TEXT NOT NULL DEFAULT 'unknown',
    workplace_type TEXT NOT NULL DEFAULT 'unknown',
    locations TEXT NOT NULL DEFAULT '[]',
    salary TEXT NOT NULL DEFAULT '{}',
    required_skills TEXT NOT NULL DEFAULT '[]',
    preferred_skills TEXT NOT NULL DEFAULT '[]',
    experience_min_years INTEGER,
    experience_level TEXT NOT NULL DEFAULT 'unknown',
    requirements_summary TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    employer_type TEXT NOT NULL DEFAULT 'unknown',
    visa_sponsorship TEXT NOT NULL DEFAULT 'unknown',
    seniority TEXT NOT NULL DEFAULT 'unknown',
    remote_region TEXT,
    apply_instructions TEXT,
    red_flags TEXT NOT NULL DEFAULT '[]',
    enriched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_enrichment_job_id
    ON job_enrichment(job_id);

CREATE TABLE IF NOT EXISTS job_embeddings (
    job_id INTEGER PRIMARY KEY
        REFERENCES jobs(id) ON DELETE CASCADE,
    model_version TEXT NOT NULL,
    embedding_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_embeddings_model
    ON job_embeddings(model_version);

-- The vector column, restored exactly as 0027 added it: tolerant of a
-- Postgres without pgvector, because migrations run inside the FastAPI
-- lifespan and a hard CREATE EXTENSION would turn a missing extension into a
-- boot failure. Schema-qualified `public.vector` — the test suite isolates
-- each test in its own schema, so an unqualified type does not resolve.
DO $do$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable — job_embeddings.embedding skipped';
    END;

    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
        ALTER TABLE job_embeddings ADD COLUMN IF NOT EXISTS embedding public.vector(384);
        CREATE INDEX IF NOT EXISTS idx_job_embeddings_missing_vector
            ON job_embeddings(job_id) WHERE embedding IS NULL;
    END IF;
END
$do$;
