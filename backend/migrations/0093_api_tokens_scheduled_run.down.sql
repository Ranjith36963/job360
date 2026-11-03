-- 0093 down: every scheduled-run token becomes a normal token again (its approvals are no longer refused).
ALTER TABLE api_tokens DROP COLUMN IF EXISTS for_scheduled_runs;
