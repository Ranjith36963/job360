-- 0075_inbox_items: feature 10 (G289) - mail the user's assistant read but could not tie to ONE
-- application. Stored once per (user, message id) so a later run skips it and the user is not asked
-- twice; the user resolves it on Needs you (pick the application, or dismiss), or the assistant
-- records the event for it (which resolves it).
--
-- APPEND-ONLY ROWS. A row is never deleted (except account erasure) and its message facts are never
-- updated. resolution/application_id/resolved_by/resolved_at are a WRITE-ONCE slot: the only UPDATE
-- is `... WHERE resolution = ''` (guard: tests/test_record_events.py).
--
-- DDL ONLY. Conventions from 0048/0041: TEXT ISO timestamps, '' = none, IF NOT EXISTS, every read
-- filters user_id by hand.

CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'email',          -- APPLICATION_EVENT_SOURCE_KINDS
    message_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',          -- '' = none
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT '',        -- '' = unknown
    recorded_by TEXT NOT NULL,                   -- actor_for(user)
    created_at TEXT NOT NULL,
    resolution TEXT NOT NULL DEFAULT '',         -- '' open | matched | dismissed (set once)
    application_id INTEGER,                      -- set with resolution = matched
    resolved_by TEXT,
    resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_inbox_items_user_message ON inbox_items(user_id, message_id);
CREATE INDEX IF NOT EXISTS idx_inbox_items_user_resolution ON inbox_items(user_id, resolution);
