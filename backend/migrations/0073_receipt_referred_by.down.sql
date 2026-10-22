-- 0073 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every receipt's referrer is dropped and NOT recoverable except
-- from a backup. The referral_given ledger rows and application events survive.

ALTER TABLE application_receipts DROP COLUMN IF EXISTS referred_by_contact_id;
