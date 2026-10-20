-- 0072_contact_relationship: outreach pack #12 (2026-10-20). How the user
-- knows this person (closed set CONTACT_RELATIONSHIP in src/core/settings.py)
-- and what they share: a pointer to a REAL profile record
-- ("cv_data.cv_positions[0]: <label read from the profile>"), checked by
-- contacts._validate_shared at write time — never invented. Both nullable,
-- unset = NULL (rule #29). The base row is append-only (S12); a later change
-- is a `contact_edits` row with field = 'relationship' / 'shared', exactly
-- like found_via (0049) and source_url (0067).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS relationship TEXT;
ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS shared TEXT;
