ALTER TABLE assistant_triggers DROP COLUMN IF EXISTS retry_until;
ALTER TABLE assistant_triggers DROP COLUMN IF EXISTS hour_count;
ALTER TABLE assistant_triggers DROP COLUMN IF EXISTS hour_started_at;
DROP TABLE IF EXISTS assistant_duty_triggers;
