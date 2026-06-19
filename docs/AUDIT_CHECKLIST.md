# Job360 — End-to-End Audit Checklist

**Method:** code inspection (frontend pages/components + backend routes + tests) cross-checked
against a LIVE API sweep of all 49 routes on a running instance (register→…→logout, real user,
real CV via LLM, real search). **Nothing assumed — every "Working" call below was hit live or
is marked CODE/GATED with the reason.**

**Legend:** ✅ yes · ➖ no/missing · ⚠️ partial · **GATED** = needs infra not present ·
**LIVE** = exercised on the running app · **CODE** = present + wired, not fired (needs sample data / external service).

**Standing gates:** real notification delivery needs **Redis + ARQ worker** (Redis not installed);
LinkedIn enrich needs a sample PDF; GitHub enrich hits live GitHub; CV parse uses Gemini→Groq→Cerebras
(free-tier daily quotas can exhaust → slow/degraded, not a bug).

---

## Auth & entry

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| Landing | `/` | ✅ `app/page.tsx` | `GET /api/status,/sources,/health` | ✅ | ✅ jobs/run_log | ✅ `landing-sources-count.test.tsx` | ✅ LIVE 200 | — |
| Sign up | `/register` | ✅ `(auth)/register` | `POST /api/auth/register` | ✅ | ✅ users,sessions | ⚠️ backend only | ✅ LIVE 201 | no dedicated FE test |
| Sign in | `/login` | ✅ `(auth)/login` | `POST /api/auth/login` | ✅ | ✅ sessions | ✅ `login-redirect.test.tsx` | ✅ LIVE 200 | — |
| Forgot/Reset pw | `/forgot-password`,`/reset-password` | ✅ both pages | `POST /api/auth/password-reset/{request,confirm}` | ✅ | ✅ users | ➖ | ⚠️ request LIVE 204; confirm CODE (needs token) | send is SMTP-conditional |
| Email verify | `/verify-email` | ✅ page | `POST /verify-email/{request,confirm}`, `GET /me/email-verified` | ✅ | ✅ users | ➖ | ⚠️ request 204/status 200 LIVE; confirm CODE | **not enforced** (`email_verified_at` stays NULL) — decide if intended |
| Logout | navbar | ✅ layout | `POST /api/auth/logout` | ✅ | ✅ sessions | ⚠️ | ✅ LIVE 204 + dead-cookie 401 | — |
| Route guard | all gated | ✅ middleware | `require_user` | ✅ | ✅ sessions | ✅ | ✅ LIVE 401 no-cookie | — |
| Session persist | — | ✅ 30-day cookie | `auth.py` | ✅ | ✅ | ➖ | ✅ CODE | — |

## Dashboard, search & jobs

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| Dashboard feed | `/dashboard` | ✅ `dashboard/page.tsx` | `GET /api/jobs` | ✅ | ✅ user_feed,jobs | ✅ `uses-hybrid`,`judge-ranking-sort` | ✅ LIVE (personalized) | — |
| Run search | dashboard / profile btn | ✅ `search-latest-handoff.test.tsx` | `POST /api/search`,`GET /search/{id}/status` | ✅ | ✅ jobs,user_feed,run_log | ✅ | ⚠️ LIVE starts; **status didn't reach completed under load** | **BUG #1 (see below)** |
| Filters / hybrid toggle | dashboard | ✅ `jobs/FilterPanel.tsx` | `/api/jobs` query params | ✅ | ✅ | ✅ `FilterPanel.test.tsx` | ✅ LIVE | — |
| Jobs list page | `/jobs` | ✅ `jobs/page.tsx` | `GET /api/jobs` | ✅ | ✅ | ⚠️ | ✅ LIVE | — |
| Job detail | `/jobs/[id]` | ✅ `jobs/[id]/page.tsx` | `GET /api/jobs/{id}` | ✅ | ✅ jobs,job_enrichment | ✅ `job-detail-*`,`ScoreRadar` | ✅ LIVE 200 (per-dim scores) | — |
| Duplicates | job detail | ⚠️ | `GET /jobs/{id}/duplicates` | ✅ | ✅ | ➖ | ✅ LIVE 200 | — |
| Like/Apply/Skip | job card | ✅ `jobs/JobCard.tsx` | `POST/DELETE /jobs/{id}/action` | ✅ | ✅ user_actions | ✅ `JobCard.test.tsx` | ✅ LIVE (like+un-like 200) | — |
| Actions list/counts | — | ⚠️ | `GET /api/actions,/actions/counts` | ✅ | ✅ user_actions | ⚠️ | ✅ LIVE 200 | — |
| CSV export | dashboard | ➖ **no UI button** | `GET /api/jobs/export` | ✅ | ✅ | ➖ | ✅ LIVE 200 (12.6 KB) | **FIX: add export button in UI** |

## Profile

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| CV upload + LLM parse | `/profile` | ✅ `profile/CVUpload` | `POST /api/profile` | ✅ | ✅ user_profiles | ✅ `CVUpload.upload-guard.test.tsx` | ✅ LIVE 200 (Cerebras fallback) | Gemini/Groq daily quota near-exhausted |
| Preferences form | `/profile` | ✅ `profile/PreferencesForm` | `POST /api/profile` | ✅ | ✅ user_profiles | ✅ `PreferencesForm.autosave.test.tsx` | ✅ LIVE | — |
| LinkedIn enrich | `/profile` | ✅ profile | `POST /api/profile/linkedin` | ✅ | ✅ user_profiles | ➖ | ⚠️ CODE (needs sample PDF) | add sample LinkedIn PDF to test |
| GitHub enrich | `/profile` | ✅ profile | `POST /api/profile/github` | ✅ | ✅ user_profiles | ➖ | ⚠️ CODE (hits live GitHub) | — |
| Keywords from profile | — | n/a | SearchConfig in pipeline | ✅ | ✅ | ✅ backend | ✅ LIVE (titles extracted) | — |
| Version history | `/profile` | ✅ profile | `GET /profile/versions` | ✅ | ✅ user_profile_versions | ⚠️ | ✅ LIVE 200 | — |
| Version diff/restore | `/profile` | ✅ profile | `GET .../diff/...`, `POST .../restore` | ✅ | ✅ | ➖ | ✅ LIVE restore 200; diff CODE | — |
| JSON-Resume export | `/profile` | ✅ profile | `GET /api/profile/json-resume` | ✅ | ✅ | ➖ | ✅ LIVE 200 | — |
| Auto re-score on save | — | n/a | `rescore.py` bg task | ✅ | ✅ user_feed | ✅ backend | ⚠️ LIVE but **dropped rows under load** | **BUG #1** |

## Pipeline / Kanban

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| Kanban board | `/pipeline` | ✅ `pipeline/KanbanBoard.tsx` | `GET /api/pipeline` | ✅ | ✅ applications | ✅ `KanbanBoard.test.tsx` | ✅ LIVE | — |
| Create card | pipeline | ✅ | `POST /pipeline/{id}` | ✅ | ✅ applications | ⚠️ | ✅ LIVE 200 | — |
| Advance stage (applied→outreach→interview→offer→rejected) | pipeline | ✅ | `POST /pipeline/{id}/advance` | ✅ | ✅ applications,application_stage_history | ⚠️ | ✅ LIVE 200 (→interview) | verify each stage transition in a future pass |
| Notes editor | pipeline | ✅ | `PATCH /pipeline/{id}/notes` | ✅ | ✅ | ⚠️ | ✅ LIVE 200 | — |
| Stage timeline | pipeline | ✅ drawer | `GET /pipeline/{id}/timeline` | ✅ | ✅ application_stage_history | ⚠️ | ✅ LIVE 200 | — |
| Counts / reminders | pipeline | ✅ | `GET /pipeline/counts,/reminders` | ✅ | ✅ | ➖ | ✅ LIVE 200 | — |
| Keyboard a11y (dnd-kit) | pipeline | ✅ KanbanBoard | n/a | n/a | n/a | ✅ a11y assertions | ✅ CODE | — |

## Channels & Notifications

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| Channels page | `/settings/channels` | ✅ page | `GET /providers`,`GET/POST /channels` | ✅ | ✅ user_channels | ✅ `channels-page.test.tsx` | ✅ LIVE (list 200, create webhook 201) | — |
| Channel test-send | channels | ✅ | `POST /{id}/test` | ✅ | ✅ | ➖ | ⚠️ CODE | — |
| Channel delete | channels | ✅ | `DELETE /{id}` | ✅ | ✅ | ➖ | ⚠️ CODE | — |
| Connect Slack/Discord | channels | ✅ buttons | **routes not exposed** | ➖ | — | ➖ | ⚠️ needs OAuth creds | only Telegram connect route present |
| Connect Telegram | channels | ✅ | `GET /connect/telegram(/poll)` | ✅ | ✅ | `channel-connect-url.test.ts` | ⚠️ CODE (needs bot creds) | — |
| Notification rules | `/settings/notifications` | ✅ page | `GET/PUT /settings/notification-rule` | ✅ | ✅ notification_rules | ⚠️ | ✅ LIVE 200 | — |
| Notifications history/stats | `/notifications` | ✅ page | `GET /api/notifications,/stats` | ✅ | ✅ notification_ledger | ⚠️ | ✅ LIVE 200 | — |
| **Actual delivery** | — | n/a | dispatcher + ARQ worker | ✅ | ✅ notification_ledger,digests | ⚠️ | **GATED** | **needs Redis + ARQ worker (not installed)** |

## Account & Ops

| Feature | Page | FE code | BE route | API | DB | Tested | Working | Issues / Fix |
|---|---|---|---|---|---|---|---|---|
| Change password | `/settings/account` | ✅ page | `PATCH /auth/users/me/password` | ✅ | ✅ users,sessions | ✅ `account-forms.test.tsx` | ✅ LIVE (wrong-pw → 401 guard) | — |
| Change email | account | ✅ | `PATCH /auth/users/me/email` | ✅ | ✅ users | ✅ account-forms | ⚠️ CODE (not fired destructively) | — |
| Delete account | account | ✅ | `DELETE /auth/users/me` | ✅ | ✅ users (soft-delete) | ✅ account-forms | ⚠️ CODE (non-destructive) | — |
| Admin source health | `/admin/sources` | ✅ page | `GET /runs/source-health` | ✅ | ✅ run_log | ➖ | ✅ LIVE 200 | **no role gate — any logged-in user** (add admin check) |
| Recent runs | admin | ✅ | `GET /runs/recent` | ✅ | ✅ run_log | ➖ | ✅ LIVE 200 | — |
| Theme toggle / navbar | all | ✅ layout | n/a | n/a | n/a | ➖ | ✅ CODE | dark-only by design; toggle is cosmetic |

---

## Summary

**Working (LIVE-verified):** ~40 of 49 routes returned the expected response — the full
auth lifecycle, profile (CV+LLM, preferences, versions, restore, json-resume), search start,
dashboard/feed, job detail/duplicates/actions/CSV, the entire pipeline (create/advance/notes/
timeline/counts/reminders), channels CRUD, notification rules/history/stats, account guards,
ops endpoints. Frontend pages all present with tests for the major flows.

**Partial / CODE (present, not fully fired):** LinkedIn enrich (needs sample PDF), GitHub
enrich (live), password-reset/email-verify confirm (need tokens), channel test/delete, account
email-change/delete (not fired destructively), Slack/Discord connect (need OAuth creds).

**Missing:** CSV export has **no UI button** (endpoint works). Admin source-health has **no role gate**.

**Gated (infra):** real notification delivery (Redis + ARQ worker not installed).

### 🐛 BUG #1 — SQLite "database is locked" under concurrent writes (the one real defect)
Under concurrent search + rescore + LLM-judge writes, SQLite throws "database is locked"
(**15 occurrences** in the run log) and the code **drops the row** instead of retrying — seen as:
search not reaching `completed`, feed under-populating, `rescore: skipping job …: database is locked`,
`match_batch: judge failed for job …: database is locked`.
**Fix:** add SQLite `busy_timeout` + write-retry/serialization per process (quick), and plan
Postgres for real concurrency (proper). This is a **Step 4 / production-readiness blocker**.

### Production-readiness gaps (tech debt)
1. SQLite write contention (BUG #1) — data loss under load.
2. Notification delivery untested (needs Redis) — can't confirm the channel actually fires.
3. Admin source-health lacks a role gate (any user can read ops data).
4. Email verification not enforced — confirm if intended.
5. CSV export endpoint has no UI affordance.
