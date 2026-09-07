-- 0041_event_source_and_schedule: slice 6 of the mission roadmap
-- (docs/plans/2026-09-07-email-evidence/spec.md §Data model).
--
-- WHY. An event can now name the email it came from (R1) so a re-read inbox
-- adds zero rows (R2), and an interview event can carry a real datetime (R3)
-- instead of prose in `detail`. Six columns on `application_events` — never a
-- new table, since a source/schedule is a property of the ONE event that
-- named it, not a fact worth its own history.
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0038: TEXT NOT NULL DEFAULT '' ('' = none, no NULL branch needed
-- anywhere), IF NOT EXISTS, partial UNIQUE index for the idempotency rule —
-- the same non-empty `source_message_id` on the same application is the same
-- event; a message id with no source (`''`) never collides with another.

ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_kind        TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_message_id  TEXT NOT NULL DEFAULT '';  -- '' = no source
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_sender      TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_subject     TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_received_at TEXT NOT NULL DEFAULT '';  -- ISO +00:00 or ''
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS scheduled_at       TEXT NOT NULL DEFAULT '';  -- ISO +00:00 or ''

CREATE UNIQUE INDEX IF NOT EXISTS uq_application_events_app_source_msg
    ON application_events(application_id, source_message_id) WHERE source_message_id <> '';
