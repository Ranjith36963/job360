# Step 2 — API→UI Seam — Ralph-Loop-Driven Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`, `superpowers:dispatching-parallel-agents`, `superpowers:executing-plans`. Steps use checkbox syntax.
>
> **Pre-reqs carried from Step 1.5:** `main @ <step-1-5-green SHA>`, ≥1,056p / 0f / 3s backend baseline, all 9 score-dim fields + 5 date-model fields + ~13 enrichment fields persisted on `JobResponse`, `user_profile_versions` writes on every save, JSON Resume export endpoint live, ESCO normalisation wired into `cv_parser`, S3-MVP profile endpoints live (`GET /profile/versions`, `POST /profile/versions/{id}/restore`, `GET /profile/json-resume`).
>
> **frontend/AGENTS.md directive:** *"This is NOT the Next.js you know. Read `node_modules/next/dist/docs/` before writing any code."* — every cohort agent MUST consult Context7 (`mcp__plugin_context7_context7__query-docs` for `next.js`) and the local Next 16 docs before introducing App Router patterns, `generateMetadata`, server actions, middleware, or async client components.

---

## Context

**Why this change is being made.** Step 1 + Step 1.5 closed the engine→API seam. The backend now persists 9 score-dim fields, 5 date-model fields, ~13 enrichment fields, profile versions, ESCO-normalised skills, JSON Resume exports, and 3 new S3-MVP endpoints. The frontend uses **almost none of it.** Two parallel audit waves (6 + 8 = 14 sub-agent explorations) on `main @ step-1-5-green` surfaced **the backend has lapped the frontend by ~40 distinct features** spread across 8 surfaces (types, ScoreRadar, FilterPanel, JobCard, JobDetail, Profile, Pipeline, Auth).

**What prompted it.** Following the "seams before surfaces" doctrine from `docs/ExecutionOrder.md`: Step 0 unblocked onboarding, Step 1 wired engine→API, Step 1.5 closed the bombshell + S3-MVP. Step 2 is the first batch where **value becomes visible to the end user.** Without it, the Pipeline page is empty (clicking "Apply" doesn't sync), the radar shows the legacy 4 dims (not the new 7), enrichment fields exist in the JSON but render nowhere, profile versions silently accumulate with no UI to browse them, and `?mode=hybrid` has no toggle to invoke it.

**Intended outcome.** After Step 2 completes:
- Every component that consumes `JobResponse` reads the new fields (seniority, salary structured, visa enum, workplace enum, employment_type, required_skills, title_canonical, industry, date-model)
- ScoreRadar's prop interface mirrors `JobResponse` field names — future callers can pass `job` whole without remapping
- FilterPanel exposes 8 new controls + hybrid-mode toggle + sort options
- Profile page surfaces version history + JSON Resume export + ESCO normalised-skill display
- Pipeline page wires `createPipelineApplication()` to the JobCard / JobDetail Apply CTAs (no more silent loop break)
- Auth: logout button in Navbar, `middleware.ts` for protected routes, structured `ApiError` typed errors, sonner toasts
- JobPosting JSON-LD on `/jobs/[id]` for Google for Jobs eligibility (free distribution lever)
- Per-page `generateMetadata` for Open Graph + Twitter cards (LinkedIn share previews)
- Vitest + React Testing Library + Playwright stack established + ≥30 component tests + ≥5 E2E smokes — the missing test floor that allowed Steps 1 and 1.5 to ship bombshells
- Frontend lint gains `eslint-plugin-jsx-a11y`; CI runs `lint + type-check + test:unit + test:e2e`
- All Step-1.5 telemetry (`per_source_duration`, `per_source_errors`) surfaced on the dashboard
- Test suite is green: backend ≥1,056p / 0f / 3s preserved + ≥30 frontend tests passing
- Every subsequent step (Step 3 endpoints expansion, Step 4 launch readiness) can trust the UI honestly reflects backend state

---

## Strategic context — why Ralph Loop for Step 2

Step 2 is a **wiring + foundations batch**, not a feature-design batch. The audits revealed three patterns:

1. **Backend lapped frontend.** ~40 fields exist in `JobResponse`/`ProfileResponse`/etc. that the frontend either has missing types, missing components, or unreached components. Pure plumbing — high parallelism opportunity once types land first.
2. **No test floor.** Frontend has zero tests. Steps 1 and 1.5 shipped bombshells partly because pytest gates ran but no DOM assertions ran. Step 2 must establish Vitest + RTL + Playwright **before** components are touched, so every cohort change is value-presence-tested per CLAUDE.md rule #21.
3. **Foundation-then-features dependency.** `request()` helper rewrite (typed `ApiError`) + `middleware.ts` (session validation) + sonner toast + `EmptyState` shared component + ESLint a11y → these block ~70% of cohort B/C/D work. Cohort A must land them first, in single-agent sequential mode (no conflict tolerance).

Ralph Loop provides the cohort-gating discipline + verification halt sentinel + retry-on-red. Parallel sub-agents do the inner execution, dispatched in 5 cohorts across ~10–14 iterations.

**Ralph Loop's role:** outer supervision + verification gate + halt sentinel + commit cadence.
**Parallel sub-agents' role:** inner execution, dispatched in 5 cohorts (A→E).

---

## Scope — what Step 2 covers (and what it doesn't)

### In scope — 22 critical blockers (must all land)

| # | Blocker | Anchor | Tier | Source |
|---|---|---|---|---|
| **F1** | `request()` helper has no 4xx/5xx differentiation, no typed `ApiError`, no 429 retry | `frontend/src/lib/api.ts:28-42` | T1 | Wave-2 |
| **F2** | No `app/error.tsx`, no `app/not-found.tsx`, no `middleware.ts` for session validation | App Router root | T1 | Wave-2 |
| **F3** | No frontend test framework (zero tests, zero a11y tooling, zero CI gate) | `frontend/package.json` | T1 | Wave-2 |
| **F4** | `eslint-plugin-jsx-a11y` not enabled | `frontend/eslint.config.mjs` | T2 | Wave-2 |
| **T1** | `JobResponse.dedup_group_ids` missing from `frontend/src/lib/types.ts` | `backend/src/api/models.py:96` ↔ types.ts | T1 | Wave-1 |
| **T2** | `ProfileResponse` missing 7 fields (`skill_tiers`, `skill_esco`, `skill_provenance`, `linkedin_subsections`, `github_temporal`, `current_version_id`) | types.ts vs models.py | T1 | Waves 1+2 |
| **T3** | `CVDetail` missing 6 fields (`name`, `headline`, `location`, `achievements`, `highlights`, `companies`) | types.ts vs models.py | T2 | Waves 1+2 |
| **T4** | `JobFilters` missing 10 fields (seniority, employment_type, workplace_type, salary_min/max, required_skills, title_canonical, industry, posted_after/before, staleness_state, sort_by) | types.ts | T2 | Wave-1 |
| **T5** | `SearchStatusResponse` missing `per_source_duration` + `per_source_errors` | types.ts | T2 | Wave-2 |
| **A1** | 3 unimplemented S3-MVP API methods (`getProfileVersions`, `restoreProfileVersion`, `getJsonResume`) + missing types | `frontend/src/lib/api.ts` | T1 | Wave-1 |
| **A2** | `createPipelineApplication()` exported but never called from JobCard or JobDetail Apply CTA | `api.ts:210-216`, `JobCard.tsx:171-183`, `[id]/page.tsx:420-429` | T1 | Waves 1+2 |
| **C1** | ScoreRadar prop interface uses `seniority`/`location` — fragile contract; align with `JobResponse` field names so callers can pass `job` whole | `ScoreRadar.tsx:17-26` | T2 | Waves 1+2 (verified) |
| **C2** | ScoreRadar has no null guards, no per-axis colors, no tooltips, no a11y (`role`/`aria-label`) | `ScoreRadar.tsx` full file | T2 | Wave-1 |
| **C3** | JobCard renders only `match_score` — no dim breakdown, no enrichment surfacing, no date-model display | `JobCard.tsx:78-80,108-137` | T1 | Waves 1+2 |
| **C4** | JobDetail renders 0 of 10 enrichment fields; date-model only shows `date_found`; description redirects external | `[id]/page.tsx:224,283,420-429` | T1 | Wave-2 |
| **C5** | FilterPanel missing 8 controls + hybrid-mode toggle + sort options | `FilterPanel.tsx:102-220` | T1 | Wave-1 |
| **C6** | Profile page: no version history UI, no JSON Resume export button, no ESCO display | `app/profile/`, `CVUpload.tsx:188`, `CVViewer.tsx:99-107` | T1 | Wave-1 |
| **C7** | Pipeline KanbanBoard read-only — no drag-and-drop, notes preview-only, no advance handler from JobCard | `KanbanBoard.tsx:28-84,168-172` | T2 | Wave-1 |
| **U1** | No logout button in Navbar; no `AuthProvider` context | `Navbar.tsx:18-22` | T1 | Wave-1 |
| **U2** | Navbar missing `/jobs` and `/settings/channels` links (Channels page is built but unreachable) | `Navbar.tsx:18-22` | T1 | Wave-2 |
| **U3** | Forced dark mode at `layout.tsx:41` — no toggle, no `ThemeProvider` | `layout.tsx:41`, `Navbar.tsx` | T2 | Wave-2 |
| **S1** | No per-page `generateMetadata` on `/jobs/[id]` (`'use client'` blocks it); no Open Graph / Twitter cards on root layout; no JSON-LD `JobPosting` | `[id]/page.tsx:1`, `layout.tsx:27-31` | T1 | Wave-2 |

### Also in scope — 6 should-fix observability + UX items

Step 1.5 ships telemetry the user can't see. Step 2 surfaces it.

| # | Should-fix | Anchor |
|---|---|---|
| O1 | Run history surface ("last run: 5 min ago, 23 new jobs") + per-source breakdown badges | `dashboard/page.tsx:317-337`, `SearchStatusResponse` |
| O2 | Sonner toast library + 429-specific UX (button disable + "wait X seconds") | `package.json`, `request()`, `dashboard/page.tsx:215-220` |
| O3 | Shared `EmptyState` component (replace per-page ad-hoc) | new `components/ui/empty-state.tsx` |
| O4 | Shared `ApplyButton` component — used by both JobCard + JobDetail (DRY for `createPipelineApplication()` wiring) | new `components/jobs/ApplyButton.tsx` |
| O5 | Versioned navigation drawer for profile (`VersionHistoryDrawer.tsx`) + `JsonResumeExportButton.tsx` | new components |
| O6 | TanStack Query for caching + `getJobs()` stale-while-revalidate + optimistic update consolidation | `package.json`, all pages |

### Non-scope (explicitly deferred)

- **Per-job notification preferences UI** — backend supports per-channel/per-urgency rules, but the surface area is large enough to warrant its own batch. Defer to Step 3 (endpoints + settings expansion).
- **Form validation library** (zod + react-hook-form) — the existing 2 forms (login, register, preferences) work via HTML5 + ad-hoc state. Migration is wide-blast but low-urgency. Defer to Step 3.
- **Confirmation dialogs** for destructive actions — `not_interested` fires immediately. Add an "Undo" toast (Cohort D scope) instead of a blocking dialog; full confirm-modal pattern can wait.
- **Storybook + visual regression** (chromatic / percy) — high upfront cost, low Step-2 ROI. Defer to Step 4 launch readiness.
- **GDPR / privacy / terms / cookie banner content** — `Footer.tsx` gets the slot wired in Step 2; Batch 4 (launch readiness, per `MEMORY.md`) fills the legal copy + ICO £40 registration.
- **PWA manifest + apple-touch-icons** — nice-to-have, not first-impression critical. Defer to Step 4.
- **Backend dashboard double-fetch consolidation** (`/jobs?include_bucket_counts=true`) — backend change, defer to a backend follow-up batch.
- **Image hero optimization via `next/image`** — current CSS gradient hero performs fine. Defer.
- **Per-job notification ledger surface ("you've been notified")** — UX nice-to-have; defer to Step 3.
- **Description rendering on `/jobs/[id]` instead of source redirect** — legal grey area (ToS on LinkedIn/Indeed); current "redirect to source" is the safe call. Defer until ToS audit.

---

## Tool orchestration — what each tool does

### Ralph Loop (outer driver)

**Invocation:** `/ralph-loop` with `completion_promise: "STEP-2-GREEN"` and `max_iterations: 18` (Step 1.5 burned ~10; Step 2 has 22 blockers + 6 should-fix items but most are independent).

**Each iteration:**
1. Check sentinel: does `.claude/step-2-verified.txt` exist? If yes, emit `STEP-2-GREEN` and halt.
2. Run `make verify-step-2` (added in iteration 1 as a new Makefile target).
3. Parse output → identify which blockers are still red.
4. Dispatch the cohort of sub-agents that matches the current dependency stage.
5. Re-run verification, write/update sentinel if all green.
6. Commit partial progress with a conventional-commit prefix.

**Stop criteria (all must hold before emitting `STEP-2-GREEN`):**
- All 22 blockers (F1–F4, T1–T5, A1–A2, C1–C7, U1–U3, S1) have landed commits
- All 6 should-fix items (O1–O6) have landed commits
- Backend regression: `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly` ≥1,056p / 0f / 3s (baseline preserved)
- Frontend unit tests: `cd frontend && npm run test:unit` ≥30 passing tests, 0 failing, ≥80% coverage on touched components
- Frontend type-check: `npm run type-check` exits 0 (no `any`, no implicit any, no missing JobResponse field references)
- Frontend lint: `npm run lint` exits 0 with `eslint-plugin-jsx-a11y` enabled
- Frontend E2E: `npm run test:e2e` ≥5 Playwright smokes passing (auth flow, dashboard load, JobCard render, profile version restore, pipeline advance)
- Value-presence end-to-end: a Playwright smoke navigates to a seeded job, asserts the radar renders ALL 8 dim values non-zero, asserts ≥3 enrichment fields render non-empty, asserts the staleness badge shows
- Lighthouse / Chrome DevTools MCP: dashboard mobile score ≥90 perf, ≥95 a11y, ≥95 best-practices, ≥95 SEO
- LinkedIn share preview: a curl to a `/jobs/{id}` page shows og:title + og:description + og:image meta tags non-default
- Google JobPosting JSON-LD: page source includes a `<script type="application/ld+json">` block with `"@type": "JobPosting"`
- `make verify-step-2` exits 0 — single source of truth for "green"
- Sentinel file `.claude/step-2-verified.txt` contains the final green commit SHA

### Parallel sub-agents (inner execution — 5 cohorts)

Driven by skill `superpowers:dispatching-parallel-agents`. Each iteration launches the cohort matching the dependency stage. Cohorts A→E run in order; within a cohort, agents are parallel.

#### Cohort A — Foundations (Iterations 1–3, sequential within cohort)

These changes are prerequisites for every downstream component change. **Sequential dispatch within cohort A** (each agent's PR feeds the next).

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Test-Stack | F3, F4 | `frontend/package.json` (add Vitest, RTL, jest-dom, user-event, coverage, jsx-a11y, Playwright), `frontend/vitest.config.ts` (new), `frontend/playwright.config.ts` (new), `frontend/eslint.config.mjs` (add jsx-a11y), `.github/workflows/frontend.yml` (new) | `update-config` + `superpowers:test-driven-development` |
| Agent-Types-Sync | T1, T2, T3, T4, T5 | `frontend/src/lib/types.ts` (add 30+ missing fields across 5 interfaces) | `/sync` |
| Agent-Api-Client | A1, F1 | `frontend/src/lib/api.ts` (add 3 S3-MVP methods, rewrite `request()` with typed `ApiError`, status-code dispatch, 429 retry hook), new `frontend/src/lib/api-error.ts` | `/implement` + `superpowers:test-driven-development` |
| Agent-Error-Boundaries | F2 | `frontend/src/app/error.tsx` (new), `frontend/src/app/not-found.tsx` (new), `frontend/src/middleware.ts` (new — session-cookie validation + 307 redirect to `/login` for protected routes), `frontend/src/components/ui/error-boundary.tsx` (new for nested boundaries) | `/implement` |
| Agent-Shared-UI | O3, O2 (toast install) | new `frontend/src/components/ui/empty-state.tsx`, install `sonner`, mount `<Toaster />` in root layout, expose `toast` helper at `frontend/src/lib/toast.ts` | `/implement` |

**Gate before cohort B:** `npm run lint && npm run type-check && npm run test:unit` exits 0; `request()` helper unit-tested with 4 scenarios (200 ok, 401 redirect signal, 429 typed retry-after, 500 generic); a Playwright smoke navigates to `/dashboard` while unauthenticated → asserts redirect to `/login`.

#### Cohort B — Component foundations (Iterations 4–6, parallel within cohort)

Depends on cohort A (types must exist; toast must mount; ApiError must throw).

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-ScoreRadar | C1, C2 | `frontend/src/components/jobs/ScoreRadar.tsx` (rename props to `seniority_score`/`location_score`; add null guards; add tooltips; add `role="img"` + `aria-label`; add framer-motion stagger reveal), `frontend/src/components/jobs/ScoreRadar.test.tsx` (new — 6 tests including value-presence per CLAUDE.md rule #21) | `/implement` + `superpowers:test-driven-development` |
| Agent-JobCard | C3 | `frontend/src/components/jobs/JobCard.tsx` (add salary range, seniority pill, workplace pill, visa enum, staleness badge, dim-breakdown popover, null guards on `matched_skills`), `JobCard.test.tsx` (new) | `/implement` + `superpowers:test-driven-development` |
| Agent-FilterPanel | C5 | `frontend/src/components/jobs/FilterPanel.tsx` (add 8 controls: Combobox for seniority, Slider for salary range, RadioGroup for workplace, Select for employment_type, multi-select for required_skills, Switch for staleness toggle, DateRange for posted_at, Select for industry, Toggle for hybrid mode), `FilterPanel.test.tsx` (new — debounce + apply test); fix visa naming drift (`visa_only` → `visa_sponsorship`) | `/implement` + `superpowers:test-driven-development` |
| Agent-EmptyStates | O3 (consume) | replace ad-hoc empty-state JSX in `JobList.tsx`, `pipeline/page.tsx`, `profile/page.tsx`, `settings/channels/page.tsx` with shared `<EmptyState>` from cohort A | `/sync` |

**Gate before cohort C:** `npm run test:unit` ≥18 tests passing (Cohort B alone); a Playwright smoke renders a JobCard with non-zero dim values + staleness badge visible.

#### Cohort C — Page surfaces + connector flows (Iterations 7–9, parallel)

Depends on cohort B (components must exist before pages can compose them).

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-JobDetail | C4, A2 | `frontend/src/app/jobs/[id]/page.tsx` (render 10 enrichment fields, add date-model display with staleness badge, integrate shared `<ApplyButton>` that calls both external link + `createPipelineApplication()`, add "Save to Pipeline" CTA, add "I applied" checkbox, add description-snippet section if backend supplies it); also create `frontend/src/components/jobs/ApplyButton.tsx` (O4) | `/implement` + `superpowers:test-driven-development` |
| Agent-Profile | C6, O5 | `frontend/src/app/profile/page.tsx` (consume new ProfileResponse fields), new `frontend/src/components/profile/VersionHistoryDrawer.tsx` (uses Sheet + lists versions + restore button), new `frontend/src/components/profile/JsonResumeExportButton.tsx`, update `CVUpload.tsx`+`CVViewer.tsx` to render ESCO-normalised skills with raw→canonical mapping, surface `skill_tiers` as 3 columns | `/implement` + `superpowers:test-driven-development` |
| Agent-Auth | U1, U2 | new `frontend/src/components/layout/AuthProvider.tsx` (Context + `me()` fetch + logout helper), update `Navbar.tsx` to add `/jobs` + `/settings/channels` links + logout button + user-email display, wire to AuthProvider, update homepage CTAs to be auth-aware | `/implement` |
| Agent-Pipeline-Wire | A2 (Pipeline side), C7 partial | wire `JobCard.tsx` Apply button to also call `createPipelineApplication()` via shared `<ApplyButton>` from Agent-JobDetail; advance-stage button on KanbanBoard already works — confirm it surfaces sonner toast on success/failure | `/implement` |
| Agent-Theme-Toggle | U3 | new `frontend/src/components/layout/ThemeProvider.tsx` (next-themes), add toggle to Navbar, persist preference to localStorage; keep dark as default | `/implement` |

**Gate before cohort D:** Playwright E2E: register → login → upload CV → view profile → restore previous version → return to dashboard → click JobCard Apply → see KanbanBoard show new "applied" entry. Single user-flow asserts ~7 components in sequence.

#### Cohort D — Polish + observability (Iterations 10–12, parallel)

Depends on cohorts B + C (UI surfaces must exist before SEO + telemetry can wrap them).

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-SEO | S1 | refactor `frontend/src/app/jobs/[id]/page.tsx` into a server-component shell (`page.tsx` exports `generateMetadata` + renders `<JobDetailClient>`) + client body (`JobDetailClient.tsx`), add Open Graph + Twitter cards to root `layout.tsx`, add `JobPosting` JSON-LD `<script>` block with structured fields from `JobResponse`, create `frontend/src/app/sitemap.ts` + `frontend/src/app/robots.ts`, add canonical URL meta from `title_canonical` | `/implement` + Context7 (Next.js 16 `generateMetadata` patterns) |
| Agent-RunSurface | O1, O2 | `dashboard/page.tsx` (add "Last run: X min ago" header card, per-source progress dots during search, 429-aware run-button disable with sonner toast), backend may need `SearchStatusResponse` payload extension (T5 gate already added the types) | `/implement` |
| Agent-Caching | O6 | install `@tanstack/react-query`, add `<QueryClientProvider>` to root layout, refactor 3 hottest fetches (`getJobs`, `getProfile`, `getPipelineApplications`) to `useQuery`, keep optimistic-update logic via `useMutation` + `setQueryData`, add 30s `staleTime` for jobs / 5min for profile | `/implement` + Context7 (TanStack Query v5 patterns) |
| Agent-Footer | (deferred-content, slot only) | `frontend/src/components/layout/Footer.tsx` add link slots for Privacy / Terms / Contact / GitHub repo (content TBD in Batch 4) | `/sync` |

**Gate before cohort E:** Lighthouse mobile run on `/jobs/{id}` shows ≥95 SEO; `curl /jobs/1 | grep og:title` returns non-default; React Query DevTools shows cached jobs across navigations.

#### Cohort E — Verification + docs + sentinel (Iterations 13–14)

Depends on cohorts A+B+C+D.

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-E2E-Smokes | F3 (final tests) | `frontend/tests/e2e/auth-flow.spec.ts`, `frontend/tests/e2e/job-render.spec.ts` (value-presence: dim ≠ 0 AND enrichment ≠ empty), `frontend/tests/e2e/profile-version-restore.spec.ts`, `frontend/tests/e2e/pipeline-advance.spec.ts`, `frontend/tests/e2e/share-preview.spec.ts` (assert og:title + JSON-LD) | `superpowers:test-driven-development` + `superpowers:verification-before-completion` |
| Agent-Code-Reviewer | meta — independent diff review | (read-only sweep of cohorts A–D diff) | `feature-dev:code-reviewer` (subagent_type) |
| Agent-Docs | — | `CLAUDE.md` (add rule #22 — pinned Next.js 16 docs reference), `docs/IMPLEMENTATION_LOG.md` (Step 2 entry with blocker closure table + test delta), `docs/step_2_plan.md` (mirror of executed plan) | `/sync` |

**Gate before STEP-2-GREEN:** `make verify-step-2` exits 0; Playwright value-presence smoke green; reviewer has no P0/P1 issues open; sentinel written.

### Skills (invoked inside agents)

| Skill | Used by | Purpose |
|---|---|---|
| `superpowers:writing-plans` | this document | already in use |
| `superpowers:executing-plans` | Ralph Loop iteration orchestrator | picks the next unfinished blocker from the cohort DAG |
| `superpowers:dispatching-parallel-agents` | each iteration's agent dispatch | batches cohort A→E agents |
| `superpowers:test-driven-development` | every component agent (Cohorts A, B, C, D, E) | RED-first for value-presence tests per rule #21 |
| `superpowers:verification-before-completion` | Ralph Loop gate | cannot emit `STEP-2-GREEN` until `make verify-step-2` passes |
| `superpowers:systematic-debugging` | Agent-SEO (App Router server/client split is finicky), Agent-Caching (React Query hydration with Next 16) | when patterns surprise, isolate root cause via Context7 first |
| `superpowers:receiving-code-review` | Cohort E review cycle | respond with evidence not performance |
| `commit` | end of each iteration | conventional-commit partial-progress snapshot |
| `update-config` | iteration 1 | add `make verify-step-2` target + `frontend/vitest.config.ts` + `frontend/playwright.config.ts` + GitHub Actions workflow + pre-commit hook for frontend |
| `less-permission-prompts` | iteration 1 | pre-allow frequent calls (`npm run lint`, `npm run test:unit`, `npm run test:e2e`, `npm run type-check`, `npm run dev`, `npm run build`, `pytest`) |

### MCP servers (mandatory inside agents per AGENTS.md directive)

| MCP | Used by | Purpose |
|---|---|---|
| **Context7** (`mcp__plugin_context7_context7__query-docs`) | every agent that touches Next 16 / React 19 / Tailwind 4 / TanStack Query v5 / next-themes / sonner | mandatory per `frontend/AGENTS.md` — Next.js 16 has breaking changes vs training data |
| **Chrome DevTools MCP** | Agent-SEO, Agent-Caching, Cohort E gate | Lighthouse audits (perf/a11y/SEO ≥95 each), JSON-LD verification, share-preview rendering, network-tab diagnosis |
| **Playwright MCP** | Agent-E2E-Smokes, Cohort D gate, value-presence verification | scripted smokes — auth flow, JobCard value-presence, profile version restore |
| **IDE diagnostics** (`mcp__ide__getDiagnostics`) | every agent post-edit | catch TS errors before commit |

### Subagent types (framework-level)

| Subagent type | Usage |
|---|---|
| `Explore` | already used in 14 audits; not needed during execution |
| `feature-dev:code-reviewer` | Cohort E **mandatory** — review the accumulated Step-2 diff (~25 files, ~2,000 LOC) before STEP-2-GREEN |
| `feature-dev:code-architect` | iteration 1 only: confirm React Query as the cache choice (vs SWR) before locking in `<QueryClientProvider>` |
| `codex:codex-rescue` | escape hatch — invoke if 3 Ralph iterations pass without progress on a specific blocker |
| `coderabbit:code-reviewer` | optional layer for the correctness-critical files: `request()` helper, `middleware.ts`, ScoreRadar prop rename, ApplyButton |

---

## Dependency DAG

```
                            ┌─────────────────────────────────────┐
                            │              COHORT A               │
                            │  F3,F4 (test stack + a11y eslint)   │
                            │  T1–T5 (types sync — 30+ fields)    │
                            │  A1,F1 (api client + ApiError)      │
                            │  F2     (error boundary + middleware)│
                            │  O3,O2  (EmptyState + Sonner)       │
                            │  ── sequential within cohort ──     │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │              COHORT B               │
                            │  C1,C2  (ScoreRadar + a11y + tests) │
                            │  C3     (JobCard enrichment)        │
                            │  C5     (FilterPanel 8 controls)    │
                            │  O3-c   (consume EmptyState)        │
                            │  ── parallel within cohort ──       │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │              COHORT C               │
                            │  C4,A2  (JobDetail + ApplyButton)   │
                            │  C6,O5  (Profile + version drawer)  │
                            │  U1,U2  (Auth + Navbar links)       │
                            │  A2-p   (Pipeline wire)             │
                            │  U3     (Theme toggle)              │
                            │  ── parallel within cohort ──       │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │              COHORT D               │
                            │  S1     (SEO + JSON-LD + sitemap)   │
                            │  O1,O2  (Run history + 429 toast)   │
                            │  O6     (TanStack Query)            │
                            │  Footer slot                         │
                            │  ── parallel within cohort ──       │
                            └─────────────────┬───────────────────┘
                                              │
                            ┌─────────────────▼───────────────────┐
                            │              COHORT E               │
                            │  E2E smokes (5 specs)               │
                            │  Code review (P0/P1 close)          │
                            │  Docs (CLAUDE.md, IMPLEMENTATION_LOG)│
                            │  Sentinel write + STEP-2-GREEN      │
                            └─────────────────────────────────────┘
```

Each arrow is a hard dependency. **Cohort A is sequential** because each agent's output is consumed by the next (test stack → types → api client → boundaries → shared UI). **Cohorts B/C/D are parallel within** but gated between.

---

## Critical files to modify (with reuse notes)

| File | Action | Reuse from existing code |
|---|---|---|
| `frontend/package.json` | add `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `@vitest/coverage-v8`, `eslint-plugin-jsx-a11y`, `@playwright/test`, `sonner`, `next-themes`, `@tanstack/react-query`, `@tanstack/react-query-devtools` | none — new deps |
| `frontend/vitest.config.ts` | new — JSDOM env, RTL setup, alias `@/*` | follow Vite + Vitest 2 docs via Context7 |
| `frontend/playwright.config.ts` | new — Chromium + Firefox + WebKit, baseURL `http://localhost:3000` | Playwright 1.50+ docs via Context7 |
| `frontend/src/test/setup.ts` | new — `@testing-library/jest-dom/vitest` import + global cleanup | RTL standard pattern |
| `frontend/eslint.config.mjs` | extend with `eslint-plugin-jsx-a11y/recommended` | existing `eslint-config-next` setup |
| `.github/workflows/frontend.yml` | new — npm ci → lint → type-check → test:unit → test:e2e | mirror existing `.github/workflows/backend.yml` if present |
| `frontend/src/middleware.ts` | new — Next.js 16 middleware checking `job360_session` cookie; `matcher` for `/dashboard`, `/profile`, `/pipeline`, `/settings`, `/jobs/:id`; redirect to `/login?next=...` if missing | Next.js middleware docs via Context7 |
| `frontend/src/app/error.tsx` | new — App Router error boundary with retry button + sonner toast | Next.js App Router error.tsx docs |
| `frontend/src/app/not-found.tsx` | new — 404 page with home link | Next.js not-found.tsx docs |
| `frontend/src/lib/types.ts` | add 30+ missing fields across `JobResponse`, `ProfileResponse`, `CVDetail`, `JobFilters`, `SearchStatusResponse`; new types `ProfileVersionsListResponse`, `ProfileVersionSummary`, `JsonResumeResponse` | mirror exact Pydantic field names from `backend/src/api/models.py` |
| `frontend/src/lib/api-error.ts` | new — `class ApiError extends Error { status, code, detail, retryAfter }` | follow JS error subclass pattern |
| `frontend/src/lib/api.ts` | rewrite `request()` to throw `ApiError` typed errors; add `getProfileVersions()`, `restoreProfileVersion(id)`, `getJsonResume()`; preserve `credentials: "include"` | preserve existing `qs()` helper at lines 45-54 |
| `frontend/src/lib/toast.ts` | new — re-export sonner `toast` with project-styled defaults | sonner docs via Context7 |
| `frontend/src/components/ui/empty-state.tsx` | new — accepts `icon`, `title`, `description`, `action`; replaces ad-hoc per-page JSX | mirror `JobList.tsx:63-77` shape |
| `frontend/src/components/ui/error-boundary.tsx` | new — for nested boundaries beyond root `error.tsx` | React 19 error boundary pattern |
| `frontend/src/components/jobs/ScoreRadar.tsx` | rename props (`seniority` → `seniority_score`, `location` → `location_score`); add null guards (`scores[d.key] ?? 0`); add tooltip per axis; add `role="img" aria-label`; add framer-motion stagger reveal on first paint | use existing `motion@12.38.0` install (currently 0 imports — finally use it) |
| `frontend/src/components/jobs/JobCard.tsx` | render `salary_min_gbp`/`salary_max_gbp` (formatted `£40k–£60k`), `seniority` enum pill, `workplace_type` pill, `visa_sponsorship` enum, staleness badge from `staleness_state` + `last_seen_at`; add Popover with dim breakdown; add null guard on `matched_skills?.slice(0,6) ?? []`; replace external-only Apply with shared `<ApplyButton>` | reuse `score-tier` classes from `globals.css:178-210`; reuse `skill-matched/missing/transferable` classes |
| `frontend/src/components/jobs/ApplyButton.tsx` | new — opens `apply_url` in new tab AND calls `createPipelineApplication(job.id, "applied")`; shows sonner success/failure | follow `setJobAction` pattern from `dashboard/page.tsx:150-173` |
| `frontend/src/components/jobs/FilterPanel.tsx` | add Combobox (seniority), Slider (salary range — pull `salary_min`/`salary_max` int from query), RadioGroup (workplace), Select (employment_type), multi-select Combobox (required_skills), Switch (staleness toggle), DateRangePicker (posted_at), Select (industry), Toggle (hybrid mode); rename `visa_only` → `visa_sponsorship`; add 250ms debounce on slider via `use-debounce` or local `useEffect` | reuse existing `Slider` primitive at `components/ui/slider.tsx`; reuse `Switch`, `RadioGroup`, `Select` from shadcn |
| `frontend/src/app/jobs/[id]/page.tsx` | split into server-component shell exporting `generateMetadata` + rendering client body `JobDetailClient.tsx`; add 10 enrichment fields (title_canonical, required_skills as Badge array, salary_min/max, seniority, visa_sponsorship enum, workplace_type, employment_type, years_experience_min, industry); add date-model display (Posted X / Last seen Y / staleness badge); replace external-only Apply with shared `<ApplyButton>`; add JSON-LD `<script>` block with `JobPosting` schema | reuse `relativeDate()` helper at line 224; reuse skill-pill classes from `globals.css:216-232` |
| `frontend/src/app/jobs/[id]/JobDetailClient.tsx` | new — extracted client body of the existing `[id]/page.tsx` so `generateMetadata` can run server-side | `'use client'` directive |
| `frontend/src/app/profile/page.tsx` | render `skill_tiers` as 3 columns (primary/secondary/tertiary), render `skill_esco` as raw→canonical mapping, render `linkedin_subsections` (positions, projects, volunteer, courses), render `github_temporal`, surface `current_version_id` + open `<VersionHistoryDrawer>` | reuse existing `Tabs` primitive |
| `frontend/src/components/profile/VersionHistoryDrawer.tsx` | new — `<Sheet>`-based drawer; lists versions via `getProfileVersions()`; "restore" button calls `restoreProfileVersion(id)` + sonner toast | reuse `Sheet` primitive |
| `frontend/src/components/profile/JsonResumeExportButton.tsx` | new — calls `getJsonResume()`; downloads as `resume.json` blob | follow CSV-export pattern at `api.ts:85-97` |
| `frontend/src/components/profile/CVUpload.tsx` + `CVViewer.tsx` | render ESCO normalised skills as `Python (raw: py, python3, python 3.11)` with collapsible details | extend existing skill Badge rendering |
| `frontend/src/components/layout/Navbar.tsx` | add `/jobs` and `/settings/channels` links (lines 18-22); add logout button; add user-email display; consume `useAuth()` from new `AuthProvider`; add `<ThemeToggle>` | mirror existing link structure |
| `frontend/src/components/layout/AuthProvider.tsx` | new — Context exposing `user`, `loading`, `logout()`; calls `me()` on mount; revalidates on focus | React 19 Context pattern |
| `frontend/src/components/layout/ThemeProvider.tsx` | new — `next-themes` provider; default `"dark"`; persists to localStorage; mounts `<ThemeToggle>` button | next-themes docs via Context7 |
| `frontend/src/components/layout/Footer.tsx` | add link slots for Privacy / Terms / Contact / GitHub (content TBD Batch 4) | preserve existing logo + tagline |
| `frontend/src/components/pipeline/KanbanBoard.tsx` | wire success/failure of `advancePipelineStage` to sonner toast; preserve existing 5-state column layout | minimal change; drag-and-drop deferred |
| `frontend/src/app/dashboard/page.tsx` | add "Last run" header card using `getStatus()`; add per-source progress dots during search; replace `setJobAction` ad-hoc state with `useMutation` + `setQueryData`; replace `getJobs()` `useEffect` with `useQuery`; gate run button on 429 with sonner toast | preserve optimistic-update logic |
| `frontend/src/app/layout.tsx` | add Open Graph + Twitter card meta to `metadata` export; mount `<QueryClientProvider>`, `<AuthProvider>`, `<ThemeProvider>`, `<Toaster />`; remove forced `className="dark"` (move to ThemeProvider default) | Next.js 16 metadata API docs via Context7 |
| `frontend/src/app/sitemap.ts` | new — dynamic sitemap including `/jobs/:id` for top 1000 active jobs | Next.js sitemap docs |
| `frontend/src/app/robots.ts` | new — allow all + sitemap pointer | Next.js robots docs |
| `Makefile` | add `verify-step-2` target | mirror existing `verify-step-1-5` target |
| `CLAUDE.md` | add rule #22: "Frontend code MUST consult Context7 for Next.js 16 / React 19 / Tailwind 4 patterns before editing — `frontend/AGENTS.md` directive" | mirror existing rule format |
| `docs/IMPLEMENTATION_LOG.md` | Step 2 entry with blocker closure table + test delta + final SHA + tag | mirror Step 1.5 entry |

---

## Verification section

Ralph Loop cannot emit `STEP-2-GREEN` until ALL of this passes.

### Gate command

```bash
make verify-step-2
```

Which runs (in order):

```bash
# 1. Backend regression — must stay ≥1,056p/0f/3s
cd backend
python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly
# Expect: ≥1,056 passed / 0 failed / 3 skipped

# 2. Frontend type-check
cd ../frontend
npm run type-check
# Expect: exits 0; no `any`, no implicit any, no missing JobResponse field references

# 3. Frontend lint (with jsx-a11y)
npm run lint
# Expect: exits 0; jsx-a11y rules clean

# 4. Frontend unit tests
npm run test:unit -- --coverage
# Expect: ≥30 passing / 0 failing; ≥80% line coverage on touched components

# 5. Build smoke
npm run build
# Expect: exits 0; .next/ generated; no warnings about missing types

# 6. Frontend E2E (Playwright headless)
npm run test:e2e
# Expect: 5 specs passing — auth-flow, job-render (value-presence), profile-version-restore, pipeline-advance, share-preview (og:title + JSON-LD)

# 7. Value-presence smoke (critical — Step 1 bombshell prevention)
npm run test:e2e -- --grep "value-presence"
# Specifically asserts:
#   - ScoreRadar renders ALL 8 dim values; min(values) > 0 for a seeded high-match job
#   - JobCard shows seniority pill, workplace pill, salary range, staleness badge — all non-empty
#   - JobDetail shows ≥3 enrichment fields with non-empty values
#   - Profile page shows ≥1 ESCO normalised-skill mapping (raw→canonical)
#   - KanbanBoard renders ≥1 application after Apply CTA was clicked

# 8. Lighthouse mobile (via Chrome DevTools MCP)
node scripts/lighthouse-step2.mjs http://localhost:3000/jobs/1
# Expect: perf ≥90, a11y ≥95, best-practices ≥95, SEO ≥95

# 9. Share-preview smoke
curl -s http://localhost:3000/jobs/1 | grep -E 'og:title|og:description|og:image|twitter:card'
# Expect: 4 lines, all non-default values

# 10. JSON-LD smoke
curl -s http://localhost:3000/jobs/1 | grep -A 20 'application/ld+json' | grep '"@type"'
# Expect: "@type": "JobPosting"

# 11. Sitemap + robots smoke
curl -s http://localhost:3000/sitemap.xml | head -5
curl -s http://localhost:3000/robots.txt
# Expect: valid XML with /jobs/ entries; robots allows all + points to sitemap

# 12. Auth middleware smoke
curl -i http://localhost:3000/dashboard 2>&1 | head -5
# Expect: HTTP/1.1 307 (redirect to /login?next=/dashboard) — middleware enforced

# 13. Pre-commit gate
cd ..
pre-commit run --all-files
# Expect: all hooks pass (backend ruff + frontend lint-staged)
```

### Sentinel write (after gate passes)

```bash
echo "$(git rev-parse HEAD)" > .claude/step-2-verified.txt
git add .claude/step-2-verified.txt
git commit -m "chore(step-2): write sentinel at green commit"
# Ralph Loop sees this on next iteration, emits STEP-2-GREEN, halts.
```

### End-to-end proof (human check after sentinel)

1. `git log --oneline main..step-2-batch` shows ~14 partial-progress commits + 1 final merge commit
2. `git diff main..step-2-batch --stat` shows ~25 files changed, ~2,000 LOC added (mostly frontend)
3. On a fresh Chrome window, load `http://localhost:3000` (anonymous) → see landing CTAs → click "Get Started" → register → login → land on dashboard
4. Dashboard shows: stats strip, "Last run X min ago" card, JobCards with seniority/workplace/salary/staleness badges, FilterPanel with 8+ controls
5. Click a JobCard → JobDetail page shows ScoreRadar (8 dims, animated reveal), enrichment field list, "Save to Pipeline" + "Apply Externally" CTAs
6. Click Apply → external link opens AND KanbanBoard now shows the job in "applied" column
7. Open `/profile` → ESCO normalised skills displayed → click "History" → drawer slides in → click "Restore version 2" → toast confirms → page re-renders with old data
8. Click "Export as JSON Resume" → `resume.json` downloads with valid JSON Resume schema
9. View page source on `/jobs/1` → `<script type="application/ld+json">` with `JobPosting` schema visible
10. Paste `http://localhost:3000/jobs/1` into LinkedIn / Twitter post composer → preview card shows job title + description + image
11. Anonymous visit to `/dashboard` → 307 to `/login?next=/dashboard` (middleware works)
12. Hit `POST /search` 4 times rapidly → 4th run shows sonner toast "rate limit — please wait"
13. Toggle theme button → app switches to light mode (or remains dark by default with toggle visible)
14. `docs/IMPLEMENTATION_LOG.md` has a completed "Step 2 — API→UI Seam" entry with test delta + blocker closure table

---

## Execution budget

- Ralph Loop: max 18 iterations (expect 12–14)
- Wall-clock: 2–3 sessions (Step 1.5 was ~1 session; Step 2 has more files but smaller per-file deltas)
- Commits: 1 per iteration (partial progress) + 1 final merge commit to `main`
- Branch: `step-2-batch` off `main @ <step-1-5-green SHA>`
- Worktree: **dual-worktree** (generator implements; reviewer runs the verification gate in isolation). Fast-forward both from `step-1-5-green` in iteration 1 before branching off.
- Merge strategy: **fast-forward only** to main; if reviewer worktree lags, rebase onto main before FF
- Tag: `step-2-green` on the final commit (mirrors `step-1-5-green`)

---

## Acknowledged trade-offs

- **5 cohorts is more than Step 1.5's 3.** The added cohorts D (polish/SEO/caching) and E (verification/docs) are deliberate — Step 2 is the first user-facing batch and the first to introduce a frontend test stack, so the verification phase is heavier. Cutting D would defer JSON-LD + caching by a batch; cutting E would risk a Step-1-style bombshell.
- **Cohort A is sequential, not parallel.** Test stack must exist before tests can be written for the api client; api client must exist before components can mock it; shared UI components must exist before pages can consume them. This costs ~1 extra iteration but eliminates ~5 conflict-resolution iterations downstream.
- **Forced dark mode → optional.** `layout.tsx:41` currently forces dark; Cohort C makes it user-toggleable but **keeps dark as default**. If audience-preference data later shows ≥30% want light, switch the default — for now, the brand identity stays dark.
- **TanStack Query > SWR.** Both work for Next 16. TanStack Query has better mutation ergonomics (`useMutation` + `setQueryData` for optimistic updates), better DevTools, and a clearer migration path to React Query Server Components. The decision is one-way (rip-out cost real once components depend on cache shape) — confirm via `feature-dev:code-architect` in iteration 1 before locking in.
- **Test floor is 30 unit + 5 E2E, not 100+.** Backend has 1,056 tests; matching that on the frontend would inflate Step 2 by 3×. The 35 tests Step 2 adds cover the value-presence assertions (rule #21), the api-client error handling (`ApiError`), and the critical user flows. Step 3+ can grow the count incrementally as components evolve.
- **No image hero optimization** (`next/image`). Current CSS gradient hero performs adequately on Lighthouse mobile. `next/image` migration is wide-blast (every JSX `<img>`) and low-urgency. Defer.
- **No description rendering on JobDetail.** Backend may have description text; current UI redirects to source. Legal grey area on LinkedIn/Indeed ToS. Cohort C's JobDetail surfacing of enrichment fields specifically *excludes* the raw description — only structured enrichment is rendered locally. Defer raw-description scraping decision to a ToS audit batch.
- **The ScoreRadar bombshell is downgraded from Tier-1 to Tier-2 after verification** (`[id]/page.tsx:308-320` correctly remaps at the call site). The rename is still worthwhile because a future caller passing `job` whole would silently render zeros — but it's no longer a runtime-broken-radar story.

---

## Post-Step-2 follow-ups (explicitly tracked here, not implemented in Step 2)

Created during planning so nothing is lost:

1. **Step 3 — endpoints expansion + settings UI.** Add per-job notification preferences (per-channel, per-urgency thresholds), form-validation library migration (zod + react-hook-form), confirmation dialogs for destructive actions, KanbanBoard drag-and-drop, notification ledger surface ("you've been notified"), `/settings` page reorganization.
2. **Step 4 — launch readiness.** GDPR / privacy / terms / cookie banner content (Footer slots already wired in Step 2), ICO £40 registration, ASA-compliant copy, Amazon SES, prod-Redis smoke, PWA manifest + apple-touch-icons, source scope-down to top 10–15, freemium metering, JSON Resume schema validator on import.
3. **Step 5 — observability + ops.** Frontend error tracking (Sentry / equivalent), `run_uuid` + `per_source_*` Step-1.5 telemetry surfaced on a dedicated `/admin/runs` dashboard, Lighthouse CI on every PR, Storybook + visual regression (chromatic).
4. **Backend follow-up — `/jobs?include_bucket_counts=true`.** Eliminate the dashboard double-fetch (`page.tsx:90-91`) by extending the `/jobs` response to include per-bucket counts in one round-trip.
5. **Description rendering ToS audit.** Determine whether scraped descriptions can be rendered in-app or must always redirect to source. Source-by-source policy table.
6. **`next/image` migration.** Once the hero design stabilises, migrate every `<img>` to `next/image`. Estimated 30-min surface sweep.
7. **`framer-motion` audit.** Either deepen usage (dashboard score-counter springs, JobCard hover-tilts) or strip the dep entirely (~50 KB savings). Step 2 introduces one focused use (ScoreRadar reveal); decide direction in Step 4.

---

_Plan written 2026-04-25 under plan mode. Anchor verification: 6 + 8 = 14 sub-agent audits across two waves on `frontend/src/**` at `main @ step-1-5-green`. User-confirmed decisions: **(1)** run Step 2 as a single Ralph-Loop-driven batch covering 22 blockers + 6 should-fix items; **(2)** establish frontend test stack as Cohort A (no test floor was the root cause of Steps 1 + 1.5 bombshells); **(3)** mirror this plan to `docs/step_2_plan.md` for the dual-worktree handoff; **(4)** ScoreRadar bombshell downgraded to Tier-2 after call-site verification; **(5)** GDPR/privacy/PWA copy deferred to Batch 4 (Step 2 wires footer slots only); **(6)** form validation lib + KanbanBoard drag-and-drop deferred to Step 3._
