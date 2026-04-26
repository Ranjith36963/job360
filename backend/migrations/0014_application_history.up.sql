-- 0014_application_history: stage history + interview dates + notes versioning
ALTER TABLE applications ADD COLUMN last_advanced_at TEXT;
ALTER TABLE applications ADD COLUMN interview_dates TEXT DEFAULT '[]';  -- JSON array of ISO date strings
ALTER TABLE applications ADD COLUMN notes_history TEXT DEFAULT '[]';    -- JSON array of {note, timestamp}

CREATE TABLE IF NOT EXISTS application_stage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    from_stage TEXT,      -- NULL for initial 'applied' entry
    to_stage TEXT NOT NULL,
    transitioned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    notes TEXT,           -- optional note at time of transition
    FOREIGN KEY (job_id, user_id) REFERENCES applications(job_id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stage_history_job_user
    ON application_stage_history(job_id, user_id);
