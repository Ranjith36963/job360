-- Reverse 0020: rebuild the per-channel table. Best-effort -- the single
-- per-user rule is restored as one row with channel='all'.
CREATE TABLE notification_rules_old (
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
INSERT INTO notification_rules_old
    (user_id, channel, score_threshold, notify_mode, quiet_hours_start,
     quiet_hours_end, digest_send_time, enabled, created_at, updated_at)
SELECT user_id, 'all', score_threshold,
       CASE notify_mode WHEN 'daily' THEN 'digest'
                        WHEN 'every_n_hours' THEN 'digest' ELSE 'instant' END,
       quiet_hours_start, quiet_hours_end, daily_send_time, enabled,
       created_at, updated_at
FROM notification_rules;
DROP TABLE notification_rules;
ALTER TABLE notification_rules_old RENAME TO notification_rules;
