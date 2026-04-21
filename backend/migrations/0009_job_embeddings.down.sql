-- 0009_job_embeddings: reverse migration.
DROP INDEX IF EXISTS idx_job_embeddings_model;
DROP TABLE IF EXISTS job_embeddings;
