-- 0034_application_receipts: the immutable "what did I send X?" record
-- (docs/plans/2026-08-27-exponential-product-research.md §8, slice one).
--
-- WHY. Today nothing remembers what a user actually sent. `applications` and
-- `user_actions` hold stage/notes/time only, and `tailored_documents` is
-- DELETE+INSERT on every regenerate (database.py::upsert_tailored_doc), so
-- the moment a user re-tailors, the CV they applied with is gone. A receipt
-- freezes the job AND the documents at the instant of "I applied".
--
-- APPEND-ONLY BY CONTRACT. No code path updates or deletes a receipt
-- (guard: tests/test_receipts.py::test_receipts_are_append_only). No UNIQUE
-- on (user_id, job_id): applying twice, months apart, is two receipts.
-- Deleting the account cascades — the record belongs to the user, not to us.
--
-- Per-user table, so `user_id` is correct here (rule #10 concerns the shared
-- `jobs` catalog, which this migration does not touch).
CREATE TABLE IF NOT EXISTS application_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,                  -- when the user said "I applied" (UTC ISO)
    -- The job as it read at that moment. Copied, not joined: the catalog row
    -- can be re-described, expired, or purged later; the receipt must not move.
    job_title TEXT NOT NULL,
    job_company TEXT NOT NULL,
    job_location TEXT NOT NULL DEFAULT '',
    job_apply_url TEXT NOT NULL DEFAULT '',
    job_source TEXT NOT NULL DEFAULT '',
    job_description TEXT NOT NULL DEFAULT '',
    -- The documents as sent. NULL = the user applied without one.
    cv_text TEXT,
    cv_origin TEXT,                         -- 'polished' | 'ai_draft' | NULL
    cover_letter_text TEXT,
    cover_letter_origin TEXT,               -- 'polished' | 'ai_draft' | NULL
    profile_version INTEGER,                -- user_profile_versions.id behind those docs
    channel TEXT NOT NULL DEFAULT '',       -- free text: 'company site', 'LinkedIn', 'email'…
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_receipts_user_time
    ON application_receipts(user_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_receipts_user_job
    ON application_receipts(user_id, job_id);
