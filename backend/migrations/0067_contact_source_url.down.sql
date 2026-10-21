-- 0067 down: reverses the DDL only. No other column or row is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact's base source_url is dropped
-- and NOT recoverable except from a backup. A correction recorded as a
-- `contact_edits` row (field = 'source_url') survives as an inert row
-- nothing reads.

ALTER TABLE application_contacts DROP COLUMN IF EXISTS source_url;
