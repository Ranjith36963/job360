-- 0001_auth: users + sessions tables. See docs/product/plans/batch-2-plan.md Phase 1.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_agent TEXT,
    ip_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
