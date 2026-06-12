-- 0015_password_resets: password-reset token storage.
--
-- Phase −2 item A. Tokens are SHA256-hashed at rest so a DB leak doesn't
-- yield usable reset links (raw token only ever exists in transit in the
-- email body). Single-use enforced via UNIQUE(token_hash) + used_at column.
-- 1-hour expiry — long enough for email delivery, short enough to bound
-- the attack window if the email account is compromised.

CREATE TABLE IF NOT EXISTS password_resets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at  TEXT NOT NULL,
    -- used_at: NULL = not yet used; set on successful consume
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_expires ON password_resets(expires_at);
