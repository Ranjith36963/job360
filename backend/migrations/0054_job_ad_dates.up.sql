-- 0054_job_ad_dates: two optional ad dates the ASSISTANT reads off the ad and
-- Job360 stores (nothing is inferred). `ad_posted_at` = the date the ad or
-- listing says it was posted; `employer_published_at` = the date on the
-- employer's own posting. Both ISO YYYY-MM-DD, NULL = not said (rule #29).
-- On `applications` (the USER's snapshot), never on the shared `jobs`
-- catalog (hard rule #10) - same reasoning as 0049. DDL only; IF NOT EXISTS.
ALTER TABLE applications ADD COLUMN IF NOT EXISTS ad_posted_at TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS employer_published_at TEXT;
