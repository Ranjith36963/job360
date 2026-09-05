-- 0038_drop_sourcing_tables: the sourcing era's three tables go
-- (docs/plans/2026-09-05-delete-sourcing-era/spec.md R6, issue #483).
--
-- WHY. Slice 5 deleted every reader and every writer of these three tables:
--
--   run_log         — one row per pipeline run. Its writer was the search
--                     orchestrator in `src/main.py`; its readers were
--                     `GET /api/status`, `/api/runs/*` and the CLI `status`
--                     command. All deleted.
--   job_enrichment  — the LLM's structured read of a job ad. Written by
--                     `services/job_enrichment.py` + `shelf_enrichment.py`,
--                     read by the scorer and the jobs API (all deleted).
--   job_embeddings  — the per-job vector + its audit row. Written by
--                     `services/embeddings.py` / `pg_vector_index.py`, read by
--                     `retrieval.py` (all deleted).
--
-- After this migration nothing in `backend/src` names any of them. A table no
-- code can read is not data, it is a trap: the next reader assumes it is
-- maintained. Product rule 4 — Job360 never sources, ranks or judges a job —
-- so none of this can come back.
--
-- WHAT THIS DOES NOT TOUCH, deliberately (spec S2 — a reviewer greps this file
-- for DROP and counts exactly three):
--   * `jobs` stays. It is the ad the user brought, and `applications.job_id`,
--     `application_receipts` and `tailored_documents` all key on it.
--   * `user_feed` stays. It goes with push notifications (VISION decision 11),
--     which is its own slice.
--   * Every applications/profile/receipt/tailor/auth/OAuth/notification table
--     is untouched.
--
-- DATA LOSS IS REAL AND INTENDED. These hold scraped-era rows produced by code
-- that no longer exists; nothing can regenerate them. `db-backup.yml` runs
-- daily, and `0038_drop_sourcing_tables.down.sql` recreates the three tables
-- EMPTY (schema only — see its header).
--
-- Indexes are dropped explicitly before their tables. Postgres would drop them
-- with the table anyway; naming them keeps this file readable as the inverse of
-- 0008 / 0009 / 0010 / 0027 and makes a partial state (index without table, from
-- a hand-edited DB) recoverable.

DROP INDEX IF EXISTS idx_run_log_run_uuid;
DROP INDEX IF EXISTS idx_job_enrichment_job_id;
DROP INDEX IF EXISTS idx_job_embeddings_model;
DROP INDEX IF EXISTS idx_job_embeddings_missing_vector;

DROP TABLE IF EXISTS run_log;
DROP TABLE IF EXISTS job_enrichment;
DROP TABLE IF EXISTS job_embeddings;
