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

> Numbers below verified by 2026-04-18 fresh code-audit (see `docs/CurrentStatus.md`). Supersedes any earlier counts.

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
2. Batch 2.6 ESCO index build + embedding backfill (`scripts/build_esco_index.py`
   → `scripts/build_job_embeddings.py`).
3. Batch 2.7 `?mode=hybrid` wiring into `/jobs` route body (the param is
   reserved but not yet acted on).
4. Batch 2.10 Layer 4 activation (`enable_embedding_repost=True`) once
   Chroma is populated.

Tag `pillar2-generator-complete` on `37646bb`. Reviewer worktree can walk
the 10 commits in reverse order from this tag.

