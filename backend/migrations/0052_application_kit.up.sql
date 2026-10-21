-- 0052_application_kit: owner decision 2026-10-08 (S3) - the APPLICATION KIT.
-- What an assistant needs to fill ONE application form: the CV and letter of
-- THAT application, every stored answer with its source, a short-lived file link.
--
-- 1. `artifact_links` - one row per file link the kit hands out. Only the SHA-256
--    of the random token is stored (the token itself lives in one API response).
--    `downloads_left` is the one counter SLOT (like `applications.status`): the
--    only column ever UPDATEd, once per download. Not part of the GDPR export
--    (it is a security credential, like `sessions`).
-- 2. Three columns on `application_receipts`, all set at INSERT time (M3, never
--    backfilled): the duplicate flag ('' | same_job | same_company), the id of
--    the kit read the assistant used, and that kit's hash.
--
-- Holds ("another assistant is on this") and the account memory are EVENTS
-- (`kit_read`, `site_account`, `account_needed`), not tables: who/when come
-- free and there is nothing to expire. DDL ONLY, IF NOT EXISTS so a re-run is
-- harmless; every existing receipt reads '' / NULL / '' (nothing flagged).

CREATE TABLE IF NOT EXISTS artifact_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,         -- sha256 hex of the token; the token is never stored
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    fmt TEXT NOT NULL DEFAULT 'pdf',
    expires_at TEXT NOT NULL,
    downloads_left INTEGER NOT NULL,
    created_by TEXT NOT NULL,                -- actor_for(user)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifact_links_user_created ON artifact_links(user_id, created_at);

ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS possible_duplicate TEXT NOT NULL DEFAULT '';
ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS kit_event_id INTEGER;
ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS kit_sha256 TEXT NOT NULL DEFAULT '';
