-- Reverse 0032. Dropping the index first: it references the column.
DROP INDEX IF EXISTS idx_applications_chase;
ALTER TABLE applications DROP COLUMN last_chased_at;
