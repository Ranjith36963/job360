-- Reverse of 0030 — drops the snapshot id column. No data loss beyond the
-- id string itself: the underlying cv_data/preferences snapshot rows are
-- untouched, so a rollback just means version rows go back to being
-- identified by their bare `id` only, exactly as before this migration.
ALTER TABLE user_profile_versions DROP COLUMN IF EXISTS snapshot_id;
