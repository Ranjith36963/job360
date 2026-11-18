-- 0094 down: the saved LinkedIn state is dropped; the slot row itself stays (its done_at is harmless).
ALTER TABLE assistant_duty_runs DROP COLUMN IF EXISTS state;
