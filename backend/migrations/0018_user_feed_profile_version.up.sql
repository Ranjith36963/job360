-- Stamps which user_profile_versions.id produced the row's score/verdict.
-- NULL = legacy/unscored row (profile version unknown at write time).
ALTER TABLE user_feed ADD COLUMN profile_version INTEGER;
