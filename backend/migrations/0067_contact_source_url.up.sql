-- 0067_contact_source_url: outreach pack #2 (2026-10-20). The page where the
-- user's own assistant found this person (a team page, a press release),
-- first-class instead of buried in `notes`. Nullable, unset = NULL (rule #29
-- — Job360 invents no default). Stored only: Job360 never fetches it (M2),
-- so it is not an outbound surface. The base row is append-only (S12); a
-- later correction is a `contact_edits` row with field = 'source_url',
-- exactly like found_via (0049).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS source_url TEXT;
