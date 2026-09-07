-- 0041 down: reverses the DDL only. No pre-existing `application_events` row
-- or column is touched beyond the six added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: every stored email source and every
-- stored interview datetime — including any recorded AFTER this migration by
-- real product usage — is dropped with its columns and is NOT recoverable
-- except from a backup. The events themselves survive: `application_events`
-- keeps every row, `event_type`/`detail`/`payload`/`occurred_at` untouched;
-- only the six slice-6 columns and their unique index are gone. A rejection,
-- an interview request or a plain reply recorded through `record_event`
-- after this rollback goes back to carrying its email source as prose in
-- `detail`, same as before slice 6.

DROP INDEX IF EXISTS uq_application_events_app_source_msg;

ALTER TABLE application_events DROP COLUMN IF EXISTS scheduled_at;
ALTER TABLE application_events DROP COLUMN IF EXISTS source_received_at;
ALTER TABLE application_events DROP COLUMN IF EXISTS source_subject;
ALTER TABLE application_events DROP COLUMN IF EXISTS source_sender;
ALTER TABLE application_events DROP COLUMN IF EXISTS source_message_id;
ALTER TABLE application_events DROP COLUMN IF EXISTS source_kind;
