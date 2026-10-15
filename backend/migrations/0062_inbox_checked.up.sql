-- 0062_inbox_checked: when the user's assistant last read Gmail for this
-- application (owner decision, 2026-10-15 - the Gmail check slice).
--
-- WHY. The daily recipe said "read Gmail since your last run", but nothing
-- stored the last run, so every check started from scratch. The assistant
-- reads the mail (Job360 reads no email, VISION rule 5); Job360 only keeps
-- the cursor: the time up to which this application's mail was read.
--
-- A cache SLOT on applications, like follow_up_on - NOT history and NO event:
-- a check every few hours would flood the timeline and make every job look
-- active (quiet_days, whats_new). What the check FOUND is still recorded as
-- events with their email as `source`.
--
-- ISO-8601 TEXT like every other time column here (goes through
-- pg.translate()), nullable, no default (never checked = NULL, rule #29).
-- DDL ONLY, IF NOT EXISTS so a re-run is harmless.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS inbox_checked_at TEXT;
