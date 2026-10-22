-- 0075 down: reverses the DDL only. No other column or row is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact's base relationship and
-- shared are dropped and NOT recoverable except from a backup. Changes
-- recorded as `contact_edits` rows (field = 'relationship' / 'shared')
-- survive as inert rows nothing reads.

ALTER TABLE application_contacts DROP COLUMN IF EXISTS shared;
ALTER TABLE application_contacts DROP COLUMN IF EXISTS relationship;
