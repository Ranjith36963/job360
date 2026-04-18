-- Reversing the baseline is a no-op; destructive teardown would drop
-- every pre-Batch-2 table. If you really need that, delete the DB file.
SELECT 1;
