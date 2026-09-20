-- 0044_fit_axes: the fit picture gets axes (owner ask, 2026-09-20).
--
-- WHY. The agent's fit verdict is one number and a sentence. The owner wants
-- the picture a radar chart gives: a few NAMED dimensions, and on each one
-- how much the role asks and how much the seeker brings. The AGENT names the
-- axes and puts the numbers on them from the ad and the profile (VISION rule
-- 4 — Job360 never judges); Job360 stores the list and draws it.
--
-- One JSON text column on the fit SLOT, next to `fit_gaps` (migration 0037,
-- same shape: a JSON list, '[]' when absent). Overwritten by every save_fit
-- like the rest of the slot; the `fit_judged` event payload carries the axes
-- too, so every past picture is history (spec S7).
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS fit_axes TEXT DEFAULT '[]';
