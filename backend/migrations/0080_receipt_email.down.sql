-- 0080 down: reverses the DDL only. No other `application_receipts` column is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: receipts forget which address an email
-- application went to and its Gmail message id. The `email_drafted` events stay.

ALTER TABLE application_receipts DROP COLUMN IF EXISTS gmail_message_id;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS recipient;
