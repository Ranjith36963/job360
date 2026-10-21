-- 0050_user_info_memory: owner decision 2026-10-08 — USER INFO MEMORY lives in
-- its own place (three stores: the CV/preferences base, the user_info memory,
-- and the profile_edits overlay on top of both).
--
-- `user_profiles.user_info` holds the facts job forms ask (contact, per-country
-- work rights and logistics, languages, equality answers, approved free-text
-- answers) as one JSON object. Plain storage — the owner chose not to encrypt.
-- It is written by ONE function only (storage.save_user_info); save_profile
-- never touches it, so a re-extraction or a version restore cannot wipe it.
-- `user_profile_versions` gets NO new column: memory is not part of a snapshot.
--
-- DDL ONLY. No existing row is read, copied or changed; every existing row
-- gets '{}' ("nothing answered", rule #29). IF NOT EXISTS so a re-run is
-- harmless.

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS user_info TEXT NOT NULL DEFAULT '{}';
