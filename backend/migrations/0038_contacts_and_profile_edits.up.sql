-- 0038_contacts_and_profile_edits: slice 4 of the mission roadmap
-- (docs/plans/2026-09-05-contacts-stats/spec.md §Data model).
--
-- WHY. The spine (0037) gave every application an event log and versioned
-- artifacts; the `contact_added` / `outreach_sent` event types and the
-- `outreach` artifact kind already exist. What is missing is a row for the
-- PERSON (VISION decision 9: "we store contacts + messages, the agent finds
-- and writes") and a row for an agent's correction to the extracted profile
-- (decision 14: "keep our extraction, add update_profile"). Both are
-- add-only: a contact is never edited or deleted at runtime, a profile edit
-- is superseded by a newer row for the same path (value NULL = cleared) and
-- never rewritten. Account deletion is the one exception and goes through
-- JobDatabase._PER_USER_TABLES.
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0037: TEXT ISO-8601 timestamps, IF NOT EXISTS, INTEGER PRIMARY KEY
-- AUTOINCREMENT (the shim rewrites this, pg.py:193-195), REFERENCES clauses
-- for documentation only (the shim strips FK clauses, pg.py:217-226) — every
-- read filters on user_id by hand.

-- R1/R2 — one row per person per application. The partial unique index is
-- the idempotency rule: the same non-empty email on the same application is
-- the same contact (the second add returns the first row); email '' carries
-- no identity and never collides.
CREATE TABLE IF NOT EXISTS application_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',          -- stored lower-cased + trimmed; '' = none
    linkedin_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL,                  -- actor_for(user): web | token:<n> | agent:<n>
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_contacts_user_app
    ON application_contacts(user_id, application_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_application_contacts_app_email
    ON application_contacts(application_id, email) WHERE email <> '';

-- R8 — the append-only overlay of agent edits over extraction. The current
-- value of a path is its NEWEST row; `value` is JSON-encoded; NULL means
-- "cleared — fall back to what extraction says". load_profile applies the
-- overlay, so every reader (web, tailor, MCP get_profile) sees one profile.
CREATE TABLE IF NOT EXISTS profile_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                      -- one of settings.PROFILE_EDITABLE_PATHS
    value TEXT,                              -- JSON; NULL = cleared
    set_by TEXT NOT NULL,                    -- actor_for(user)
    set_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_edits_user_path_id
    ON profile_edits(user_id, path, id DESC);
