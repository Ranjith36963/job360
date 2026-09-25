-- 0046_people_outreach: outreach tracking for people (owner decisions,
-- 2026-09-25). Job360 remembers every message version, who/when/channel,
-- sent, reply, and which job (or none — cold networking). The user's own
-- assistant WRITES messages; the USER sends; Job360 never sends, never reads
-- Gmail/LinkedIn, has no AI of its own.
--
-- WHY three changes:
--
-- 1. `application_contacts.application_id` becomes NULLABLE. A person can be
--    linked to a job or to none (a cold contact — recruiter met at a meetup,
--    no job yet). The old per-application unique-email index (0038) still
--    dedupes a linked contact by (application_id, email); a NEW partial
--    unique index dedupes a COLD contact by (user_id, email) — one row per
--    person per user when there is no job to scope by. The SAME recruiter
--    linked to two different jobs is deliberately TWO rows (each application
--    gets its own contact row and its own outreach ledger — decision "EACH
--    PERSON HAS THEIR OWN FULL RECORD end to end").
--
-- 2. `contact_outreach` — append-only ledger of everything said to/about one
--    contact: a message VERSION the assistant drafted (`entry='message'`,
--    numbered `version_no` 1, 2, 3…), a `sent` mark (the user told the
--    assistant it went out), or a `reply` mark (LinkedIn: the user says so;
--    email: the daily-check agent, carrying `source_message_id` for
--    idempotent re-reads of the same email). `version_no` is NULL for
--    `sent`/`reply` rows (a person can be sent to or replied to many times;
--    NULL is not equal to NULL under UNIQUE in Postgres or SQLite, so the
--    `UNIQUE(contact_id, entry, version_no)` constraint only actually
--    constrains `message` rows, which is exactly the identity that matters:
--    the assistant should never silently double-number the same version).
--
-- 3. `contact_edits` — append-only overlay of edits to a contact's own
--    fields (name/role/email/linkedin_url/notes), same shape as `profile_
--    edits` (0038): the base `application_contacts` row is NEVER updated
--    (guard: tests/test_slice4_contacts.py::test_contacts_are_append_only,
--    extended here to also cover contact_outreach/contact_edits), so
--    `test_contacts_are_append_only` stays green — reads apply the newest
--    edit per field on top of the base row, and history is every value with
--    who/when ("was X").
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0037/0038: TEXT ISO-8601 timestamps, IF NOT EXISTS, INTEGER PRIMARY
-- KEY AUTOINCREMENT (the shim rewrites this, pg.py:193-195), REFERENCES
-- clauses for documentation only (the shim strips FK clauses, pg.py:217-226)
-- — every read filters on user_id by hand.

-- (1) application_id becomes optional.
ALTER TABLE application_contacts ALTER COLUMN application_id DROP NOT NULL;

-- One row per person per user when the contact carries no job at all.
CREATE UNIQUE INDEX IF NOT EXISTS uq_application_contacts_cold_user_email
    ON application_contacts(user_id, email) WHERE application_id IS NULL AND email <> '';

-- (2) the outreach ledger.
CREATE TABLE IF NOT EXISTS contact_outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES application_contacts(id) ON DELETE CASCADE,
    entry TEXT NOT NULL,                     -- 'message' | 'sent' | 'reply'
    channel TEXT NOT NULL,                   -- 'linkedin' | 'email' | 'other'
    text TEXT NOT NULL DEFAULT '',           -- the message body; '' for sent/reply marks
    version_no INTEGER,                      -- message versions only; NULL for sent/reply
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,               -- actor_for(user): web | token:<n> | agent:<n>
    source_message_id TEXT NOT NULL DEFAULT ''  -- idempotent re-read of the same email
);
CREATE INDEX IF NOT EXISTS idx_contact_outreach_user_contact
    ON contact_outreach(user_id, contact_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_outreach_version
    ON contact_outreach(contact_id, entry, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_outreach_source
    ON contact_outreach(contact_id, source_message_id) WHERE source_message_id <> '';

-- (3) the contact-edit overlay (same shape as profile_edits, 0038).
CREATE TABLE IF NOT EXISTS contact_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES application_contacts(id) ON DELETE CASCADE,
    field TEXT NOT NULL,                     -- name | role | email | linkedin_url | notes
    value TEXT NOT NULL,                     -- the NEW value this edit set
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contact_edits_user_contact_field
    ON contact_edits(user_id, contact_id, field, id DESC);
