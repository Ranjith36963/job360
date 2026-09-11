-- 0042_visa_signal: slice 7 of the mission roadmap
-- (docs/plans/2026-09-11-visa-signal/spec.md §The two facts).
--
-- WHY. The agent judges from the ad whether the employer sponsors visas;
-- Job360 stores that judgement on the application and compares it with the
-- candidate's own list of countries where they need no sponsorship. No
-- country rule lives anywhere in this codebase — five columns, all text.
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0041: TEXT NOT NULL DEFAULT ('unknown' / ''), IF NOT EXISTS. A slot
-- like the fit verdict (spec S7) — overwritten, not history; the
-- `fit_judged` event payload carries the value when it arrives via save_fit.
--
-- The dead `jobs.visa_flag` column (sourcing era) is NOT touched here: eight
-- source files and four test files still name it; dropping it is its own
-- cleanup migration.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS visa_signal       TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS visa_detail       TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS visa_country      TEXT NOT NULL DEFAULT '';  -- ISO 3166-1 alpha-2 upper, or ''
ALTER TABLE applications ADD COLUMN IF NOT EXISTS visa_recorded_by  TEXT NOT NULL DEFAULT '';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS visa_recorded_at  TEXT NOT NULL DEFAULT '';
