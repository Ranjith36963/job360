-- 0081 down: reverses the DDL only.
--
-- WHAT THIS COSTS, STATED PLAINLY: Job360 forgets when each scheduled duty (hunt, inbox,
-- batch) was last done, so the next `whats_due` reports every enabled duty as due once.

DROP TABLE IF EXISTS assistant_duty_runs;
