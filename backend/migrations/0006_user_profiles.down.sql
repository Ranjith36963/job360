-- 0006_user_profiles DOWN — drop the table, losing per-user profile rows.
-- Legacy `data/user_profile.json` is NOT restored here; that file was
-- one-shot migrated into this table on first load in Batch 3.5.2 and
-- deleted. A revert should be accompanied by restoring the JSON from
-- backup if single-user fallback is desired.

DROP TABLE IF EXISTS user_profiles;
