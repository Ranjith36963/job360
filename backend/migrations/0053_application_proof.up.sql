-- 0053_application_proof: owner decision 2026-10-10 (S7) - PROOF OF APPLICATION.
-- A receipt says "applied"; proof is what backs it up: the pasted confirmation
-- text (an ordinary `proof_text` event, so no table), a screenshot of the
-- confirmation page, or an email (an `applied`/`note` event with an email source).
--
-- 1. `application_proof_screenshots` - one row per uploaded image. `bytes` is
--    the only column ever blanked: deleting a screenshot sets `bytes` NULL plus
--    `deleted_at` / `delete_note`, so the timeline keeps the fact it existed
--    while the image itself is really erased. `mime` comes from the magic
--    bytes, never from the client. Exported (without `bytes`), erased with the
--    account.
-- 2. `proof_upload_links` - one row per single-use upload link. Only the SHA-256
--    of the token is stored; `used_at` is the one slot ever UPDATEd. A security
--    credential like `artifact_links`: erased with the account, never exported.
--
-- DDL ONLY, IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS application_proof_screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    mime TEXT NOT NULL,                      -- image/png | image/jpeg | image/webp
    bytes BYTEA,                             -- NULL once deleted
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_by TEXT NOT NULL,                -- actor_for(user)
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    delete_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_proof_screenshots_user_app ON application_proof_screenshots(user_id, application_id);

CREATE TABLE IF NOT EXISTS proof_upload_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,         -- sha256 hex of the token; the token is never stored
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_by TEXT NOT NULL,                -- actor_for(user)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proof_links_user_created ON proof_upload_links(user_id, created_at);
