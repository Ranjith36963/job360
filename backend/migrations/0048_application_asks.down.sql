-- 0048 down: drops the asks table (every ask and answer is lost) and the
-- corrects_event_id index.
DROP INDEX IF EXISTS idx_application_events_corrects;
DROP INDEX IF EXISTS idx_application_asks_application;
DROP INDEX IF EXISTS idx_application_asks_user_answered;
DROP TABLE IF EXISTS application_asks;
