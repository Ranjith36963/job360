-- 0073_receipt_referred_by: outreach pack #5 (2026-10-22).
-- application_receipts.referred_by_contact_id — the contact (linked to the same application)
-- whose referral got the user this application. Set ONLY at INSERT by spine.record_receipt
-- (receipts are append-only, M3); NULL = not said (rule #29).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a re-run is harmless.

ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS referred_by_contact_id INTEGER;
