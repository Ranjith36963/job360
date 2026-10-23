-- 0082_assistant_duty_runs: "what is due now?" (owner decision 2026-10-23).
--
-- The user's settings drive their OWN assistant on a schedule with their PC off. The clock
-- lives at the AI vendor (a scheduled cloud task that wakes hourly); Job360 has no worker and
-- runs nothing. MCP `whats_due` answers which duties are due from the user's settings and the
-- last time each was done; `mark_done` stamps it. Due = time since last done >= the interval
-- (never fixed slots), so a late or skipped run heals itself.
--
-- One row per (user, duty): a SLOT, replaced on every mark, never history (the history is the
-- application events the run records). `duty` is a closed set: hunt | inbox | batch | tick
-- (tick = the last time a scheduled run asked or marked anything). `done_at` is ISO-8601 UTC.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS assistant_duty_runs (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    duty TEXT NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (user_id, duty)
);
