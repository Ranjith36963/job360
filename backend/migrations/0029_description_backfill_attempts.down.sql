-- Reverse of 0029 — drops the retry counter. No real data loss: dropping it
-- just means every job looks "never attempted" again on the next boot,
-- which is the safe direction for a rollback (worst case, a few jobs get
-- fetched once more before the column comes back).
ALTER TABLE jobs DROP COLUMN IF EXISTS description_backfill_attempts;
