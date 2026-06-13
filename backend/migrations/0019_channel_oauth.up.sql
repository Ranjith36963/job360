-- 0019_channel_oauth: OAuth flow state table + connection-status metadata.
--
-- connection_status: 'connected' for all rows (OAuth success or manual paste).
--   Existing rows default to 'connected' — they are live channels.
-- target_label: human-readable channel name returned by the provider
--   (e.g. "#jobs"). NULL for manually-added channels.
-- oauth_states: one-time-use nonces linking an OAuth authorization redirect
--   back to the user who initiated it. Rows are deleted on callback (success
--   or failure). Created_at indexed for a future expiry sweep.

ALTER TABLE user_channels ADD COLUMN connection_status TEXT NOT NULL DEFAULT 'connected';
ALTER TABLE user_channels ADD COLUMN target_label TEXT;

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_created ON oauth_states(created_at);
