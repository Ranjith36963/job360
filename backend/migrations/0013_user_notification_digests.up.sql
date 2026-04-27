-- 0013_user_notification_digests: queue for pending digest notifications
CREATE TABLE IF NOT EXISTS user_notification_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    queued_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    sent INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_digests_user_channel_pending
    ON user_notification_digests(user_id, channel, sent);
