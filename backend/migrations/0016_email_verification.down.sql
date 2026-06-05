-- Reverse of 0016_email_verification.
-- Note: SQLite doesn't support DROP COLUMN cleanly across all versions;
-- skipping the column drop here keeps the down-migration idempotent on
-- pre-3.35 SQLite. The column is harmless if left in place; production
-- callers wanting a clean strip should target a fresh DB instead.
DROP INDEX IF EXISTS idx_email_verifications_expires;
DROP INDEX IF EXISTS idx_email_verifications_user;
DROP TABLE IF EXISTS email_verifications;
