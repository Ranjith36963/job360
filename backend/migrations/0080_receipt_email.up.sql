-- 0080_receipt_email: who an EMAIL application went to (gap roadmap slice 31, G078).
--
-- Email applications bypassed the submit gate, and the receipt had nowhere to
-- say who the email was sent to. record_application now stores, at INSERT
-- (M3: receipts are append-only, never backfilled):
--   recipient         ONE email address, lower-cased ('' = not an email / not
--                     given). For channel email with none given, the `to` of
--                     the newest `email_drafted` since the last `applied`.
--   gmail_message_id  the sent message's Gmail id ('' = not given).
-- Never the email's subject or body.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless; every existing receipt reads ''.

ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS recipient TEXT NOT NULL DEFAULT '';
ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS gmail_message_id TEXT NOT NULL DEFAULT '';
