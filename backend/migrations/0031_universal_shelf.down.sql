-- Reverse of 0031 — drops all twelve Universal Shelf Step-1 columns. No real
-- data loss beyond the columns themselves: nothing in this step wrote to
-- them (no source mapper, no wired gate — see 0031's up.sql), so on the day
-- this rolls back in prod there is no live data behind these columns to lose.
-- If a later step's data DOES exist when this runs, that data is gone —
-- rolling back step 1 after step 2/3 have shipped needs a fresh plan, not
-- this file.
ALTER TABLE jobs DROP COLUMN IF EXISTS employment_type;
ALTER TABLE jobs DROP COLUMN IF EXISTS workplace_mode;
ALTER TABLE jobs DROP COLUMN IF EXISTS seniority;
ALTER TABLE jobs DROP COLUMN IF EXISTS category;
ALTER TABLE jobs DROP COLUMN IF EXISTS source_tags;
ALTER TABLE jobs DROP COLUMN IF EXISTS visa_status;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_currency;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_period;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_is_estimated;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_min_gbp_annual;
ALTER TABLE jobs DROP COLUMN IF EXISTS salary_max_gbp_annual;
ALTER TABLE jobs DROP COLUMN IF EXISTS shelf_provenance;
