-- 0016_email_verification: per-user email verification tokens.
--
-- Phase −2 item B. Mirrors 0015_password_resets shape because the token
-- mechanics are identical (single-use, hashed at rest, expiry). The
-- difference is semantic: this token proves the user controls the email
-- address they registered with.
--
-- Verification is *not* enforced at login today — gating sensitive
-- features (configure paid channels, etc) on verification is a product
-- decision left for Phase 2 of LAUNCH_PLAN.md. This migration only
-- introduces the data model and the email_verified_at column so the
-- product gate can be flipped on later without a schema change.

ALTER TABLE users ADD COLUMN email_verified_at TEXT;  -- NULL = unverified

CREATE TABLE IF NOT EXISTS email_verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    -- The email this token verifies. Stored explicitly so a token issued
    -- before an email-change attack remains tied to the old address.
    email       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at  TEXT NOT NULL,
    used_at     TEXT  -- NULL = not yet used
);

CREATE INDEX IF NOT EXISTS idx_email_verifications_user ON email_verifications(user_id);
CREATE INDEX IF NOT EXISTS idx_email_verifications_expires ON email_verifications(expires_at);
