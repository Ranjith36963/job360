-- 0034 down: drops the receipts. This is the ONE sanctioned way a receipt
-- disappears, and it is a schema rollback, not a user-facing operation.
DROP INDEX IF EXISTS idx_receipts_user_job;
DROP INDEX IF EXISTS idx_receipts_user_time;
DROP TABLE IF EXISTS application_receipts;
