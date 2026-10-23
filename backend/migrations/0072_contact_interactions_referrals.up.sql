-- 0072_contact_interactions_referrals: outreach pack #5/#6 (2026-10-20).
-- contact_outreach.follow_up_on — the next chase date the user's assistant set on a
-- meeting / call / intro / referral ledger entry. Lives on the ROW so a cold contact
-- (no job, no follow-up slot) can carry one too. NULL = none (rule #29). sent/reply/
-- message rows leave it NULL (a linked send's chase stays the job's own slot).
-- entry/channel need no change: 0046 declared them as plain TEXT (comment-only enums);
-- the closed sets live in core/settings.py and contacts.record_outreach.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a re-run is harmless.

ALTER TABLE contact_outreach ADD COLUMN IF NOT EXISTS follow_up_on TEXT;
