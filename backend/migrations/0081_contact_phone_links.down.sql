-- 0081 down: reverses the DDL only. No other column or row is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact's base phone and links are
-- dropped and NOT recoverable except from a backup. Corrections recorded as
-- `contact_edits` rows (field = 'phone' / 'links') and Confirm marks
-- (field = 'confirmed') survive as inert rows nothing reads.

ALTER TABLE application_contacts DROP COLUMN IF EXISTS links;
ALTER TABLE application_contacts DROP COLUMN IF EXISTS phone;
