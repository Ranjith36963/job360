-- 0074 down: reverses the DDL only. No other column or row is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact's base email_source is dropped
-- and NOT recoverable except from a backup. Changes recorded as `contact_edits`
-- rows (field = 'email_source') survive as inert rows nothing reads. `bounced`
-- rows in contact_outreach stay (append-only) and are still read by the code.

DROP INDEX IF EXISTS idx_contact_outreach_bounced;
ALTER TABLE application_contacts DROP COLUMN IF EXISTS email_source;
