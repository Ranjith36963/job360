-- 0054 down: drops the two ad-date columns. The stored dates are NOT
-- recoverable except from a backup. No other column or row is touched.
ALTER TABLE applications DROP COLUMN IF EXISTS employer_published_at;
ALTER TABLE applications DROP COLUMN IF EXISTS ad_posted_at;
