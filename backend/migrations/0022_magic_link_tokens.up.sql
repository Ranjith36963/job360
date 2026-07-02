-- 0022_magic_link_tokens: passwordless magic-link login tokens.
--
-- A user enters their email; the backend mints a single-use token, stores
-- only its SHA256 hash, and emails a link containing the raw token. Clicking
-- the link proves the user controls the address, so consuming a valid token
-- both authenticates AND verifies the email in one step (find-or-create the
-- user, set email_verified_at, create a session).
--
-- Mirrors 0016_email_verification shape — same token mechanics (single-use,
-- hashed at rest, short expiry). The difference is semantic: this token is
-- keyed on the EMAIL (not a user_id), because at request time the user may
-- not exist yet — the account is created lazily on consume.

CREATE TABLE IF NOT EXISTS magic_link_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,  -- NULL = not yet used
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_hash ON magic_link_tokens(token_hash);
