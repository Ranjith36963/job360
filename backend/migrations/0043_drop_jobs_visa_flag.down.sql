-- 0043 down: put the column back, empty.
--
-- The data is NOT restored — there was none to lose (no writer since slice 5,
-- #483), so every row returns to the `0` default the column always carried.
-- Shape only, so an older revision of the code that still SELECTs or INSERTs
-- `visa_flag` can run again.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS visa_flag INTEGER DEFAULT 0;
