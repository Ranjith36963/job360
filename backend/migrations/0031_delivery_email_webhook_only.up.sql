-- 0031_delivery_email_webhook_only: delivery is EMAIL + WEBHOOK, nothing else.
--
-- The per-user Slack / Discord / Telegram channels were deleted on 2026-08-24.
-- Rationale, evidence and the full cut list:
--   docs/plans/2026-08-24-email-webhook-only-delivery.md
--
-- SAFETY — measured against production before writing this file:
--   users                            11
--   user_channels                     0   <- nobody ever connected ANY channel
--   notification_rules                0
--   notification_ledger               0   <- nothing was ever delivered
--   user_notification_digests         0
--   oauth_states                      0
-- So the DELETEs below are a no-op in production. They exist for local and
-- staging databases where someone did click "connect", and so that this
-- migration is correct wherever it runs rather than only where we looked.
--
-- WHAT IS DELIBERATELY *NOT* DROPPED:
--   * user_channels.connection_status / .target_label (added by 0019) — the
--     email channel still uses connection_status. Dropping two columns to tidy
--     up would be schema churn on a live table for no behavioural gain.
--   * notification_ledger rows whose channel is 'slack'/'telegram'/'discord' —
--     they are audit history AND the dedup key (UNIQUE user/job/channel).
--     Deleting audit rows to make a column look tidy is how you lose the
--     ability to answer "did we ever send this?". (Production has none anyway.)

-- 1. Per-user channels for the three dead types.
DELETE FROM user_channels
 WHERE channel_type IN ('slack', 'discord', 'telegram');

-- 2. Queued digest rows addressed to a channel that can no longer be delivered.
--    Left behind, these are undrainable: send_bundle would look up a channel
--    row that no longer exists and the rows would sit at sent=0 forever.
DELETE FROM user_notification_digests
 WHERE channel IN ('slack', 'discord', 'telegram');

-- 3. The OAuth handshake table. It existed only to carry a one-time nonce
--    between our /connect/{slack,discord} redirect and the provider callback.
--    Both routes are gone, so every writer and every reader of this table is
--    gone. Also removed from database._PER_USER_TABLES in the same commit —
--    account deletion iterates that tuple, and a dropped table left in the
--    list would make DELETE ME crash (rule #26).
DROP TABLE IF EXISTS oauth_states;
