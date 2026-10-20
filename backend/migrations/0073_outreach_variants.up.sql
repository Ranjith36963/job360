-- 0073_outreach_variants: outreach pack #13 (2026-10-20).
-- 1) contact_outreach.label — the caller's own name for the approach a MESSAGE
--    version used (e.g. a template's label). '' = not labelled (rule #29: stats
--    shows it as "Not set"). Set at INSERT on entry='message' only; sent/reply
--    marks keep ''. The table stays append-only (S12).
-- 2) user_templates — the user's reusable wording, NOT tied to a job or a person.
--    One row per version; a new save of the same label (lower(trim)) is the next
--    version_no. Append-only like application_artifacts (M3): nothing UPDATEs or
--    DELETEs it except account erasure. Job360 never fills, renders or sends one.
--    kind is a closed set (settings.USER_TEMPLATE_KINDS).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE contact_outreach ADD COLUMN IF NOT EXISTS label TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS user_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,          -- as given, trimmed (display)
    label_key TEXT NOT NULL,      -- lower(trim(label)): the identity versions hang off
    version_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    chars INTEGER NOT NULL,
    made_by TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, kind, label_key, version_no)
);
CREATE INDEX IF NOT EXISTS idx_user_templates_user_kind_label
    ON user_templates(user_id, kind, label_key, version_no DESC);
