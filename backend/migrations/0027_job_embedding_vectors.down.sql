-- Reverse of 0027 — drops the in-Postgres vector column. The extension is
-- left installed (dropping it would break any other consumer, and it is
-- inert when unused). Registry-only rollback for the column would leave the
-- schema lying, so this one really drops it: no other table references it.
ALTER TABLE job_embeddings DROP COLUMN IF EXISTS embedding;
DELETE FROM _schema_migrations WHERE id = '0027_job_embedding_vectors';
