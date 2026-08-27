-- Reverse 0033. Drop the columns first, then the table they point into.
ALTER TABLE applications DROP COLUMN cover_letter_version_id;
ALTER TABLE applications DROP COLUMN cv_version_id;
DROP INDEX IF EXISTS idx_tailored_versions_user_job;
DROP TABLE IF EXISTS tailored_document_versions;
