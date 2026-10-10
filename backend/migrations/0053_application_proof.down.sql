-- 0053 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: every stored proof screenshot is erased for
-- good and every live upload link dies. The `proof_text` / `proof_screenshot`
-- events stay on the timelines (history is never rewritten), but the images
-- they point at are gone.

DROP TABLE IF EXISTS proof_upload_links;
DROP TABLE IF EXISTS application_proof_screenshots;
