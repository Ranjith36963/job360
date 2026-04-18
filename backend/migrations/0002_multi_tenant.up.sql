-- 0002_multi_tenant: add user_id scoping to per-user tables.
--
-- Design (see docs/plans/batch-2-decisions.md §D6, and plan Phase 2):
-- * `jobs` is a shared catalog (one row per unique company+title across all
--   users). It is NOT modified here — Batch 1 owns its schema, CLAUDE.md
--   rule #1 forbids touching its uniqueness key.
-- * `user_actions` and `applications` become per-user. We widen their
--   UNIQUE(job_id) to UNIQUE(user_id, job_id) so two users can separately
--   like/apply to the same job.
-- * Existing rows migrate to the default tenant (well-known UUID
--   00000000-0000-0000-0000-000000000001), represented by a placeholder
--   user whose password_hash is "!" (argon2 never produces "!" → unloginable).

-- 1. Ensure the placeholder user exists for backfill.
INSERT OR IGNORE INTO users(id, email, password_hash)
VALUES ('00000000-0000-0000-0000-000000000001',
        'local@job360.local',
        '!');

-- 2. user_actions — rebuild to add user_id + widen UNIQUE.
--    SQLite cannot ALTER an existing UNIQUE constraint; rebuild pattern required.
CREATE TABLE user_actions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
        REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);
INSERT INTO user_actions_new (id, user_id, job_id, action, notes, created_at)
SELECT id,
       '00000000-0000-0000-0000-000000000001',
       job_id, action, notes, created_at
FROM user_actions;
DROP TABLE user_actions;
ALTER TABLE user_actions_new RENAME TO user_actions;
CREATE INDEX IF NOT EXISTS idx_user_actions_user ON user_actions(user_id);

-- 3. applications — same pattern.
CREATE TABLE applications_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
        REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'applied',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);
INSERT INTO applications_new
    (id, user_id, job_id, stage, notes, created_at, updated_at)
SELECT id,
       '00000000-0000-0000-0000-000000000001',
       job_id, stage, notes, created_at, updated_at
FROM applications;
DROP TABLE applications;
ALTER TABLE applications_new RENAME TO applications;
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);
