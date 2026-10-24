-- 0087 down: reverses the DDL only.
ALTER TABLE application_asks DROP COLUMN IF EXISTS answers;
ALTER TABLE application_asks DROP COLUMN IF EXISTS questions;
ALTER TABLE application_asks DROP COLUMN IF EXISTS kind;
