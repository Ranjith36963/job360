-- 0042 down: reverses the DDL only. No pre-existing `applications` row or
-- column is touched beyond the five added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: every stored visa judgement — including
-- any recorded AFTER this migration by real product usage — is dropped with
-- its columns and is NOT recoverable except from a backup. The judgements
-- that arrived through `save_fit` survive as prose inside the `fit_judged`
-- event payload (append-only); the ones set on `bring_job` or through the web
-- dropdown do not.

ALTER TABLE applications DROP COLUMN IF EXISTS visa_recorded_at;
ALTER TABLE applications DROP COLUMN IF EXISTS visa_recorded_by;
ALTER TABLE applications DROP COLUMN IF EXISTS visa_country;
ALTER TABLE applications DROP COLUMN IF EXISTS visa_detail;
ALTER TABLE applications DROP COLUMN IF EXISTS visa_signal;
