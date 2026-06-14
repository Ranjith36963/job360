-- SQLite <3.35 can't DROP COLUMN cleanly; best-effort down (see 0014):
-- mark the schema version only. Production rollback restores from backup.
DELETE FROM _schema_migrations WHERE id = '0020_add_job_deadline';
