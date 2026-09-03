-- 0035 down: every personal token stops working the moment this runs.
DROP INDEX IF EXISTS idx_api_tokens_user;
DROP TABLE IF EXISTS api_tokens;
