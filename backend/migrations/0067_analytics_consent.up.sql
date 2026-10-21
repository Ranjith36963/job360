-- 0067_analytics_consent: a stored, per-user analytics consent (gap roadmap slice 4).
--
-- WHY. Until now the only consent choice lived in the browser (the PostHog
-- banner, frontend/src/lib/consent.ts), so the backend could not ask "did this
-- user agree?" before sending `first_tool_call` / `first_bring` to PostHog, and
-- Sentry session replay recorded every visitor's screen with no consent at
-- all. This column is that answer, set and cleared only by the user from
-- Settings > Account ("Help improve Job360", PUT /api/auth/me/analytics-consent,
-- a browser session only). Nullable TEXT (ISO timestamp of when they turned it
-- on), like every other date column on `users`: NULL = no consent, which is
-- every existing user — off by default.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE users ADD COLUMN IF NOT EXISTS analytics_consent_at TEXT;
