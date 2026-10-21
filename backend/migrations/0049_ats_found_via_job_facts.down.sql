-- 0049 down: reverses the DDL only. No pre-existing row or other column is
-- touched beyond the six added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: every stored ATS score/notes, every
-- contact's base found_via, and every application's job country / remote /
-- found_on are dropped and NOT recoverable except from a backup. A
-- found_via correction recorded as a `contact_edits` row (field =
-- 'found_via') survives as an inert row nothing reads.

ALTER TABLE applications DROP COLUMN IF EXISTS job_found_on;
ALTER TABLE applications DROP COLUMN IF EXISTS job_remote;
ALTER TABLE applications DROP COLUMN IF EXISTS job_country;

ALTER TABLE application_contacts DROP COLUMN IF EXISTS found_via;

ALTER TABLE application_artifacts DROP COLUMN IF EXISTS ats_notes;
ALTER TABLE application_artifacts DROP COLUMN IF EXISTS ats_score;
