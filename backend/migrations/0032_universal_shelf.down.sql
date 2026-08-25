-- Reverse of 0032 — drops all twelve Universal Shelf columns.
--
-- READ THIS BEFORE RUNNING IT: THIS DESTROYS REAL DATA.
--
-- The original text here said nothing wrote to these columns, because step 1
-- was written before steps 2 and 3 existed. They now ship together: source
-- mappers populate them (lever.py, recruitee.py, smartrecruiters.py and
-- others) and services/shelf_enrichment.py calls fill_shelves(), including
-- LLM-derived values that cost money to produce. Every one of those is gone
-- the moment this runs, and re-deriving the enriched ones means paying again.
--
-- Kept as a real reverse migration because a forward migration without one is
-- a door that only opens one way — but this is a deliberate act now, not the
-- free undo the old comment promised. (CodeRabbit, PR #388.)
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
