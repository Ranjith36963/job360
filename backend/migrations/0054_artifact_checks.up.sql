-- 0054_artifact_checks: the deterministic save-time checks (services/applications/
-- cv_checks.py) ride on the artifact VERSION they were run against, as JSON text
-- [{"code","severity","detail"}]. NULL = never checked (every row saved before this).
-- Written once at INSERT time; never updated. DDL ONLY, IF NOT EXISTS so a re-run is harmless.
ALTER TABLE application_artifacts ADD COLUMN IF NOT EXISTS checks_json TEXT;
