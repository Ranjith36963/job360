# Step 3 — New Endpoints + Settings UI — Ralph-Loop-Driven Execution Plan
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`, `superpowers:dispatching-parallel-agents`, `superpowers:executing-plans`. Steps use checkbox syntax.
>
> **Pre-reqs carried from Step 2:** `main @ 9868877`, tag `step-2-green`, ≥1,056p / 0f / 3s backend baseline + ≥30 frontend unit tests + ≥5 Playwright E2E. Step 1.6 generator/reviewer contract enforced (every commit produces `.claude/generator-commit.md` + `.claude/reviewer-verdict.md` + green `make verify-batch`).
>
> **frontend/AGENTS.md directive:** *"This is NOT the Next.js you know. Read `node_modules/next/dist/docs/` before writing any code."* — every cohort agent MUST consult Context7 before introducing App Router patterns.

---

## Context

**Why this change is being made.** Steps 0 → 1 → 1.5 → 1.6 → 2 shipped the backend depth + the API→UI seam + the contract enforcement. The user can now register, upload a CV, run a search, see scored jobs, advance pipeline applications, and view enrichment fields. **What's missing is the *control surface*:** users have no way to configure when/how they get notified, no account management (delete / change password / change email), no pipeline timeline (only current stage), no dedup-group exploration (when same job appears across 5 sources), no notification history view, no `/settings` reorganization. Per `docs/harness/ExecutionOrder.md` line 49: *"Step 3 → New endpoints (versions, export, dedup, ledger) [1-2 sessions]"* — this batch finishes that scope.

**What prompted it.** Seven parallel sub-agent audits on `main @ 9868877` surfaced a clear pattern: **backend ships, frontend doesn't surface it, user value invisible**. Same pattern as Step 2, now at the settings/preferences layer. Notification ledger backend has 5 tests + filtering + pagination, but no UI consumes it. `urgency` parameter on `send_notification()` writes to the ledger but never drives dispatch routing. `deleted_at` column exists on `users` table but no DELETE route uses it. The `dedup_group_ids` field is in `JobResponse` but the endpoint that would populate + serve it is missing. Step 3 closes this consistently.

**Intended outcome.** After Step 3 completes:
- Users can configure per-channel notification rules (score thresholds, instant vs digest, quiet hours)
- Users can browse their notification history (`/notifications` page consuming the existing ledger backend)
- Users can manage their account (change password, change email, delete account → closes GDPR Article 17 / report C-07)
- Users can browse a job's duplicates ("this job posted at 3 sources — see alternates")
- Users can compare profile versions side-by-side (diff view)
- Pipeline applications carry a timeline (stage history + interview dates + editable notes)
- KanbanBoard supports drag-and-drop, confirmation dialogs, notes editing
- All 4 forms (login, register, preferences, channel-add) migrated to react-hook-form + zod with per-field 422 error mapping
- `/settings` becomes a proper landing page with tabs (Channels, Notifications, Account)
- Ghost-detection writer runs nightly off-cycle (closes D-03 from evaluation report)
- Backend ships 8 new endpoints; frontend ships 5 new pages + ~12 new components
- Test floor grows: backend +25 tests, frontend +20 tests + 3 new E2E specs
- Production-readiness aggregate score: **6.5 → 7.0** (Gates 4/8/9/10 partial closure)
- Every subsequent step (Step 4 ops hardening, Step 5 launch) can trust the *control surface* is honest

---

## Strategic context — why Ralph Loop for Step 3

Step 3 is a **schema-then-feature batch** with strong sequential dependencies:

1. **3 schema-related migrations + 1 query-time feature.** Migration `0012_notification_rules` (B-01, table + `users.timezone` column), migration `0013_user_notification_digests` (B-05, queue table), migration `0014_application_history` (B-06, history columns + new history table). The 4th feature (dedup-group view, B-09) is **not** schema-first — it uses Option A (re-run grouping at query time, no migration). Each schema migration requires migration → backend route → frontend component, in that order. **Cohort B locks the schemas first** (within Lane-Notifications: B-01 → B-02 → B-03; within Lane-Pipeline: B-06 → B-07/B-08); Cohort C frontend pages parallelise after Cohort B's gate.
2. **Form validation library is a foundation.** Once `react-hook-form + zod` lands, every new Step-3 form (notification rules, account management, notes editing) consumes it. Migrating the existing 4 forms first means new forms ship in the modern style from day 1.
3. **Audit-confirmed: backend lapped frontend.** 7 audits agreed: 6+ backend routes are missing UI surfaces (notifications ledger, profile-version-diff, dedup-group, account-mgmt, notification-rules, pipeline-timeline). Step 3 is mostly **wiring + new pages**, not refactoring.
4. **Step-2 P1 carryovers** must close in Cohort A, before any Step-3 page lands. Two items: (a) F-01 `?next` redirect post-login — otherwise every Step-3 deep link inherits the broken post-auth redirect; (b) F-02 TanStack Query cache-key consistency — simplifying keys before adding more `useQuery` callsites prevents future cache-collision pain.

Ralph Loop provides cohort-gating + verification halt + retry-on-red + Step-1.6 contract enforcement (`make verify-batch` runs at end of each cohort).

**Ralph Loop's role:** outer supervision + verification gate + sentinel + commit cadence + reviewer-verdict gate.
**Parallel sub-agents' role:** inner execution, dispatched in 5 cohorts.

---

## Scope — what Step 3 covers (and what it doesn't)

### In scope — 28 deliverables (must all land)

| # | Deliverable | Severity | Source |
|---|---|---|---|
| **F-01** | Step-2 `?next` redirect post-login (login + register pages consume `useSearchParams()`) | P1 | Audit-7 |
| **F-02** | TanStack Query cache-key consistency (document strategy + simplify keys) | P2 | Audit-7 |
| **F-03** | Migrate `EmptyState` shared component into all 4 ad-hoc empty-state JSX sites | P2 | Audit-7 |
| **V-01** | Install `react-hook-form` + `zod` + `@hookform/resolvers/zod` | P0 | Audit-2 |
| **V-02** | Migrate 4 forms (login, register, preferences, channel-add) to RHF + zod | P0 | Audit-2 |
| **V-03** | Extend `ApiError` to extract per-field 422 errors → `setError(field, ...)` | P0 | Audit-2 |
| **V-04** | CV upload client-side size cap (5MB) + MIME-type allowlist | P0 | Audit-2 + S-19/S-20 |
| **V-05** | Optional: OpenAPI → TS codegen via `openapi-typescript` | P2 | Audit-2 |
| **B-01** | Migration `0012_notification_rules` (table + `users.timezone` column) | P0 | Audit-1 |
| **B-02** | `GET/POST/PATCH/DELETE /api/settings/notification-rules` (4 routes) | P0 | Audit-1 |
| **B-03** | Wire `dispatcher.dispatch()` to consult rules (threshold filter, quiet-hours skip, digest queue). **Quiet-hours MUST convert UTC `now` to the user's `users.timezone` (IANA, e.g. `Europe/London`) before comparing to `quiet_hours_start/end` HH:MM strings** — otherwise non-UTC users get incorrectly skipped or notified. Use `zoneinfo.ZoneInfo(user.timezone)` (stdlib Python 3.9+). | P0 | Audit-1 |
| **B-04** | New ARQ periodic task `send_daily_digest()` per user at configured time | P1 | Audit-1 |
| **B-05** | Migration `0013_user_notification_digests` (queue table) | P1 | Audit-1 |
| **B-06** | Migration `0014_application_history` (`last_advanced_at`, `interview_dates` JSON, `notes_history` JSON, new `application_stage_history` table). **Existing `applications.notes` column is preserved as the "latest note" view** — on PATCH (B-08), the previous `notes` value is appended to `notes_history` JSON list (with timestamp), and `notes` is overwritten with the new latest. Do NOT rename or drop `notes`. | P0 | Audit-3 + Audit-7 |
| **B-07** | `GET /api/pipeline/{job_id}/timeline` returning stage history | P0 | Audit-3 + Audit-7 |
| **B-08** | `PATCH /api/pipeline/{job_id}/notes` for notes editing | P1 | Audit-3 |
| **B-09** | `GET /api/jobs/{id}/duplicates` — Option A re-run grouping at query time | P0 | Audit-7 |
| **B-10** | `GET /api/profile/versions/{id1}/diff/{id2}` returning per-field changes | P1 | Audit-7 |
| **B-11** | `DELETE /api/users/me` (soft-delete via existing `deleted_at` column → closes GDPR C-07) | P0 | Audit-7 |
| **B-12** | `PATCH /api/users/me/password` (authenticated change with current-password check) | P0 | Audit-7 |
| **B-13** | `PATCH /api/users/me/email` — **MVP ships confirm-via-current-password only** (full magic-link re-verification deferred to Step 5 launch readiness, needs SES wiring). Body: `{current_password, new_email}`. Verify password → update email → invalidate session → require re-login. | P1 | Audit-7 |
| **B-14** | New ARQ periodic task: nightly ghost-detection sweep (closes D-03 fully). **Includes** new test file `backend/tests/test_ghost_sweep.py` covering: (a) sweep transitions `active`→`possibly_stale` for jobs missed > N polls, (b) `possibly_stale`→`likely_stale`→`confirmed_expired`, (c) `update_last_seen()` resets state on re-fetch. | P1 | Audit-5 |
| **B-15** | `GET /api/runs/recent` (paginated run history from `run_log` with per-source breakdown) | P2 | Audit-5 |
| **C-01** | `/settings/page.tsx` landing with tabs (Channels / Notifications / Account) | P0 | Audit-4 |
| **C-02** | `/settings/notifications/page.tsx` consuming notification rules API | P0 | Audit-1 + Audit-4 |
| **C-03** | `/settings/account/page.tsx` (password change, email change, delete account) | P0 | Audit-7 |
| **C-04** | `/notifications/page.tsx` consuming `GET /api/notifications` (ledger viewer) | P1 | Audit-4 |
| **C-05** | `DedupGroupViewer.tsx` component on `/jobs/[id]` consuming `GET /jobs/{id}/duplicates` | P1 | Audit-4 + Audit-7 |
| **C-06** | `VersionDiffDrawer.tsx` (extend existing `VersionHistoryDrawer`) | P2 | Audit-7 |
| **C-07** | KanbanBoard drag-and-drop via `@dnd-kit/core` | P1 | Audit-3 |
| **C-08** | KanbanBoard notes-edit affordance (Dialog + Textarea + Save) | P1 | Audit-3 |
| **C-09** | KanbanBoard confirmation dialogs (5 destructive call sites) | P1 | Audit-3 |
| **C-10** | KanbanBoard pipeline-scoped FilterPanel (company / role / score / applied-date) | P2 | Audit-3 |
| **C-11** | KanbanBoard timeline drawer (stage history from B-07) | P1 | Audit-3 |

### Also in scope — 3 should-fix observability + UX items

| # | Should-fix | Source |
|---|---|---|
| O-01 | Notification ledger filters: time-range + job_id (extend existing route) | Audit-7 |
| O-02 | `GET /api/notifications/stats` per-channel success/failure aggregation | Audit-5 |
| O-03 | Notes auto-save on blur (debounced) — UX micro-polish | Audit-3 |

### Non-scope (explicitly deferred)

- **Bulk multi-select on KanbanBoard** — large surface, low immediate value. Defer to Step 4.
- **`/admin/runs` page** consuming `GET /api/runs/recent` — observability dashboard. Defer to Step 4 (ops hardening home).
- **Account email re-verification flow** — full magic-link reset. B-13 ships confirm-via-current-password as MVP; full magic-link defers to Step 5.
- **Password reset (forgot-password)** — separate from authenticated change. Defer to Step 5 launch readiness (needs SES wiring).
- **Stripe / freemium metering** — explicit Batch 4 / Step 5 scope per `MEMORY.md`.
- **Storybook + visual regression** — defer to Step 4.
- **OpenAPI → TS codegen (V-05)** — flagged P2; ships if budget allows, otherwise carries to Step 4.
- **Per-job notification rules** (rule applies to one specific job vs all jobs) — Step 3 ships per-channel-per-score-threshold; per-job-rules defer to Step 5.

---

## Tool orchestration — what each tool does

### Ralph Loop (outer driver)

**Invocation:** `/ralph-loop` with `completion_promise: "STEP-3-GREEN"` and `max_iterations: 20` (Step 2 burned ~12 with no surprises; Step 3 cohort math is A(3) + B(4) + C(4) + D(2) + E(2) = 15 baseline, plus expected 1-2 retries per cohort, so 20 gives ~5 iterations of headroom). **Cohort A and Cohort B can run in parallel** — Cohort A is 100% frontend (login, register, forms, CV upload), Cohort B is 100% backend (migrations, routes, dispatcher). Zero file overlap. Running them concurrently saves ~3 wall-clock iterations.

**Each iteration:**
1. Check sentinel: does `.claude/step-3-verified.txt` exist? If yes, emit `STEP-3-GREEN` and halt.
2. Run `make verify-step-3` (added in iteration 1 as a new Makefile target).
3. Parse output → identify which deliverables are still red.
4. Dispatch the cohort of sub-agents that matches the current dependency stage.
5. **Run `make verify-batch`** (Step-1.6 contract) — generator commit + reviewer verdict required before iteration completes.
6. Re-run verification, write/update sentinel if all green.
7. Commit partial progress with conventional-commit prefix.

**Stop criteria (all must hold before emitting `STEP-3-GREEN`):**
- All 28 deliverables (F-01..02, V-01..05, B-01..15, C-01..11) have landed commits
- All 3 should-fix items (O-01..03) landed
- Backend regression: `make test` against the **actual post-Step-2 baseline** (re-verified in iteration 1 — assumed ≥1,056p but may have grown via Step-2's 30+ test deltas) + 25 new tests, 0 failing
- Frontend unit tests: `npm run test:unit` against actual post-Step-2 floor + 20 new = ≥50 passing, 0 failing
- Frontend E2E: `npm run test:e2e` ≥5 + 3 new = ≥8 specs (notification-rules-flow, account-delete-flow, pipeline-timeline-flow)
- Migration round-trip: `0012`, `0013`, `0014` all up→down→up clean (via `make migrate-roundtrip` target added in iteration 1)
- `make verify-batch` exits 0 (Step-1.6 contract — reviewer verdict APPROVED)
- `make verify-step-3` exits 0
- Sentinel `.claude/step-3-verified.txt` written with green commit SHA

### Parallel sub-agents (5 cohorts)

Driven by skill `superpowers:dispatching-parallel-agents`. Cohorts A→E run in order; within a cohort, agents are parallel (or sequential if file-conflict noted).

#### Cohort A — Frontend Foundations (Iterations 1–3, sequential within cohort)

Step-2 carryover + form-validation library. Sequential within (each agent's output feeds the next), **but runs in parallel with Cohort B** — zero file overlap, both can dispatch simultaneously in iteration 1.

**Iteration 1 setup tasks (must land before Cohort A/B agents dispatch):**
- Add Makefile target `migrate-roundtrip` calling `bash scripts/migration_roundtrip.sh` (mirrors `verify-step-2` style).
- Create `scripts/migration_roundtrip.sh`: iterates every `.up.sql` → `.down.sql` → `.up.sql`, asserts row counts preserved + schema clean. Sets `JOB360_DB=:memory:` + fresh `TMPDIR` for hermeticity.
- Re-verify backend pytest baseline against `main @ 9868877` (post-Step-2 actual count may differ from the assumed 1,056) and update §Stop-criteria floor accordingly.

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Step2-Cleanup | F-01, F-02, F-03 | `frontend/src/app/(auth)/login/page.tsx` (consume `?next`), `register/page.tsx` (same), `frontend/src/app/dashboard/page.tsx` (cache-key cleanup), 4 sites consume `EmptyState` | `/implement` + `superpowers:test-driven-development` |
| Agent-Form-Validation | V-01, V-02, V-03 | `frontend/package.json` + `frontend/src/lib/api-error.ts` (extend) + 4 form components (login, register, preferences, channel-add) + matching test files | `/implement` + `superpowers:test-driven-development` + Context7 (RHF + zod) |
| Agent-CV-Upload-Validation | V-04 | `frontend/src/components/profile/CVUpload.tsx` (size cap 5MB + MIME allowlist `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`) + test | `/implement` + `superpowers:test-driven-development` |
| Agent-Codegen (optional) | V-05 | `frontend/package.json` + `frontend/scripts/gen-types.ts` + npm script `gen:types` | `/implement` + Context7 (`openapi-typescript`) |

**Gate before Cohort B:**
- All 4 forms pass `npm run test:unit` with field-level error tests
- Login + register correctly redirect to `?next` param post-auth
- `npm run lint && npm run type-check && npm run test:unit` green
- `make verify-batch` (Step-1.6 contract) green

#### Cohort B — Backend Foundations (Iterations 1–7, parallel with Cohort A)

All migrations + routes. **Runs in parallel with Cohort A** (zero file overlap). Sequential within Lane-Notifications (B-01 → B-02 → B-03), parallel across the 4 lanes.

| Lane | Items | Files touched | Skills |
|---|---|---|---|
| Lane-Notifications | B-01, B-02, B-03, B-04, B-05, O-01, O-02 | `backend/migrations/0012_notification_rules.{up,down}.sql`, `0013_user_notification_digests.{up,down}.sql`, `backend/src/api/routes/notification_rules.py` (new), `backend/src/services/channels/dispatcher.py` (consult rules), `backend/src/workers/tasks.py` (`send_daily_digest`), `backend/src/workers/settings.py` (register periodic), tests | `/implement` + `superpowers:test-driven-development` |
| Lane-Pipeline | B-06, B-07, B-08 | `backend/migrations/0014_application_history.{up,down}.sql`, `backend/src/api/routes/pipeline.py` (extend), `backend/src/repositories/database.py` (`get_application_timeline`, `update_notes`), tests | `/implement` + `superpowers:test-driven-development` |
| Lane-Account-Mgmt | B-11, B-12, B-13 | `backend/src/api/routes/auth.py` (add 3 routes), `backend/src/services/auth/passwords.py` (re-use existing `verify_password` + `hash_password`), tests including IDOR audit | `/implement` + `superpowers:test-driven-development` |
| Lane-Discovery | B-09, B-10, B-14, B-15 | `backend/src/api/routes/jobs.py` (`/duplicates`), `backend/src/api/routes/profile.py` (`/versions/{id1}/diff/{id2}`), `backend/src/workers/tasks.py` (`nightly_ghost_sweep`), `backend/src/api/routes/runs.py` (new), tests | `/implement` + `superpowers:test-driven-development` |

**Gate before Cohort C:**
- All 3 migrations round-trip cleanly (`make migrate-roundtrip`)
- Backend pytest ≥1,081p / 0f / ≤3s
- All new routes have at least one happy-path + one IDOR test
- Notification rule consultation: integration test verifies `urgency=instant + score=85 + threshold=80 → dispatch fires; score=70 → skipped`
- `make verify-batch` green

#### Cohort C — Frontend pages + KanbanBoard (Iterations 8–11, parallel within cohort)

Depends on Cohort A (forms) + Cohort B (backend routes). Most agents independent.

> **Cohort C internal dependency.** `Agent-Settings-Layout` (C-01) creates `frontend/src/app/settings/layout.tsx` (the tab shell). `Agent-Notification-Rules-UI` (C-02) and `Agent-Account-UI` (C-03) create child pages under that layout. **C-01 MUST land before C-02 and C-03** — otherwise children render without tab navigation and the user lands on disconnected pages. Sequence within Cohort C: C-01 first (single-agent iteration), then dispatch C-02..C-11 in parallel.

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Settings-Layout | C-01 | `frontend/src/app/settings/page.tsx` (new), `frontend/src/app/settings/layout.tsx` (tabs), redirect existing `/settings/channels` to live under new layout | `/implement` |
| Agent-Notification-Rules-UI | C-02 | `frontend/src/app/settings/notifications/page.tsx` (new) — per-channel rule editor: Slider (thresholds) + RadioGroup (instant/digest) + TimePicker (quiet hours) + TimePicker (digest send time) + zod schema | `/implement` + `superpowers:test-driven-development` + Context7 |
| Agent-Account-UI | C-03 | `frontend/src/app/settings/account/page.tsx` (new) — change password form + change email form + delete-account confirm dialog | `/implement` + `superpowers:test-driven-development` |
| Agent-Notifications-Page | C-04 | `frontend/src/app/notifications/page.tsx` (new) — paginated ledger viewer with channel + status + time-range filters | `/implement` |
| Agent-Dedup-Viewer | C-05 | `frontend/src/components/jobs/DedupGroupViewer.tsx` (new) + integration into `/jobs/[id]/JobDetailClient.tsx` | `/implement` |
| Agent-Version-Diff | C-06 | `frontend/src/components/profile/VersionDiffDrawer.tsx` (new), extend `VersionHistoryDrawer` with "compare" button | `/implement` |
| Agent-Kanban-DnD | C-07, C-09, C-11 | `frontend/package.json` (`@dnd-kit/core`, `@dnd-kit/sortable`), `frontend/src/components/pipeline/KanbanBoard.tsx` (drag handlers + confirmation dialogs + timeline drawer trigger) | `/implement` + Context7 (`@dnd-kit`) |
| Agent-Kanban-Notes | C-08, O-03 | `frontend/src/components/pipeline/NotesEditor.tsx` (new — Dialog + Textarea + auto-save on blur), wire to KanbanBoard card | `/implement` + `superpowers:test-driven-development` |
| Agent-Pipeline-Filters | C-10 | `frontend/src/components/pipeline/PipelineFilterPanel.tsx` (new) — pipeline-scoped filters | `/implement` |

**Gate before Cohort D:**
- Playwright E2E: notification-rules-flow (set rule → trigger search → assert dispatch fires only on matching jobs)
- Playwright E2E: account-delete-flow (delete → re-login fails → re-register clean)
- Playwright E2E: pipeline-timeline-flow (advance card → open timeline drawer → assert entries)
- `npm run test:unit && npm run test:e2e` green
- `make verify-batch` green

#### Cohort D — Polish + integration (Iterations 12–13, parallel)

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Toasts | sonner integration on every new mutation | new files in cohort C | `/implement` |
| Agent-A11y | a11y sweep on new pages (axe checks) | new pages in cohort C | `/implement` |
| Agent-Loading-States | skeleton loaders on every new page | new pages in cohort C | `/implement` |

**Gate before Cohort E:**
- `npx @axe-core/cli http://localhost:3000/settings/{notifications,account}` zero violations
- All new pages render skeleton during initial fetch
- `make verify-batch` green

#### Cohort E — Verification + docs + sentinel (Iterations 14–15)

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Verify-Step3 | end-to-end smoke tests | `Makefile` (add `verify-step-3`), `scripts/smoke_step3.sh` | `superpowers:verification-before-completion` |
| Agent-Code-Reviewer | meta — independent diff review | (read-only sweep of cohorts A–D diff) | `feature-dev:code-reviewer` |
| Agent-Docs | — | `CLAUDE.md` (rule #23 if needed: notification rule schema), `docs/harness/IMPLEMENTATION_LOG.md` (Step 3 entry), `harness/eval/evaluation_report.md` (§II.A append + §III strikethroughs) | `/sync` |

**Gate before STEP-3-GREEN:**
- `make verify-step-3` exits 0
- Reviewer agent has no P0/P1 issues open
- `make verify-batch` green
- Sentinel written

---

## Dependency DAG

```
                  ┌──────────────────── ITERATION 1 SETUP ─────────────────────┐
                  │ Add Makefile `migrate-roundtrip` + scripts/migration_      │
                  │ roundtrip.sh + re-verify post-Step-2 pytest baseline       │
                  └──────────────────────────────┬─────────────────────────────┘
                                                 │
            ┌────────────────────────────────────┴──────────────────────────────┐
            │                                                                   │
            ▼                                                                   ▼
┌─────────────────────────────┐                              ┌──────────────────────────────┐
│       COHORT A              │                              │       COHORT B               │
│ (Frontend, sequential)      │      ── parallel ──          │ (Backend, 4 lanes parallel)  │
│  F-01,F-02,F-03 cleanup     │       no file overlap        │  Lane-Notifications (B-01..05│
│  V-01,V-02,V-03 RHF+zod     │                              │    +O-01,O-02 — sequential)  │
│  V-04 CV upload validation  │                              │  Lane-Pipeline (B-06..08)    │
│  V-05 codegen (optional)    │                              │  Lane-Account-Mgmt (B-11..13)│
└─────────────────┬───────────┘                              │  Lane-Discovery (B-09,B-10,  │
                  │                                          │    B-14,B-15)                │
                  │                                          └─────────────────┬────────────┘
                  └──────────────── sync gate ───────────────────────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │           COHORT C                  │
                            │  C-01 (Settings layout) — FIRST     │
                            │  ── then parallel: ──               │
                            │  C-02 (Notification rules UI)       │
                            │  C-03 (Account UI)                  │
                            │  C-04 (Notifications page)          │
                            │  C-05 (Dedup viewer)                │
                            │  C-06 (Version diff)                │
                            │  C-07,C-09,C-11 (Kanban DnD+confirm+timeline)│
                            │  C-08,O-03 (Notes editor)           │
                            │  C-10 (Pipeline filters)            │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │           COHORT D                  │
                            │  Toasts (sonner)                    │
                            │  A11y (axe)                         │
                            │  Loading states                     │
                            │  ── parallel within cohort ──       │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │           COHORT E                  │
                            │  E2E smokes (3 new specs)           │
                            │  Code review (P0/P1 close)          │
                            │  Docs (CLAUDE.md, IMPLEMENTATION_LOG, evaluation_report)│
                            │  Sentinel write + STEP-3-GREEN      │
                            └─────────────────────────────────────┘
```

---

## Critical files to modify (with reuse notes)

| File | Action | Reuse from existing code |
|---|---|---|
| `frontend/src/app/(auth)/login/page.tsx` | consume `useSearchParams().get('next')` for post-login redirect | Next.js 16 router pattern via Context7 |
| `frontend/src/app/(auth)/register/page.tsx` | same `?next` consumption | mirror login |
| `frontend/src/app/dashboard/page.tsx` | simplify TanStack Query keys to `["jobs", "filtered"]` + `["jobs", "all"]` for cleaner invalidation | preserve existing `setJobAction` optimistic update |
| `frontend/src/lib/api-error.ts` | extend with `parseFieldErrors()` returning `Record<string, string>` from Pydantic 422 detail | preserve existing `ApiError` class |
| `frontend/src/components/profile/CVUpload.tsx` | add 5MB size guard + MIME-type allowlist | wire pre-flight to existing upload handler |
| `backend/migrations/0012_notification_rules.up.sql` | new — `notification_rules` table + `users.timezone` column | mirror existing migration style |
| `backend/migrations/0013_user_notification_digests.up.sql` | new — queue table | mirror |
| `backend/migrations/0014_application_history.up.sql` | new — `last_advanced_at`, `interview_dates`, `notes_history` cols + `application_stage_history` table | mirror |
| `backend/src/api/routes/notification_rules.py` | new — 4 routes scoped by `require_user` | mirror `channels.py` structure |
| `backend/src/services/channels/dispatcher.py` | consult rules: filter by `score_threshold`, skip on `quiet_hours`, queue if `notify_mode=digest` | preserve existing `dispatch()` signature |
| `backend/src/workers/tasks.py` | add `send_daily_digest(ctx, user_id)` periodic task + `nightly_ghost_sweep(ctx)` periodic task | follow `send_notification` pattern |
| `backend/src/workers/settings.py` | register both periodic tasks via ARQ `cron_jobs` | extend `WorkerSettings.cron_jobs` |
| `backend/src/api/routes/pipeline.py` | extend with `GET /pipeline/{id}/timeline` + `PATCH /pipeline/{id}/notes` | preserve existing 5 routes |
| `backend/src/repositories/database.py` | add `get_application_timeline`, `update_notes`, `update_email`, `update_password`, `soft_delete_user` | mirror existing repo patterns |
| `backend/src/api/routes/auth.py` | add `DELETE /users/me`, `PATCH /users/me/password`, `PATCH /users/me/email` | reuse `verify_password`, `hash_password`; gate via `require_user` |
| `backend/src/api/routes/jobs.py` | add `GET /jobs/{id}/duplicates` (Option A query-time grouping) | reuse `deduplicator.group_by_normalized_key` |
| `backend/src/api/routes/profile.py` | add `GET /profile/versions/{id1}/diff/{id2}` returning per-field changes | reuse `get_profile_version` |
| `backend/src/api/routes/runs.py` | new — `GET /api/runs/recent` paginated from `run_log` | mirror existing route style |
| `frontend/src/app/settings/page.tsx` | new — landing with tabs | new |
| `frontend/src/app/settings/layout.tsx` | new — tab navigation shell | new |
| `frontend/src/app/settings/notifications/page.tsx` | new — per-channel rule editor | use Slider, RadioGroup, TimePicker primitives (install `react-time-picker` or build via shadcn) |
| `frontend/src/app/settings/account/page.tsx` | new — password change + email change + delete account | RHF + zod from Cohort A |
| `frontend/src/app/notifications/page.tsx` | new — paginated ledger viewer | reuse existing pagination pattern from `/dashboard` |
| `frontend/src/components/jobs/DedupGroupViewer.tsx` | new — list of duplicates as JobCard variants | reuse `JobCard` |
| `frontend/src/components/profile/VersionDiffDrawer.tsx` | new — side-by-side diff (or unified diff) | extend `VersionHistoryDrawer` |
| `frontend/src/components/pipeline/KanbanBoard.tsx` | wire `@dnd-kit` drag handlers + `<NotesEditor>` per card + `<TimelineDrawer>` per card + confirmation dialogs | preserve existing 5-column layout |
| `frontend/src/components/pipeline/NotesEditor.tsx` | new — Dialog + Textarea + auto-save on blur | reuse `Dialog` primitive |
| `frontend/src/components/pipeline/PipelineFilterPanel.tsx` | new — pipeline-scoped filters | mirror `FilterPanel.tsx` shape |
| `Makefile` | add `verify-step-3` aggregate gate | mirror `verify-step-2` |

---

## Verification section

Ralph Loop cannot emit `STEP-3-GREEN` until ALL of this passes.

### Gate command

```bash
make verify-step-3
```

Which runs (in order):

```bash
# 1. Backend regression
cd backend && python -m pytest tests/ -q -p no:randomly --tb=short
# Expect: ≥1,081p / 0f / ≤3s

# 2. Migration round-trip for 0012, 0013, 0014
bash scripts/migration_roundtrip.sh

# 3. Notification rule consultation
cd backend && python -m pytest tests/test_notification_rules_dispatch.py -v

# 4. Account management IDOR audit
cd backend && python -m pytest tests/test_account_mgmt_idor.py -v

# 5. Ghost-detection nightly sweep
cd backend && python -m pytest tests/test_ghost_sweep.py -v

# 6. Frontend unit + e2e
cd ../frontend && npm run lint && npm run type-check && npm run test:unit && npm run test:e2e

# 7. Step-3-specific E2E specs
cd frontend && npm run test:e2e -- --grep "notification-rules-flow|account-delete-flow|pipeline-timeline-flow"

# 8. Form validation behaviour
cd frontend && npm run test:e2e -- --grep "form-422-error-mapping"

# 9. A11y sweep on new pages
cd frontend && npx @axe-core/cli http://localhost:3000/settings/notifications http://localhost:3000/settings/account http://localhost:3000/notifications

# 10. Step-1.6 contract gate
make verify-batch

# 11. Pre-commit gate
cd .. && pre-commit run --all-files
```

### Sentinel write (after gate passes)

```bash
echo "$(git rev-parse HEAD)" > .claude/step-3-verified.txt
git add .claude/step-3-verified.txt
git commit -m "chore(step-3): write sentinel at green commit"
```

### End-to-end proof (human dogfood after sentinel)

1. Register → log in → go to `/settings/notifications` → set rule "Slack on score ≥80, instant"
2. Run a search → see JobCard with score 85 → confirm Slack notification arrives within 10s
3. Set quiet hours 22:00-07:00 → trigger search at 23:00 → confirm no notification
4. Switch to digest mode for email → trigger 3 high-score jobs → confirm single digest at next configured time
5. Open `/settings/account` → change password → log out → log in with new password
6. Open `/notifications` → see ledger entries with channel + status + timestamp
7. Open a JobCard with `dedup_group_ids` → see "3 duplicates" badge → click → DedupGroupViewer renders alternates
8. Open `/profile` → version drawer → click "Compare with v2" → diff renders
9. KanbanBoard: drag a card from Applied → Interview → confirmation dialog → confirm → toast → timeline drawer shows transition
10. Edit notes inline on a Kanban card → blur → toast confirms auto-save
11. Open `/settings/account` → "Delete my account" → confirm dialog → user soft-deleted → log in fails → register fresh works
12. Verify `make verify-step-3` exits 0; sentinel written; tag `step-3-green` ready

---

## Execution budget

- Ralph Loop: max 16 iterations (expect 12-14)
- Wall-clock: 2-3 sessions
- Commits: 1 per iteration (partial progress) + 1 final merge commit to `main`
- Branch: `step-3-batch` off `main @ 9868877`
- Worktree: dual-worktree (generator implements; reviewer verifies via `scripts/review_batch.sh`)
- Merge strategy: fast-forward only to main; rebase reviewer onto main if it lags
- Tag: `step-3-green` on the final commit

---

## Acknowledged trade-offs

- **Cohort A is sequential, not parallel.** Form validation library must land before any new form is built — otherwise Step-3 forms ship in the legacy style and need re-migration. ~1 extra iteration cost; eliminates ~5 cleanup iterations later.
- **Notification rules schema-first.** Migration 0012 ships before backend route, which ships before dispatcher rewrite, which ships before UI. 4-layer dependency. Cohort B Lane-Notifications is sequential within itself.
- **`@dnd-kit/core` over `react-dnd`.** `@dnd-kit` is ~10KB gzipped (smaller), accessibility-first (built-in keyboard support), and has the best Next.js 16 + React 19 compatibility. `react-dnd` is older and heavier. **Rationale captured inline in this trade-offs section** — Step 3 does not establish `docs/adr/` (deferred to Step 5 per evaluation report X-04); when ADRs land, this decision migrates as ADR-001.
- **Cohort A and Cohort B run in parallel.** They have zero file overlap (frontend-only vs backend-only). This is a deliberate concurrency optimisation — saves ~3 iterations of wall-clock. Risk is minimal: if either fails, the other is unaffected. The Cohort-C gate is the synchronisation point.
- **`Option A` for dedup endpoint** (re-run grouping at query time). Slower per-query than persisted dedup_groups table but ships in 2h instead of 1d. Acceptable at <1k jobs/day; revisit at scale.
- **Email change uses confirm-via-current-password, not magic-link.** Full magic-link flow needs SES wiring (Step 5). MVP confirm-by-password is safe enough for early users.
- **Timezone column on `users` is opinionated.** We assume the user's timezone is fixed; in reality, frequent travelers' quiet hours don't follow them. For Job360's UK target, `Europe/London` default is fine.
- **Test floor +25 backend +20 frontend.** Conservative — could ship fewer if budget tight. But Step 1.5 + Step 2 each shipped ~30 new tests; this matches the cadence.
- **No bulk multi-select on KanbanBoard.** Defer to Step 4. Single-card actions are sufficient for MVP launch.
- **No `/admin/runs` UI.** B-15 ships the backend route; UI defers to Step 4 (ops dashboard home).

---

## Post-Step-3 follow-ups (explicitly tracked)

1. **Step 4 — Ops hardening.** GitHub Actions CI matrix, Dockerfile + docker-compose, deploy platform config, secret manager integration, security headers middleware, `/livez` + `/readyz` split, worker timeouts (R-01..R-11 from evaluation report), pip-audit + npm audit + gitleaks + bandit in CI, FastAPI request timeout middleware (T-02), LLM call timeouts (T-04), per-query DB deadlines (T-03), DB backup script + restore drill, `/admin/runs` UI consuming B-15.
2. **Step 5 — Launch readiness (Batch 4).** ICO £40 registration, privacy notice, terms, cookie banner, AI-Act CV-scoring disclosure, password-reset (forgot-password) flow with SES, friend dogfood, ASA marketing audit, freemium metering, source scope-down, prod-Redis smoke.
3. **Step-3 carry-overs (if budget pressure):** OpenAPI codegen (V-05), `/admin/runs` UI, bulk multi-select, account email re-verification magic-link.
4. **Architecture decision:** When per-user job count grows beyond ~10k, migrate dedup endpoint from Option A (query-time grouping) to Option B (persisted `dedup_groups` table with backfill).

---

_Plan written 2026-04-26 in plan mode-ready format. Anchor verification: 7 sub-agent audits on `frontend/src/**` + `backend/src/**` at `main @ 9868877`. **Reviewer pass 2026-04-26 applied 13 fixes** (5 P1 + 6 P2 + 2 P3): migration numbering reconciled, quiet-hours timezone logic explicit in B-03, `applications.notes` vs `notes_history` collision spec'd in B-06, B-13 email-change disambiguated to confirm-via-current-password, B-14 ghost-sweep test file made explicit, iteration cap raised 16→20, Cohort A and Cohort B parallelisation called out, Cohort C internal dependency (C-01 first) flagged, ADR-001 reference inlined, baseline drift annotated, iteration-1 setup tasks added (`migrate-roundtrip` Makefile target + `scripts/migration_roundtrip.sh`). User-confirmed decisions: **(1)** run Step 3 as a single Ralph-Loop-driven batch covering 28 deliverables + 3 should-fix items; **(2)** Cohort A sequential within itself; **(3)** Cohort A + Cohort B run in parallel (zero file overlap, ~3 iterations saved); **(4)** schema-first within Cohort B Lane-Notifications; **(5)** Option A for dedup endpoint (defer Option B); **(6)** mirror this plan to `docs/harness/step_3_plan.md` for dual-worktree handoff; **(7)** account email change ships confirm-via-current-password (defer magic-link to Step 5)._
