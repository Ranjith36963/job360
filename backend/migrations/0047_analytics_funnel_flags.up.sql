-- 0047_analytics_funnel_flags: two "claimed once" markers on `users` for the
-- backend-side funnel events (owner decision, 2026-09-28).
--
-- WHY. `first_tool_call` (any MCP tool, first ever) and `first_bring` (first
-- successful bring_job, web or MCP) must fire EXACTLY ONCE per user, however
-- many processes or concurrent requests race to be first. A plain in-memory
-- flag cannot do that (a deploy restarts the process; two workers don't
-- share memory) — only an atomic `UPDATE ... WHERE col IS NULL RETURNING`
-- against a stored column can (see src/services/analytics.py). Nullable TEXT
-- (ISO timestamp), like every other date column here: unset = never fired,
-- set = fired, no sentinel value.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE users ADD COLUMN IF NOT EXISTS first_tool_call_at TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_bring_at TEXT;
