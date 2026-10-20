-- 0070 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every interaction/referral follow-up date and every
-- receipt's referrer are dropped and NOT recoverable except from a backup. The ledger rows
-- with entry meeting/call/intro/referral_asked/referral_given SURVIVE: the previous code shows
-- none of them (its views filter message/sent/reply) but counts them toward
-- OUTREACH_ENTRIES_PER_CONTACT_MAX. Their job-timeline events survive as unlabelled notes.

ALTER TABLE application_receipts DROP COLUMN IF EXISTS referred_by_contact_id;
ALTER TABLE contact_outreach DROP COLUMN IF EXISTS follow_up_on;
