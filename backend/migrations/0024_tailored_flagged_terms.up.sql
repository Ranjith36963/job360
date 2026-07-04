-- 0024 — surface possible-fabrication flags to the user (guardrail #2).
-- The proper-noun integrity pass (services/tailoring/integrity.py) records output
-- tokens that match nothing in the source CV/job (a possible fabrication). Store
-- them per document so the editor can warn the user to review before they apply.
-- IF NOT EXISTS: init_db() already mirrors this column, so on a fresh boot the
-- column can pre-exist; this keeps the runner idempotent either way.
ALTER TABLE tailored_documents ADD COLUMN IF NOT EXISTS flagged_terms TEXT DEFAULT '[]';
