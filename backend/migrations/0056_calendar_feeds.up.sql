-- 0056_calendar_feeds: the private calendar feed (GET /api/calendar/{token}.ics).
-- One row per user: the SHA-256 of the feed token (the token itself is never stored)
-- is the whole credential. Regenerating DELETEs the row and INSERTs a new one - a
-- credential, not history. Erased with the account; never exported.
-- DDL ONLY, IF NOT EXISTS so a re-run is harmless.
CREATE TABLE IF NOT EXISTS calendar_feeds (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,         -- sha256 hex of the token
    created_at TEXT NOT NULL
);
