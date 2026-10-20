-- 0072 down: reverses the DDL only. No other `application_receipts` column is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: receipts forget which uploaded files (and their
-- byte hashes) they were recorded from. The `document_uploaded` events stay.

ALTER TABLE application_receipts DROP COLUMN IF EXISTS files;
