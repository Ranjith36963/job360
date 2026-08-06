-- Postgres can DROP COLUMN cleanly, but house style for user_feed adds (0014,
-- 0018) is a registry-only rollback so a down-migration can never destroy
-- per-user scoring state. Production rollback restores from backup.
DELETE FROM _schema_migrations WHERE id = '0026_user_feed_scorer_version';
