-- 0052 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every live file link is dropped (an
-- assistant simply asks for the kit again), and each receipt loses its
-- duplicate flag and the kit it was filled from. Nothing else changes.

DROP TABLE IF EXISTS artifact_links;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS possible_duplicate;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS kit_event_id;
ALTER TABLE application_receipts DROP COLUMN IF EXISTS kit_sha256;
