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
- [ ] 1. Landing `/` renders — hero, stats card, CTAs. **The card should read 41 sources** (41 registry keys; 40 live instances). The hardcoded **47** that made this box fail through 2026-08-24 is gone: every rendered count — hero, stats card, footer strapline, and the OG/Twitter card metadata — now reads `SOURCE_COUNT` from `frontend/src/lib/catalog.ts` (`page.tsx`, `layout.tsx`, `Footer.tsx`). If the number on screen disagrees with `SOURCE_REGISTRY`, the bug is in that one constant, and `scripts/doc_sync_check.py` (guard `landing-source-count`) should already be red.
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
- [ ] 14. Profile → keywords — CV produces a SearchConfig (titles/skills) used by search
- [ ] 15. Version history → `GET /profile/versions` 200, count grows on each save
- [ ] 16. Version **diff** → `/profile/versions/{a}/diff/{b}` 200, and **restore** a prior version
- [ ] 17. JSON-Resume export → `GET /profile/json-resume` 200
- [ ] 18. Auto re-score on profile save — `user_feed` rows (re)scored for the user after save

## D. Search & feed
- [ ] 19. Run search → `POST /api/search` 200, returns `run_id`, pipeline runs
- [ ] 20. Search status poll → `GET /search/{run_id}/status` reaches `completed`
- [ ] 21. Dashboard feed → `GET /api/jobs` (user cookie) returns the user's OWN scored jobs (personalized, not the raw catalog)
- [ ] 22. Dashboard UI — "New Search" button, filter panel, hybrid-mode toggle all work; jobs render with scores

## E. Job interaction
- [ ] 23. Job detail → `GET /api/jobs/{id}` 200 with per-dimension scores (role/skill/loc/recency)
- [ ] 24. Duplicate detection → `GET /jobs/{id}/duplicates`
- [ ] 25. Like / Apply / Skip → `POST /jobs/{id}/action` 200, `user_actions` row lands
- [ ] 26. Un-like / remove action → `DELETE /jobs/{id}/action`
- [ ] 27. Actions list + counts → `GET /actions`, `/actions/counts`
- [ ] 28. CSV export → `GET /api/jobs/export` 200 real CSV — AND the UI export button (currently missing in UI; flag it)

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

## J. Ops / admin
- [ ] 42. Source health → `GET /runs/source-health` 200 (note: no role gate — any logged-in user)
- [ ] 43. Recent runs → `GET /runs/recent`

## K. Cross-cutting
- [ ] 44. Every page renders with no console errors. Enumerate the pages from the code — `find frontend/src/app -name page.tsx` — never from a list written here; the list that used to live on this line still sent runs to `/settings/channels` months after Channels moved to top-level `/channels`.
- [ ] 45. Theme toggle works; spot-click every primary button on every page (no dead buttons)

---

## Report format
Produce a table: `# | item | LIVE/CODE/GATED/FAIL | evidence`. End with:
- counts (e.g. "40 LIVE, 3 CODE, 1 GATED, 1 FAIL")
- the FIRST real FAIL with exact file:line + error (if any)
- what's needed to close the gates (install Redis; add LinkedIn sample; set OAuth creds)
