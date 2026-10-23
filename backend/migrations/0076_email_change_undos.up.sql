-- 0076_email_change_undos: the "this wasn't me" link for an email change (gap roadmap slice 35).
--
-- Changing the account email now mails the OLD address a one-time link that puts
-- the old address (and its old verified state) back and signs everyone out. The
-- token is SHA256-hashed at rest like password_resets (0015): a DB leak alone
-- yields no usable link. UNIQUE(token_hash) + used_at make it single-use. 24 h expiry.
--
-- previous_email / previous_verified_at are what the undo restores. One row per
-- change, and an older row is NOT voided by a newer change: an attacker who
-- changes the address twice must not be able to cancel the owner's first link.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS email_change_undos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash           TEXT NOT NULL UNIQUE,
    previous_email       TEXT NOT NULL,
    previous_verified_at TEXT,  -- NULL = the old address was not verified
    expires_at           TEXT NOT NULL,
    used_at              TEXT,  -- NULL = not yet used; set on a successful undo
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_email_change_undos_user ON email_change_undos(user_id);
