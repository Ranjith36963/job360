-- 0089_duty_run_outcomes: how each scheduled run ended (owner, 2026-10-25).
--
-- mark_done now says how a duty's run ended: outcome done | partial | failed, and a reason from a
-- closed list (services/profile/schedule.py). `failures` counts failed runs in a row: a done or
-- partial run resets it. After a failed run the duty waits 2h, 4h, 8h... (at most 24h) before it is
-- due again; at 3 failures in a row Home and Needs you show one plain card. Still one SLOT per duty.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless; every existing row reads done / '' / 0.

ALTER TABLE assistant_duty_runs ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'done';
ALTER TABLE assistant_duty_runs ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '';
ALTER TABLE assistant_duty_runs ADD COLUMN IF NOT EXISTS failures INTEGER NOT NULL DEFAULT 0;
