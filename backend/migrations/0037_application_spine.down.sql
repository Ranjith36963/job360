-- 0037 down: reverses the DDL only. Every pre-migration row in `applications`,
-- `application_stage_history`, `application_receipts` and `tailored_documents`
-- survives untouched — the fold only ever COPIED forward (see the up file's
-- header). A rollback the day after deploy loses nothing.
--
-- WHAT THIS COSTS, STATED PLAINLY: every `application_events` row and every
-- `application_artifacts` row — including any created AFTER this migration
-- by real product usage (a save_artifact, a record_event, a save_fit call) —
-- is dropped with its table and is NOT recoverable except from a backup.
-- `application_receipts.application_id` and the fit slot on `applications`
-- are also lost, so `record_application` calls made after the up-migration
-- lose their link back to an application until 0037 is re-applied. This is
-- the SQLite-can't-DROP-COLUMN caveat from 0014's down file made obsolete in
-- the other direction: the store has been Postgres since the pg migration,
-- so a plain `DROP COLUMN` here is correct and complete.

DROP TABLE IF EXISTS application_artifacts;
DROP TABLE IF EXISTS application_events;

DROP INDEX IF EXISTS idx_receipts_application;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS recorded_by;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS confirmation;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS fields_filled;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS answers;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS cover_letter_artifact_id;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS cv_artifact_id;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS application_id;

DROP INDEX IF EXISTS idx_applications_user_status;
DROP INDEX IF EXISTS idx_applications_user_last_event;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_recorded_at;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_recorded_by;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_reasoning;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_gaps;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_verdict;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_score;
ALTER TABLE applications DROP COLUMN IF EXISTS snapshot_at;
ALTER TABLE applications DROP COLUMN IF EXISTS job_description_snapshot;
ALTER TABLE applications DROP COLUMN IF EXISTS job_source;
ALTER TABLE applications DROP COLUMN IF EXISTS job_url;
ALTER TABLE applications DROP COLUMN IF EXISTS job_location;
ALTER TABLE applications DROP COLUMN IF EXISTS job_company;
ALTER TABLE applications DROP COLUMN IF EXISTS job_title;
ALTER TABLE applications DROP COLUMN IF EXISTS last_event_at;
ALTER TABLE applications DROP COLUMN IF EXISTS status;
