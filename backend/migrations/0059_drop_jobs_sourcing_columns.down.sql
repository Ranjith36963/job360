-- 0059 down: put the twelve sourcing-era columns back, empty.
--
-- The data is NOT restored. Nothing has written these columns since slice 5
-- (#483), so every row returns to the default the column always carried.
-- Shape only — so an older revision of the code (and the 0011 / 0032 down
-- migrations, which copy or drop these columns) can run again. The score
-- columns keep their historical INTEGER DEFAULT 0; the GBP pair was always
-- nullable REAL.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS match_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS role INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS skill INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS seniority_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS experience INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS credentials INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS location_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS recency INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS semantic INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS penalty INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_min_gbp_annual REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_max_gbp_annual REAL;
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
