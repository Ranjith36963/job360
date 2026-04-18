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

**Status:** Blocked on Batch 1

**Reference:** `docs/research/pillar_3_batch_2.md`

**Scope:** Auth + multi-tenant schema, `user_feed` SSOT table + FeedService, ARQ worker + Apprise notifications, 99% pre-filter cascade, channel config UI.

**Branch:** `pillar3/batch-2`

**Pre-flight:** REQUIRES `superpowers:brainstorming` skill before plan — too many irreversible design choices (ARQ vs Celery, Apprise vs Novu, polling vs SSE, when to migrate auth).

_Completion entry will be appended here when merged._

---

## Batch 3 — Tiered Polling + Source Expansion

**Status:** Blocked on Batch 2

**Reference:** `docs/research/pillar_3_batch_3.md`

**Scope:** Tiered polling scheduler (60s for ATS / 5min for Reed / 15min for Workday / etc.), conditional fetching layer, 5 new sources (Teaching Vacancies, GOV.UK Apprenticeships, NHS XML, Rippling, Comeet), slug expansion 104 → 500+, drop YC Companies + Nomis + FindAJob, circuit breakers replacing "newly_empty".

**Branch:** `pillar3/batch-3`

**Pre-flight:** Update `len(SOURCE_REGISTRY) == N` assertion in `test_cli.py` per CLAUDE.md rule #8.

_Completion entry will be appended here when merged._

---

## Batch 4 — Launch Readiness

**Status:** Blocked on Batch 3

**Reference:** `docs/research/pillar_3_batch_4.md`

**Scope:** Scope down to top 10–15 sources for MVP, freemium metering, pricing page, ICO registration (£40), privacy notice + LIA, ASA-compliant marketing copy, Amazon SES setup.

**Branch:** `pillar3/batch-4`

**Pre-flight:** Update PRD's "all UK white-collar domains" claim — currently fails CAP Code rule 3.7 substantiation.

_Completion entry will be appended here when merged._

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
