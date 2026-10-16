-- 0064_fit_patterns: the assistant's richer fit record (feedback slice 5).
--
-- All columns are on `applications` - the USER's own row - never on the
-- shared `jobs` catalog (hard rule #10), so the ad's requirements
-- (fit_jd_requirements) live here too.
--   fit_score_source    TEXT      who/what produced the score: full_ad | listing | owner
--   fit_gaps_detail     TEXT      JSON [{text, kind}] (kind hard | soft | null); NULL = a fit saved before this column
--   fit_jd_requirements TEXT      JSON [{text, kind}] read off the ad by the assistant
--   fit_archetype       TEXT      the assistant's short name for the role type
--   fit_remote_policy   TEXT      remote | hybrid | onsite
--   fit_team_size       INTEGER   whole-number headcount, >= 1
--   fit_via             TEXT      agency | direct
--
-- Nullable, unset = NULL (rule #29): Job360 invents no default.
-- A SLOT: overwritten by every save_fit; every value also lives in the
-- append-only `fit_judged` event payload, which is the history.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_score_source TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_gaps_detail TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_jd_requirements TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_archetype TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_remote_policy TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_team_size INTEGER
    CHECK (fit_team_size IS NULL OR fit_team_size >= 1);
ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_via TEXT;
