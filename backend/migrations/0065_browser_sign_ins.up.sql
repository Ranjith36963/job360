-- 0065_browser_sign_ins: the Job360 browser agent's saved sign-ins (slice 2,
-- docs/product/BROWSER_AGENT.md, VISION decision 29).
--
-- One row per (user, site): the cookies and site storage the user's own
-- sign-in left in the cloud browser, ENCRYPTED (Fernet, key from
-- BROWSER_SIGN_IN_KEY or derived from SESSION_SECRET). Never a password: the
-- user typed it into the site in the live view and Job360 kept only what the
-- site itself chose to remember. A SLOT, not history: signing in again
-- replaces the row; "Forget" deletes it.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS browser_sign_ins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    site_host TEXT NOT NULL,                 -- the host the user signed in on, lower case
    state_enc TEXT NOT NULL,                 -- Fernet token of the JSON {cookies, origins}
    cookie_count INTEGER NOT NULL DEFAULT 0, -- shown on the web, never a value
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, site_host)
);
CREATE INDEX IF NOT EXISTS idx_browser_sign_ins_user ON browser_sign_ins(user_id);
