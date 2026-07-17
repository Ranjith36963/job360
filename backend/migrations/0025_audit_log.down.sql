-- Reverse of 0025_audit_log. Dropping the table discards audit history —
-- dev-only, like every down migration in this repo.
DROP INDEX IF EXISTS idx_audit_log_event_time;
DROP INDEX IF EXISTS idx_audit_log_user_time;
DROP TABLE IF EXISTS audit_log;
