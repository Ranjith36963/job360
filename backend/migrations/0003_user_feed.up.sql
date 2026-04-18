-- 0003_user_feed: per-user SSOT table that dashboard + notifications both
-- read from. See docs/plans/batch-2-plan.md Phase 3 and blueprint §3.

CREATE TABLE IF NOT EXISTS user_feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    bucket TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- 'active','skipped','stale','applied'
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id)
);

-- Dashboard query: user's active jobs by bucket and score
CREATE INDEX IF NOT EXISTS idx_feed_dashboard
    ON user_feed(user_id, bucket, score DESC)
    WHERE status = 'active';

-- Notification query: unnotified active jobs
CREATE INDEX IF NOT EXISTS idx_feed_notify
    ON user_feed(user_id, status, created_at)
    WHERE notified_at IS NULL AND status = 'active';

-- Cascade index: delete / mark-stale across all users for a given job
CREATE INDEX IF NOT EXISTS idx_feed_job ON user_feed(job_id);
