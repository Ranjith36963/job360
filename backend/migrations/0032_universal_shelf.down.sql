-- Reverse of 0032 — drops all twelve Universal Shelf columns.
--
-- READ THIS BEFORE RUNNING IT: THIS DESTROYS REAL DATA.
--
-- These columns hold values the deleted sourcing pipeline wrote, including
-- LLM-derived ones that cost money to produce. Every one of them is gone the
-- moment this runs, and nothing in the codebase can re-derive them any more
-- (slice 5, #483, removed the writers).
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
