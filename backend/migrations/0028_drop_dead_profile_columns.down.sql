-- Reverse of 0028 — restores the (always-empty) column shape. Nothing to
-- restore data-wise: no code ever wrote these, so re-adding them as NULL
-- columns returns the schema to its prior state exactly.
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS linkedin_data TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS github_data TEXT;
