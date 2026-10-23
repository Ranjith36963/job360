-- 0085 down: reverses the DDL only. Every stored quote, including any recorded
-- after this migration, is dropped with its column and is NOT recoverable except
-- from a backup. The events themselves survive untouched (an outcome_suggested
-- keeps its own payload.quote). Revert the code that reads source_quote first.

ALTER TABLE application_events DROP COLUMN IF EXISTS source_quote;
