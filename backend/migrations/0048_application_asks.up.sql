-- 0048_application_asks: the "Needs you" queue. When the user's assistant is
-- stuck or would have to guess (a form question the profile cannot answer, an
-- unclear email) it records an ASK here; the user answers once, in chat (the
-- assistant calls answer_ask) or on the Job360 Needs-you page. An ask may be
-- about one application or general (application_id NULL).
--
-- DDL ONLY. Conventions copied from 0046: TEXT ISO-8601 timestamps, IF NOT
-- EXISTS, INTEGER PRIMARY KEY AUTOINCREMENT (the shim rewrites it), REFERENCES
-- for documentation (the shim strips FK clauses) - every read filters on
-- user_id by hand.

CREATE TABLE IF NOT EXISTS application_asks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER,                  -- NULL = a general question
    question TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    asked_by TEXT NOT NULL,                  -- actor_for(user): web | token:<n> | agent:<n>
    asked_at TEXT NOT NULL,
    answer TEXT,                             -- the LATEST answer; may be changed
    answered_by TEXT,
    answered_at TEXT,                        -- the LATEST answer (history = "answered" events)
    withdrawn_at TEXT                        -- the assistant/user took the question back
);
CREATE INDEX IF NOT EXISTS idx_application_asks_user_answered
    ON application_asks(user_id, answered_at);
CREATE INDEX IF NOT EXISTS idx_application_asks_application
    ON application_asks(application_id);

-- stats uses NOT EXISTS on corrects_event_id (fix #668).
CREATE INDEX IF NOT EXISTS idx_application_events_corrects
    ON application_events(corrects_event_id);
