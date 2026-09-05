-- Reverse of 0040 — recreates notification_rules, notification_ledger,
-- user_channels, user_notification_digests, user_actions and user_feed.
--
-- READ THIS BEFORE RELYING ON IT: IT BRINGS BACK THE SCHEMA, NOT THE DATA.
-- The rows are gone; a forward migration's reverse restores the shape so the
-- runner's up -> down -> up cycle is clean and an operator can roll a deploy
-- back without a schema mismatch. If you need the ROWS, restore from the
-- daily backup (`.github/workflows/db-backup.yml`).
--
-- The column lists below are each table's FINAL shape (the last migration
-- that touched it before this one), not its original CREATE TABLE:
--   notification_rules         — 0012 + 0020 (collapsed to one row per user).
--   notification_ledger        — 0004, unchanged since.
--   user_channels               — 0005, unchanged since.
--   user_notification_digests  — 0013, unchanged since.
--   user_actions                — 0002 (added user_id, widened UNIQUE).
--   user_feed                   — 0003, unchanged since.
--
-- No code reads any of this. Nothing in `backend/src` will start writing to
-- these tables again just because they exist.

CREATE TABLE IF NOT EXISTS notification_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score_threshold INTEGER NOT NULL DEFAULT 60,
    notify_mode TEXT NOT NULL DEFAULT 'instant'
        CHECK (notify_mode IN ('instant', 'daily', 'every_n_hours')),
    interval_hours INTEGER NOT NULL DEFAULT 6,
    daily_send_time TEXT NOT NULL DEFAULT '08:00',
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    last_sent_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id)
);

CREATE TABLE IF NOT EXISTS notification_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_ledger_user_status ON notification_ledger(user_id, status);
CREATE INDEX IF NOT EXISTS idx_ledger_job ON notification_ledger(job_id);

CREATE TABLE IF NOT EXISTS user_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_encrypted BLOB NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_channels_user ON user_channels(user_id, enabled);

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

CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
        REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_user_actions_user ON user_actions(user_id);

CREATE TABLE IF NOT EXISTS user_feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    bucket TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_feed_dashboard
    ON user_feed(user_id, bucket, score DESC)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_feed_notify
    ON user_feed(user_id, status, created_at)
    WHERE notified_at IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_feed_job ON user_feed(job_id);
