-- 0086 down: drops every stored unmatched-mail item and its resolution (not recoverable except from
-- a backup). Events are untouched. Revert the record_events route and tool first.
DROP INDEX IF EXISTS idx_inbox_items_user_resolution;
DROP INDEX IF EXISTS uq_inbox_items_user_message;
DROP TABLE IF EXISTS inbox_items;
