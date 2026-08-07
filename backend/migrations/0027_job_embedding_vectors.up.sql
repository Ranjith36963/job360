-- 0027_job_embedding_vectors: the VECTOR itself moves into Postgres.
--
-- WHY (2026-08-07, found while diagnosing "SEMANTIC_ENABLED is true but
-- nothing is embedded"). The vectors lived in ChromaDB at /data/chroma on the
-- BACKEND container's local filesystem, and that placement had three
-- structural consequences, all measured in prod:
--
--   1. The worker image ships without the [semantic] extra
--      (Dockerfile.worker), and the nightly catalog refresh — the only thing
--      that runs the pipeline on a schedule — runs on the WORKER. It could
--      never embed anything. Catalog grew 7,761 -> 8,184 overnight while
--      embeddings stayed at exactly 284.
--   2. Backend and worker are separate containers with separate disks, so
--      they cannot share an index even if both had the encoder.
--   3. Worst: `job_embeddings` (this table, in Postgres, permanent) recorded
--      "job N is embedded", so the backfill SKIPPED those jobs — while the
--      vectors sat on a container disk a redeploy can reset. The bookkeeping
--      could claim coverage the index did not have, and nothing would ever
--      refill the hole.
--
-- Putting the vector in the SAME ROW as its audit record removes all three:
-- one store shared by every service, surviving deploys, inside the existing
-- db-backup, and structurally unable to desync from its own bookkeeping.
--
-- TOLERANT BY DESIGN. Migrations run inside the FastAPI lifespan
-- (RUN_MIGRATIONS_ON_BOOT), so a hard `CREATE EXTENSION` would turn a
-- Postgres without pgvector into a BOOT FAILURE — trading a dark feature for
-- an outage. Prod has pgvector 0.8.3 available (verified 2026-08-07) and CI
-- now runs the pgvector image; anywhere else this migration is a clean no-op
-- and PgVectorIndex degrades to "no semantic results" instead of erroring.
--
-- At ~8k jobs an exact cosine scan is milliseconds, so no ANN index is
-- created here; add ivfflat/hnsw when the catalog justifies it.
--
-- Shared catalog — no user_id (CLAUDE.md rule #10).

DO $do$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable — job_embeddings.embedding skipped';
    END;

    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
        ALTER TABLE job_embeddings ADD COLUMN IF NOT EXISTS embedding vector(384);
        -- `embedding IS NULL` is exactly the "needs embedding" predicate the
        -- backfill uses, so rows carried over from the Chroma era (audit row,
        -- no vector) are picked up automatically.
        CREATE INDEX IF NOT EXISTS idx_job_embeddings_missing_vector
            ON job_embeddings(job_id) WHERE embedding IS NULL;
    END IF;
END
$do$;
