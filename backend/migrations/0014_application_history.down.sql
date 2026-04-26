DROP TABLE IF EXISTS application_stage_history;
-- SQLite <3.35 doesn't support DROP COLUMN cleanly; we recreate the table
-- For down migration, we do a best-effort approach:
-- The added columns (last_advanced_at, interview_dates, notes_history) cannot be
-- easily removed in SQLite; the down migration marks the schema version only.
-- Production rollback should restore from backup.
DELETE FROM _schema_migrations WHERE id = '0014_application_history';
