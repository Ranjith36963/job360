-- 0051 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every user's stored base settings and every
-- waiting / decided change request are dropped and NOT recoverable except from a
-- backup. Defaults return (ask each, confirm every submit, no cap, not paused).
-- Values set through the overlay stay in `profile_edits` as inert
-- `assistant_settings.*` rows that nothing reads.

DROP TABLE IF EXISTS assistant_setting_requests;
ALTER TABLE user_profiles DROP COLUMN IF EXISTS assistant_settings;
