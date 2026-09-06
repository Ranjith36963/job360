-- 0040_drop_notification_tables: the notifications/channels/pipeline era's
-- six tables go (docs/product/VISION.md, mission decision 2026-09-03).
--
-- WHY. VISION §133 makes notifications PULL, not PUSH: the seeker's own
-- agent reads Job360's store, Job360 never pushes anything at them. The
-- worker + Redis services that could have delivered a push were deleted
-- 2026-09-02, so nothing has run any of this code since. This cleanup
-- deletes every reader and every writer left in `backend/src`:
--
--   notification_rules         — per-user delivery preferences. Written by
--                                 `PUT /api/settings/notification-rule`, read
--                                 by the (deleted) dispatch tick. No writer
--                                 or reader survives this commit.
--   notification_ledger        — durable send-audit trail. Written by the
--                                 (deleted) channel dispatcher, read by the
--                                 (deleted) `GET /api/settings/notifications`.
--   user_channels               — per-user webhook/email channel config,
--                                 Fernet-encrypted credentials. Written/read
--                                 by the deleted `src/services/channels/`.
--   user_notification_digests  — queued digest rows for the deleted digest
--                                 sender.
--   user_actions                — the pre-spine "kanban card" action stamp
--                                 (`applied`/`saved`/`dismissed`). Slice 5
--                                 (#483) already deleted the save/dismiss
--                                 routes and every reader; this commit
--                                 removes the last writer
--                                 (`POST /receipts/{job_id}`), so nothing
--                                 in `backend/src` names it any more.
--   user_feed                   — the scorer's per-user ranked feed row.
--                                 Slice 5 deleted the scorer that wrote it
--                                 and the dashboard that read it; the one
--                                 remaining reader (`get_fit_reason`, for the
--                                 tailoring prompt) is removed in this same
--                                 commit.
--
-- The Kanban pipeline API (`/api/pipeline/*`) that read/wrote `user_actions`
-- and the `applications.stage` column is deleted in the same commit as this
-- migration; `applications` itself and `application_stage_history` are NOT
-- touched — the application spine (`services/applications/spine.py`) still
-- owns them (rule: the list and the schema move together).
--
-- DATA LOSS IS REAL AND INTENDED. `db-backup.yml` runs daily, and
-- `0040_drop_notification_tables.down.sql` recreates the six tables EMPTY
-- (schema only — see its header).
--
-- Indexes are dropped explicitly before their tables, same convention as
-- 0039_drop_sourcing_tables.

DROP INDEX IF EXISTS idx_ledger_user_status;
DROP INDEX IF EXISTS idx_ledger_job;
DROP INDEX IF EXISTS idx_channels_user;
DROP INDEX IF EXISTS idx_digests_user_channel_pending;
DROP INDEX IF EXISTS idx_user_actions_user;
DROP INDEX IF EXISTS idx_feed_dashboard;
DROP INDEX IF EXISTS idx_feed_notify;
DROP INDEX IF EXISTS idx_feed_job;

DROP TABLE IF EXISTS notification_rules;
DROP TABLE IF EXISTS notification_ledger;
DROP TABLE IF EXISTS user_channels;
DROP TABLE IF EXISTS user_notification_digests;
DROP TABLE IF EXISTS user_actions;
DROP TABLE IF EXISTS user_feed;
