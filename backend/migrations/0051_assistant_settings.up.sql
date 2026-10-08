-- 0051_assistant_settings: owner decision 2026-10-08 (S2) — ASSISTANT SETTINGS
-- are a fourth head beside cv_data / preferences / user_info: how much the
-- user's assistant may do on its own (apply mode, score line, submit mode,
-- daily cap, pause switch). Same pattern as 0050.
--
-- 1. `user_profiles.assistant_settings` holds the BASE as one JSON object. It is
--    written by ONE function only (storage.save_assistant_settings); save_profile
--    never touches it. Values the assistant or the user sets ride the
--    `profile_edits` overlay on `assistant_settings.*` paths (who/when history
--    for free). `user_profile_versions` gets NO column: settings are not part
--    of a snapshot.
-- 2. `assistant_setting_requests` is the "Waiting for your OK" queue. When an
--    assistant asks for a RISKIER change (more freedom) it is stored here, NOT
--    applied; only the signed-in user on the website can confirm it. `decision`
--    NULL = waiting; else confirmed | declined | superseded | cleared. The
--    decision columns are set ONCE (`... WHERE decision IS NULL`), asks-table
--    style. REFERENCES is documentation only (the shim strips FK clauses), so
--    every read filters on user_id by hand.
--
-- DDL ONLY. No existing row is read, copied or changed; every existing profile
-- gets '{}' ("nothing chosen" — safe defaults apply, rule #29). IF NOT EXISTS
-- so a re-run is harmless.

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS assistant_settings TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS assistant_setting_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                      -- an assistant_settings.* path or preferences.daily_check
    value TEXT,                              -- JSON-encoded, already validated + normalised
    requested_by TEXT NOT NULL,              -- actor_for(user): token:<n> | agent:<n>
    requested_at TEXT NOT NULL,
    decision TEXT,                           -- NULL = waiting | confirmed | declined | superseded | cleared
    decided_by TEXT,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_assistant_setting_requests_user_decision
    ON assistant_setting_requests(user_id, decision);
