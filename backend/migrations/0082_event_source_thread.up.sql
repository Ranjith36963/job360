-- 0082_event_source_thread: feature 8 (G088) - an event's email source can name its THREAD.
--
-- WHY. A recruiter's reply arrives in the same mail thread as the message we
-- already recorded. With the thread id stored, `match_email` can name the
-- application that thread was recorded on (reason `same_thread`) instead of
-- the assistant re-scoring company and sender every time.
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0041: TEXT NOT NULL DEFAULT '' ('' = none), IF NOT EXISTS. The index is
-- per USER (a thread can span several of the user's events, and match_email
-- looks across all their applications) and NOT unique: many events share a thread.

ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_thread_id TEXT NOT NULL DEFAULT '';  -- '' = no thread

CREATE INDEX IF NOT EXISTS idx_application_events_user_thread
    ON application_events(user_id, source_thread_id) WHERE source_thread_id <> '';
