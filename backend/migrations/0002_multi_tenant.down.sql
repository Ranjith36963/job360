-- Reverse 0002_multi_tenant: drop user_id + narrow UNIQUE.

DROP INDEX IF EXISTS idx_user_actions_user;
CREATE TABLE user_actions_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(job_id)
);
INSERT OR IGNORE INTO user_actions_old (id, job_id, action, notes, created_at)
SELECT id, job_id, action, notes, created_at FROM user_actions;
DROP TABLE user_actions;
ALTER TABLE user_actions_old RENAME TO user_actions;

DROP INDEX IF EXISTS idx_applications_user;
CREATE TABLE applications_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'applied',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id)
);
INSERT OR IGNORE INTO applications_old
    (id, job_id, stage, notes, created_at, updated_at)
SELECT id, job_id, stage, notes, created_at, updated_at FROM applications;
DROP TABLE applications;
ALTER TABLE applications_old RENAME TO applications;

DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000001';
