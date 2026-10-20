-- 0066_assistant_triggers: "Send starts my assistant" (browser agent, docs/product/BROWSER_AGENT.md).
--
-- One row per user: the address Job360 calls when the user presses Send / Send all on
-- Needs you, e.g. a Claude Code routine's API trigger
-- (POST https://api.anthropic.com/v1/claude_code/routines/<trig_id>/fire). The bearer token
-- is ENCRYPTED (Fernet, the same key as the browser sign-ins) and never read back to any
-- client. Set and changed only from the website (a browser session, never a token), so no
-- assistant can point Send somewhere else. A SLOT: saving replaces it; the user can delete it.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS assistant_triggers (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,                       -- https only; screened by the SSRF guard on every call
    token_enc TEXT NOT NULL DEFAULT '',      -- Fernet token of {"token": ...}; '' = no token
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_fired_at TEXT,                      -- the last time Send started the assistant
    last_result TEXT NOT NULL DEFAULT ''     -- started | rate_limited | refused | failed: <status>
);
