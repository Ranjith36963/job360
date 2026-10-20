-- 0068 down: reverses the DDL only. No other column or row is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact's base company is dropped and
-- NOT recoverable except from a backup. A correction recorded as a
-- `contact_edits` row (field = 'company') survives as an inert row nothing reads.

ALTER TABLE application_contacts DROP COLUMN IF EXISTS company;
