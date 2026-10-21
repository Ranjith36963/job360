-- 0056_missing_tool_reports: feature 7 - report_missing_tool.
-- A connected assistant tells the owner "a tool I needed does not exist". The
-- row is only STORED (no web UI; the owner reads it with a DB query). A table,
-- not audit_log: the audit handler also writes to stdout, and `why` is free
-- text an assistant typed. Per-user: erased with the account, exported with it.
--
-- `name` is a closed shape (^[a-z][a-z0-9_]{1,59}$, checked by the route);
-- `why` is at most 280 characters; `reported_by` = actor_for(user).
--
-- DDL ONLY, IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS missing_tool_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '',
    reported_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_missing_tool_reports_user_created ON missing_tool_reports(user_id, created_at);
