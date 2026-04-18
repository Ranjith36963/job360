-- 0000_baseline: record pre-Batch-2 schema as migration version 0.
-- No-op; the legacy init path in src/repositories/database.py creates
-- the pre-Batch-2 tables. This stem exists only so `status` reports
-- a coherent baseline and later migrations stay numbered from 0001.
SELECT 1;
