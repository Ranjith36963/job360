-- 0037_application_spine: the application spine
-- (docs/plans/2026-09-04-application-spine/spec.md).
--
-- WHY. `applications` today is a bare Kanban stage (rule #10's per-user
-- table); nothing remembers the job as it read when the user brought it, no
-- append-only history exists beyond the legacy stage-transition log, and
-- artifacts (`tailored_documents`) are DELETE+INSERT — a re-tailor destroys
-- the version that was actually sent. This migration adds: a durable job
-- snapshot + status cache on `applications` (R2/R4), the one append-only
-- event log `application_events` (R3/R7), versioned-forever artifacts in
-- `application_artifacts` (R5), and the receipt columns `record_application`
-- needs to freeze a NAMED artifact version (R8).
--
-- FOLD, NOT MIGRATE. Every row already in `applications`,
-- `application_stage_history`, `application_receipts` and
-- `tailored_documents` is COPIED forward, never moved or deleted — see the
-- "no-row-loss" count table in spec.md §Migration fold. The whole body below
-- runs in ONE transaction (migrations/runner.py wraps every stem), so a
-- failure partway rolls back completely; no stem is ever recorded half-done.
--
-- THE ONE UPDATE THIS FILE MAKES AGAINST A RECEIPT ROW (step 6 below) sets
-- `application_id` on `application_receipts` — a column that does not exist
-- until the ALTER two statements earlier in THIS SAME file runs, and it
-- changes no user-visible field of the receipt. It is a migration-time
-- backfill, not a runtime rewrite, and is therefore outside the append-only
-- guard's scope: that guard greps `backend/src/`, never `migrations/`
-- (tests/test_receipts.py:129-135 pins this same carve-out for 0034's own
-- receipts table). Runtime code (`backend/src/**`) NEVER updates or deletes
-- an `application_events` / `application_artifacts` row — see
-- tests/test_application_spine.py::test_events_are_append_only, which greps
-- `backend/src/` for exactly that.
--
-- Conventions copied from 0034/0036: TEXT ISO-8601 timestamps, IF NOT
-- EXISTS, INTEGER PRIMARY KEY AUTOINCREMENT (the shim rewrites this to
-- BIGSERIAL-equivalent identity, pg.py:193-195). REFERENCES clauses below are
-- written for documentation value even though the shim strips every FK
-- clause (pg.py:217-226) — there is no DB-level cascade here; every read
-- filters on user_id by hand and account deletion goes through
-- `JobDatabase._PER_USER_TABLES`.

-- ── Step 1: DDL only — add columns, create the two new tables. No row touched. ──

ALTER TABLE applications ADD COLUMN status TEXT NOT NULL DEFAULT 'considering';
ALTER TABLE applications ADD COLUMN last_event_at TEXT;
ALTER TABLE applications ADD COLUMN job_title TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN job_company TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN job_location TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN job_url TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN job_source TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN job_description_snapshot TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN snapshot_at TEXT;
ALTER TABLE applications ADD COLUMN fit_score INTEGER;
ALTER TABLE applications ADD COLUMN fit_verdict TEXT;
ALTER TABLE applications ADD COLUMN fit_gaps TEXT DEFAULT '[]';
ALTER TABLE applications ADD COLUMN fit_reasoning TEXT;
ALTER TABLE applications ADD COLUMN fit_recorded_by TEXT;
ALTER TABLE applications ADD COLUMN fit_recorded_at TEXT;

CREATE INDEX IF NOT EXISTS idx_applications_user_last_event
    ON applications(user_id, last_event_at DESC);
CREATE INDEX IF NOT EXISTS idx_applications_user_status
    ON applications(user_id, status);

-- R3/R7 — the whole history. App-validated event_type (deliberately no CHECK
-- constraint — constraint 5: a new type must never need a migration).
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    corrects_event_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_application_events_user_recorded
    ON application_events(user_id, recorded_at, id);
CREATE INDEX IF NOT EXISTS idx_application_events_app_occurred
    ON application_events(application_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_application_events_user_type
    ON application_events(user_id, event_type);

-- R5 — artifacts versioned forever. UNIQUE(application_id, kind, version_no)
-- turns a version-allocation race into a retry, never a duplicate.
CREATE TABLE IF NOT EXISTS application_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    made_by TEXT NOT NULL,
    model TEXT,
    profile_version INTEGER,
    label TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(application_id, kind, version_no)
);
CREATE INDEX IF NOT EXISTS idx_application_artifacts_user_app_kind
    ON application_artifacts(user_id, application_id, kind, version_no DESC);

-- R8 — the rich receipt: name a version, don't just copy text.
ALTER TABLE application_receipts ADD COLUMN application_id INTEGER;
ALTER TABLE application_receipts ADD COLUMN cv_artifact_id INTEGER;
ALTER TABLE application_receipts ADD COLUMN cover_letter_artifact_id INTEGER;
ALTER TABLE application_receipts ADD COLUMN answers TEXT NOT NULL DEFAULT '[]';
ALTER TABLE application_receipts ADD COLUMN fields_filled TEXT NOT NULL DEFAULT '{}';
ALTER TABLE application_receipts ADD COLUMN confirmation TEXT NOT NULL DEFAULT '';
ALTER TABLE application_receipts ADD COLUMN recorded_by TEXT NOT NULL DEFAULT 'web';

CREATE INDEX IF NOT EXISTS idx_receipts_application
    ON application_receipts(user_id, application_id);

-- ── Step 2: no orphans. An applications row for every (user_id, job_id) that ──
-- appears in a legacy child table but not yet in `applications`. Written as
-- INSERT ... SELECT DISTINCT ... WHERE NOT EXISTS (not INSERT OR IGNORE) so it
-- needs no translate() rule and reads the same in either dialect. Each source
-- is its own statement so a pair present in TWO sources is only inserted once
-- (the second statement's WHERE NOT EXISTS sees the first statement's rows,
-- inside the same transaction).

INSERT INTO applications (user_id, job_id, stage, status, created_at, updated_at)
SELECT DISTINCT ar.user_id, ar.job_id, 'applied', 'applied', ar.created_at, ar.created_at
FROM application_receipts ar
WHERE NOT EXISTS (
    SELECT 1 FROM applications a WHERE a.user_id = ar.user_id AND a.job_id = ar.job_id
);

INSERT INTO applications (user_id, job_id, stage, status, created_at, updated_at)
SELECT DISTINCT td.user_id, td.job_id, 'applied', 'applied', td.created_at, td.created_at
FROM tailored_documents td
WHERE NOT EXISTS (
    SELECT 1 FROM applications a WHERE a.user_id = td.user_id AND a.job_id = td.job_id
);

INSERT INTO applications (user_id, job_id, stage, status, created_at, updated_at)
SELECT DISTINCT ash.user_id, ash.job_id, 'applied', 'applied', ash.transitioned_at, ash.transitioned_at
FROM application_stage_history ash
WHERE NOT EXISTS (
    SELECT 1 FROM applications a WHERE a.user_id = ash.user_id AND a.job_id = ash.job_id
);

-- ── Step 3: backfill `status` from `stage` — R4's mapping, reversed. ──
UPDATE applications SET status = CASE stage
    WHEN 'applied' THEN 'applied'
    WHEN 'outreach' THEN 'applied'
    WHEN 'interview' THEN 'interview_scheduled'
    WHEN 'offer' THEN 'offer'
    WHEN 'rejected' THEN 'rejected'
    WHEN 'ghosted' THEN 'ghosted'
    ELSE 'applied'
END;

-- ── Step 4: backfill the job snapshot from `jobs` (LEFT JOIN semantics via a ──
-- correlated subquery — portable across both dialects). Blank strings where
-- the catalog row is already gone: honest, and better than a NULL join later.
-- Scoped to `job_title = ''` so this only touches rows the ALTER just created
-- (their default), never a snapshot a later run already filled in.
UPDATE applications SET
    job_title = COALESCE((SELECT j.title FROM jobs j WHERE j.id = applications.job_id), ''),
    job_company = COALESCE((SELECT j.company FROM jobs j WHERE j.id = applications.job_id), ''),
    job_location = COALESCE((SELECT j.location FROM jobs j WHERE j.id = applications.job_id), ''),
    job_url = COALESCE((SELECT j.apply_url FROM jobs j WHERE j.id = applications.job_id), ''),
    job_source = COALESCE((SELECT j.source FROM jobs j WHERE j.id = applications.job_id), ''),
    job_description_snapshot = COALESCE((SELECT j.description FROM jobs j WHERE j.id = applications.job_id), ''),
    snapshot_at = datetime('now')
WHERE job_title = '';

-- ── Step 5: application_stage_history -> events. One event per row. ──
INSERT INTO application_events
    (user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by)
SELECT
    ash.user_id,
    a.id,
    CASE ash.to_stage
        WHEN 'interview' THEN 'interview_scheduled'
        WHEN 'offer' THEN 'offer'
        WHEN 'rejected' THEN 'rejected'
        WHEN 'ghosted' THEN 'ghosted'
        WHEN 'outreach' THEN 'applied'
        ELSE 'applied'
    END,
    COALESCE(ash.notes, ''),
    '{"from_stage": ' || (CASE WHEN ash.from_stage IS NULL THEN 'null' ELSE '"' || ash.from_stage || '"' END)
        || ', "to_stage": "' || ash.to_stage || '"}',
    ash.transitioned_at,
    ash.transitioned_at,
    'migration:0014_history'
FROM application_stage_history ash
JOIN applications a ON a.user_id = ash.user_id AND a.job_id = ash.job_id;

-- ── Step 6: application_receipts -> application_id + one 'applied' event each. ──
-- The ONE UPDATE this migration makes against a receipt row (see header note).
UPDATE application_receipts SET application_id = (
    SELECT a.id FROM applications a
    WHERE a.user_id = application_receipts.user_id AND a.job_id = application_receipts.job_id
)
WHERE application_id IS NULL;

INSERT INTO application_events
    (user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by)
SELECT ar.user_id, ar.application_id, 'applied', COALESCE(ar.note, ''), '{}', ar.sent_at, ar.sent_at,
       'migration:0034_receipts'
FROM application_receipts ar
WHERE ar.application_id IS NOT NULL;

-- ── Step 7: tailored_documents -> artifact versions. A draft + a polish fold ──
-- into TWO versions (both the draft AND the edit survive — precisely what
-- upsert_tailored_doc's DELETE+INSERT has been destroying); a draft-only row
-- folds into exactly one.
INSERT INTO application_artifacts
    (user_id, application_id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at)
SELECT td.user_id, a.id, td.doc_kind, 1, td.ai_draft, 'migration:0023_tailored',
       td.model, td.profile_version, '', LENGTH(td.ai_draft), td.created_at
FROM tailored_documents td
JOIN applications a ON a.user_id = td.user_id AND a.job_id = td.job_id
WHERE td.ai_draft IS NOT NULL AND td.ai_draft <> '';

INSERT INTO application_artifacts
    (user_id, application_id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at)
SELECT td.user_id, a.id, td.doc_kind, 2, td.polished, 'human',
       td.model, td.profile_version, '', LENGTH(td.polished), td.updated_at
FROM tailored_documents td
JOIN applications a ON a.user_id = td.user_id AND a.job_id = td.job_id
WHERE td.polished IS NOT NULL;

-- ── Step 7b: a synthetic status event for any application that STILL has ──
-- zero events after steps 5-7 (B1 fix). A legacy row with no stage_history,
-- no receipt and no tailored_documents row (or one that folded into no
-- artifact) reaches here with `status` already backfilled by step 3 but
-- NOTHING in `application_events` naming it. Without this row,
-- `append_event`'s status recompute (`replay_status`, which replays the
-- WHOLE log) defaults to 'considering' on the very first REAL event this
-- application ever gets — silently wiping a legacy 'offer'/'rejected'/etc.
-- `a.status` is always one of `_VALID_STAGES`' step-3 targets here, which are
-- all real APPLICATION_STATUS_EVENT_TYPES (never 'considering' — no
-- pre-migration row can hold that value), so it needs no CASE mapping.
INSERT INTO application_events
    (user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by)
SELECT a.user_id, a.id, a.status, '', '{}', a.updated_at, a.updated_at, 'migration:0037_status_backfill'
FROM applications a
WHERE NOT EXISTS (SELECT 1 FROM application_events e WHERE e.application_id = a.id);

-- ── Step 8: last_event_at = MAX(recorded_at) of that application's events, ──
-- else updated_at.
UPDATE applications SET last_event_at = COALESCE(
    (SELECT MAX(e.recorded_at) FROM application_events e WHERE e.application_id = applications.id),
    updated_at
);
