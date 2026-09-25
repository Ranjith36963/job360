-- 0045_follow_up: a follow-up date on an application (owner decision, 2026-09-25).
--
-- WHY. Job360 stores dates and serves "what's due" — the user's own agent
-- (a scheduled ChatGPT/Claude task with its Gmail connector) does the daily
-- email check and writes back through the existing MCP tools (record_event,
-- list_applications). Job360 reads no email, runs no worker, sends no push
-- (VISION rule 4/5).
--
-- One TEXT column on the applications SLOT, like every other date/time
-- column here (goes through pg.translate()): ISO YYYY-MM-DD, nullable, no
-- default (unset = no follow-up, never a sentinel date). Every set/clear
-- also appends an `application_events` row carrying `payload.follow_up_on`
-- (a `note`-family event — no new event type), so the whole history survives
-- even though this column is a cache SLOT, not history (S7).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS follow_up_on TEXT;
