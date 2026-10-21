-- 0050 down: reverses the DDL only. No other column is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every user's stored memory in the base
-- column (contact, right to work, logistics, languages, equality answers,
-- saved answers) is dropped and NOT recoverable except from a backup.
-- Memory the assistant wrote through the overlay stays in `profile_edits` as
-- inert `user_info.*` rows that nothing reads.

ALTER TABLE user_profiles DROP COLUMN IF EXISTS user_info;
