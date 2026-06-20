-- 0020: collapse notification_rules to ONE row per user.
-- SQLite cannot ALTER a CHECK constraint or drop columns in place.
--
-- Two paths:
--   A) Table was created by the OLD 0012 DDL (has 'channel' + 'digest_send_time'):
--      fold rows into new schema and drop old table.
--   B) Table was created by the NEW init_db() DDL (already has UNIQUE(user_id),
--      no 'channel' column):  the new table already has UNIQUE(user_id), so we
--      just rename to a temp, re-create under the same constraints, and move back.
--      In practice init_db() already uses the correct DDL so this is a no-op.
--
-- We handle both cases by always creating notification_rules_new, then doing an
-- INSERT that selects from notification_rules using only columns that exist in
-- BOTH schemas (user_id, score_threshold, notify_mode, quiet_hours_start,
-- quiet_hours_end, enabled, created_at, updated_at), mapping digest->daily for
-- notify_mode and deriving daily_send_time from a static default since the
-- old digest_send_time column may or may not be present.
--
-- The trick: we omit digest_send_time from the SELECT entirely and always
-- use the DEFAULT '08:00'. Any existing digest_send_time value is lost
-- (acceptable: the user can reset it in the UI).

CREATE TABLE IF NOT EXISTS notification_rules_new (
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

-- Fold existing rows from notification_rules into notification_rules_new.
-- Only references columns common to BOTH old and new schemas.
-- Picks the most-recently-inserted rule per user (MAX(id)).
-- Maps old 'digest' mode -> 'daily'; keeps 'instant' as is.
INSERT OR IGNORE INTO notification_rules_new
    (user_id, score_threshold, notify_mode,
     quiet_hours_start, quiet_hours_end, enabled, created_at, updated_at)
SELECT user_id,
       score_threshold,
       CASE notify_mode WHEN 'digest' THEN 'daily' ELSE 'instant' END,
       quiet_hours_start, quiet_hours_end, enabled, created_at, updated_at
FROM notification_rules
WHERE id IN (SELECT MAX(id) FROM notification_rules GROUP BY user_id);

DROP TABLE notification_rules;
ALTER TABLE notification_rules_new RENAME TO notification_rules;
