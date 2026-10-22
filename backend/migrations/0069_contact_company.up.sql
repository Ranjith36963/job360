-- 0069_contact_company: outreach pack #4 (2026-10-20). The employer a contact
-- works at, as the user's assistant gave it (display text, trimmed). Nullable,
-- unset = NULL (rule #29). Required by add_contact only for a COLD contact (no
-- application_id); a linked contact without one counts at its application's
-- job_company, read at query time and never copied here. Matching uses
-- kit.normalized_company_key (rule #1), computed at read, so no key column.
-- The base row is append-only (S12): a later value is a `contact_edits` row
-- with field = 'company', exactly like found_via (0049) and source_url (0067).
--
-- DDL ONLY. No existing row is read, copied or changed (no backfill). IF NOT
-- EXISTS so a re-run is harmless.

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS company TEXT;
