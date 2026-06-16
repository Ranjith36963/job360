-- SQLite <3.35 can't DROP COLUMN cleanly; best-effort down (see 0014):
-- mark the schema version only. Production rollback restores from backup.
DROP TABLE IF EXISTS oauth_states;
DELETE FROM _schema_migrations WHERE id = '0019_channel_oauth';
