-- 0060_ask_user_words: when the user answers an ask in CHAT, the assistant types
-- the answer (answered_by = agent:/token:), so the row alone cannot say the words
-- are the user's. `user_words` keeps the user's exact chat words beside the
-- assistant's clean `answer`. '' = none given (a web answer, or an older row).
-- DDL ONLY, metadata-only, IF NOT EXISTS so a re-run is harmless.
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS user_words TEXT NOT NULL DEFAULT '';
