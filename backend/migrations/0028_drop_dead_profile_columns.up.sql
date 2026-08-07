-- 0028_drop_dead_profile_columns: remove two columns nothing ever wrote.
--
-- WHY (2026-08-07). Migration 0006 created `user_profiles.linkedin_data` and
-- `user_profiles.github_data` for a design where each input was stored in its
-- own column. That design was superseded before it shipped: the two-pass
-- extractor merges every input into ONE `cv_data` JSON document (linkedin_*
-- and github_* fields inside it), which is what every reader and writer
-- actually uses. `storage.save_profile` writes exactly (user_id, cv_data,
-- preferences, updated_at) — these two columns have never been written by any
-- code path, and prod confirms it: 0 of 3 rows populated, verified twice.
--
-- Dead schema is not harmless. It actively misleads: an architecture review of
-- this system read the empty columns as "LinkedIn and GitHub data is unused"
-- and recommended normalising both into new tables — a conclusion that was
-- exactly backwards, since that data drives skill tiering, the judge prompt
-- and the embedding. A future session could equally decide to "fix" the gap by
-- writing to them, creating the second write path (and the desync) the funnel
-- work has spent days eliminating.
--
-- No data is lost: the columns are, and always have been, entirely NULL.

ALTER TABLE user_profiles DROP COLUMN IF EXISTS linkedin_data;
ALTER TABLE user_profiles DROP COLUMN IF EXISTS github_data;
