-- 0033 — stop destroying the CV the user actually applied with (wiring.md W-08 + W-10).
--
-- Two failures, one table.
--
-- W-10: `tailored_documents` is UNIQUE(user_id, job_id, doc_kind) and
--       `upsert_tailored_doc` is a DELETE followed by an INSERT. So there is no v1
--       and v2 — only ever "the current one". Generate, apply, regenerate later, and
--       the document actually sent to the employer is gone from the database
--       permanently, with no way to recover it or even prove it existed.
--
-- W-08: nothing tied an application to a document. `applications` stored job_id and
--       user_id; the documents live in a separate table keyed by job_id. So "which CV
--       did I send for this job?" could only be answered with "whatever happens to be
--       there now" — which is a different question, and the wrong one.
--
-- Why an append-only side table rather than versioning `tailored_documents` itself:
-- that table's UNIQUE(user_id, job_id, doc_kind) is what every existing reader relies
-- on for "the current draft". Widening it would touch every one of those call sites
-- for no user-visible gain. A snapshot table leaves the live-document contract exactly
-- as it is and adds the history beside it.
--
-- Deliberately stores the TEXT, not a foreign key back to tailored_documents: the
-- whole point is to survive that row's deletion. A pointer to a deleted row answers
-- nothing.

CREATE TABLE IF NOT EXISTS tailored_document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    doc_kind TEXT NOT NULL,                 -- 'cv' | 'cover_letter'
    -- What the user would have sent: their edit if they made one, else the AI draft.
    content TEXT NOT NULL DEFAULT '',
    -- 'applied'     — snapshotted because they applied to the job
    -- 'kept'        — snapshotted because they downloaded/kept it
    -- 'superseded'  — snapshotted because a regenerate was about to destroy it
    source TEXT NOT NULL,
    model TEXT,
    profile_version INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tailored_versions_user_job
    ON tailored_document_versions(user_id, job_id, doc_kind);

-- The binding W-08 asked for: which snapshot was in hand when they applied.
-- Nullable on purpose — applying before tailoring anything is a normal thing to do,
-- and a NULL here honestly means "no document existed at that moment".
ALTER TABLE applications ADD COLUMN cv_version_id INTEGER;
ALTER TABLE applications ADD COLUMN cover_letter_version_id INTEGER;
