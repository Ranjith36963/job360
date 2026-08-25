-- 0032 — the chase cron needs to remember who it already chased (wiring.md W-19).
--
-- Before this, the product could SEE that an application had gone quiet
-- (get_stale_applications, dormant 7+ days) but that fact only ever reached an
-- in-app banner on the Pipeline page — and only if the user went and looked. The
-- back half of the journey (apply -> silence -> the product notices -> it nudges
-- you) did not exist in any form.
--
-- Why a column and not the notification_ledger: the ledger is
-- UNIQUE(user_id, job_id, channel) — "never send the same (user, job, channel)
-- twice" — and a chase is BY DEFINITION about a job the user was already notified
-- about. Reusing the ledger would either collide with the original match row or
-- force a discriminator column onto a table whose whole contract is that
-- uniqueness. One nullable timestamp on the row being chased is honest and local.
--
-- Without this column the cron would re-chase every dormant application on every
-- run: an application quiet for 30 days would generate ~23 emails. The cooldown
-- read off this column is what makes the feature a nudge instead of a nuisance.

ALTER TABLE applications ADD COLUMN last_chased_at TEXT;

-- The cron scans "dormant AND not chased recently" for every user on a schedule,
-- so both columns it filters on want to be indexable together.
CREATE INDEX IF NOT EXISTS idx_applications_chase
    ON applications(user_id, updated_at, last_chased_at);
