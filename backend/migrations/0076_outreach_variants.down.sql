-- 0076 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every saved template version and every
-- outreach message label is dropped and NOT recoverable except from a backup.

DROP INDEX IF EXISTS idx_user_templates_user_kind_label;
DROP TABLE IF EXISTS user_templates;
ALTER TABLE contact_outreach DROP COLUMN IF EXISTS label;
