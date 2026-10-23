-- 0081_contact_phone_links: outreach pack #14 (2026-10-20). A contact's phone
-- (stored normalised: an optional leading '+' then digits only; 7+ digits is an
-- identity for add_contact's no-email dedupe) and up to CONTACT_LINKS_MAX other
-- profile links (GitHub, a personal site): JSON text of a list of http(s) URLs.
-- Nullable, unset = NULL (rule #29). Stored only: Job360 never opens a link (M2)
-- and never dials. The base row is append-only (S12); a later correction is a
-- `contact_edits` row with field = 'phone' / 'links' ('' clears), exactly like
-- source_url (0067). The web-only Confirm mark needs no DDL: it is a
-- `contact_edits` row with field = 'confirmed', value = 'true'.
-- Never in the calendar feed (calendar_ics reads no contact table); the GDPR
-- export reads application_contacts and contact_edits with SELECT *, so both
-- columns are exported with no further change.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS links TEXT;
