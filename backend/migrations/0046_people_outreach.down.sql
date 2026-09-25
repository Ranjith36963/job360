-- 0046 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every outreach message/sent/reply ledger
-- row and every contact-detail edit recorded after the up-migration is
-- dropped with its table and is NOT recoverable except from a backup. Every
-- job-less (cold) contact is DELETED outright — the NOT NULL constraint this
-- restores cannot hold while those rows exist. A linked contact's row and its
-- `contact_added` event survive untouched.

DROP TABLE IF EXISTS contact_edits;
DROP TABLE IF EXISTS contact_outreach;

DROP INDEX IF EXISTS uq_application_contacts_cold_user_email;
DELETE FROM application_contacts WHERE application_id IS NULL;
ALTER TABLE application_contacts ALTER COLUMN application_id SET NOT NULL;
