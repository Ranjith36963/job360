-- 0036_oauth: OAuth 2.1 authorization server for MCP clients
-- (docs/plans/2026-09-03-oauth-mcp/spec.md, Design). Five tables: a client
-- registers itself (oauth_clients), asks for authorization
-- (oauth_authorization_requests, pre-consent, one per browser round trip),
-- gets a durable per-user grant on consent (oauth_grants), trades a one-time
-- code for tokens (oauth_authorization_codes), and holds the access/refresh
-- pair (oauth_tokens).
--
-- Same conventions as 0035: TEXT timestamps, IF NOT EXISTS, only sha256
-- hashes of secrets are stored (never a plaintext code or token). The
-- REFERENCES below document the real parent/child relationship for anyone
-- reading this file; `src/repositories/pg.py`'s SQLite->Postgres translate()
-- strips every FK clause at execute time (this codebase runs with SQLite's
-- foreign_keys=OFF semantics even against Postgres — see pg.py's own
-- comment), so nothing here is enforced at the DB level. Cascade-on-delete
-- is therefore done in application code
-- (`JobDatabase.hard_delete_user`), not by the database.
CREATE TABLE IF NOT EXISTS oauth_clients (
    id TEXT PRIMARY KEY,                    -- "j360c_" + token_urlsafe(24)
    client_name TEXT NOT NULL,              -- sanitised, untrusted display text
    redirect_uris TEXT NOT NULL,            -- JSON array, normalised (S3)
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_authorization_requests (
    id TEXT PRIMARY KEY,                    -- "rid": secrets.token_urlsafe(32)
    client_id TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    redirect_uri TEXT NOT NULL,
    scope TEXT NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    resource TEXT NOT NULL,                 -- RFC 8707; never NULL (S13)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_oauth_authz_requests_client
    ON oauth_authorization_requests(client_id);

CREATE TABLE IF NOT EXISTS oauth_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,                      -- written at most once per 5 min per grant
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_oauth_grants_user
    ON oauth_grants(user_id, revoked_at);
-- One ACTIVE grant per (user, client): a fresh approval after a revoke
-- inserts a new row, never un-revokes the old one.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_oauth_grants_user_client_active
    ON oauth_grants(user_id, client_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    code_hash TEXT PRIMARY KEY,             -- sha256 of the opaque code (S4); never the plaintext
    grant_id INTEGER NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT                            -- set atomically at claim time (R5)
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_grant
    ON oauth_authorization_codes(grant_id);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_id INTEGER NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                     -- 'access' | 'refresh'
    token_hash TEXT NOT NULL UNIQUE,
    audience TEXT NOT NULL,                 -- RFC 8707; the code's `resource` (S13)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    replaced_by INTEGER                     -- rotation: id of the token that superseded this one
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_grant_kind
    ON oauth_tokens(grant_id, kind);
