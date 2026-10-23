-- 0078 down: reverses the DDL only. No other `application_receipts` column is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: receipts forget which form event their answers
-- were copied from and the count that event gave. The answers themselves stay.

ALTER TABLE application_receipts DROP COLUMN IF EXISTS answers_count;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS answers_from;
