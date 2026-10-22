-- 0074_contact_email_source: outreach pack #7 (2026-10-20). Where the contact's
-- EMAIL ADDRESS came from (closed set CONTACT_EMAIL_SOURCES in src/core/settings.py:
-- they_emailed | on_company_page | provider_verified | pattern_guess | user_gave),
-- separate from found_via (where the PERSON was found). Nullable, unset = NULL
-- (rule #29). The base row is append-only (S12); a later change is a
-- `contact_edits` row with field = 'email_source', exactly like found_via (0049)
-- and source_url (0067).
--
-- The `bounced` outreach entry needs no column: contact_outreach.entry is free
-- TEXT (closed in code: settings.OUTREACH_ENTRIES), a bounce row's `text` holds
-- the address that bounced, and uq_contact_outreach_source (0046) already makes
-- the bounce email's message id idempotent per contact. The partial index below
-- serves check_outreach's "did this address bounce for this user" lookup.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS email_source TEXT;
CREATE INDEX IF NOT EXISTS idx_contact_outreach_bounced
    ON contact_outreach(user_id, text) WHERE entry = 'bounced';
