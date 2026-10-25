-- 0090_connector_check: which helper tools the user's assistant has (owner, 2026-10-25).
--
-- Job360 cannot see an assistant's other connectors; the ASSISTANT can (its own tool list). It reports
-- them with MCP `report_capabilities`: which categories it has (gmail, calendar, job_search,
-- people_lookup, company_lookup, browser, web_search), the connector's display name, and whether one
-- needs a sign-in or a paid plan. Job360 then shows the user what to connect, from a catalog file.
--
-- assistant_capability_reports: APPEND-ONLY history; the newest row per (user, assistant) is the
-- current one. `categories` / `needed` are JSON lists of closed values. Unset = unknown (rule #29).
-- connector_cards: one slot per card the web showed - when it was first shown (the one-new-card-a-week
-- cap) and when the user dismissed it (so it never nags again).
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS assistant_capability_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assistant TEXT NOT NULL,
    categories TEXT NOT NULL DEFAULT '[]',
    needed TEXT NOT NULL DEFAULT '[]',
    reported_by TEXT NOT NULL DEFAULT '',
    reported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assistant_capability_reports_user_idx
    ON assistant_capability_reports (user_id, assistant, id);

CREATE TABLE IF NOT EXISTS connector_cards (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_key TEXT NOT NULL,
    first_shown_at TEXT NOT NULL,
    dismissed_at TEXT,
    PRIMARY KEY (user_id, card_key)
);
