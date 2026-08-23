-- 0023 — Per-User AI CV & Cover Letter (docs/product/peruser_cv_coverletter.md)
--
-- Three tables:
--   tailored_documents  — the generated CV / cover letter per (user, job, kind),
--                         holding BOTH the AI draft and the user's polished final
--                         (the draft->final diff is the learning signal, spec §5).
--   tailored_usage      — append-only generation-event log for the monthly quota
--                         gate (spec guardrail #1: paid/capped). One row per call.
--   tailoring_patterns  — Layer-1 universal store (spec §6). PATTERNS ONLY, no
--                         user_id, no document text — privacy-safe (spec §7).

CREATE TABLE IF NOT EXISTS tailored_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    doc_kind TEXT NOT NULL,                 -- 'cv' | 'cover_letter'
    ai_draft TEXT NOT NULL DEFAULT '',      -- the original AI generation (never overwritten by edits)
    polished TEXT,                          -- the user's edited final (NULL until edited)
    status TEXT NOT NULL DEFAULT 'draft',   -- 'draft' | 'kept' (kept = finalized/downloaded/used)
    model TEXT,                             -- which LLM produced the draft
    profile_version INTEGER,                -- which user_profile_versions snapshot fed the draft
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    kept_at TEXT,
    UNIQUE(user_id, job_id, doc_kind)
);
CREATE INDEX IF NOT EXISTS idx_tailored_user_kind_status
    ON tailored_documents(user_id, doc_kind, status);
CREATE INDEX IF NOT EXISTS idx_tailored_user_job
    ON tailored_documents(user_id, job_id);

CREATE TABLE IF NOT EXISTS tailored_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tailored_usage_user_time
    ON tailored_usage(user_id, created_at);

-- Universal layer: PATTERNS ONLY. Deliberately NO user_id and NO document text —
-- only privacy-scrubbed structural features (counts/ratios/labels). Never joins
-- back to a person (spec §7 "learn the pattern, not the person").
CREATE TABLE IF NOT EXISTS tailoring_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_kind TEXT NOT NULL,                 -- 'cv' | 'cover_letter'
    features TEXT NOT NULL DEFAULT '{}',    -- JSON: structural features only, no PII/content
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tailoring_patterns_kind
    ON tailoring_patterns(doc_kind);
