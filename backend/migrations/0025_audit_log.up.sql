-- 0025_audit_log: durable, queryable audit trail (docs/fable/05 C8).
-- The audit logger (job360.audit) previously wrote JSON lines to a rotating
-- file only — rotation eventually DELETES history, and grepping files is not
-- "queryable". This table receives the same records via a logging handler
-- (src/services/audit_trail.py), so every security-relevant event (register,
-- login ok/fail/locked, logout, magic-link request/consume, channel changes,
-- account mutations) survives rotation and can be queried with SQL.
--
-- user_id is nullable ON PURPOSE: failed logins have no user, and GDPR erasure
-- (hard_delete_user) anonymises rows rather than deleting them — the security
-- history survives, the personal link does not (same pattern as run_log).
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    event TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    user_id TEXT,
    client_ip TEXT,
    user_agent TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user_time
    ON audit_log(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_time
    ON audit_log(event, occurred_at DESC);
