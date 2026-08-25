-- 0033_run_log_enrichment_stats: reverse migration.
--
-- Drops the enrichment spend counter. Losing it means losing the only record
-- of what JOB SOURCE ENRICHMENT cost — the rows themselves (run_uuid LIKE
-- 'shelf-enrichment-%') survive, but with an empty stats payload.
ALTER TABLE run_log DROP COLUMN IF EXISTS enrichment_stats;
