-- 0088_assistant_duty_triggers: Ignition (owner 2026-10-24) - a press in Job360 starts the user's
-- assistant at once, one start link per duty, instead of waiting for the next hourly run.
--
-- `batch` (Apply) keeps using the Send link in assistant_triggers (0066); this table holds the
-- other duties' links: hunt, inbox, reach. Same rules as 0066: https only (SSRF guard on every
-- call), the token ENCRYPTED and never read back, set / changed / fired only from the website.
-- Both tables gain an hourly cap window and the time a 429 Retry-After asked us to wait until.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS assistant_duty_triggers (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    duty TEXT NOT NULL,                      -- hunt | inbox | reach (closed set, checked in code)
    url TEXT NOT NULL,
    token_enc TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_fired_at TEXT,
    last_result TEXT NOT NULL DEFAULT '',
    hour_started_at TEXT,                    -- start of the current hourly cap window
    hour_count INTEGER NOT NULL DEFAULT 0,   -- starts claimed inside that window
    retry_until TEXT,                        -- a 429 Retry-After: no start before this time
    PRIMARY KEY (user_id, duty)
);

ALTER TABLE assistant_triggers ADD COLUMN IF NOT EXISTS hour_started_at TEXT;
ALTER TABLE assistant_triggers ADD COLUMN IF NOT EXISTS hour_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE assistant_triggers ADD COLUMN IF NOT EXISTS retry_until TEXT;
