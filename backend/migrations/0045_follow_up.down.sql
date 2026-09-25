-- 0045 down: reverses the DDL only. No pre-existing `applications` row or
-- other column is touched beyond the one added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: the CURRENT follow-up date of every
-- application is dropped with the column. Every date that was ever set or
-- cleared through record_event also lives in the `application_events`
-- payload (append-only), so the history survives; only the slot copy goes.

ALTER TABLE applications DROP COLUMN IF EXISTS follow_up_on;
