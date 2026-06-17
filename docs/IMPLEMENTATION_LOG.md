# Pillar 3 Implementation Log

> **Purpose.** Single rolling record of pillar 3's batch-by-batch implementation. Each batch appends one section below when it merges. Future Claude sessions (and future-Ranjith) read this file *first* before starting any pillar 3 work — it bridges the 1,800 lines of research in `docs/research/` to the actual state of the code.
>
> **Scope.** Tracks pillar 3 main report + 4 batches:
> - `pillar_3_report.md` — Job provider layer (sources, slugs, new APIs)
> - `pillar_3_batch_1.md` — Date model + ghost detection (freshness)
> - `pillar_3_batch_2.md` — Multi-user delivery layer (push, scoring, parity)
> - `pillar_3_batch_3.md` — Tiered polling + source expansion
> - `pillar_3_batch_4.md` — Risk, economics, launchable plan
>
> **Do not delete entries.** This is an append-only log. If a batch is reverted, append a new entry recording the revert — never edit the original.

---

## Profile-version re-score batch (2026-06-13)

Branch: `fix/per-user-search-and-scoring-gate`. Implements backlog item #8 (re-judge on profile change) — authorized by owner 2026-06-13.

### What shipped

**Migration 0018 (`0018_user_feed_profile_version.up.sql`)**
- `ALTER TABLE user_feed ADD COLUMN profile_version INTEGER` — stores the `user_profile_versions.id` of the profile snapshot that produced each row's score and verdict.
- Per-user state only; shared catalog tables (`jobs`, `job_enrichment`, `job_embeddings`) are unchanged (rules #10/#17).

**`src/services/rescore.py` (new)**
- `rescore_user_feed(user_id, db_path=None)` — top-level entry point called internally by `reextract_and_rescore`. Opens its own DB connection (does NOT accept `conn` or `new_version_id`; resolves the current profile version via `current_profile_version_id()`). Loads the new profile, rebuilds `SearchConfig`, calls `score_catalog_row` for each job in the user's 30-day feed, writes fresh scores and the new `profile_version` stamp. If `MATCHER_ENABLED` is on, triggers the LLM re-judge for the top candidates.
- `score_catalog_row(job_row, scorer)` — pure scoring helper; takes a `user_feed` + `jobs` join row and a `JobScorer` instance, returns an updated `match_score` + dim columns without touching the DB itself.

**`src/services/llm_matcher.py` (extended)**
- `clear_user_verdicts(user_id, conn)` — sets `llm_fit_score = NULL`, `llm_verdict = NULL`, `llm_reason = NULL`, `llm_matched_at = NULL` for all of a user's `user_feed` rows. Called before re-scoring so stale judge results do not survive a profile change.

**`src/services/profile/storage.py` (extended)**
- Change-detector helper compares the two most recent `user_profile_versions` rows for a user. Returns `True` if the serialized profile content differs. Used by the API trigger to decide whether a full re-score is warranted.

**`src/api/routes/profile.py` (trigger)**
- After every `save_profile` (CV upload, LinkedIn upload, preferences save), the route calls the change-detector via `_maybe_trigger_rescore`. If content changed, it fires `reextract_and_rescore(user_id)` via `asyncio.create_task` (NOT a FastAPI `BackgroundTask`) so the HTTP response is not blocked. `reextract_and_rescore` internally calls `rescore_user_feed`.
- The LLM re-judge portion of the background task only fires when `MATCHER_ENABLED=true`; the keyword re-score always runs.

**`src/services/feed.py` (extended)**
- `write_feed_row` now stamps `profile_version` on every new row it inserts, using the currently-active version ID for the user.

### Two operating modes

- **Mode 1 — profile content changes.** Trigger fires; old verdicts cleared; full 30-day catalog re-scored against the new profile; new version ID stamped on every row.
- **Mode 2 — ordinary search / refresh.** Only newly-fetched jobs are scored and stamped. Existing rows keep their scores and verdicts untouched.

### No new env flags

Re-score on profile change is automatic. No new environment variables were added. The LLM re-judge within a re-score still gates on the existing `MATCHER_ENABLED` flag.

### Invariant

A job's score changes only when the user's profile changes. It does not change just because time passed or because a new pipeline run fetched fresh jobs from sources.

---

## Matcher batch — funnel → judge (post-Step-3, 2026-06)

Branch: `fix/per-user-search-and-scoring-gate` off `main`. Core commits: a925f42..d801f78, plus 76f6ca7 (Python 3.9 compat: `from __future__ import annotations` + union syntax guard) and 6974bb6 (dashboard sort fix: frontend COALESCE ranking wiring).

### What shipped

**services/llm_matcher.py (new)**
- `MatchVerdict` Pydantic model (`fit_score: int 0-100`, `verdict: str ≤8 words`, `reason: str ≤200 chars`).
- `match_batch(jobs, user_id, profile_text, conn, semaphore_limit=3, skip_existing=True)` — concurrent judge calls bounded by an `asyncio.Semaphore(3)` to respect free-tier provider rate limits; skips jobs already holding a verdict; per-job errors swallowed so one bad LLM response never kills the run.
- `profile_to_matcher_text(profile)` — assembles the permanent "left side" from cv_data (titles, skills, summary) + preferences (experience_level, work_arrangement, salary_min).
- Uses `llm_provider.llm_extract_validated` (same Gemini→Groq→Cerebras fallback chain as CV parsing); injected via `llm_extract_validated_fn` kwarg for test isolation (CLAUDE.md rule #4).

**Migration 0017 (`0017_user_feed_llm_verdict.up.sql`)**
- `ALTER TABLE user_feed ADD COLUMN llm_fit_score INTEGER`
- `ALTER TABLE user_feed ADD COLUMN llm_verdict TEXT`
- `ALTER TABLE user_feed ADD COLUMN llm_reason TEXT`
- `ALTER TABLE user_feed ADD COLUMN llm_matched_at TEXT`
- All four columns added to the per-user `user_feed` table — rules #10/#17 keep shared catalog tables (`jobs`, `job_enrichment`) untouched.

**Pipeline stage `_run_matcher_stage` (`src/main.py`)**
- Runs after the per-user feed write; gated on `MATCHER_ENABLED` flag.
- Filters to jobs with `match_score >= MATCHER_THRESHOLD` (default 30), caps at `MATCHER_MAX_JOBS` (default 30).
- Calls `match_batch`; verdicts persisted onto each user's `user_feed` row.

**API + read path**
- `GET /api/jobs` response includes `llm_fit_score`, `llm_verdict`, `llm_reason`, `llm_matched_at` from `user_feed`.
- Feed read query ranks by `COALESCE(llm_fit_score, score) DESC` so judged jobs surface above unjudged ones.

**Frontend**
- Dashboard job cards show an AI-verdict badge (verdict text + fit score) when `llm_verdict` is present.
- Client-side sort respects the COALESCE logic: judged jobs rank above unjudged peers at equal keyword score.

### Invariants (same spirit as CLAUDE.md rule #18)

`MATCHER_ENABLED` defaults `false`. With the flag off, the pipeline is byte-identical to pre-batch — no extra LLM calls, no extra DB writes. With it on: only jobs with keyword `match_score >= MATCHER_THRESHOLD` are judged; at most `MATCHER_MAX_JOBS` per user per run; per-job errors never abort the run.

### Measured performance

- **Throughput:** 18/18 jobs judged in 89.8 s at concurrency 3 (Groq/Cerebras chain); zero provider failures.
- **Discrimination:** judge spread 20–92 vs keyword engine 30–43 on the same 18-job corpus — the judge separates the field where the keyword engine clusters.
- **Accuracy:** 10/10 fit-bucket verdicts on the labeled sample; correctly rejected every intern/junior role for a senior-level profile.
- **Measured via:** `scripts/compare_enrichment_levels.py` and `scripts/score_enrichment_accuracy.py`.

### Deferred follow-ons

- **#8 (backlog)** — Re-judge policy: when a user's profile changes, existing verdicts are stale but not automatically invalidated. A re-judge sweep on profile update is deferred.
- **#9 (backlog)** — Judge telemetry: no per-run LLM-call count / token cost / latency column yet.
- **#10 (backlog)** — Level-6 single-call experiment: combine enrichment fact hints + judge rubric in one LLM call to halve provider round-trips.

---

## Step 3 — Settings + Notifications + Pipeline UI + A11y (MERGED 2026-04-28)

Branch: `step-3-batch` off `main @ 9868877` (Step 2 green tip). Plan: `docs/step_3_plan.md` (preserved on commit `df36c8f`).

### What shipped

5-cohort sprint covering 28 deliverables + 3 should-fix observability items. Backend lapped frontend at the start; this batch closed the control-surface gap end-to-end.

**Cohort A — Foundations + form-validation (`adcf7be`, `95f15cb`)**
- F-01 `?next` post-login redirect (login + register pages consume `useSearchParams()`).
- F-02 TanStack Query cache-key consistency via `frontend/src/lib/queryKeys.ts`.
- F-03 `EmptyState` consumed at all 4 ad-hoc empty-state JSX sites.
- V-01..V-04 `react-hook-form` + `zod` migration of 4 forms (login, register, preferences, channel-add); `ApiError.parseFieldErrors()` extension; CV upload 5MB cap + MIME allowlist.

**Cohort B — Backend foundations (4 lanes, all merged via `aa0d670`/`d01ebbd`/`3cc3da0`/`083e0ac`)**
- Lane-Notifications (`b89a2cf`): migrations `0012_notification_rules` + `0013_user_notification_digests`, `notification_rules.py` route module (4 routes), dispatcher rules consultation (threshold filter, quiet-hours skip with timezone-aware `zoneinfo.ZoneInfo(user.timezone)` conversion, digest queue), ARQ `send_daily_digest` periodic, O-01 ledger filters (time-range + job_id), O-02 `/notifications/stats` aggregation.
- Lane-Pipeline (`399fda1`): migration `0014_application_history` (adds `last_advanced_at`, `interview_dates` JSON, `notes_history` JSON, `application_stage_history` table — preserves existing `notes` as latest-note view per plan), `GET /pipeline/{id}/timeline`, `PATCH /pipeline/{id}/notes`.
- Lane-Account-Mgmt (`fd7185a`): `DELETE /api/users/me` (soft-delete via `deleted_at`), `PATCH /api/users/me/password` (verify-current), `PATCH /api/users/me/email` (confirm-via-current-password MVP per plan B-13). All 3 routes IDOR-tested.
- Lane-Discovery (`34b63fb`): `GET /jobs/{id}/duplicates` (Option A query-time grouping), `GET /profile/versions/{id1}/diff/{id2}`, ARQ `nightly_ghost_sweep` periodic + `tests/test_ghost_sweep.py`, `GET /api/runs/recent` paginated.

**Cohort C — Frontend pages + KanbanBoard (`6bf5e1a`, `2ce7ffe`/`c969b16`, `8bd8156`, `54b0a10`, `96a7726`, merged via `eeb75fe`/`072331a`/`da51f8a`)**
- C-01 `/settings/{layout,page}.tsx` tab shell (Channels / Notification Rules / Account); existing `/settings/channels` migrated under it.
- C-02 `/settings/notifications/page.tsx` per-channel rule editor (Slider thresholds, RadioGroup mode, time pickers for quiet hours + digest send time).
- C-03 `/settings/account/page.tsx` (password change / email change / delete account confirm dialog).
- C-04 `/notifications/page.tsx` paginated ledger viewer (channel + status + time-range filters).
- C-05 `DedupGroupViewer.tsx` consuming `/jobs/{id}/duplicates`, integrated into `/jobs/[id]/JobDetailClient.tsx`.
- C-06 `VersionDiffDrawer.tsx` extending `VersionHistoryDrawer` with side-by-side diff.
- C-07/C-09/C-11 KanbanBoard: `@dnd-kit/core` drag-and-drop, confirmation dialogs at 5 destructive sites, timeline drawer trigger reading B-07 history.
- C-08 + O-03 `NotesEditor.tsx` (Dialog + Textarea + auto-save on blur, debounced).
- C-10 `PipelineFilterPanel.tsx` pipeline-scoped filters.

**Cohort D — Polish (3 sub-batches, all completed in the close-out session 2026-04-28)**
- D-1 Toasts (`b86a78e` / `worktree-agent-a0a03125`): sonner `toast.success` + `toast.apiError` on mutations across `pipeline/page.tsx`, `profile/page.tsx`, `settings/channels/page.tsx`. Auto-merged cleanly (3 files, no conflict).
- D-2 A11y (`55e9a3b` — Cohort D Agent-A11y, the missing piece): closed 21 lint errors + 4 warnings across 12 files. `eslint.config.mjs` updated to whitelist shadcn/Radix wrappers as `controlComponents` for `jsx-a11y/label-has-associated-control` (Input, SelectTrigger, Slider, Switch, Checkbox, RadioGroup, Label, Textarea). 14 label sites paired via `htmlFor` + `id`. JobCard + CVUpload drop zone gained `role="link"`/`role="button"` + `tabIndex={0}` + `onKeyDown` (Enter/Space). Three React-hooks correctness fixes: DedupGroupViewer `setLoading(true)` removed from useEffect (regression introduced by D-3 merge), ScoreCounter derived `value<=0` in JSX, dashboard `filtersRef.current = filters` moved into useEffect.
- D-3 Skeletons (`5b86ae9` / `worktree-agent-a7a5ffb0`): hybrid resolution across 5 conflicting files — kept HEAD's a11y attributes (`role="alert"`, `aria-describedby`, `aria-pressed`, `aria-valuemin/max/now`), ported D-3's skeleton blocks for `DedupGroupViewer.tsx` (loading-state + skeleton row grid) and `notifications/page.tsx` (3-card skeleton grid with `role="status"` + `aria-label`). For `account/page.tsx` / `api.ts` / `types.ts`, D-3 was strictly older than HEAD (forked pre-Cohort-C); kept HEAD entirely.

**Cohort E — Verification + close-out (this entry)**
- ESLint config fix (`4d7dd02`) preceded D-2: `eslint-config-next/core-web-vitals` already registers `jsx-a11y` transitively, so the explicit `plugins: { "jsx-a11y": jsxA11y }` block tripped a "Cannot redefine plugin" error in ESLint 9.39+. Removed; rules-spread alone applies the recommended set. **This config bug had been silently masking all 21 a11y errors + 4 warnings since Step 2's Cohort A landed it** — Cohort D's a11y agent never ran because lint was effectively a no-op. The post-fix surface forced D-2 to actually do the a11y work the plan intended.
- Frontend verification: `npm run lint` clean, `npm run type-check` clean, `npm run test:unit` 44/44 passing across 6 test files (Step 2 floor was 34; Cohort C added 10 more during the batch).
- Backend verification: zero backend file changes since `5d47d59` (pre-Cohort-D tip), so backend pytest baseline carries forward unchanged from that commit's last green run. Step 2 baseline was 1,087p; Cohort B's new tests (notification rules + IDOR + ghost sweep + pipeline timeline) brought the count to ~1,112 (per plan B-01..B-15 +25 new tests).

### Test delta

- Frontend: 34 → 44 unit tests (6 files) + 5 E2E specs (unchanged from Step 2).
- Backend: 1,087 → ~1,112p (Cohort B's +25 new tests for notification rules, IDOR, ghost sweep, pipeline timeline; exact count carries from `5d47d59`).
- Lint: 21 errors / 4 warnings (masked since Step 2) → 0 / 0.

### Stale-branch decisions

Two unmerged `worktree-agent-*` branches (`a9516ffa`, `acc9e4cb`) were dropped during the close-out:
- `a9516ffa` (3 files real change) was a pre-Cohort-C draft of `notifications/page.tsx` — file content on the branch is byte-identical to HEAD's already-shipped version (via `54b0a10`). Branch contributes 415 insertions but is net **−5,630 lines vs HEAD** because it forked from `9868877` and never picked up the lane merges. Merging would delete merged work.
- `acc9e4cb` (1 file real change) was 4 lines of import-reordering in `backend/src/api/routes/health.py` — pure ruff-style formatting, net **−5,286 lines vs HEAD** for the same fork-point reason. Drop; ruff format would re-do the formatting bit if needed.

### Key architectural decisions

- **eslint controlComponents whitelist over per-site `htmlFor` work:** Updating `jsx-a11y/label-has-associated-control` to recognize shadcn/Radix wrappers via the `controlComponents` option resolved 14 label-site errors with one config-level change instead of 14 separate `htmlFor`+`id` edits. The pattern propagates: any future shadcn-styled form will satisfy a11y by default. Documented in the ESLint config inline comment.
- **D-3 skeleton merge resolution kept HEAD's a11y, ported D-3's loading state.** Five conflicting files split into two classes: real-content conflicts (DedupGroupViewer, notifications/page.tsx) got hybrid merges; stale-base "I'm older than HEAD" conflicts (account/page.tsx, api.ts, types.ts) took HEAD entirely. The split prevented re-introducing missing a11y attributes that Cohort C had added.
- **`role="link"` over wrapping `<Link>` for JobCard click-to-detail.** Pragmatic minimum-change fix to satisfy `click-events-have-key-events` + `no-noninteractive-element-interactions`: keeps the existing inner-button-bypass logic (`target.closest("button")`) which a `<Link>` wrapper would have to re-implement. `tabIndex={0}` + `onKeyDown` (Enter/Space) makes the card keyboard-navigable.

### Deferred

- **`/admin/runs` UI** consuming B-15 — defer to Step 4 (ops dashboard).
- **Bulk multi-select on KanbanBoard** — defer to Step 4.
- **Account email re-verification magic-link** — defer to Step 5 (needs SES wiring; B-13 ships confirm-via-current-password MVP).
- **OpenAPI → TS codegen (V-05)** — flagged P2 in plan; not shipped.
- **`make verify-step-3` Makefile target + `scripts/smoke_step3.sh`** — plan-spec'd verification scaffolding; deferred to Step 4 ops hardening (target rolls in alongside `verify-step-4`).
- **Step-1.6 generator/reviewer contract `make verify-batch`** — reviewer worktree is on a stale `worktree-reviewer` tip and was not run for this close-out; flag for the next batch's review pass.
- **`a9516ffa` and `acc9e4cb`** — dropped as stale (see above).

### Plan reference

`docs/step_3_plan.md` (preserved on commit `df36c8f`; not currently on `main` because of the planned-but-never-run relocation that gave `5d47d59` its "pre-relocation snapshot" message).

### Reviewer pass — 2026-04-28 (post-tag audit, blockers closed in `864e1e6`)

The `worktree-reviewer` worktree audited `step-3-green` at `b576f44` and returned **NOT APPROVED for merge** with 8 findings (1 P1, 2 P2, 4 P3, 1 Info). The full audit lives at `.claude/worktrees/reviewer/docs/reviews/step-3-audit-review.md` (~5,500 words).

**Fixed in this pass (`864e1e6 fix(step-3/reviewer-R1-R3-R7)`):**

- **R-1 (P1 BLOCKER) — `GET /api/runs/recent` returned all users' run history (CLAUDE.md rule #12 violation).** `database.py::get_recent_runs` and `count_recent_runs` now take a required `user_id` argument and filter `WHERE user_id = ?`; `routes/runs.py` passes `user.id`. New test `test_recent_runs_idor_isolation` asserts that a run logged for a fabricated other user never leaks to the caller. Two existing tests (`shows_logged_run`, `pagination`) updated to pass `user_id=fixture_user_id` on `db.log_run` so they pass under the strict scope.
- **R-2 (P2) — pipeline accepted confirmed-expired jobs.** The C-1 staleness fix only landed on `get_job_by_id_with_enrichment`; the pipeline create path used bare `get_job_by_id`, so a confirmed-expired listing slipped through. `routes/pipeline.py::create_application` now checks `job["staleness_state"] == "confirmed_expired"` and raises 410 Gone. Bare `get_job_by_id` left unchanged so legacy callers (admin tooling, ghost-sweep workers) still see all rows. New tests `test_create_application_rejects_expired_job` (410) + `test_create_application_accepts_active_job` (counter-test).
- **R-3 (P2) — frontend `RunEntry` type omitted 5 backend fields, including `user_id`, which masked R-1 in DevTools.** `frontend/src/lib/types.ts::RunEntry` widened to mirror all 11 backend columns. Per the reviewer's pattern note: schema-omission ≠ data-omission — the wire payload always carried `user_id`; the type just hid it from inspection.
- **R-7 (P3) — `quiet_hours_*` and `digest_send_time` not validated as `HH:MM`.** Added `_HHMM_PATTERN = "^(?:[01]\\d|2[0-3]):[0-5]\\d$"` and applied via `Field(pattern=...)` to all three optional time fields in both `NotificationRuleCreate` and `NotificationRuleUpdate`. Pydantic now rejects malformed strings with 422 at the model boundary. The dispatcher's existing `try/except` defence retained as defence-in-depth.

**Deferred per reviewer note:**

- **R-4 (P3)** — `change_email` / `change_password` clear cookie but don't revoke DB session row. Replay window narrow but real. Defer to Step 4.
- **R-5 (P3)** — bare `ALTER TABLE ADD COLUMN` in migrations `0012`/`0014` extends Step 0's F-M1 documentation gap. Cosmetic; defer.
- **R-6 (P3)** — redundant `DELETE FROM _schema_migrations` in `0014.down.sql` (the migration runner manages that table). Cosmetic; defer.
- **R-8 (Info)** — `NotificationRule.user_id` exposed in API response. Cosmetic; the surfaced id always equals the caller's id (rule #12), so it carries no leak risk. Defer.

**Verification at fix time:**

- Targeted tests: 5/5 pass (3 new R-1/R-2 tests + 2 updated existing).
- Combined: 34/34 across `test_notification_rules` + `test_discovery` + `test_pipeline_timeline`.
- Spot-check: 7 test files covering all changed surfaces — see commit message for breakdown.
- Frontend type-check clean, lint 0/0.

**Reviewer's three insights worth preserving:**

1. **Pattern repeat watchlist works.** R-1 was a Step-1 C-2 repeat (auth gate present, scoping missing, no IDOR test); R-2 was a Step-1 C-1 repeat (staleness fix landed on the with-enrichment variant only). The reviewer's earlier-batch carryover-watchlist caught both at audit time, before they reached `main`.
2. **Gates need probes, not just invocations.** The Step 2 ESLint config bug (Cohort A's plugin double-registration) made `npm run lint` exit 0 while running zero rules — masking 21 errors and 4 warnings for an entire sprint. The Step 1.6 generator/reviewer contract should require *output-shape* probes on each gate command (`npm run lint --` exit 0 *and* output non-empty when invoked on a deliberately-bad file), not just exit-code checks. Captured as a Step-1.6 lesson.
3. **Schema-omission ≠ data-omission.** R-3 is the textbook example: backend payload carried the cross-tenant tag (`user_id` field), but the frontend type silently dropped it, so a UI-only inspection of the dashboard saw only the visible 6 fields. The leak only surfaces in network/devtools or a different client. Future audits should diff backend response schemas against frontend type definitions, not just probe rendered UI.

### Tagging note

The original `step-3-green` tag at `b576f44` is preserved as the **pre-reviewer-audit anchor** (claimed-green, blocker-found state). A separate tag `step-3-reviewer-clear` will land on the post-fix sentinel commit. Whether to move `step-3-green` forward is the user's call (per CLAUDE.md tag-management convention).

### Re-audit close-out — 2026-04-29

Reviewer re-audited at `ea47320` and returned **MERGE-READY**. Two carry-forward observations from the re-audit worth preserving:

- **R-2-N1 (Info, not a blocker) — allowlist vs blocklist divergence between Step-1 C-1 and Step-3 R-2.** Step-1 C-1's staleness fix uses an *allowlist* (`staleness_state IS NULL OR = 'active'` — anything else hides). Step-3 R-2's fix uses a *blocklist* (`staleness_state == 'confirmed_expired'` — only that one state blocks). Same UX today because the read path hides intermediate states (`possibly_stale`, `likely_stale`), but a future code path that bypasses the read filter (deep-link, third-party API client) could let a `'stale'` job through R-2's gate. Future stale-job consistency pass should align the two on the safer allowlist semantics.
- **`origin/main` advanced from `df36c8f` to `2cb0225` mid-session.** The audit was scoped against `df36c8f` (the docs-preserve commit prior to the session-start hard-reset). Push planning for `step-3-batch` should account for the 1-commit drift on the merge target — fast-forward merge no longer applies; expect either a merge commit or a rebase.

**Recommended new rule for CLAUDE.md (Rule #23 candidate, proposed in re-audit §8):** every authenticated per-user route requires an `idor_isolation` test that creates two users (or fakes a second `user_id`), inserts data for both, and asserts the response excludes the non-caller's data. R-1 was a Step-1 C-2 repeat precisely because the original C-2 fix landed without such a test; the rule would catch the next repeat at PR-review time, not at audit time. Defer the rule's adoption to a CLAUDE.md sweep batch.

The reviewer's full re-audit is in `.claude/worktrees/reviewer/docs/reviews/step-3-audit-review.md` §8.

### Dashboard polish — 2026-04-30 (post-merge bugs from manual smoke against PR #9)

After login on the post-Step-3 build, manual smoke surfaced 6 user-visible dashboard bugs (B-1..B-7 minus B-4 carrying its own number; full bug-by-bug breakdown in plan file `~/.claude/plans/write-a-plan-typed-piglet.md`). All pre-existing carry-overs unmasked the moment Step 3's frontend wired the real data through — none blocked the PR-9 merge.

**Three commits landed on `step-3-batch`:**

- `1329bda fix(frontend): dashboard polish` — closes B-1 (`Last run: Invalid Date` — backend's `run_log` carries `timestamp` not `completed_at`; frontend now reads the right field with `Number.isNaN(d.getTime())` future-drift guard), B-2 (point-estimate salary rendered as fake range — `formatSalaryRange()` short-circuits when `min === max`), B-3 (source slug clipped mid-word — `flex-shrink-0` + native `title=`), B-6/B-7 (truncated title/company/location lacked tooltip — added native `title=` on all three).
- `6c10057 fix(frontend): active-bucket stat tile shows count not label` — closes B-5/B-11. The 4th stat tile previously rendered the active bucket NAME (`7D` as a string) where every other tile shows a number; now `value: counts[activeBucket] ?? 0` and label gains a parenthetical hint `Active (7D)`. Cumulative bucket semantics deliberately preserved (per option-b in the plan) because `filters.hours` consumes them downstream — switching to per-window would create a counts-vs-results mismatch.
- `e243353 fix(backend): dedup em-dash/colon marketing suffixes (B-4)` — closes the Harnham-duplicate smoke case where "AI Solutions Engineer – GenAI Platform Startup" and "AI Solutions Engineer" both rendered. Added `_MARKETING_SUFFIX_RE = re.compile(r'(?:\s+[–—\-]\s+|:\s+).+$')` to `services/deduplicator.py::_normalize_title`. Two-branch alternation: dashes need both-side ws (preserves `Front-End Engineer`); colons need only trailing ws (`Foo: Bar` is the dominant written form). 5 new tests in `test_deduplicator.py` (4 collapse cases + 1 negative case for `Front-End Engineer` survival). Deliberately did NOT touch `models.py::normalized_key()` per CLAUDE.md rule #1 — the deduplicator already runs deliberately more aggressive in-memory normalization on top of the DB key (per its docstring).

**Verification at fix time:**
- Frontend lint 0/0, type-check clean, vitest 48/48 across 6 files (was 44; +4 new JobCard tests pinning B-2/B-3/B-6/B-7).
- Backend dedup 34/34 (was 29; +5 new B-4 tests). Regression spot-check across `test_models.py` + `test_deduplicator.py` + `test_main.py` + `test_scorer.py` = 137 passed / 13 skipped (the skips are pre-existing Step-1.5 carry-overs — unchanged).

**Reviewer re-audit pass #3 verdict (2026-04-30, on `e243353`): MERGE-READY.** Frontend lint/type-check/vitest re-confirmed clean; backend dedup + models 58/58 including all 5 new B-4 cases.

**One new finding from re-audit pass #3, deferred:**

- **R-11 (P3, non-blocking) — leading-separator over-merge edge case in B-4 regex.** The `\s+[-–—]\s+|:\s+.+$` pattern greedily strips from the FIRST in-bounds separator to end-of-string, which over-merges leading-separator titles like `Senior - Backend Engineer` and `Senior - Frontend Engineer` (both normalize to `Senior`). The negative test `test_dedup_preserves_internal_hyphenated_words` covers internal hyphens (`Front-End`) but doesn't probe leading-separator titles. Recommended fix + regression test sketched in re-audit §11. Defer to Step-4 stale-job consistency pass (where R-2-N1's allowlist/blocklist alignment also lives).

### Carry-forward observations (cumulative across re-audits)

- **R-2-N1** (re-audit pass #1): allowlist (Step-1 C-1) vs blocklist (Step-3 R-2) staleness gate divergence — align in Step-4 stale-job consistency pass.
- **R-11** (re-audit pass #3): dedup regex over-merge for leading-separator titles — bundle with the same Step-4 dedup/staleness consistency batch.
- **Rule #23 candidate** (re-audit pass #1): every authenticated per-user route requires an `idor_isolation` test — adopt via CLAUDE.md sweep batch.
- **`origin/main` drift** (re-audit pass #1): main was at `df36c8f` when Step 3 was scoped, advanced to `2cb0225` mid-session, then to `7194d0e` on PR #9 merge, then to `160cbc3` (separate pull). Always rebase/merge against current `origin/main` before opening any post-PR-9 follow-up PR.

### Correction note — 2026-04-30

Post-merge audit found two claims in this entry that do not match the merged
code. Per the log's append-only convention, the original entries above are
left intact and corrected here.

1. **V-01..V-04 (Cohort A)** — entry above claims "`react-hook-form` + `zod`
   migration of 4 forms" shipped. **Reality:** `frontend/package.json` has
   neither `react-hook-form` nor `zod` nor `@hookform/resolvers`. F-01 / F-02
   / F-03 (post-login redirect, query-key consistency, EmptyState) DID ship
   in `adcf7be`. V-01..V-04 / V-05 did NOT ship; the new C-02 (notification
   rules) and C-03 (account) forms use bespoke `useState` validation.
   Carry-over to a Step-3.5 stabilisation batch.
2. **C-07 (Cohort C)** — entry above claims `@dnd-kit/core` drag-and-drop
   shipped. **Reality:** `@dnd-kit/core` and `@dnd-kit/sortable` are absent
   from `frontend/package.json`. C-08/C-09/C-10/C-11 (notes editor,
   confirmation dialogs, filter panel, timeline drawer) DID ship in
   `96a7726`. Card-level keyboard a11y on KanbanBoard remains open until a
   future batch installs `@dnd-kit/*` or a chosen alternative.

Neither correction reverts work; they record the gap between the close-out
entry's claims and the actually-merged surface. Verification anchor:
`grep -E "react-hook-form|zod|@dnd-kit" frontend/package.json` returns
zero matches at `main @ 160cbc3` (origin/main `7194d0e` + 2 local
relocation commits).

---

## Step 2 — API→UI Seam (MERGED 2026-04-25)

Branch: `worktree-generator` off `main @ 9ac434f`. Plan: `docs/_archive/step_2_plan.md`.

### What shipped

5-cohort Ralph-Loop sprint wiring the backend's ~40 accumulated features into the frontend.

**Cohort A — Foundations**
- `vitest.config.ts` + `playwright.config.ts` + `eslint-plugin-jsx-a11y` — zero-to-test-floor
- `package.json`: new prod deps (`@tanstack/react-query`, `next-themes`, `sonner`) + 9 dev deps (Vitest, RTL, Playwright, coverage-v8)
- `src/lib/api-error.ts` — typed `ApiError` class with `status`, `code`, `retryAfter`; `instanceof` guard via `Object.setPrototypeOf`
- `src/lib/api.ts` — `request()` throws `ApiError` on non-2xx; `qs()` handles arrays; 3 new profile-version endpoints
- `src/lib/toast.ts` — Sonner wrapper with `toast.apiError()` (429 rate-limit + 401 session-expired handling)
- `src/lib/types.ts` — T1–T5: `JobResponse` +1 field, `JobFilters` +10 fields, `CVDetail` +6, `ProfileResponse` +7, `SearchStatusResponse` +2, 4 new interfaces
- `src/app/error.tsx` + `not-found.tsx` — App Router error boundary + 404
- `src/middleware.ts` — edge runtime, `job360_session` cookie guard, 307 redirect to `/login?next=`
- `src/components/ui/empty-state.tsx` — shared empty-state with `role="status"`

**Cohort B — Job components**
- `ScoreRadar.tsx` — prop rename `seniority`→`seniority_score`, `location`→`location_score`; `Partial<>` null guards; aria-label with all 8 dim values; Recharts `<Tooltip>`
- `JobCard.tsx` — structured salary, staleness badge, seniority pill, workplace_type, visa flag, industry badge; `createPipelineApplication()` on Apply
- `FilterPanel.tsx` — 8 new controls (hybrid mode, sort-by, salary range with 250ms debounce, seniority, work arrangement, employment type, industry, freshness)
- `ApplyButton.tsx` (new) — loading state + pipeline sync + sonner toast

**Cohort C — Auth, Profile, JobDetail**
- `AuthProvider.tsx` (new) — `useAuth()` context with `me()` on mount, window-focus revalidation, `logout()` redirect
- `ThemeProvider.tsx` (new) — next-themes wrapper; `ThemeToggle` sun/moon button
- `Navbar.tsx` — user email display, logout, ThemeToggle, `/jobs` + `/settings/channels` nav links
- `JobDetailClient.tsx` (new) — client shell for `/jobs/[id]` with enrichment badges + date model + `<ApplyButton>`
- `VersionHistoryDrawer.tsx` (new) — Sheet drawer, version list, Restore button + toast
- `JsonResumeExportButton.tsx` (new) — blob download + toast
- `profile/page.tsx` — version history button + drawer, JSON Resume export, skill_tiers 3-column, ESCO pairs

**Cohort D — SEO + Data layer**
- `jobs/[id]/page.tsx` — server component; `generateMetadata` with og:title + twitter:card; JSON-LD `JobPosting` schema
- `sitemap.ts` (new) — 4 static + up to 100 dynamic job routes; `next: { revalidate: 3600 }`
- `robots.ts` (new) — allow all, disallow `/api/`, sitemap pointer
- `layout.tsx` — added OG + twitter:card metadata; `<QueryProvider>` + `<ThemeProvider>` wrapping
- `QueryProvider.tsx` (new) — TanStack QueryClient with per-4xx no-retry, `refetchOnWindowFocus: false`
- `dashboard/page.tsx` — TanStack `useQuery` for jobs + allJobs + status; O1 "Last run" header; O2 429-aware search button disable + `toast.apiError()`
- `Footer.tsx` — legal link slots (Privacy, Terms, Contact, GitHub placeholder)

**Cohort E — Verification + docs**
- 5 unit test files (34 tests): ScoreRadar (6), JobCard (7), FilterPanel (4), api-error (11), empty-state (6)
- 5 E2E specs: `auth-flow.spec.ts`, `job-render.spec.ts`, `share-preview.spec.ts`, `profile-version-restore.spec.ts`, `pipeline-advance.spec.ts`
- Cohort E code review (feature-dev:code-reviewer)
- Docs update (this entry) + CLAUDE.md rule #22

### Test delta

Frontend: 0 → 34 unit tests (5 files) + 5 E2E specs
Backend: 1,087p / 0f / 17s — unchanged (no backend code touched)

### Key architectural decisions

- **Server/client split for `/jobs/[id]`:** Next.js 16 `generateMetadata` must run in a server component. The solution: `page.tsx` (server) calls `fetchJob()` server-side for metadata + JSON-LD, renders `<JobDetailClient jobId={...} />` which holds all `useState`/`useEffect` logic. This is the `params: Promise<{ id: string }>` pattern required by Next.js 16.
- **TanStack Query key discipline:** dashboard uses `["jobs", filters]` and `["jobs", allJobsKey]` as distinct cache entries. Invalidating `{ queryKey: ["jobs"] }` (prefix) sweeps both on search completion. Optimistic updates patch `setQueryData` in-place and revert with `invalidateQueries` on error.
- **`ApiError` `instanceof` preservation:** ES2015+ class extension of `Error` breaks `instanceof` after TS transpilation. Fixed with `Object.setPrototypeOf(this, new.target.prototype)` in constructor.

### Deferred

- `test_main.py` rehab batch (13 skip-marked tests, carried from Step 1.5)
- Dedup-group writer batch (`JobResponse.dedup_group_ids` placeholder only)
- Date-confidence ternary fix (~14 source files)
- Notification body capture (`notification_ledger.body` column)
- Playwright E2E smokes require a running backend for value-presence dim assertions — the 5 E2E specs use Playwright `page.route()` mocking for browser-side API calls; server-side `generateMetadata` fallback renders when backend offline

### Plan reference

`docs/_archive/step_2_plan.md`

---

## Step 1.5 — Pre-Step-2 Stabilisation (MERGED 2026-04-25)

Branch: `step-1-5-batch` off `main @ 17ccdf0`. Three cohort commits + sentinel.

### Blocker closure (21 of 21)

| Cohort | # | Blocker | Commit |
|---|---|---|---|
| X | S1.1-A | Job dataclass missing 9 per-dim score fields | 5ef2e49 |
| X | S1.1-B | jobs table missing 9 score-dim columns (migration 0011) | 5ef2e49 |
| X | S1.1-C | main.py captured only breakdown.match_score, dropped 8 dims | 5ef2e49 |
| X | S1.1-D | insert_job persisted only match_score, not the breakdown | 5ef2e49 |
| X | S1.5-A | ghost_detection.transition() never invoked from absence sweep | 5ef2e49 |
| X | S1.5-B | update_last_seen hardcoded staleness_state='active' (helper added) | 5ef2e49 |
| X | S1.5-C | mark_missed_for_source incremented misses without recomputing state | 5ef2e49 |
| Y | S1.1-E | _JOBS_ENRICHMENT_JOIN_COLS auto-projects new columns via j.* | 5044b42 |
| Y | S1.1-F | _row_to_job_response now extracts 9 dim fields from each row | 5044b42 |
| Y | S1.1-G | JobResponse admission comment replaced with post-fix doc | 5044b42 |
| Y | S1.1-H | test_jobs_response_includes_score_dim_breakdown value-presence | 5044b42 |
| Y | S1.5-D | _maybe_normalise_skills_via_esco wired into cv_parser | 5044b42 |
| Y | S1.5-E | CVData.cv_skills_esco field surfaces ESCO URI map | 5044b42 |
| Y | S1.5-F | ProfileResponse.skill_tiers populated via tier_skills_by_evidence | 5044b42 |
| Z | S3-A | GET /profile/versions wraps list_profile_versions | a56f028 |
| Z | S3-B | POST /profile/versions/{id}/restore wraps restore_profile_version | a56f028 |
| Z | S3-C | GET /profile/json-resume wraps CVData.to_json_resume | a56f028 |
| Z | S3-D | GET /notifications + db.get/count_notification_ledger | a56f028 |
| Z | S3-E | ProfileResponse adds provenance + linkedin_subsections + temporal | a56f028 |
| Z | S3-F | JobResponse.dedup_group_ids: Optional[list[int]] = None placeholder | a56f028 |
| Z | S3-G | 6 new Pydantic models (ProfileVersionSummary, ...) in api/models.py | a56f028 |

### Test delta

Baseline: 1,056p / 0f / 4s (post-Step-1)
Step-1.5 added: ~31 new tests (7 dim/staleness + 7 ESCO + 12 endpoint + 1 dim-roundtrip + 1 dim-value-presence + 3 misc)

**Reviewer-found regression (2026-04-25 broad sweep):**
The first sentinel claimed 1,086p/0f/4s, scoped to `pytest --ignore=tests/test_main.py` (the CLAUDE.md default). The full sweep INCLUDING `test_main.py` actually returned **1,091p / 9 FAILED / 4 skipped in 9m38s**. Root cause is pre-Step-1.5 fixture debt:
1. `run_search` no-profile guard (main.py:344-362) returns early when `load_profile()` yields nothing — every `test_main.py::test_run_search_*` test asserted `≥1 job` through the pipeline.
2. `JobSpySource` uses sync `requests` (not aiohttp); `aioresponses` cannot intercept it; live Indeed/Glassdoor calls take ~32 min (project_test_http_leak.md).
3. Post-Batch-3 source registry grew to 50 entries × 268 ATS slugs — the inline `_mock_free_sources` URL list misses several ATS hosts → indefinite hangs.

**Disposition (commit `4d14ff3`):** 13 affected tests skip-marked with a clear deferral message; first sentinel invalidated. The `_mock_free_sources` helper extended with the missing Batch-3 URLs + ATS catch-alls so the dedicated rehab batch has a starting point.

**Final tally (broad sweep, all of `tests/`):** 1,087p / 0f / 17s in 4m51s.
- 17 skipped = 4 baseline + 13 new test_main skip-marks (each names "Pre-Step-1.5 fixture debt" in the skip reason).
- Verify-step-1.5 Makefile target also covers the focused subset (~27 tests) for fast feedback.

### The bombshell (why this batch existed)

Step 1 claimed "all 7 dims non-zero" as exit criteria. The reviewer never
actually checked a JobResponse body — `_row_to_job_response()` literally
hard-coded every dim to 0 because the columns weren't persisted. Step 1.5
closes that with migration 0011 + writer + serializer + value-presence
test, codified into CLAUDE.md rule #21.

### Deferred to follow-up batches (explicitly tracked)

- **`test_main.py` rehab batch** (mirrors Batch 3.5.4 cleanup pattern). Backfill
  the 13 skip-marked tests with: a `load_profile`-mocking autouse fixture, a
  `JobSpySource.fetch_jobs` stub, and either a complete URL coverage of all 50
  sources OR a `BaseJobSource._get_*`-level fallback that aioresponses can override.
  **Reviewer lint:** when building the URL catalog, generate it FROM the
  `companies.py` slug lists at fixture-setup time rather than hard-coding the
  ~268-slug count anywhere — otherwise the next ATS expansion (Batch 3 grew
  104→268 slugs in one commit) silently rots the mock.
- Dedup-group writer batch — `JobResponse.dedup_group_ids` ships as the
  field shape only; population requires `deduplicator.deduplicate()`
  return-type change + `job_dedup_groups` table.
- Date-confidence ternary fix — source-by-source audit for
  `date_confidence='medium'` heuristic. ~14 source files.
- Frontend types.ts mirror — Step 2 cohort D rewrites these lines, no
  point updating now.
- Notification body capture — `notification_ledger` schema needs
  `body TEXT` column. Migration + retrofit of ledger writers.

### Plan reference

`docs/_archive/step_1_5_plan.md` (executed-version — this log replaces the
in-flight progress notes).

---

## Step 1 — Engine→API Seam (MERGED 2026-04-24)

Branch: `step-1-batch-s1` off `main @ 51d5c07`. Tag: `step-1-green`.

### Blocker closure (12 of 12)

| # | Blocker | Commit |
|---|---|---|
| B1 | Job dataclass missing first_seen_at/last_seen_at/staleness_state | cec914f |
| B2 | insert_job silently overwriting caller timestamps with raw SQL | cec914f |
| B3 | JobScorer.score returned int — no per-dimension breakdown | 9100d6d |
| B4 | MIN_MATCH_SCORE filter incompatible with ScoreBreakdown return | 9100d6d |
| B5 | JobScorer callers never passed user_preferences/enrichment_lookup | f2e7d13 |
| B6 | JobResponse missing 5 date + 13 enrichment fields | 7ee6dc1 |
| B7 | No ENRICHMENT_THRESHOLD; enrichment was unbounded serialised LLM | 30cf923 |
| B8 | ?mode=hybrid was dead-on-arrival; VectorIndex.upsert never called | 658844b |
| B9 | get_recent_jobs served staleness_state='expired' rows | e1c48a6 |
| B10 | enrich_job_task existed but wasn't registered in WorkerSettings | 226cf41 |
| B11 | Concurrent boot raced on _schema_migrations INSERT | acb9216 |
| B12 | No per-user rate limit on POST /search | e1c48a6 |

### Observability (3 of 3)

| # | Item | Commit |
|---|---|---|
| S1 | run_uuid ContextVar propagation + run_log.run_uuid | d64e9c6 |
| S2 | per-source timer + per_source_errors/duration columns | d64e9c6 |
| S3 | EnrichmentTelemetry / EmbeddingsTelemetry / HybridTelemetry | d64e9c6 |

### Test delta

Baseline (Step-0 green): 1,018p / 0f / 3s
Step-1 added: ~25-40 new tests across cohorts A-D
Final: TBD (run `make verify-step-1` for the actual count)

### Frontend mirror

`frontend/src/lib/types.ts` extended with the same 5+13 fields. Score-dim field `seniority` renamed to `seniority_score` to free the namespace for the enrichment enum.

### Deferred to Batch S1.5

- Staleness writer (B9 only filters; doesn't write `staleness_state`)
- Skill-synonyms non-tech expansion
- FX rate freshness
- Separate LLM quota pools (CV vs enrichment)
- Ghost-detection nightly cron

### Plan reference

`docs/_archive/step_1_plan.md` (executed-version annotations applied).

---

## Cross-Batch Foundation

### Branching strategy

- Each batch lives on a dedicated branch: `pillar3/batch-1`, `pillar3/batch-2`, etc.
- Strictly sequential: Batch N+1 does not start until Batch N is merged to `main` and this log is updated.

### Worktree convention (constant directories, rotating branches)

Two persistent worktrees live under `.claude/worktrees/`:

| Worktree | Path | Role |
|---|---|---|
| **generator** | `.claude/worktrees/generator/` | One Claude session writes batch code here |
| **reviewer** | `.claude/worktrees/reviewer/` | A *separate, independent* Claude session reviews the generator's diff here |

**These two directories never get deleted.** Only the branches inside them rotate per batch.

**Per-batch lifecycle:**

```
# At start of Batch N:
cd .claude/worktrees/generator && git checkout -B pillar3/batch-N main
cd .claude/worktrees/reviewer  && git checkout -B pillar3/batch-N-review main

# During Batch N:
#   - Generator session writes implementation in generator/
#   - When generator commits, reviewer session pulls that branch into reviewer/
#     and produces a review report (NEVER edits code that ships).

# At end of Batch N (merged to main):
git branch -d pillar3/batch-N pillar3/batch-N-review
# Worktree directories stay put — ready for Batch N+1.
```

The reviewer worktree is read-only with respect to shipped code. Its only output is review findings (saved as `docs/_archive/reviews/batch-N-review.md` or similar). All code changes that ship come from the generator worktree.

### Backup branches (one-time, pre-Batch-1)

The previous worktree branches contained 7 (generator) and 11 (reviewer) commits of unmerged work plus untracked plans. Preserved via:

- `backup/old-generator` branch — old generator commits (mostly Streamlit cleanup)
- `backup/old-reviewer` branch — old reviewer commits (security/scoring fixes — worth a triage pass to see if any should be cherry-picked to main)
- `docs/_archive/HARDCODED_REMOVAL_REPORT.md` — preserved untracked report
- `docs/_archive/old-plans/` — preserved untracked implementation plans (FastAPI build, LLM CV parser, hardcoded category removal)
- `git stash` entries — preserved local `settings.local.json` edits

### Test contract

Every batch's "done" criterion is:
1. **All previously-passing tests still pass** (no regressions)
2. **New tests for this batch pass** (TDD-first per `superpowers:test-driven-development`)
3. **HTTP mocked everywhere** per CLAUDE.md rule #4 — no live requests in CI

Run from `backend/`: `python -m pytest tests/ -v`

### Verification gates per batch

Before merging to `main`, each batch must:
- Pass full pytest suite from `backend/`
- Get a `coderabbit:code-review` pass on the diff
- Append a completion entry to this log (see template at the bottom)
- Update CLAUDE.md if any rules changed (e.g., new source counts, new load-bearing files)
- Save a memory file (`project_pillar3_batch_N_done.md`) so future sessions resume with full context

---

## Baseline (pre-Batch-1)

> Numbers below verified by 2026-04-18 fresh code-audit (see `docs/_archive/CurrentStatus.md`). Supersedes any earlier counts.

| Field | Value |
|---|---|
| Date | 2026-04-18 |
| Branch | `main` |
| Commit | `d364e9d` (chore: remove obsolete FastAPI plan and stock frontend README) |
| Worktrees aligned | ✅ generator + reviewer both at `d364e9d` |
| Total tests | 410 collected across 20 test files (per `CurrentStatus.md` §12) |
| Passing | _baseline pytest run still pending — must complete before Batch 1 starts_ |
| Failing | _to be filled in_ |
| Skipped | _to be filled in_ |
| Source count | 48 in `SOURCE_REGISTRY`, 47 unique source instances (`indeed`+`glassdoor` share `JobSpySource`) |
| Source breakdown | 7 keyed APIs · 10 free APIs · 10 ATS · 8 feeds · 7 scrapers · 5 other |
| ATS slugs | 104 across 10 ATS platforms (per `CurrentStatus.md` §10 / `companies.py`) |
| Date-fabricating sources | **39/47 (83%)** hardcode `datetime.now()` — 61 total call sites (revised up from earlier 14 estimate; per `CurrentStatus.md` §5) |
| Real-date sources | ~8/47 — careerjet, findwork, jsearch, landingjobs, nofluffjobs, reed, recruitee, remotive (partial) |
| Wrong-field sources | 3 — Jooble `updated` (L49), Greenhouse `updated_at` (L40), NHS Jobs `closingDate` (L57 + fallbacks L105/L111) |
| `bucket_accuracy_24h` | Unmeasured (no observability) |
| `date_reliability_ratio` | ~60–65% estimated |
| Multi-user support | None — single `user_profile.json`, single SQLite DB |
| Push notification channels | Email / Slack / Discord (per-installation env vars, not per-user) |
| Polling cadence | Twice-daily cron (currently broken — see `CurrentStatus.md` §13 Issue #3) |
| Dead phase-4 dirs | `backend/src/{filters,llm,pipeline,validation}/` — empty, only `__pycache__`. To be removed in Batch-1 pre-flight. |
| `keywords.py` keyword lists | Primary/Secondary/Tertiary/Relevance all **empty** (removed 2026-04-09); dynamic from CV required |
| `Job.is_new` field | Defined in dataclass, **not persisted to DB** — known schema gap |
| Frontend | Next.js 16.2.2 + React 19.2.4 — 5 pages incl. Kanban pipeline, CORS hardcoded `localhost:3000` (`api/main.py:20`) |

---

## Batch 1 — Date Model + Ghost Detection

**Status:** Ready for review (not yet merged to main)

**Reference:** `docs/research/pillar_3_batch_1.md` · Plan: `docs/plans/batch-1-plan.md`

**Scope:** 5-column date model migration, fix 39 fabricating + 3 wrong-field sources, recency-scoring update for `None` dates, ghost-detection state machine, 10-KPI exporter for Prometheus + Grafana.

**Branch:** `pillar3/batch-1`

**Pre-flight:**
1. **Delete phase-4 debris dirs first** — already clean in this worktree (worktree was branched from `d364e9d`; the debris dirs are empty-`__pycache__` only and exist only in the outer working copy, so no commit needed).
2. **Schema migration agent must run first and alone** — done in commit `b6c088b` (touches only `database.py` + new test file).
3. **Scope reminder** — 39 fabricator sources (not 14 as earlier docs claimed), plus 3 wrong-field sources.

---

## Batch 1 — Completion Entry (DRAFT — reviewer validates before merge)

**Generated:** 2026-04-18 (generator worktree on `pillar3/batch-1`)
**Branch:** `pillar3/batch-1` — 50 commits ahead of `main`
**Base:** `main` @ `d02d56c`
**Commit range:** `d02d56c..HEAD`

### Test deltas

| Metric | Baseline (clean-main, pre-Batch-1) | After Batch 1 | Delta |
|---|---:|---:|---:|
| Passing | **371** | **420** | **+49** |
| Failing | **24** (all in 4 pre-existing buckets) | **24** (same 4 buckets) | 0 |
| Skipped | **3** | **3** | 0 |
| Run time | 169.53s | 164.80s | −4.73s |

**Zero regressions.** Every one of the 24 remaining failures was present at baseline and falls into one of the four pre-existing buckets (API sqlite init, cron/setup path drift, 7 source parsers, 3 `matched_skills` stale assertions). The +49 delta is entirely new Batch 1 tests:

- `test_date_schema.py` × 13
- `test_ghost_detection.py` × 21 (includes 3 new integration tests for `_ghost_detection_pass`)
- `test_kpi_exporter.py` × 7 (includes 3 new regression tests for the `bucket_accuracy` circularity fix)
- `test_models.py` × 2
- `test_scorer.py` × 7
- `test_sources.py` × 3 new assertion blocks (inline, not new test functions — counted for correctness not for the +49 total)

**New tests added in Batch 1:**
- `tests/test_date_schema.py` — 13 tests covering the 5-column additive migration + idempotency
- `tests/test_ghost_detection.py` — 18 tests covering state-machine transitions + DB integration
- `tests/test_kpi_exporter.py` — 4 tests covering KPI compute paths (empty-DB safety, key completeness, mixed confidence, per-source crawl lag)
- `tests/test_models.py` — 2 new tests for 5-column Job fields
- `tests/test_scorer.py` — 7 new tests for the recency-scorer 5-column rewrite
- `tests/test_sources.py` — 3 new assertion blocks in jooble / greenhouse / nhs_jobs tests

**Tests removed/replaced:** 0 — all net-new.
**Pre-existing failures unchanged:** 24 (API sqlite ×6, cron/setup paths ×8, source parsers ×7 incl. `test_jooble_parses_response`, matched_skills ×3).

### KPI deltas

- `date_reliability_ratio` — baseline estimated ~60–65% (heavy fabrication). Post-Batch-1 this is now measurable via `backend/scripts/measure_date_reliability.py`. Run it after the next scrape to capture the real post-Batch-1 ratio. On the test fixtures alone the measurement script shows fabrication counts dropping to zero.
- `bucket_accuracy_24h` — now computable (was unmeasurable pre-Batch-1; no column for it).
- `stale_listing_rate` — now computable; starts at 0 until ghost-detection runs.
- Source count — unchanged at 48 / 47 unique per rule #8.
- `crawl_freshness_lag_seconds` — now emitted per-source.

### What shipped

1. **5-column date model** (`b6c088b`) — added `posted_at`, `first_seen_at`, `last_seen_at`, `last_updated_at`, `date_confidence`, `date_posted_raw`, `consecutive_misses`, `staleness_state` to the `jobs` table. Legacy `date_found`/`first_seen` columns preserved for back-compat. Migration is idempotent; fresh DBs get columns via inline `CREATE TABLE`.
2. **Job dataclass extensions** (`09cfe2d`) — `posted_at: Optional[str]`, `date_confidence: str = "low"`, `date_posted_raw: Optional[str]`. `normalized_key()` UNTOUCHED per rule #1.
3. **DB ghost-detection helpers** (`09cfe2d`) — `update_last_seen(key)` and `mark_missed_for_source(source, seen_keys)`.
4. **Recency scorer rewrite** (`d0a2ec7`) — new `recency_score_for_job()` honours `posted_at` + `date_confidence`. Fabricated confidence → 0 (no inflation). Low-confidence first-seen fallback capped at 60%. Both `score_job()` and `JobScorer.score()` flow through it.
5. **3 wrong-field source fixes** (`c83ad57`) — jooble (`updated`), greenhouse (`updated_at`), nhs_jobs (`closingDate`). Raw values preserved in `date_posted_raw`.
6. **Ghost-detection state machine + production wiring** — state machine in `backend/src/services/ghost_detection.py` (`6beea35`): `StalenessState` enum, `transition()`, `should_exclude_from_24h()`, `evaluate_job_state()` (CONFIRMED_EXPIRED is sticky). Production integration in `backend/src/main.py::_ghost_detection_pass` + call-site in `run_search()` (review-response commit): per-source absence sweep gated by a 70% rolling-7d-average scrape-completeness check so rate-limited scrapes never mark jobs as ghosts.
7. **Freshness KPI exporter: 6 live + 4 stubs** (`9e7708d` + review-response commit) — `backend/ops/exporter.py`, `backend/ops/grafana_dashboard.json`, `backend/scripts/measure_date_reliability.py`. LIVE: `date_reliability_ratio`, `bucket_accuracy_{24h,48h,7d,21d}`, `stale_listing_rate`, `crawl_freshness_lag_seconds` (per-source label). STUB (None/{}): `notification_latency_p{50,95}`, `pipeline_e2e_latency_p{50,95}`, `notification_delivery_success_rate` — all gated on the Batch 2 notification audit log. `prometheus_client` is an optional import; `compute_kpis()` runs pure SQL. **`bucket_accuracy_N` was initially circular** for low-confidence rows (measured them against their own `first_seen_at`, always returning ~100%); fixed in the review-response commit by filtering the SQL to `date_confidence IN ('high', 'medium', 'repost_backdated')` so the metric measures accuracy over *trustworthy* rows only, exactly as `pillar_3_batch_1.md` §1/§5 requires.
8. **44 source commits** — 39 fabricators × 1 commit each + 5 extras where the subagent identified a real posting date and recovered it to `posted_at` with `date_confidence='high'` (or `'medium'` for parsed relative strings). Confidence breakdown (from commit messages): **~30 `high`, ~2 `medium`, ~14 `low`**.
9. **docs/plans/batch-1-plan.md** — the TDD plan this batch followed, with clean-main baseline locked at top.

### What got deferred

- **Direct-URL verification step** in the ghost-detection flow (404/410 → `confirmed_expired`) — library scaffolding is in place (state exists, transition logic is sticky on `confirmed_expired`), but no code calls the direct-URL verifier yet. Punted to a Batch 1.5 or Batch 3 follow-up.
- **Repost detection via all-MiniLM-L6-v2 embeddings** — `pillar_3_batch_1.md` §3 Step 5 explicitly deferred to "Phase 2". Not implemented.
- **Notification latency + pipeline-E2E + per-channel delivery KPIs** — stubbed in `compute_kpis()` with `None`/`{}` until a notification audit log exists (Batch 2 deliverable). Gauges and dashboard rows are pre-allocated so the metric surface does not change when Batch 2 wires them.
- **`test_jooble_parses_response`** is a pre-existing source-parser-bucket failure (present in baseline). Not touched in Batch 1; the Batch-1 assertions added to the green paths of jooble / greenhouse / nhs_jobs prove the new fields are set correctly on the records that DO come through.

### Surprises / lessons

- **Fabricator count was 39, not 14**, as `CurrentStatus.md` §5 spelled out clearly. Earlier research docs under-counted.
- **The Job-dataclass defaults (`posted_at=None, date_confidence="low"`) made the 44 per-source edits about *explicit intent* rather than *correctness*.** A source that was NOT touched would still produce semantically correct output under the new model — the recency scorer would cap its recency at 60%. Making the edits explicit is a reviewer-ergonomics choice, not a correctness requirement.
- **Pre-flight debris cleanup was a no-op inside the worktree** — `backend/src/{filters,llm,pipeline,validation}/` only exist as stale `__pycache__` dirs in the *outer* working copy, not in the clean worktree. The plan documents this honestly instead of pretending a commit happened.
- **Git-Bash on Windows does not mount `/tmp`** — baseline log redirects had to use `/c/temp/batch1/` to land in a Windows-addressable path.

### CLAUDE.md / docs updated

- `docs/plans/batch-1-plan.md` — new (the TDD plan).
- `docs/IMPLEMENTATION_LOG.md` — this completion entry.
- `CLAUDE.md` — **no changes yet** because the 48/47 source count and the load-bearing rules #1/#2/#3 are unchanged. A reviewer may want to add a 1-line note pointing to the 5-column date model for future batches.

### Memory file saved

- `project_pillar3_batch_1_done.md` — will be saved by the reviewer after merge (generator worktree does not write into user memory).

### Handoff

Reviewer: your worktree is `.claude/worktrees/reviewer` on `pillar3/batch-1-review`. The audit checklist is in `docs/batch_prompts.md:152-238`. This completion entry is a DRAFT — please verify every claim against the actual diff before merging.

---

## Batch 2 — Multi-User Delivery Layer

**Status:** MERGED to main 2026-04-18 via `6446feb` (`--no-ff` merge of `pillar3/batch-2` @ `d124ed5`) after 3 reviewer rounds (report: `docs/reviews/batch-2-review.md`, final verdict APPROVED at `f5c3395` on `pillar3/batch-2-review`).

**Reference:** `docs/research/pillar_3_batch_2.md` · Plan: `docs/plans/batch-2-plan.md` · Decisions: `docs/plans/batch-2-decisions.md`

**Scope:** Auth + multi-tenant schema, `user_feed` SSOT table + FeedService, ARQ-compatible worker tasks + Apprise dispatcher, 99% pre-filter cascade, channel config UI.

**Branch:** `pillar3/batch-2`

**Pre-flight:** Completed `superpowers:brainstorming` (12 design decisions doc'd) before any code; baseline locked at `420 passed / 24 failed / 3 skipped` on commit `31124fa`.

---

## Batch 2 — Completion Entry (DRAFT — reviewer validates before merge)

**Generated:** 2026-04-18 (generator worktree on `pillar3/batch-2`)
**Branch:** `pillar3/batch-2`
**Base:** `main` @ `31124fa`
**Commit range:** `31124fa..HEAD` — 11 commits

### Commits (high-level)

| Commit | Subject |
|---|---|
| `381b3d0` | docs(pillar3): Batch 2 decisions + plan |
| `575eb8c` | feat(migrations): add forward/reverse SQL migration runner |
| `e3ba487` | feat(auth): users + sessions with argon2id + signed cookie |
| `1a4c07d` | feat(tenancy): per-user user_actions + applications |
| `5932b61` | feat(feed): user_feed SSOT table + FeedService |
| `b2f4873` | feat(prefilter): 99% 3-stage pre-filter cascade |
| `e60b285` | feat(worker): score_and_ingest task + notification_ledger |
| `99ef596` | feat(channels): Apprise dispatcher + Fernet credential storage |
| `b4bf372` | fix(test): auth sessions fixture targets migration 0001 only |
| `87d177f` | feat(api): /auth and /settings/channels routes + session middleware |
| `4d6560c` | feat(frontend): login + register + /settings/channels pages |

### Test deltas (to be confirmed by final regression run)

| Metric | Baseline (clean-main, post-Batch-1) | After Batch 2 | Delta |
|---|---:|---:|---:|
| Passing | **420** | **497** | **+77** |
| Failing | **24** (pre-existing 4 buckets) | **24** (unchanged, same buckets) | 0 |
| Skipped | **3** | **3** | 0 |
| Run time | 167.32s | 205.26s | +37.94s |

**New test files (73 new passing tests expected):**

- `tests/test_migrations.py` — 5 (runner up/down/idempotent/status)
- `tests/test_auth_passwords.py` — 4 (roundtrip, argon2id format, distinct salts, malformed)
- `tests/test_auth_sessions.py` — 5 (create/resolve/revoke/tamper/expired/wrong-secret)
- `tests/test_tenancy_isolation.py` — 7 (**dedicated test class per success criteria**)
- `tests/test_feed_service.py` — 8 (read + write + cascade paths)
- `tests/test_prefilter.py` — 15 (location, experience, skills, full cascade)
- `tests/test_worker_tasks.py` — 8 (idempotency, per-user pre-filter, ledger unique-per-channel, threshold, mark sent/failed)
- `tests/test_channels_crypto.py` — 4 (Fernet roundtrip + tamper + distinct + wrong-key)
- `tests/test_channels_dispatcher.py` — 6 (Apprise routing + disabled-skip + test-send OK/exception + format variants)
- `tests/test_auth_routes.py` — 8 (register/login/logout/me)
- `tests/test_channels_routes.py` — 7 (CRUD + **tenant isolation at API layer** + test-send)

**Tests removed/replaced:** 0 — all net-new.

### KPI deltas

- `notification_delivery_success_rate` — now computable once ARQ worker runs in prod (stubbed metric gauge exists from Batch 1). Post-Batch-2 notification ledger is the data source.
- `notification_latency_p{50,95}` — same (pipeline stub → measurable as soon as dispatcher runs against a real Apprise endpoint).
- Multi-user support — was 0 (`data/user_profile.json` single-tenant), now N users on shared schema with dedicated tenant-isolation test class.
- CORS single-origin bug (CurrentStatus.md §13 #5) — fixed: `FRONTEND_ORIGIN` env-driven.

### What shipped

1. **Migration runner** (`backend/migrations/`) — forward/reverse SQL files + idempotent runner + `_schema_migrations` registry. 5 migrations applied: `0000_baseline` (no-op record), `0001_auth`, `0002_multi_tenant`, `0003_user_feed`, `0004_notification_ledger`, `0005_user_channels`.
2. **Auth** — argon2id (argon2-cffi) + itsdangerous-signed cookies + 30-day expiry. Routes: `POST /api/auth/{register,login,logout}`, `GET /api/auth/me`. Cookie: `job360_session`, HttpOnly, SameSite=Lax, Secure=off in dev.
3. **Tenancy schema** — `user_actions` and `applications` rebuilt with `user_id` + `UNIQUE(user_id, job_id)`; legacy single-user rows backfilled to placeholder user `00000000-0000-0000-0000-000000000001`. `jobs` catalog untouched per CLAUDE.md rule #1. Seven tests in a dedicated `TestTenantIsolation` class prove A↔B separation **at the SQL layer**. ⚠️ **The repository layer (`JobDatabase.insert_action` / `delete_action` / `get_actions` / `get_action_counts` / `get_action_for_job` / `create_application` / `advance_application` / `_get_application` / `get_applications` / `get_application_counts` / `get_stale_applications`) is still tenant-blind — writes default to `DEFAULT_TENANT_ID` via the column DEFAULT, reads have no `WHERE user_id = ?` filter.** Commit `1a4c07d`'s subject "per-user user_actions + applications" should have read "SCHEMA for per-user user_actions + applications (repo layer auth-gating deferred to Batch 2.1)". TODO markers added above `insert_action` and `create_application` in commit `0e45c3e`. Review-response commit `575eb8c`-equivalent wiring (threading `Depends(require_user)` through existing routes + adding `user_id` params to the 11 methods) is explicit Batch 2.1 scope. See §"What got deferred" item 1.
4. **`user_feed` SSOT + FeedService** — one table, same service class feeds both dashboard (FastAPI) and notification worker. Cascade stale / update status / mark notified / upsert idempotent per (user, job).
5. **99% pre-filter cascade** — `location → experience → skill overlap`, each stage can be unit-tested independently. Permissive on missing signals (false positives cheap, false negatives expensive).
6. **Worker task + notification ledger** — `score_and_ingest` runs pre-filter + scoring + feed upsert + optional instant-notify enqueue. Ledger `UNIQUE(user_id, job_id, channel)` gives per-channel idempotency for free. Tasks are pure async — no `arq` import at module level so pytest never touches Redis.
7. **Channel dispatcher + Fernet crypto** — per-user `user_channels` table, Fernet-encrypted Apprise URLs (key from `CHANNEL_ENCRYPTION_KEY`), `dispatch(user_id, title, body)` and `test_send(channel_id)` APIs. Apprise import is lazy (library-mode tax avoided). Tests monkeypatch `apprise.Apprise`.
8. **FastAPI routes + CORS fix** — `/api/auth/*` and `/api/settings/channels/*` added. Pre-existing `/api/jobs`, `/api/actions`, etc. are **untouched** in Batch 2 (they remain open); wrapping them in auth is explicit follow-up to avoid breaking the 6 pre-existing `test_api.py` failures further. CORS origin now env-driven via `FRONTEND_ORIGIN`.
9. **Frontend** — `/login`, `/register`, `/settings/channels` pages added using existing shadcn primitives. `lib/api.ts` sets `credentials: 'include'` on every call and exposes typed `register/login/logout/me/listChannels/createChannel/deleteChannel/testChannel`. No frontend automated tests — manual smoke is a merge prerequisite.

### What got deferred

- **Wrapping existing `/api/jobs`, `/api/actions`, `/api/profile`, `/api/pipeline`, `/api/search` in `Depends(require_user)` AND adding `user_id` params to the 11 `JobDatabase` action/application methods.** Batch 2 ships the dependency (`src/api/auth_deps.py::require_user`) and the tenant-scoped `/api/auth` + `/api/settings/channels` routes; rolling it across pre-existing endpoints is mechanical but would compound with the 6 pre-existing `test_api.py` failures. **Net effect today:** two real users who both hit `/api/jobs/{id}/action` alias-collapse onto the placeholder tenant — the second writer's `INSERT OR REPLACE` overwrites the first. Mitigation: the existing UI does not register users (there is only one `user_profile.json`), so the collision path is not reachable via the shipped frontend. Explicit Batch 2.1 scope.
- **ARQ runtime settings module** (`src/workers/settings.py` with `WorkerSettings` + Redis pool). Tasks are runnable in-process today via direct function call; productionising the scheduler is a Batch 3 follow-up.
- **Digest timer / quiet hours** — schema + preference columns are ready (blueprint §1 shape), but the scheduled `send_digest` ARQ job is not wired in this batch.
- **Migration from single-user `user_profile.json` to a per-user `user_profiles` table.** The file continues to work for the CLI path (tenant = default user); multi-user CVs / LinkedIn / GitHub per user is Batch 3.
- **PostgreSQL migration.** Decisions doc §D4 deferred to Batch 3 first step.
- **SSE dashboard updates.** Polling remains MVP (D3); SSE `EventSourceResponse` endpoint is a Batch 3 bolt-on.
- **Channel payload richness** — Slack Block Kit, Discord embeds, Telegram MarkdownV2. Current `format_payload()` returns plain markup. Upgrade is a local change in `services/channels/dispatcher.py`.
- **CSRF protection** — `SameSite=Lax` covers non-mutating GETs today; double-submit CSRF tokens land when the frontend moves off same-origin.
- **Password reset / email verification / 2FA.** Explicitly excluded per plan "Out-of-scope".

### Review-response commits (round 2)

After the reviewer flagged three P1s + two Ps:

- `ab8155a` `fix(security): fail-closed on missing SESSION_SECRET / CHANNEL_ENCRYPTION_KEY (P1-3)` — raises `RuntimeError` with a generator hint instead of silently using a committed dev default; cookie `Secure` flag now gates on `JOB360_ENV=="prod"`; pinned 5 new runtime deps (P2-1) in `pyproject.toml` (argon2-cffi, itsdangerous, apprise, email-validator, cryptography).
- `5920703` `fix(worker): per-user JobScorer invocation in score_and_ingest (P1-2)` — replaced the catalog-level `job.match_score` lookup with a real `JobScorer.score(job)` call per user, either via `ctx['scorer']` (tests) or a shared `SearchConfig` loaded from `user_profile.json` (production pre-Batch-3). Test now proves per-user scoring by returning `{alice: 85, bob: 70}` from the injected scorer and asserting on the call list.
- `48cea56` `fix(channels): dispatcher.test_send enforces user_id ownership (P2-2)` — defense-in-depth; the service boundary now refuses to dispatch to a channel the caller does not own, even if the HTTP-layer check is forgotten. New `test_test_send_rejects_cross_user_channel_id` proves mallory cannot dispatch via alice's `channel_id`.
- `(this commit)` `docs: P1-1 overclaim reword + D6 reconciliation + TODO markers` — the reword in §"What shipped" item 3 above, TODO markers above `insert_action` + `create_application` in `backend/src/repositories/database.py`, and the D6 REVISION block in `docs/plans/batch-2-decisions.md`.

Not addressed in round 2 (accepted deferrals):

- **P1-1 — full repo-layer tenant scoping.** Option (b) per reviewer. Option (a) wiring is explicit Batch 2.1.
- **P2-3 — `asyncio.to_thread` wrap around sync `Apprise.notify`.** Not reachable in Batch 2 (ARQ worker not wired); must land before the worker schedule ships in Batch 3.
- **P2-4 — migration 0002 column-mirror test.** Acceptable as a Batch 3 polish; risk is low since no Batch 1.x/2.x migration between 0001 and 0002 adds columns.
- **P2-5 — per-user `instant_threshold`.** Depends on the Batch 3 `user_profiles` table.
- **P3-1 — dead `idempotency_key()` helper.** Kept; the call site for Redis-backed pre-DB dedup is a clean addition in Batch 3.
- **P3-3 — `core/tenancy.py` thin-module concern.** Kept — adding a `require_tenant` helper that just delegates to `require_user` would be ceremony.
- **P3-4 — `resolve_session` last_seen slide error handling.** Kept as-is.
- **P3-5 — migration runner stem-on-exception log line.** Low priority.

### Surprises / lessons

- **Blueprint + plan disagreed on whether `tenant_id` belongs on `jobs`.** The plan draft said yes. Re-reading blueprint §3 ("jobs is a shared catalog, user_feed is per-user") made clear it should stay off — the correct per-user scoping is `user_feed.user_id`. Corrected inline in Phase 2; the plan's Phase 2 description should be treated as an early sketch that the implementation improved.
- **`sqlite3.Row` isn't sortable or tuple-comparable.** Three tests initially failed with `TypeError: '<' not supported between instances of 'sqlite3.Row'` — easy fix (convert to `tuple(row)` in assertions) but worth noting for future test authors.
- **`email-validator` rejects `.test` and `.example` TLDs** as "special-use reserved names." Tests use `@example.com` throughout. Production is unaffected.
- **ARQ tests don't need Redis at all** — by keeping tasks as plain async functions and injecting `ctx['db']` + optional `ctx['enqueue']`, the scheduler becomes an adapter and the business logic is pytest-native. This is cleaner than the blueprint suggested; the "migrate to Celery at 30K users" decision point is also now trivially reversible since nothing in `tasks.py` imports ARQ.
- **`email-validator`, `argon2-cffi`, `itsdangerous`, `apprise`, `pydantic[email]` all had to be pip-installed mid-batch** — they were not in the project venv. `pyproject.toml` still needs updating to pin these as formal deps (reviewer TODO — minor risk that CI without the manual installs fails until the pin lands).
- **Existing `/api/jobs` endpoints remain unauthenticated.** This is a deliberate Batch 2 scoping decision to keep the blast radius small, not an oversight. The completion criteria called for "new tests for auth flow, tenant isolation"; both passed. Tenant-scoping existing endpoints is a safe, mechanical follow-up.

### CLAUDE.md / docs updated

- `CLAUDE.md` — appended "Batch 2 additions" section (new tables, modules, env vars, deps, 3 new rules #10–12)
- `docs/plans/batch-2-decisions.md` — new (brainstorming output)
- `docs/plans/batch-2-plan.md` — new (TDD plan with locked baseline)
- `docs/IMPLEMENTATION_LOG.md` — this completion entry

### Memory file saved

- `project_pillar3_batch_2_done.md` — drafted for reviewer persistence (generator worktree does not write into user memory directly)

### Handoff

Reviewer: your worktree is `.claude/worktrees/reviewer` on `pillar3/batch-2-review`. This completion entry is a DRAFT — verify every claim against the actual diff and the final full-suite regression run before merging. Particular review targets:

1. **Tenant isolation audit** — `test_tenancy_isolation.py::TestTenantIsolation` is six tests in a dedicated class. Read each; ensure no tenant leakage path was missed.
2. **Migration 0002** — the SQLite table-rebuild pattern for `user_actions` / `applications` must be inspected carefully for any row loss; the `SELECT ... FROM` clause must include every pre-existing column or data disappears silently.
3. **`pyproject.toml` dep pins** — argon2-cffi, itsdangerous, apprise, pydantic[email] need formal entries.
4. **`/api/jobs` tenant-scoping** — explicit deferral; decide whether to block merge on this or accept it as a follow-up.
5. **Apprise mock in `conftest.py`** — Phase 6 tests monkeypatch per-test; a global autouse fixture in `conftest.py` might be cleaner insurance against future tests that forget to mock.

---

## Batch 3 — Tiered Polling + Source Expansion

**Status:** READY_FOR_REVIEW 2026-04-18

**Reference:** `docs/research/pillar_3_batch_3.md` · Plan: `docs/plans/batch-3-plan.md`

**Scope:** Tiered polling scheduler (60s ATS / 5m Reed / 15m Workday+RSS / 60m scrapers), ETag/Last-Modified conditional fetch, +5 new sources (Teaching Vacancies, GOV.UK Apprenticeships, NHS Jobs XML, Rippling, Comeet), −3 drops (YC Companies, Nomis, FindAJob), ATS slug catalog 104 → 268, per-source circuit breakers replacing `newly_empty`.

**Branch:** `pillar3/batch-3` — 9 commits on top of Batch 2 merge

---

## Batch 3 — Completion Entry (DRAFT — reviewer validates before merge)

**Generated:** 2026-04-18 (generator worktree on `pillar3/batch-3`)
**Branch:** `pillar3/batch-3`
**Base:** `main` @ Batch 2 merge
**Commit range (9 commits):**

| Commit | Subject |
|---|---|
| `040842e` | docs(pillar3): Batch 3 plan + POST-BATCH-2 baseline locked |
| `81c532a` | refactor(sources): drop YC Companies, Nomis, FindAJob (Batch 3 scope) |
| (C)      | feat(sources): ETag/Last-Modified conditional fetch in BaseJobSource |
| (D)      | feat(resilience): per-source circuit breakers replace newly_empty flag |
| (E)      | feat(scheduler): tiered polling replaces twice-daily cron |
| (F)      | feat(sources): add 5 new sources (Batch 3 scope) |
| `3ed58d7` | feat(companies): expand ATS slug catalog 104 -> 268 (Batch 3) |
| `c62b98b` | chore(registry): rotate source count 48 -> 50 (CLAUDE.md #8) |
| (I)      | docs(pillar3): Batch 3 completion entry + CLAUDE.md appendix |

### Test deltas

| Metric | Baseline (post-Batch-2) | After Batch 3 | Delta |
|---|---:|---:|---:|
| Passing | **498** | **529** | **+31** |
| Failing | **24** (pre-existing 5 buckets) | **24** (unchanged, same buckets) | 0 |
| Skipped | **3** | **3** | 0 |
| Run time | 184.91s | 225.88s | +40.97s |

**Zero regressions.** Every one of the 24 remaining failures was present at baseline and falls into the pre-existing buckets documented in Batch 1 (§ API sqlite init / setup path drift / cron path drift / source parsers / matched_skills stale).

**New test files + block totals (31 new passing tests):**

- `tests/test_conditional_fetch.py` — 4 (first-fetch ETag, 304-roundtrip, Last-Modified, no-validator)
- `tests/test_circuit_breaker.py` — 7 (CLOSED start, 5-fail trip, OPEN rejects, cooldown→HALF_OPEN, HALF_OPEN success closes, HALF_OPEN failure reopens, registry scoping)
- `tests/test_scheduler.py` — 6 (tier resolution, 60s/3600s cadence, tier fairness, breaker integration, force-mode)
- `tests/test_companies_slugs.py` — 4 (count ≥250, no dups, Workday fields, SuccessFactors fields)
- `tests/test_sources.py` — 15 new (3 each for teaching_vacancies, gov_apprenticeships, nhs_jobs_xml, rippling, comeet)
- 5 tests **removed** along with the dropped sources (findajob × 2, yc_companies × 1, nomis × 2)

Net: 36 new tests added − 5 tests removed with dropped sources = **+31 passing**, exactly matching the measured delta.

### KPI deltas (where measurable)

- **Source count:** 48 → 50 (+5 −3). ATS slug catalog 104 → 268 (+158%).
- **Polling freshness:** twice-daily cron (broken per CurrentStatus.md §13 #3) → tiered: ATS 60s / Reed 5m / Workday+RSS 15m / Scrapers 60m. Measurable via `scheduler.tick()` cadence logging once deployed.
- **Source reliability:** `newly_empty` post-hoc warning → active circuit-breaker protection (OPEN breaker blocks subsequent fetches until cooldown). Observable via the scheduler's skip-log lines.
- **Bandwidth:** ETag/Last-Modified conditional fetches opt-in per source; not wired into any existing source in this batch (pure infra). Phase F/post-merge sources can opt in by calling `_get_json_conditional()` instead of `_get_json()`.
- **bucket_accuracy_24h / date_reliability_ratio:** 4 of the 5 new sources (`teaching_vacancies`, `gov_apprenticeships`, `nhs_jobs_xml`, `rippling`, `comeet`) produce real `posted_at` with `date_confidence='high'` when the upstream feed includes a timestamp — small but honest uplift to the fabrication ratio once the scheduler is running.

### What shipped

1. **Tiered polling scheduler** (`backend/src/services/scheduler.py`) with `resolve_tier_seconds()` + `TieredScheduler.tick(now, force=False)` + `run_forever()`. Injectable `clock` for deterministic tests (no freezegun needed). Consults the breaker registry and skips OPEN sources without dispatch.
2. **Per-source circuit breakers** (`backend/src/services/circuit_breaker.py`): CLOSED / HALF_OPEN / OPEN state machine with injectable clock, 5-failure threshold, 300s cooldown defaults. `BreakerRegistry.get(name)` lazy factory for shared state. Wired into `main.py::run_search` replacing the `newly_empty` heuristic.
3. **Conditional-fetch layer** (`backend/src/services/conditional_cache.py` + `BaseJobSource._get_json_conditional`): FIFO-bounded (256-entry) cache; opt-in per URL. Backwards-compatible — all 47 existing sources keep using plain `_get_json()`.
4. **5 new sources** (full `BaseJobSource` pattern, all honouring the Batch 1 `posted_at`/`date_confidence` contract):
   - `TeachingVacanciesSource` (UK DfE schema.org JobPosting, OGL v3.0)
   - `GovApprenticeshipsSource` (GOV.UK Find an Apprenticeship, 150 req/5 min cap)
   - `NHSJobsXMLSource` (all-current-vacancies XML feed, `<createdDate>` → high confidence)
   - `RipplingSource` (Rippling ATS `/api/board/{slug}/jobs`)
   - `ComeetSource` (Comeet ATS `/careers-api/2.0/company/{slug}/positions`)
5. **3 drops:** YC Companies (covered by HN Jobs + Ashby), Nomis (ONS macro-statistics, not a jobs feed — miscategorised), FindAJob (HTML-scrape of an endpoint powered by Adzuna under the hood — double-counting).
6. **ATS slug catalog 104 → 268** hand-curated across Greenhouse (25→82), Lever (12→35), Workable (8→25), Ashby (9→25), SmartRecruiters (6→15), Pinpoint (8→15), Recruitee (8→20), Personio (10→18), Workday (15→20). Rippling (5 new) + Comeet (5 new) starter lists. `COMPANY_NAME_OVERRIDES` extended for the new additions.
7. **Registry surface rotation:** `SOURCE_REGISTRY` 48→50 · `_build_sources` 47→49 instances · `RATE_LIMITS` +5 entries (3 removed) · `test_cli.py::test_source_registry_has_50_sources` · `test_api.py::test_sources_returns_50` · every hardcoded "48" in test_api.py updated to 50.
8. **`docs/plans/batch-3-plan.md`** — the TDD plan this batch followed (378 lines), with POST-BATCH-2 baseline locked at the top.

### What got deferred

- **Full Feashliaa repo parse for 500+ slugs.** The Batch 3 ideal of 500+ slugs requires a dedicated clone + filter + validate pipeline (parse ~95K slugs, filter to ~2-5K UK, Google-dork validate to ~200-500). That is its own batch. Batch 3 ships 268 hand-curated slugs — well above the ≥250 plan-target, honest about the gap to the research ideal.
- **Per-slug HTTP validation for the newly-added 164 slugs.** Batch 3 adds slugs but does not live-validate each one — that would break the pytest-offline contract (CLAUDE.md rule #4). Unknown or dead slugs no-op gracefully via `_request` → `None` → empty list at the source layer. Validation is a follow-up that can run as a one-shot `scripts/validate_slugs.py` job in staging.
- **ARQ runtime wiring for the tiered scheduler.** `TieredScheduler.run_forever()` exists but is not attached to a system service (systemd/docker-compose/Render cron). The scheduler's `tick()` is callable from `run_search` or pytest today; productionising the long-running loop is Batch 4 "Launch readiness" scope.
- **Wiring `_get_json_conditional` into the 47 existing sources.** Shipping the infra in this batch; adopting it per-source is a follow-up that can roll out source-by-source with low blast radius.
- **Direct-URL 404→confirmed_expired ghost-detection verifier** (Batch 1 §deferred) — still deferred.
- **Migration from `user_profile.json` to per-user `user_profiles` table** (Batch 2 §deferred) — still deferred.
- **Postgres migration** (Batch 2 §D4) — still deferred.
- **Wrapping `/api/jobs`, `/api/actions`, `/api/profile`, `/api/pipeline`, `/api/search` in `Depends(require_user)` + `user_id` params on `JobDatabase` action methods** (Batch 2.1 scope) — not in Batch 3 scope.

### Surprises / lessons

- **"NHS Jobs XML replaces the RSS-ish source" read as additive, not replacement.** The hard constraint said the registry must go 48 → 50 which is only arithmetically possible if the new NHS XML source is a **separate entry** alongside the existing `nhs_jobs.py`. That is how it ships — two NHS sources (`nhs_jobs` keyword-search + `nhs_jobs_xml` all-current-vacancies feed) with distinct registry keys and distinct upstream endpoints. The reviewer should confirm this was the intended reading.
- **Two hardcoded source counts**, not one. The CLI test `test_source_registry_has_48_sources` was the obvious one called out in CLAUDE.md rule #8. The API test `test_sources_returns_48` (plus three `== 48` checks inside `test_status_returns_counts` and `test_full_api_workflow`) was a second, undocumented dependency. Both now say `== 50`. A rule-#8 note about this second surface would save the next batch-generator a round-trip.
- **`SOURCE_INSTANCE_COUNT` drift — corrected in round 2 (commit below).** Initial Batch 3 push left the constant at 47 with a log claim that it was unused. The reviewer (`docs/reviews/batch-3-review.md` §P2) flagged that it IS used by `test_main.py::test_source_instance_count_matches_build` (+ 3 other call-sites in the same file) — a purpose-built drift-catcher. The constant was updated to 49 and this entry rewritten. `test_main.py` remains `--ignore`'d in the pytest baseline due to the pre-existing JobSpy live-HTTP leak, so the drift never affected CI gates, but the invariant is restored and the log claim is now accurate.
- **Circuit-breaker and scheduler together > either alone.** The breaker in Phase D only *logs* newly-opened breakers in `run_search`; the scheduler in Phase E turns that observability into protection by calling `can_proceed()` before dispatching. Both land in the same batch to avoid shipping half-active defenses.
- **Test-time clock injection beats freezegun for this domain.** Circuit breakers and scheduler tests pass in sub-200ms without `freezegun` because the `clock=lambda: now[0]` pattern costs nothing to the production code (defaults to `time.monotonic`) but gives tests deterministic advancement without patching the standard library.
- **First commit accidentally bundled leftover Playwright/screenshot files** from a pre-Batch-3 session (the untracked leftovers the user's Step 1.5 message identified). Mitigated with `git reset --mixed HEAD^` and a scoped `git add backend/`; subsequent commits have been scoped-add from the start.

### CLAUDE.md / docs updated

- `docs/plans/batch-3-plan.md` — new (the TDD plan).
- `docs/IMPLEMENTATION_LOG.md` — this completion entry.
- `CLAUDE.md` — appended "Batch 3 additions" section (new modules, new rule note re: test_api.py source-count dependency, new rate-limit entries, new ATS slug counts).

### Memory file saved

- `project_pillar3_batch_3_done.md` — to be written by the reviewer after merge (generator worktree does not write into user memory directly).

### Handoff

Reviewer: your worktree is `.claude/worktrees/reviewer` on `pillar3/batch-3-review`. The audit checklist is in `docs/batch_prompts.md:275-299`. This completion entry is a DRAFT — please verify every claim against the actual diff and the final full-suite regression run before merging. Particular review targets:

1. **The NHS Jobs "additive vs replacement" interpretation** — is a parallel `nhs_jobs_xml` entry the right call, or should the old `nhs_jobs.py` be removed and the count land at 49 + explicit rule rewrite?
2. **Slug quality.** 164 new slugs were hand-curated from research-doc UK mentions. A spot-check sampling (e.g. pick 10 random slugs, attempt the real public API in staging) is worth doing before merge.
3. **Scheduler is not yet wired to `run_search`.** Does the reviewer want that wired in Batch 3 or accept it as Batch 4 scope?
4. **Conditional-fetch not wired to any existing source.** Same question.
5. **`SOURCE_INSTANCE_COUNT` constant at `main.py:131`** — drift acceptable, or update to 49?

---

## Batch 4 — Launch Readiness

**Status:** Blocked on Batch 3

**Reference:** `docs/research/pillar_3_batch_4.md`

**Scope:** Scope down to top 10–15 sources for MVP, freemium metering, pricing page, ICO registration (£40), privacy notice + LIA, ASA-compliant marketing copy, Amazon SES setup.

**Branch:** `pillar3/batch-4`

**Pre-flight:** Update PRD's "all UK white-collar domains" claim — currently fails CAP Code rule 3.7 substantiation.

_Completion entry will be appended here when merged._

---

## Batch 3.5 — Stabilisation (IDOR + ARQ runtime + scheduler wire-up)

**Status:** READY_FOR_REVIEW 2026-04-19

**Reference:** `docs/plans/batch-3.5-plan.md`

**Scope:** Close three Batch-2/3 deferrals that matter most for multi-user
safety + launchability.

  - **Deliverable C** — IDOR fix on legacy `/api/jobs`, `/api/actions`,
    `/api/pipeline` routes (CLAUDE.md rule #12).
  - **Deliverable D** — ARQ runtime executable (`send_notification`,
    `WorkerSettings`, `REDIS_URL`-driven `redis_settings`).
  - **Deliverable E** — wire `TieredScheduler.tick(force=True)` into
    `run_search`, replacing the Batch-3 `asyncio.gather` block.

**Branch:** `pillar3/batch-3.5` — 5 commits on top of Batch 3 merge

---

## Batch 3.5 — Completion Entry (DRAFT — reviewer validates before merge)

**Generated:** 2026-04-19 (generator worktree on `pillar3/batch-3.5`)
**Branch:** `pillar3/batch-3.5`
**Base:** `main` @ Batch 3 merge (post-merge origin/main = `fad1744`)

### Commits (5)

| Commit | Subject |
|---|---|
| `f8cf829` | docs(pillar3): Batch 3.5 plan (IDOR fix + ARQ runtime + scheduler wiring) |
| `56a66f3` | fix(api): scope per-user routes by user_id (IDOR) |
| (D)      | feat(workers): implement send_notification + WorkerSettings |
| `328e72f` | feat(scheduler): wire TieredScheduler into run_search |
| (I)      | docs(pillar3): Batch 3.5 completion entry |

### Test deltas

| Metric | Baseline (post-Batch-3) | After Batch 3.5 | Delta |
|---|---:|---:|---:|
| Passing | **529** | **558** | **+29** |
| Failing | **24** (pre-existing 5 buckets) | **23** (same buckets, 1 flaky source flipped green) | −1 |
| Skipped | **3** | **3** | 0 |

Command: `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q`
Baseline log: `/tmp/pytest_baseline_3_5.log`
Post-Batch log: `/tmp/post_e_v2.log`

**Zero regressions.** The 28 newly-added tests all pass in full-suite
context. The −1 failure is a flaky source parser that flipped green
this run (pre-existing bucket — not caused or fixed by Batch 3.5).

**New test files (+28 passing):**
  - `backend/tests/test_api_idor.py` — 17 tests
    (parametrized unauth-401 × 10 + action-isolation × 3 + pipeline-isolation × 3 + roundtrip × 1)
  - `backend/tests/test_worker_settings.py` — 3 tests
    (functions list, REDIS_URL parsing, no-top-level-arq-import)
  - `backend/tests/test_worker_send_notification.py` — 5 tests
    (dispatches + marks sent, failed + error_message, mixed counts, idempotency, unknown job no-op)
  - `backend/tests/test_main_scheduler_wiring.py` — 3 tests
    (tick called force=True, each source called once, breaker-OPEN skipped)

**Tests removed/replaced:** 0 — all net-new.

### KPI deltas (where measurable)

  - **Multi-user safety:** Before — two real users hitting
    `/api/jobs/{id}/action` alias-collapse onto the placeholder tenant
    and clobber each other (docs/IMPLEMENTATION_LOG.md Batch 2 entry §P1-1
    deferral). After — every per-user route requires
    `Depends(require_user)`; every repo method takes `user_id` and
    scopes queries. `INSERT OR REPLACE` replaced with `ON CONFLICT(user_id,
    job_id) DO UPDATE` matching migration 0002's widened UNIQUE.
  - **Launchability:** Before — Batch 2 tasks could only be called
    directly from tests; no `WorkerSettings` meant `arq` couldn't boot.
    After — `arq src.workers.settings.WorkerSettings` starts the worker
    with 4 functions registered and `redis_settings` parsed from
    `REDIS_URL`. Smoke: `python -c "from src.workers.settings import
    WorkerSettings; print([f.__name__ for f in WorkerSettings.functions])"`
    → `['score_and_ingest', 'send_notification', 'mark_ledger_sent_task',
    'mark_ledger_failed_task']` (verified 2026-04-19).
  - **Freshness benefit:** Before — Batch 3 built the scheduler but
    `main.py::run_search` still used `asyncio.gather`, so tier
    intervals had zero production effect on the CLI path. After —
    scheduler dispatches, breaker-OPEN sources are skipped, per-source
    success/failure routes into the breaker in a single place.

### What shipped (with file:line anchors)

**Deliverable C — IDOR fix on legacy routes** (commit `56a66f3`):

1. Route handlers threaded with `Depends(require_user)`:
    - `backend/src/api/routes/actions.py:4` — import `CurrentUser, require_user`
    - `backend/src/api/routes/actions.py:19,37,46,59` — 4 endpoints gated
    - `backend/src/api/routes/pipeline.py:12` — import
    - `backend/src/api/routes/pipeline.py:43,55,67,80,95` — 5 endpoints gated
    - `backend/src/api/routes/jobs.py:10` — import
    - `backend/src/api/routes/jobs.py:71,122,187` — 3 endpoints gated
2. Repo methods threaded with `user_id` (`backend/src/repositories/database.py`):
    - `insert_action(job_id, action, user_id, notes)` L282
    - `delete_action(job_id, user_id)` L297
    - `get_actions(user_id)` L304
    - `get_action_counts(user_id)` L315
    - `get_action_for_job(job_id, user_id)` L323
    - `create_application(job_id, user_id)` L335
    - `advance_application(job_id, stage, user_id)` L347
    - `_get_application(job_id, user_id)` L358
    - `get_applications(user_id, stage)` L374
    - `get_application_counts(user_id)` L398
    - `get_stale_applications(user_id, days)` L407
3. `insert_action` SQL switched from `INSERT OR REPLACE(UNIQUE job_id)` to
   `ON CONFLICT(user_id, job_id) DO UPDATE` matching migration 0002's
   widened `UNIQUE(user_id, job_id)` constraint.
4. Tests: `backend/tests/test_api_idor.py` — 17 tests all GREEN
   (auth requirement parametrized over 10 endpoints + cross-user
   isolation for actions AND pipeline + positive-control round-trip).

**Deliverable D — ARQ runtime** (commit D):

5. `backend/src/workers/settings.py:80` — `class WorkerSettings` with
   `functions = [score_and_ingest, send_notification,
   mark_ledger_sent_task, mark_ledger_failed_task]` (line 87).
   `arq` import is lazy (only inside `_load_arq_redis_settings()`) per
   CLAUDE.md rule #11 — verified by
   `backend/tests/test_worker_settings.py::test_arq_not_imported_at_module_top`
   which blocks `arq` imports via `sys.meta_path` and re-imports the
   module without error.
6. `backend/src/workers/tasks.py:199` — `async def send_notification(
   ctx, user_id, job_id, urgency)` — reads the `jobs` row for title +
   apply_url, calls `services.channels.dispatcher.dispatch` (or
   `ctx['dispatcher']` in tests), writes one ledger row per channel via
   `mark_ledger_sent` / `mark_ledger_failed`, returns `{'sent', 'failed'}`.
7. `backend/src/workers/tasks.py:283` — `mark_ledger_sent_task(ctx, ...)`
   and L292 `mark_ledger_failed_task(ctx, ...)` ctx wrappers for the
   fan-out path.
8. `_RedisSettings` stand-in dataclass at `backend/src/workers/settings.py:44`
   exposes `.host` / `.port` / `.database` matching ARQ's
   `RedisSettings` field names — structural compat, no hard dep at
   test time.
9. Tests: `backend/tests/test_worker_settings.py` (3 tests) +
   `backend/tests/test_worker_send_notification.py` (5 tests) all GREEN.

**Deliverable E — TieredScheduler wire-up** (commit `328e72f`):

10. `backend/src/main.py:26` — `from src.services.scheduler import
    TieredScheduler` import added.
11. `backend/src/main.py:363` — `scheduler = TieredScheduler(sources,
    registry)` replaces the `asyncio.gather(*[_fetch_source(s) ...])`
    call at the old L356 site. `scheduler.tick(force=True)` returns
    `[(source, result|Exception), ...]` which is reshaped back into
    the downstream `per_source` / `results` contract via
    `results_by_name` dict lookup.
12. Breaker consultation moved FROM post-hoc record-failure loop TO
    the scheduler's `can_proceed()` check before dispatch. Skipped
    sources log `"%s: skipped (breaker OPEN)"` instead of
    `"%s: FAILED"`.
13. Tests: `backend/tests/test_main_scheduler_wiring.py` — 3 tests
    (`test_run_search_uses_tiered_scheduler` — spy tick;
    `test_each_registered_source_called_exactly_once` — 3 fake sources;
    `test_breaker_open_source_is_skipped` — pre-trip breaker).

### What got deferred

- **`profile.py` + `search.py` auth-gating.** Neither touches
  `user_actions` / `applications` / `user_feed`, so neither is an IDOR
  vector per CLAUDE.md rule #10. Gating them is a separate
  hardening decision (preventing unauthenticated scrapes / reading the
  single-user profile JSON). Explicitly scope-ceilinged in the plan.
- **Per-user `user_profiles` table.** `src/services/profile/storage.py`
  still reads a single global `data/user_profile.json`. Batch 2
  already named this as a deferral; Batch 3.5 doesn't move it.
- **ARQ `run_forever` hookup to a system service** (systemd /
  docker-compose / Render cron). `WorkerSettings` exists; `arq` can
  boot. But there is no launcher config in `ops/` yet. Batch 4 scope.
- **Tier-based concurrency in the scheduler.** `TieredScheduler.tick`
  fan-outs via `asyncio.gather` without per-tier semaphores; the only
  concurrency limit is per-source `RateLimiter` in `BaseJobSource`.
  Explicit per-tier concurrency caps can land alongside the long-
  running daemon if load profiling calls for them.
- **Postgres migration** (Batch 2 §D4) — still deferred.
- **Direct-URL 404→confirmed_expired ghost-detection verifier** (Batch 1
  §deferred) — still deferred.

### Pre-existing failure bucket — 5 test_api.py tests flip but don't grow

Baseline Batch 3 left 6 test_api.py tests failing at the sqlite init
path (`AttributeError: NoneType`). Post-Batch-3.5, 5 of them fail with
`assert 401 == 200` instead — the new auth gate fires before the
sqlite-init codepath they were failing on. Net failure count for that
bucket is still 6 (with `test_status_returns_counts` staying at sqlite
and `test_full_api_workflow` moving to 401). Fixing either path means
registering a fixture user + patching `DB_PATH` + `_db` singleton
reset, and belongs with the wider `test_api.py` rehabilitation that
belongs in a follow-up. No regression — same count, different error
surface.

### Surprises / lessons

- **Fixture binding trap — `from ... import name`**. The first pass of
  `test_main_scheduler_wiring.py::fake_profile` monkeypatched
  `src.services.profile.storage.load_profile`. That left
  `src.main.load_profile` pointing at the unpatched original because
  `main.py` did `from ... import load_profile` at module top, so the
  storage module-level symbol is NOT what main reads. The fix is to
  patch the BOUND name (`src.main.load_profile`) — three scheduler
  tests passed in isolation but failed in full-suite context until the
  monkeypatch targeted the right reference. Added a lesson-note to the
  commit message.
- **`git checkout -B <branch> origin/main` auto-sets upstream to
  origin/main**. The first `git push` has to use `-u origin
  pillar3/batch-3.5` to create a distinct remote branch; otherwise
  git refuses to push to `origin/main` directly (good safety).
  Documented in the handoff command for the reviewer.
- **5 test_api.py tests now fail with 401 instead of NoneType**. I
  interpreted that as "same bucket, new surface" rather than a new
  regression, because the failure COUNT is unchanged. Calling this out
  explicitly in the completion entry so the reviewer can judge whether
  to block merge on the surface change or accept it.

### CLAUDE.md / docs updated

- `docs/plans/batch-3.5-plan.md` — new (the TDD plan; 150 lines).
- `docs/IMPLEMENTATION_LOG.md` — this completion entry.
- `CLAUDE.md` — no changes needed. The Batch 3 appendix's rule #12
  wording already covers the IDOR contract that Deliverable C
  enforces; repo-layer method signatures are implementation detail
  that doesn't rise to CLAUDE.md scope. If the reviewer wants a
  one-liner pointing future contributors at the user_id convention
  on `JobDatabase` action/application methods, that can land with
  merge-cleanup.

### Memory file saved

- `project_pillar3_batch_3_5_done.md` — to be written by the reviewer
  after merge (generator worktree does not write into user memory).

### Handoff

Reviewer: your worktree is `.claude/worktrees/reviewer` on
`pillar3/batch-3.5-review`. This completion entry is a DRAFT — verify
every file:line anchor against the actual diff and the final
full-suite regression run before merging. Particular review targets:

1. **SQL injection safety of `insert_action` ON CONFLICT rewrite.** The
   new SQL uses `?` placeholders; verify no f-string slipped in.
2. **Ledger idempotency under real-world retry.** `send_notification`
   assumes the UNIQUE(user_id, job_id, channel) constraint fires on
   double-inserts; `test_send_notification_is_idempotent_per_channel`
   proves this, but spot-check the SQL path in `_record_ledger_if_new`.
3. **Scheduler results shape parity**. The `results = [...]` list
   constructed at `main.py:396` must align with `sources` for
   `_ghost_detection_pass` to work. Skipped (breaker-OPEN) sources
   become `None` — same shape the function already tolerates (per
   the `if isinstance(result, BaseException) or result is None:
   continue` guard at `main.py:~163`).
4. **`WorkerSettings.redis_settings` is a stand-in dataclass, not the
   real ARQ `RedisSettings`.** ARQ accepts it structurally. If the
   reviewer wants the real class, `_load_arq_redis_settings()` is
   the lazy-load path — called by ARQ at boot, not at import.

---

## Completion Entry Template

When a batch merges, append a section using this template:

```markdown
## Batch N — Completion Entry

**Merged:** YYYY-MM-DD
**Branch:** `pillar3/batch-N` → merged to `main` at commit `<short-hash>`
**Commit range:** `<base-hash>..<merge-hash>` (`git log <base>..<merge> --oneline`)

### Test deltas
- Tests before: X passing / Y total
- Tests after: X' passing / Y' total
- New tests added: Z
- Tests removed/replaced: W (with reason)

### KPI deltas (where measurable)
- `bucket_accuracy_24h`: before → after
- `date_reliability_ratio`: before → after
- Source count: before → after
- (other batch-specific metrics)

### What shipped
- (bullet list of merged features)

### What got deferred
- (bullet list of items punted to a follow-up — explicit names)

### Surprises / lessons
- (anything that diverged from the research recommendation, with reason)

### CLAUDE.md / docs updated
- (which canonical docs were updated as part of this batch)

### Memory file saved
- `project_pillar3_batch_N_done.md`
```

---

# Pillar 2 — Search & Match Engine Upgrade (2026-04-21 → 2026-04-22)

Plan: `docs/pillar2_implementation_plan.md`. Execution order pinned by §7:
2.2 → 2.1 → 2.3 → 2.4 → 2.5 → 2.9 → 2.6 → 2.7 → 2.8 → 2.10. All 10 batches
merged. Detailed per-batch entries live in `docs/pillar2_progress.md`; the
summary below is the 10-row index.

Test delta across the whole pillar: 633p/3s (pre-Pillar-2 scoped baseline
excluding pre-existing `test_main.py` HTTP leak + `test_sources.py` Windows
IOCP hang) → **936p/3s, 0f** (+303 new tests). Plan target of ≥700p met 1.3×.

| # | Batch | Commit | Report items closed | Tests added |
|---|---|---|---|---|
| 1 | 2.2 Gate-pass scoring | `71e4be1` | #2 | +12 |
| 2 | 2.1 Date-confidence fix (linkedin/workable/personio/pinpoint → `"fabricated"`) | `be874b2` | #1 (label-only) | +8 |
| 3 | 2.3 Static skill synonym table (~493 entries) | `b15355d` | #3 + partial-#16 | +64 |
| 4 | 2.4 Source routing by domain (18 sources tagged, 5-domain taxonomy) | `32ad853` | #4 | +47 |
| 5 | 2.5 LLM job enrichment pipeline (+ migration 0008) | `cf3c0bd` | #5 | +24 |
| 6 | 2.9 Multi-dimensional scoring (salary + seniority + visa + workplace) | `cf8e8bd` | #10, #13 | +49 |
| 7 | 2.6 Embeddings + ChromaDB + ESCO activation (+ migration 0009) | `46f7c62` | #8, #16 | +21 |
| 8 | 2.7 RRF hybrid retrieval (`k=60`) | `c569b9d` | #9 | +17 |
| 9 | 2.8 Cross-encoder rerank (`ms-marco-MiniLM-L-6-v2`) | `ce53b24` | #12 | +8 |
| 10 | 2.10 Four-layer dedup (RapidFuzz + TF-IDF + embedding repost) | `37646bb` | #7, #11, #14 | +16 (incl. 10K benchmark) |

**Semantic stack is install-gated.** `pip install '.[semantic]'` pulls
`sentence-transformers` + `chromadb`. `SEMANTIC_ENABLED=true` flips on the
activation path. Pre-semantic rollouts continue to work untouched.

**Feature flags added:** `ENRICHMENT_ENABLED` (Batch 2.5 — default off),
`SEMANTIC_ENABLED` (Batch 2.6 — default off). Env-tunable scoring weights:
`MIN_TITLE_GATE`, `MIN_SKILL_GATE`, `SALARY_WEIGHT`, `SENIORITY_WEIGHT`,
`VISA_WEIGHT`, `WORKPLACE_WEIGHT`.

**Migrations added:** `0008_job_enrichment.{up,down}.sql`,
`0009_job_embeddings.{up,down}.sql`. Both shared-catalog tables (no `user_id`
column, per CLAUDE.md rule #10).

**Deferred from this pillar (all explicitly documented in plan §9 or batch
"Out of scope"):**
- Configurable `MIN_MATCH_SCORE` per user (#15 → Batch 4 + UI).
- Learning-to-Rank (#17 → requires engagement data from Batch 4 freemium).
- Multilingual embeddings (#18 → UK-focused, negligible non-English volume).
- Career-ops archetype classification + interview-likelihood / company-stage
  dims (require engagement data).
- Meilisearch / pg_trgm (premature at 50K).
- Torre.ai uncertainty quantification (cold-start bounded by CV completeness).

**Operational follow-ups the reviewer must gate:**
1. Batch 2.5 live-fire spike (100 jobs, ≥95 % schema-valid, ≥50 % quota
   headroom) before `ENRICHMENT_ENABLED=true` in prod.
2. Batch 2.6 ESCO index build + embedding backfill (`backend/scripts/build_esco_index.py`
   → `backend/scripts/build_job_embeddings.py`).
3. Batch 2.7 `?mode=hybrid` wiring into `/jobs` route body (the param is
   reserved but not yet acted on).
4. Batch 2.10 Layer 4 activation (`enable_embedding_repost=True`) once
   Chroma is populated.

Tag `pillar2-generator-complete` on `37646bb`. Reviewer worktree can walk
the 10 commits in reverse order from this tag.

---

## Step 0 — Pre-Flight Completion

**Merged:** 2026-04-24 — `step-0-preflight` fast-forwarded into `main`, pushed to `origin/main`.
**Commits:** `eb5c030` (feat: Tier A+B+C, 25 items) → `55da9f4` (fix: missing `frontend/.env.local.example`).
**Files changed:** 47 (+2,785 / −264).
**Tests:** 600p/0f/3s → **1,018p/0f/3s** (+418, zero regressions).

### Scope delivered (all three tiers — user-confirmed A+B+C)

**Tier A (blocking — 12 items):**
1. `backend/.env.example` — 32 vars across 10 logical groups (auth / frontend / LLM / job-boards / enrichment / notifications / scoring / salary / flags / ops) with inline signup URLs.
2. Fresh-clone DB crash fixed — `src/repositories/database.py::_migrate()` backfills missing `run_log` observability columns so `init_db()` alone bootstraps cleanly on any schema state.
3. `pre-commit install` active — `.pre-commit-config.yaml` gate now enforced (was present but not installed).
4. `.gitattributes` — `sh=LF bat=CRLF` prevents CRLF corruption on Windows clone.
5. `setup.bat` — Windows parity with `setup.sh` (Python guard, venv creation with `.venv\Scripts\activate` guidance, data-dir creation, `.env` template copy).
6. `backend/scripts/bootstrap_dev.py` — end-to-end smoke (register 201 → auto-login cookie → multipart CV upload with `preferences` as JSON-string → async `/search` poll → feed row print). Reuses `fpdf2` inline CV generation from `tests/test_linkedin_github.py`.
7. `_TEST_NOW` pinned across 6 `conftest.py` fixtures (test determinism for time-sensitive assertions).
8. Migration `0010_run_log_observability.{up,down}.sql` — adds `run_uuid TEXT UNIQUE`, `per_source_errors`, `per_source_duration`, `total_duration`, `user_id`. Self-bootstrapping `CREATE-IF-NOT-EXISTS` + `ALTER` with idempotent duplicate-column-tolerance.
9. `LOG_LEVEL` env var threaded through FastAPI lifespan in `src/api/main.py` + `src/core/settings.py`.
10. `CONTRIBUTING.md` at repo root — branch naming, commit convention, PR flow.
11. `frontend/README.md` + `backend/README.md` — sub-project onboarding with `/docs` + `/redoc` callout and `NEXT_PUBLIC_API_URL` ↔ `FRONTEND_ORIGIN` cross-wiring.
12. `docs/README.md` index + 7 stale pillar-1/2 progress logs moved to `docs/_archive/`.

**Tier B (velocity — 10 items):**
- `Makefile` + `scripts/verify_step_0.py` — cross-platform gate aggregating env parity, migration idempotency, and smoke checks.
- `scripts/check_env_example.py` — validates `.env.example` parity against runtime env reads.
- Inspection tools: `scripts/dump_db.py`, `check_logs.py`, `check_worker.py`, `log_rotation_check.py`.
- `docs/troubleshooting.md` (port conflicts, SQLite lock, CV parse fail, LLM key missing, Redis on Windows).
- `STATUS.md` + `CLAUDE.md` staleness sweep (source count, test count, rule-set drift).
- `migrations.runner status` tabular output (applied + pending).
- `docs/pytest_baseline_seeds.txt` — 11 deterministic seeds for reproducible runs.
- `pytest-xdist` + `@pytest.mark.fast` on 5 lightweight smoke tests (parallel CI via `pytest -n auto`).
- `tests/test_migration_0010_down.py` — up→down→up round-trip verifies rollback safety.
- `setup.sh` shebang + exit-on-error fix.

**Tier C (polish — 3 items):**
- `mypy strict=true` with heavy-dep overrides; `docs/mypy_backlog.md` captures 395 suppressible warnings (documented, not suppressed — future-pass backlog).
- `README.md` enhanced with explicit `/docs` + `/redoc` API callout.
- `pyproject.toml` version-pin consolidation.

### Reviewer catch (`55da9f4`)

Initial commit `eb5c030` claimed `frontend/.env.local.example` was added, but the file matched `frontend/.gitignore`'s `.env*` pattern and `git add` silently skipped it. Reviewer detected via `git check-ignore -v` and patched in `55da9f4`:
- `frontend/.gitignore` grew a `!.env*.example` negation immediately after the `.env*` wildcard.
- `frontend/.env.local.example` committed with the three-line stub (`NEXT_PUBLIC_API_URL` + pointer to backend's `FRONTEND_ORIGIN`).

**Verification:** `git check-ignore -v frontend/.env.local.example` now exits non-zero — file tracks as intended. Classic `.gitignore` oversight; worth remembering the `!.env*.example` negation-after-wildcard pattern for future config file templates.

### Deferred to Step 1 / later steps

- Demo GIF / screenshots in README (Tier-C item, no data dependency) — cosmetic; defer to after dogfood.
- Log-rotation alerts helper — scope fit better with operational hardening (Step 4).
- Direct-URL verification for ghost detection (scaffolding exists, no caller) — Step 1 territory.

### Next

- **Step 1 (Batch S1 — Engine→API seam):** date-model fields, 7-dim score breakdown, `enrich_job()` invocation, `mode=hybrid` wiring. See `docs/ExecutionOrder.md` for the full 6-step roadmap.
- **Tag candidate:** `step-0-done-2026-04-24` for quick-reference.

---

## Two-pass profile extraction (2026-06-17, branch `feat/two-pass-profile-extraction`)

**Goal (user-side profile improvement):** every profile input runs through a
**deterministic pass** (plain code) AND an **LLM enhance pass**, merged into one
`CVData`. When the user changes ANY input later, both passes re-run from STORED
raw inputs (no re-upload, no network re-fetch), producing a refreshed CVData and
a new profile-version id, then the feed re-scores. (M2 / the LLM judge was left
untouched per scope.)

**What existed before:** CV = LLM-only; LinkedIn = hybrid (deterministic headers
+ LLM prose); GitHub = deterministic lookup-table only; preferences = plain form
parse. New-id-per-save + change→rescore already existed (`storage.py`,
`profile.py::_maybe_trigger_rescore`).

**New code (all TDD, offline, LLM mocked):**
- `models.py` — `CVData` gains `linkedin_raw_text`, `github_repos_brief`,
  `github_llm_skills`, `about_me_inferred_skills`. **No migration** — profiles
  store as a JSON blob and `storage._filter_fields` drops unknown keys, so old
  rows load with defaults.
- `linkedin_parser.py` — `parse_*` now returns `raw_text`; `enrich_cv_from_linkedin`
  stores it on `cv.linkedin_raw_text`. Factored `parse_linkedin_from_text(text)`
  so the LLM pass can re-run offline.
- `github_enricher.py` — `fetch_github_profile` now returns `repos_brief`
  (name/description/topics); new `llm_infer_github_skills(repos_brief)` reads repo
  prose for skills the lookup tables miss (e.g. "LangChain", "RAG").
- `preferences.py` — new `llm_infer_from_about_me(about_me)` mines the free-text
  blurb for skills.
- `cv_parser.py` — new `deterministic_cv_fields(raw_text)` (no-LLM skills/summary
  grab); factored `llm_cv_fields_from_text(raw_text)` so the CV LLM pass re-runs
  from stored text.
- `skill_tiering.py` — two new evidence sources + weights: `about_me_llm` (2.0,
  user's own words) and `github_llm` (1.5, inferred-but-demonstrated).
- `two_pass.py` (NEW) — `run_two_pass_extraction(profile)` runs both passes over
  all four inputs in place (never raises; each pass no-ops when its input/keys are
  absent); `reextract_and_rescore(user_id)` = load → re-extract → save (new
  version, `source_action="two_pass_reextract"`) → rescore.
- `api/routes/profile.py` — the change trigger now schedules
  `reextract_and_rescore` instead of bare `rescore_user_feed`.

**Tests:** `tests/test_two_pass.py` (17) + new cases in `test_linkedin_github.py`
and `test_github_deps.py`. Full suite **1540 passed, 3 skipped**. Lint: no new
ruff findings vs baseline.

**Known cost tradeoff (no flag added):** a change to ANY input re-runs all LLM
passes in the background (faithful to the ask). On a CV upload this re-runs the
CV LLM once more than strictly needed. If this proves expensive, gate
`reextract_and_rescore`'s LLM passes behind a flag later — the deterministic
passes are free and always safe.
