-- 0038 down: reverses the DDL only. Nothing outside the two new tables was
-- touched by the up file, so nothing else moves.
--
-- WHAT THIS COSTS, STATED PLAINLY: every contact and every profile edit
-- recorded after the up-migration is dropped with its table and is NOT
-- recoverable except from a backup. The `contact_added` events stay in
-- `application_events` (their `payload.contact_id` then points nowhere), and
-- the web/MCP profile silently returns to what extraction says.

DROP TABLE IF EXISTS profile_edits;
DROP TABLE IF EXISTS application_contacts;
