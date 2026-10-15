-- 0061_ask_confirmed_at: a chat answer an assistant relayed (0060 user_words) can
-- be CONFIRMED by the user on the web. confirmed_at is a slot on the ask (asks
-- are not the spine event log): set when the user clicks Confirm, never cleared.
-- An answer counts as confirmed only while confirmed_at >= answered_at, so a
-- later changed answer reads unconfirmed again without rewriting anything.
-- DDL ONLY, metadata-only, IF NOT EXISTS so a re-run is harmless.
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS confirmed_at TEXT;
