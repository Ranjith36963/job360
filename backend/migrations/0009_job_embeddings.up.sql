-- 0009_job_embeddings: audit row per job embedding.
--
-- Pillar 2 Batch 2.6. The actual vector lives in ChromaDB at
-- `backend/data/chroma/`. This table records which jobs have an embedding,
-- under which model version, and when. That lets us (a) re-embed jobs
-- whose model_version drifts out of support and (b) detect drift where a
-- Chroma row exists without its SQL audit counterpart.
--
-- Shared catalog — no user_id (CLAUDE.md rule #10).

CREATE TABLE IF NOT EXISTS job_embeddings (
    job_id INTEGER PRIMARY KEY
        REFERENCES jobs(id) ON DELETE CASCADE,
    model_version TEXT NOT NULL,
    embedding_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_embeddings_model
    ON job_embeddings(model_version);
