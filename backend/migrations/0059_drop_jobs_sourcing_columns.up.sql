-- 0059_drop_jobs_sourcing_columns: retire the sourcing-era score columns on
-- the shared `jobs` catalog.
--
-- WHY. The scorer, the per-dimension breakdown and the GBP salary normaliser
-- were all deleted in slice 5 (#483, 2026-09-05). Since then nothing in
-- backend/src, frontend/src, the scripts or the tests reads or writes any of
-- these twelve columns: `insert_job` leaves them at their defaults and every
-- reader (`job_row_to_response`, `birth_application`, the receipt copy, the
-- tailor) picks named keys that are not among them. They only ever held what
-- the scorer guessed about a job for one profile — which product rule 4 says
-- we never compute.
--
--   match_score                                    (0001 / init_db)
--   role, skill, seniority_score, experience,
--   credentials, location_score, recency,
--   semantic, penalty                              (0011_score_dimensions)
--   salary_min_gbp_annual, salary_max_gbp_annual   (0032_universal_shelf)
--
-- KEPT on purpose: salary_min / salary_max (read by `job_row_to_response`,
-- written by `insert_job`), and every other column on the table.
--
-- DDL ONLY. In Postgres DROP COLUMN is a catalog change — it marks the column
-- dropped and does not rewrite the ~19k rows, so this is instant (it takes a
-- brief ACCESS EXCLUSIVE lock on `jobs`). The index on match_score goes with
-- the column; it is dropped explicitly first for clarity. IF EXISTS so a
-- database that never had a column (e.g. a fresh init_db-only schema, which
-- no longer creates them) still migrates cleanly — same pattern as 0043.
--
-- `database.py` init_db/_migrate no longer creates these columns, so a fresh
-- schema and a migrated one end up identical.

DROP INDEX IF EXISTS idx_jobs_match_score;
ALTER TABLE jobs DROP COLUMN IF EXISTS match_score;
ALTER TABLE jobs DROP COLUMN IF EXISTS role;
ALTER TABLE jobs DROP COLUMN IF EXISTS skill;
ALTER TABLE jobs DROP COLUMN IF EXISTS seniority_score;
ALTER TABLE jobs DROP COLUMN IF EXISTS experience;
ALTER TABLE jobs DROP COLUMN IF EXISTS credentials;
ALTER TABLE jobs DROP COLUMN IF EXISTS location_score;
ALTER TABLE jobs DROP COLUMN IF EXISTS recency;
ALTER TABLE jobs DROP COLUMN IF EXISTS semantic;
ALTER TABLE jobs DROP COLUMN IF EXISTS penalty;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_min_gbp_annual;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_max_gbp_annual;
