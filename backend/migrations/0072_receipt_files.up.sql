-- 0072_receipt_files: which FILES a receipt's application was sent with (gap roadmap slice 29).
--
-- The browser's upload and the kit's /api/files/{token} link each write a
-- server-only `document_uploaded` event {kind, artifact_id, version, sha256,
-- fmt, renderer_version, host} with the sha256 of the EXACT bytes served.
-- record_application freezes the uploads it used into the receipt:
--   files  JSON list [{kind, artifact_id, sha256, fmt, event_id}]; '[]' = no
--          upload was used (an older flow, or the caller named other versions).
-- Set at INSERT only (M3: receipts are append-only, never backfilled). A hash
-- only: no file bytes, no text, no file name, no link token.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless; every existing receipt reads '[]'.

ALTER TABLE application_receipts ADD COLUMN IF NOT EXISTS files TEXT NOT NULL DEFAULT '[]';
