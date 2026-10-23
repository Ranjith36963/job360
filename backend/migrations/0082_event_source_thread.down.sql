-- 0082 down: reverses the DDL only. Every stored thread id, including any
-- recorded after this migration, is dropped with its column and is NOT
-- recoverable except from a backup. The events themselves survive untouched;
-- match_email then loses its `same_thread` reason (the route must be reverted first).

DROP INDEX IF EXISTS idx_application_events_user_thread;

ALTER TABLE application_events DROP COLUMN IF EXISTS source_thread_id;
