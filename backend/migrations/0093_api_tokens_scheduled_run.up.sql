-- 0093_api_tokens_scheduled_run: a personal token made for the user's scheduled runs (owner, 2026-11-02).
--
-- The owner's hourly scheduled tasks and his live chats used one connector, so the server could not
-- tell a run from him. A token minted with for_scheduled_runs = TRUE is the run's own credential:
-- every approval it sends for the user (a chat cv_seen / submit_approved / submit_declined, a
-- remembered or approved answer, an answered ask) is refused with 403 chat_approval_from_run.
-- Set once at mint, never changed; every existing token reads FALSE (a normal token).
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.
ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS for_scheduled_runs BOOLEAN NOT NULL DEFAULT FALSE;
