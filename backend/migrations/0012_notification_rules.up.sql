-- 0012_notification_rules: per-user per-channel notification preferences
CREATE TABLE IF NOT EXISTS notification_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    score_threshold INTEGER NOT NULL DEFAULT 60,
    notify_mode TEXT NOT NULL DEFAULT 'instant'
        CHECK (notify_mode IN ('instant', 'digest')),
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    digest_send_time TEXT DEFAULT '08:00',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id, channel)
);

ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
