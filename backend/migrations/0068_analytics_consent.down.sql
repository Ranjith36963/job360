-- 0068 down (renumbered from 0067): reverses the DDL only. No other `users` column is touched.
--
-- WHAT THIS COSTS, STATED PLAINLY: every user's "Help improve Job360" choice
-- is forgotten, so everyone reads as NOT consented (the safe direction):
-- server analytics and session replay stay off until they turn it on again.

ALTER TABLE users DROP COLUMN IF EXISTS analytics_consent_at;
