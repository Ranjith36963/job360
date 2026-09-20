-- 0044 down: reverses the DDL only. No pre-existing `applications` row or
-- column is touched beyond the one added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: the CURRENT axes of every fit picture are
-- dropped with the column. Every set of axes that arrived through save_fit
-- also lives in the `fit_judged` event payload (append-only), so the history
-- survives; only the slot copy goes.

ALTER TABLE applications DROP COLUMN IF EXISTS fit_axes;
