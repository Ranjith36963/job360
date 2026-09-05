# Job360 — Full End-to-End Sweep Checklist (the loop's contract)
<!-- doc: LIVING -->

**Purpose:** the authoritative list a complete `/verify-job360` run must cover — every
feature, page, button, and route, from landing on the site to closing it. A "full sweep"
is not done until every item below is exercised (or explicitly marked gated, with the reason).

**How to use:** start the full stack (backend + frontend + Redis/ARQ worker when present),
register a fresh user, then walk the list top to bottom. Prove each with evidence
(HTTP code, DB row, screenshot, log line). Report a PASS / FAIL / GATED table.

**Verdict legend:** `LIVE` = exercised against the running app · `CODE` = present + wired but
not fired (needs external service or sample data) · `GATED` = needs infra not present.

**Standing gates (note in every report until resolved):**
- **Real notification delivery (#38)** needs **Redis + the ARQ worker** running. Redis not
  installed by default → mark GATED unless present (`redis-cli ping`).
- **LinkedIn enrich (#12)** needs a sample LinkedIn PDF in `test-artifacts/`.
- **GitHub enrich (#13)** hits **live GitHub** (rate-limited; needs a real handle).
- **LLM CV parse (#11)** uses the Gemini→Groq→Cerebras fallback; free-tier daily quotas can
  exhaust → extraction may degrade to titles-only or fall back slowly. Not a bug.

---

## A. Landing & entry
- [ ] 1. Landing `/` renders — hero + CTAs, and **no source count anywhere** (hero, footer strapline, OG/Twitter metadata). Job360 never sources jobs (VISION rule 4); `frontend/src/app/__tests__/landing-sources-count.test.tsx` pins the absence.
- [ ] 2. Every nav + footer link and the Get-started / Login buttons navigate correctly

## B. Auth (full lifecycle)
- [ ] 3. Register → `POST /api/auth/register` 201, `users` row lands, cookie issued
- [ ] 4. Email verify — `POST /verify-email/request`, `/verify-email/confirm`, `GET /me/email-verified` (confirm whether enforcement is on/off — currently `email_verified_at` stays NULL = not enforced)
- [ ] 5. Login + session — `GET /api/auth/me` resolves the exact user from the cookie
- [ ] 6. Password reset **request** → 204 (send is SMTP-conditional)
- [ ] 7. Password reset **confirm** → `/password-reset/confirm` with a token
- [ ] 8. Logout → `POST /logout` 204, cookie cleared, old cookie → `/me` 401
- [ ] 9. Route guard — gated route with no cookie → 401
- [ ] 10. Session persists — 30-day cookie max-age (`auth.py`)

## C. Profile
- [ ] 11. CV upload + LLM parse → `POST /api/profile` 200, skills+titles returned, `user_profiles` row lands
- [ ] 12. LinkedIn enrich → `POST /profile/linkedin` (GATED: needs sample LinkedIn PDF)
- [ ] 13. GitHub enrich → `POST /profile/github` (CODE: hits live GitHub)
- [ ] 14. Profile → agent — `GET /api/profile` returns what the CV contains; an unset preference is ABSENT, not zero (rule #29)
- [ ] 15. Version history → `GET /profile/versions` 200, count grows on each save
- [ ] 16. Version **diff** → `/profile/versions/{a}/diff/{b}` 200, and **restore** a prior version
- [ ] 17. JSON-Resume export → `GET /profile/json-resume` 200
- [ ] 18. Profile save bumps the version — `GET /profile/versions` count grows; no other side effect (nothing is re-scored any more)

## D. Bring a job (the product path)
- [ ] 19. Bring a job → `POST /api/jobs/bring` (title, company, description) 201; response carries `application_id` + `status`, a `jobs` row AND an `applications` row land
- [ ] 20. Same ad twice → second `POST /api/jobs/bring` returns `existing: true` and the SAME `job_id` (dedup on `normalized_key()`)
- [ ] 21. Bring page UI — `/bring` form submits and lands on `/applications/{id}`
- [ ] 22. Applications list → `GET /api/applications` (user cookie) returns ONLY the caller's applications

## E. Application object
- [ ] 23. Application detail → `GET /api/applications/{id}` 200 with `events`, `artifacts`, `receipts`; another user's id → 404
- [ ] 24. By job → `GET /api/applications/job/{job_id}` 200 for the caller's own application, 404 otherwise
- [ ] 25. Save an artifact → `POST /applications/{id}/artifacts` 201; a second save is version 2, version 1 still readable (append-only)
- [ ] 26. Record an event → `POST /applications/{id}/events` lands an `application_events` row; `status` moves; history never rewritten
- [ ] 27. Receipt → `POST /applications/{id}/receipt` 201; `GET /api/receipts` lists it; `/receipts/{id}` shows note + channel
- [ ] 28. Export → `GET /api/applications/export` 200 with every application, event, artifact and receipt of the caller

## F. Pipeline / Kanban
- [ ] 29. Create card → advance stage → `POST /pipeline/{id}` + `/advance`; `applications.stage` + history row update
- [ ] 30. Notes editor → `PATCH /pipeline/{id}/notes`
- [ ] 31. Stage-history timeline → `GET /pipeline/{id}/timeline`
- [ ] 32. Counts + interview reminders → `GET /pipeline/counts`, `/pipeline/reminders`
- [ ] 33. Kanban drag — mouse AND keyboard (a11y: Space pick up, arrows move, Enter drop, Esc cancel)

## G. Channels
- [ ] 34. Channel create / list / delete → CRUD + `DELETE /{channel_id}` (email + webhook work without OAuth)
- [ ] 35. Test-send → `POST /{channel_id}/test`

## H. Notifications
- [ ] 36. Notification rules — `POST/GET/PUT /settings/notification-rule`, rule row lands
- [ ] 37. History + stats → `GET /api/notifications`, `/notifications/stats`
- [ ] 38. **Actual delivery** (GATED: Redis + ARQ worker) — fire a real send through a configured channel and confirm the ledger row

## I. Account management
- [ ] 39. Password change guard — wrong current password → 401 (rule #26), tested non-destructively
- [ ] 40. Email change → `PATCH /users/me/email` (verify current password first)
- [ ] 41. Account delete → `DELETE /users/me` soft-delete (sets `deleted_at`; restore after to keep the demo account)

## J. Agent surface (MCP)
- [ ] 42. Token → `POST /api/tokens` 201 returns a `j360_…` token once; `GET /api/tokens` lists names only; `DELETE /api/tokens/{id}` revokes
- [ ] 43. MCP → a POST to `/api/mcp` (a mounted ASGI app, not a router) with no bearer = 401 + `WWW-Authenticate: Bearer`; with the token, `tools/list` names every tool (count it with `grep -c "@mcp.tool()" backend/src/api/mcp_server.py`)

## K. Cross-cutting
- [ ] 44. Every page renders with no console errors: `/`, `/login`, `/register`, `/forgot-password`, `/reset-password`, `/bring`, `/applications`, `/applications/[id]`, `/receipts`, `/pipeline`, `/profile`, `/settings/channels`, `/settings/notifications`, `/settings/account`, `/notifications`
- [ ] 45. Theme toggle works; spot-click every primary button on every page (no dead buttons)

---

## Report format
Produce a table: `# | item | LIVE/CODE/GATED/FAIL | evidence`. End with:
- counts (e.g. "40 LIVE, 3 CODE, 1 GATED, 1 FAIL")
- the FIRST real FAIL with exact file:line + error (if any)
- what's needed to close the gates (install Redis; add LinkedIn sample; set OAuth creds)
