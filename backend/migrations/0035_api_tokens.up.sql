-- 0035_api_tokens: personal API tokens — the credential an agent (Claude Code,
-- Cursor, a script) presents instead of a browser cookie
-- (docs/plans/2026-09-03-mcp-server/spec.md R1).
--
-- Only sha256(token) is stored. The plaintext is shown once at mint time and
-- never again; lookup is by hash (UNIQUE = indexed), so there is no timing
-- channel on a 256-bit random value. `revoked_at` is soft on purpose: the
-- row keeps its audit trail, the credential dies immediately (checked on
-- every request, no cache).
--
-- Per-user table: erased with the account (FK cascade + _PER_USER_TABLES),
-- exported without the hash (_EXPORT_REDACT_COLUMNS covers `token_hash`).
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,                   -- first 12 chars, for "which token is this?"
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,                      -- written at most once per 5 min per token
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user
    ON api_tokens(user_id, revoked_at);
