-- 0071_receipt_answers_from: where a receipt's answers came from (gap roadmap slice 11).
--
-- record_application with no `answers` now freezes the answers of the newest
-- form_filled / form_swept of that application into the receipt. Two facts
-- about that freeze, both set at INSERT (M3: receipts are append-only, never
-- backfilled):
--   answers_from   '<event_type>#<event_id>' the answers were copied from;
--                  '' = the caller passed them, or nothing was frozen.
--   answers_count  how many answers that event says were typed (its own
--                  `answers_count`, else the length of its list); NULL = not
--                  frozen. The counts line reads it when the list is empty, so
--                  a receipt never says "0 of 11" when 11 were filled.
-- Neither fits an existing JSON column: `answers` is the list itself, and every
-- key of `fields_filled` is counted as a filled field.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless; every existing receipt reads
-- '' / NULL (nothing frozen).

ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS answers_from TEXT NOT NULL DEFAULT '';
ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS answers_count INTEGER;
