-- Reverse 0031: recreate the OAuth handshake table.
--
-- HONEST LIMITS OF THIS ROLLBACK:
--   * Structure comes back. ROWS DO NOT. The deleted user_channels and
--     user_notification_digests rows are gone for good — a down migration
--     cannot invent an encrypted credential it never stored. Production held
--     zero of both when 0031 ran (see the up file), so nothing was lost there;
--     restoring a database that did hold them needs a backup, not this file.
--   * Recreating the table does NOT bring back the /connect and /callback
--     routes. Rolling back the schema without rolling back the code leaves an
--     empty table with no writers. That is intentional: the table is the easy
--     half to restore, and pretending otherwise would be worse than saying so.
--
-- Definition copied verbatim from 0019_channel_oauth.up.sql so a down-then-up
-- cycle lands on exactly the shape 0019 created.

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_created ON oauth_states(created_at);
