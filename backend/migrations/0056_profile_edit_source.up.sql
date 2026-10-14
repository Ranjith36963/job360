-- 0056_profile_edit_source: feature 9 (confirmed vs inferred facts). `set_by` records who
-- WROTE a profile fact, not whether the user said it. `source` records that:
-- 'user_said' | 'agent_inferred' | '' (a row written before this migration = nobody
-- confirmed it). Written once at INSERT time; never updated. DDL ONLY, metadata-only,
-- IF NOT EXISTS so a re-run is harmless.
ALTER TABLE profile_edits ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT '';
