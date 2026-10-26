-- 0091_application_fill_steps: the replayable fill script (owner, 2026-10-26: "Refill the form").
--
-- Every form action the user's assistant carried out through Job360's browser for an application
-- (type / choose / tick a field, click a radio or a list option, upload the saved CV or letter) is
-- one row: a stable locator for the control (tag, type, role, label, question group, nth among the
-- same) and the value. "Refill the form" (website only) replays them in Job360's own browser after
-- a restart lost the page, then holds it for the user's "Finish it here". Never a submit click,
-- never a password or one-time code (refused before a row is written). APPEND-ONLY: a later row
-- for the same control wins at replay. `locator` is JSON; `host` is the form page's host name.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS application_fill_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    action TEXT NOT NULL,                    -- type | choose | tick | click | upload (closed set, in code)
    locator TEXT NOT NULL DEFAULT '{}',
    value TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS application_fill_steps_app_idx
    ON application_fill_steps (user_id, application_id, id);
