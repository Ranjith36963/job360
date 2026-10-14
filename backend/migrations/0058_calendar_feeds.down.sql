-- 0058 down: drops the table. Every calendar feed URL stops working (404);
-- the user mints a new one. No history is lost - the feed only ever read events.
DROP TABLE IF EXISTS calendar_feeds;
