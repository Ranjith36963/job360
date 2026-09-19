-- 0043_drop_jobs_visa_flag: retire the dead `jobs.visa_flag` column (#571).
--
-- WHY. `visa_flag` was a sourcing-era artefact: a scraper guessed from the ad
-- whether the employer sponsored a visa and stored one boolean on the shared
-- catalog row. That pipeline was deleted in slice 5 (#483, 2026-09-05), so
-- nothing has written the column since — every brought job has carried the
-- `0` default. A column no writer fills is a lie the API keeps repeating.
--
-- The REAL signal is `applications.visa_signal` (migration 0042): five text
-- columns on the per-user application, recorded by the seeker's own agent. It
-- is NOT touched here. 0042 deliberately deferred this drop to its own
-- cleanup migration; this is that migration.
--
-- DDL ONLY, and lossless in practice: the column holds nothing but defaults.
-- IF EXISTS so a database that never had the column still migrates cleanly.

ALTER TABLE jobs DROP COLUMN IF EXISTS visa_flag;
