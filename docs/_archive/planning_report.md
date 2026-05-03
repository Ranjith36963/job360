# Job360 — Pillar 2/3 Quality Refactor Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute task-by-task. Checkbox (`- [ ]`) syntax.

**Goal:** Make time-bucketing, recency scoring, and the frontend's posted-date display reflect **when employers posted the job** — not scraper time — and add disappearance tracking so stale jobs stop polluting results.

**Architecture:** Split the overloaded `date_found` field into two semantically distinct fields: `date_posted` (employer, nullable) and `discovered_at` (scraper). Every consumer reads `date_posted` first with explicit unknown handling. Disappearance tracked via per-source `run_hash` snapshot diff.

**Tech stack:** Python 3.9+, aiosqlite, aiohttp, pytest + aioresponses. No new runtime deps for Phases 0-4.

---

## Table of Contents

1. [Ground Truth (Real Data State)](#1-ground-truth-real-data-state)
2. [Critical Bugs Identified in Audit](#2-critical-bugs-identified-in-audit)
3. [Architecture Decisions](#3-architecture-decisions)
4. [File Inventory](#4-file-inventory)
5. [Phase Index](#5-phase-index)
6. [Phases 0-4 (Critical Path)](#6-phases-0-4-critical-path)
7. [Phases 5-8 (Optional)](#7-phases-5-8-optional)
8. [Freshness Target & Infrastructure](#8-freshness-target--infrastructure)
9. [Open-Source Reference (Tier-Ranked)](#9-open-source-reference-tier-ranked)
10. [Out of Scope](#10-out-of-scope)
11. [Execution Handoff](#11-execution-handoff)

---

## 1. Ground Truth (Real Data State)

Verified against `backend/data/jobs.db` on 2026-04-11.

| Metric | Value |
|---|---|
| Jobs in DB | 86 |
| Score AVG / MIN / MAX | 34.7 / 30 / 63 |
| Jobs scoring ≥ 50 | 4 (4.7% high-confidence) |
| **`date_found` == `first_seen` (fallback fired)** | **66 / 86 = 77%** |
| Actual test count (`pytest --collect-only`) | **412** (CLAUDE.md claims vary 397/409) |
| `SOURCE_REGISTRY` entries | 48 (47 unique — indeed/glassdoor share `JobSpySource`) |
| Last run | 2026-04-09 21:12 UTC — 3446 found, 86 new, 47 sources queried |

**Per-source fallback dominance (live DB):**
```
devitjobs:      13/13 same-day (100%)  ← bug manifest
eightykhours:   11/11 same-day (100%)  ← bug manifest
linkedin:        9/9  same-day (100%)  ← bug manifest
indeed:         15/14 same-day (93%)
greenhouse:      4/3  same-day (75%)   ← expected (updated_at is stale)
ashby:           9/1  same-day (11%)   ← healthy real-date coverage
adzuna:         12/2  same-day (17%)   ← healthy
```

**Interpretation:** The bug is not theoretical. 77% of current DB carries fallback-inflated dates. DevITjobs, EightyKHours, and LinkedIn are 100% fallback — every job from those sources currently claims "posted today."

---

## 2. Critical Bugs Identified in Audit

### 2.1 The root bug

`backend/src/utils/time_buckets.py:51-63` `get_job_age_hours()` reads `date_found` first. But `date_found` is set by sources via the pattern `item.get("X") or datetime.now(...)` — a missing API field silently becomes "posted right now." All 46 sources have this fallback; 13 sources use `now()` unconditionally because their upstream has no date field at all.

### 2.2 Original-plan bugs caught by Round-2 audit

| # | Bug | Fix lives in |
|---|---|---|
| **H1** | Original plan referenced `DatabaseManager.connect()` — **class is actually `JobDatabase` with `init_db()`**. An idempotent `_migrate()` already exists at `backend/src/storage/database.py:75-97` with a column-name allowlist ready for extension. | Phase 0.1 |
| **H2** | Upsert `description = excluded.description` silently **clobbers** non-empty Reed descriptions with empty LinkedIn ones when dedup winner flips between runs. `apply_url` flip-flop breaks bookmarks. | Phase 3.2 corrected SQL |
| **H3** | Frontend breaks silently. `frontend/src/lib/types.ts:15` is `date_found: string` (non-null). Three consumers at `frontend/src/components/jobs/JobCard.tsx:100`, `frontend/src/app/dashboard/page.tsx:38`, `frontend/src/app/jobs/[id]/page.tsx:224` read `date_found` with no fallback. Dropping it from the API crashes the frontend. | Phase 2.7 |
| **H5** | `mark_disappeared` per-source fails when dedup winner switches source between runs — row's `source` field moves from Reed→LinkedIn, so next run's `WHERE source="reed"` misses it. | Phase 3.3 (corrected SQL) |
| **H6** | Phase 2.2 sets `_recency_score(None) → 0`, causing historical rows with `date_posted=NULL` to deflate 0-10 recency points overnight. **Estimated 15-30% of qualifying jobs vanish below `MIN_MATCH_SCORE=30` on deploy.** | Phase 2.0 — fallback to `first_seen` with -2 confidence penalty |
| **NHS** | `backend/src/sources/feeds/nhs_jobs.py:66` stores `<closingDate>` (a **future** deadline) as `date_found`. `_recency_score` computes `days_old = (now - future).days = negative`. Path `if days_old <= 1` matches → NHS jobs always score **full 10 recency points**. Silent bias for months. | Phase 4.2 + one-line sanity clamp `if days_old < 0: return 0` |

### 2.3 Missed consumers (file inventory additions)

These files/lines were not in the original plan's touch list:

| File:line | Role | Phase |
|---|---|---|
| `backend/src/api/routes/jobs.py:17-39` | **Duplicate `_compute_bucket`** — independent bucketing implementation reading `date_found` | 2.8 — consolidate into `time_buckets.get_job_age_hours` |
| `backend/src/api/routes/jobs.py:58,63,74,94,127,148` | `JobResponse.date_found`, CSV export, hours filter, bucket filter | 2.6 / 2.8 |
| `backend/src/storage/csv_export.py:10,41` | `HEADERS` list + row writer — **public contract** (user spreadsheets) | 2.8 |
| `backend/src/notifications/email_notify.py:28` | Builds `{"date_found": j.date_found}` for subject line | 2.8 |
| `backend/src/notifications/report_generator.py:26,76,122` | `_jobs_to_dicts` + 2× `Job(date_found=...)` reconstructions | 2.8 |
| `backend/src/main.py:137,442,463` | `_format_date()` + `_print_bucketed_summary` | 2 |
| `backend/src/filters/skill_matcher.py:236` | **Module-level `score_job()`** also reads `job.date_found` (plan caught only `JobScorer.score` at 299) | 2.2 |
| `backend/tests/test_api.py:27,36,97,102` | Hardcodes `48` source count | 1.12 |
| `backend/tests/test_cli_view.py:25` | Hand-written `CREATE TABLE` DDL with `date_found TEXT NOT NULL` | 0/2 |
| `backend/tests/conftest.py:24,37,48,65,78,91` | All 6 shared fixtures set `date_found=now()` | 0.3 (new) |
| `frontend/src/lib/types.ts:15` + 3 components | TypeScript contract + display sites | 2.7 (new) |

### 2.4 Verified correct in original plan (no change)

- Workday already uses CXS endpoint — `backend/src/sources/ats/workday.py:52` hits `POST /wday/cxs/{tenant}/{site}/jobs`, 15 dict-format companies in `companies.py:58-74`, 2 passing tests.
- ChromaDB / sentence-transformers **not** in `backend/pyproject.toml` — ghost detection is greenfield.
- The interactive UI is the Next.js frontend at `frontend/src/app/`, consumed via the FastAPI backend. No Python-rendered UI module exists.
- `nomis` + `yc_companies` are not job sources (market stats / company directory) — removal stands.
- `is_new: bool = True` on `Job` dataclass is dead code (grep confirms zero runtime readers, one test assertion). Delete in Phase 0.4.

---

## 3. Architecture Decisions

1. **Two fields, not one.** Add nullable `date_posted: Optional[str]` and `discovered_at: Optional[str]` alongside existing `date_found`. Keep `date_found` for backwards compat indefinitely — dropping it is a hypothetical Phase 9 gated on frontend migration.

2. **`None` is first-class.** Sources that can't determine a real posting date set `date_posted=None`. Never substitute `datetime.now()`. Bucketing and scoring handle `None` via explicit fallback to `first_seen` with a confidence penalty.

3. **Fallback chain for recency:** `date_posted` → `first_seen` (−2 pts confidence penalty) → 0.

4. **Stable source attribution in upsert.** On conflict, `source` and `apply_url` stay with the first-seen value (bookmarks don't break). `description` keeps whichever is longer. `salary_min/max` use COALESCE. `match_score` replaces with latest (current profile scoring wins).

5. **Per-run `run_hash` drives disappearance detection** via `INSERT ... ON CONFLICT DO UPDATE`. The naive per-source `mark_disappeared` is insufficient (H5); corrected SQL uses a subquery to check "was this job's key seen by *any* source in the current run."

6. **Idempotent migrations reuse existing scaffolding.** Extend `backend/src/storage/database.py:_migrate()` line 82-85 `migrations` list — do not invent a parallel `_migrate_schema()`.

7. **Backfill strategy: recency fallback, not column backfill.** Historical rows keep `date_posted=NULL`; the scorer's `first_seen` fallback (−2 pts) preserves continuity without lying about Greenhouse job ages.

---

## 4. File Inventory

### Modified

**Core schema & scoring:** `backend/src/models.py` · `backend/src/storage/database.py` · `backend/src/storage/csv_export.py` · `backend/src/filters/skill_matcher.py` · `backend/src/utils/time_buckets.py` · `backend/src/main.py`

**Consumers:** `backend/src/cli_view.py` · `backend/src/api/models.py` · `backend/src/api/routes/jobs.py` · `backend/src/notifications/email_notify.py` · `backend/src/notifications/report_generator.py`

**All 46 sources:** `backend/src/sources/**/*.py` (grouped into `apis_keyed/`, `apis_free/`, `ats/`, `feeds/`, `scrapers/`, `other/` — see Phase 1 batching table)

**Tests:** `backend/tests/conftest.py` · `backend/tests/test_database.py` · `backend/tests/test_scorer.py` · `backend/tests/test_time_buckets.py` · `backend/tests/test_deduplicator.py` · `backend/tests/test_sources.py` · `backend/tests/test_api.py` · `backend/tests/test_cli.py` · `backend/tests/test_cli_view.py` · `backend/tests/test_csv_export.py` · `backend/tests/test_main.py` · `backend/tests/test_notifications.py` · `backend/tests/test_reports.py` · `backend/tests/test_models.py`

**Frontend (Phase 2.7):** `frontend/src/lib/types.ts` · `frontend/src/components/jobs/JobCard.tsx` · `frontend/src/app/jobs/[id]/page.tsx` · `frontend/src/app/dashboard/page.tsx`

### Created

- `backend/src/utils/date_parsing.py` — shared `parse_iso_utc`, `parse_ms_epoch_utc`, `parse_relative_date_utc`, `now_utc_iso`, `to_iso_or_none`
- `frontend/src/lib/jobDates.ts` — `displayedPostedDate(job)` + `isApproximateDate(job)` helpers
- `backend/tests/test_date_parsing.py`
- `backend/tests/test_disappearance.py`

### Deleted

- `backend/src/sources/other/nomis.py` + `backend/src/sources/apis_free/yc_companies.py` (not job sources)

---

## 5. Phase Index

| Phase | Scope | Risk | Depends on |
|---|---|---|---|
| **0** | DB schema migration + Job dataclass + conftest fixtures + delete `is_new` | Low | — |
| **1** | 46-source `date_posted` refactor + 3 batched commits + remove nomis/yc | Medium | 0 |
| **2** | Time bucketing + recency scoring + backfill policy + CLI/API/CSV/reports + frontend | Medium | 1 |
| **3** | Run hash + corrected upsert + corrected mark_disappeared + purge + notifications | Medium | 0 |
| **4** | Greenhouse `updated_at` + NHS future-date fix | Low | 1 |
| **5** | Optional ghost detection (SQLite age-based, no ML) | High | 3 |
| **6** | Optional BambooHR source | Medium | 1 |
| **7** | Optional HiringCafe source | Medium | — |
| **8** | Optional company slug expansion 104 → 500+ | Low | — |

**Critical path:** 0 → 1 → 2 → 3 → 4. Phases 5-8 are parallelisable after core.

---

## 6. Phases 0-4 (Critical Path)

Each task uses a compressed TDD cycle: **(a)** write failing test with concrete assertion, **(b)** run to confirm FAIL, **(c)** implement minimal code, **(d)** run to confirm PASS, **(e)** run full source test suite to confirm no regressions, **(f)** commit with conventional message. Each task is one commit unless noted.

### Phase 0 — Schema migration & prep

**Task 0.1 — Add 5 columns via existing `_migrate()`**

Extend `backend/src/storage/database.py:82-85` `migrations` list to:
```python
migrations = [
    ("date_posted",    "TEXT"),
    ("discovered_at",  "TEXT"),
    ("last_seen_at",   "TEXT"),
    ("run_hash",       "TEXT"),
    ("disappeared_at", "TEXT"),
]
```
Also add matching columns to the inline `CREATE TABLE` at `database.py:23-42` for fresh DBs. Add indexes on `date_posted`, `last_seen_at`, `run_hash`. Test in `backend/tests/test_database.py` following existing `asyncio.run(...)` pattern: fresh DB has new columns, legacy DB auto-migrates, second open is idempotent.

Commit: `feat(db): migrate to date_posted/last_seen_at/run_hash schema`

**Task 0.2 — Extend `Job` dataclass**

`backend/src/models.py` — add `date_posted: Optional[str] = None` and `discovered_at: Optional[str] = None`. Test that defaults are `None`. Run `backend/tests/test_models.py`.

Commit: `feat(models): add date_posted and discovered_at fields`

**Task 0.3 — Extend conftest fixtures**

`backend/tests/conftest.py:7-93` — all 6 fixtures get `date_posted=datetime.now(timezone.utc).isoformat()` alongside existing `date_found`. **Why:** prevents Phase 2.2 cascade where `_recency_score` reads `date_posted` and every fixture-consuming test drops 10 points.

Commit: `chore(tests): backfill date_posted into shared fixtures`

**Task 0.4 — Delete vestigial `is_new`**

Grep confirms `Job.is_new` has zero runtime readers, one test assertion. Delete `backend/src/models.py:30` and the assertion in `backend/tests/test_models.py:32`.

Commit: `refactor(models): remove vestigial is_new flag`

### Phase 1 — Source `date_posted` refactor

**Task 1.1 — Shared date parsing utilities**

Create `backend/src/utils/date_parsing.py` with `parse_iso_utc`, `parse_ms_epoch_utc`, `parse_sec_epoch_utc`, `parse_relative_date_utc`, `now_utc_iso`, `to_iso_or_none`. **All functions return None on missing/unparseable input — never substitute `now()`.** Write `backend/tests/test_date_parsing.py` covering ISO with `Z` suffix, with `+01:00` offset, null/empty/garbage, ms epoch, second epoch, relative phrases ("2 days ago", "yesterday", "just posted"), future-date handling.

Commit: `feat(utils): shared UTC date parsing helpers`

**Task 1.2 — `_discovered_at` helper on `BaseJobSource`**

Add a single method returning `now_utc_iso()`. Do not touch `BaseJobSource` constructor or existing `_get_json`/`_get_text` methods (CLAUDE.md Rule 2).

Commit: `feat(sources): add _discovered_at helper`

**Task 1.3 — Lever (reference implementation)**

`backend/src/sources/ats/lever.py:38-42`. Replace inline `fromtimestamp(created_at/1000)` with `parse_ms_epoch_utc`. Set `date_posted = to_iso_or_none(parse_ms_epoch_utc(item.get("createdAt")))`, `discovered_at = now_utc_iso()`, `date_found = date_posted or discovered_at`. Test the None case explicitly.

Commit: `fix(sources/lever): populate date_posted from createdAt; None on missing`

**Task 1.4-1.9 — High-touch sources (individual commits)**

| File | Source field | Parser |
|---|---|---|
| `backend/src/sources/ats/ashby.py:35` | `publishedAt` / `updatedAt` | `parse_iso_utc` |
| `backend/src/sources/apis_keyed/reed.py:50` | `date` / `datePosted` | `parse_iso_utc` |
| `backend/src/sources/apis_keyed/adzuna.py:49` | `created` | `parse_iso_utc` |
| `backend/src/sources/apis_free/hn_jobs.py:64` | Firebase epoch | `parse_sec_epoch_utc` |
| `backend/src/sources/scrapers/linkedin.py:67` | **HTML `<time datetime="...">`** attribute (JobSpy pattern) | `parse_iso_utc` |
| `backend/src/sources/ats/workday.py:95` | "Posted X Days Ago" text | `parse_relative_date_utc` |

Same TDD cycle for each. One commit per source.

**Task 1.10 — Batch A: 20 ISO-date sources (ONE commit)**

One parametrised pytest covering all 20, one pass editing all files. Sources: `arbeitnow`, `aijobs`, `careerjet`, `devitjobs`, `eightykhours`, `findwork`, `hackernews`, `himalayas`, `indeed`, `jobicy`, `jooble`, `jsearch`, `landingjobs`, `nofluffjobs`, `recruitee`, `remoteok`, `remotive`, `smartrecruiters`, `themuse`, `google_jobs`.

Mechanical change: `item.get("X") or datetime.now(...)` → `parse_iso_utc(item.get("X"))` → `to_iso_or_none` → None on missing.

Commit: `fix(sources): stop substituting now() for missing ISO dates across 20 sources`

**Task 1.11 — Batch B: 6 RSS `<pubDate>` sources (ONE commit)**

Sources: `biospace`, `jobs_ac_uk`, `realworkfromanywhere`, `uni_jobs`, `weworkremotely`, `workanywhere`. Replace ad-hoc `strptime` chains with `parse_iso_utc` (handles RFC 822 after trivial normalisation) or keep strptime wrapped to return None on failure.

Commit: `fix(sources): RSS sources return None on unparseable pubDate`

**Task 1.12 — Batch C: 10 no-date sources (ONE commit)**

Sources: `aijobs_ai`, `aijobs_global`, `bcs_jobs`, `climatebase`, `findajob`, `jobtensor`, `personio`, `pinpoint`, `successfactors`, `workable`. All set `date_posted=None`, `discovered_at=now_utc_iso()`, `date_found=now_utc_iso()` unconditionally.

Commit: `fix(sources): no-date sources leave date_posted=None, record discovered_at`

**Task 1.12.2 — Remove `nomis` and `yc_companies`**

Delete source files. Remove from `SOURCE_REGISTRY` + `_build_sources()` + `RATE_LIMITS`. Bump `backend/tests/test_cli.py` assertion from 48 to 46. **Also bump `backend/tests/test_api.py:27,36,97,102` hardcoded 48** (plan originally missed these).

Commit: `refactor(sources): remove nomis and yc_companies (not job sources)`

**Task 1.13 — Per-source date coverage observability**

Compute `date_coverage[source] = sum(1 for j in jobs if j.date_posted) / len(jobs)` at end of `run_search()`. Persist into `run_log.per_source` JSON alongside existing counts. Test in `test_main.py`: mock two sources with different fill rates, assert the coverage is logged.

Commit: `feat(observability): log per-source date_posted fill rate`

### Phase 2 — Bucketing/Scoring cutover

**Task 2.0 — Backfill policy (BLOCKS 2.2)**

Update `backend/src/filters/skill_matcher.py:_recency_score` signature to `(date_posted: Optional[str], first_seen: Optional[str] = None) -> int`. When `date_posted is None`, fall back to `first_seen` with a **−2 point confidence penalty** across all tiers. Add NHS sanity clamp: `if days_old < 0: return 0`.

```python
def _recency_score(date_posted: Optional[str], first_seen: Optional[str] = None) -> int:
    source_str = date_posted
    penalty = 0
    if not source_str:
        source_str, penalty = first_seen, 2
    if not source_str:
        return 0
    try:
        posted = datetime.fromisoformat(source_str)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - posted).days
    except (ValueError, TypeError):
        return 0
    if days_old < 0:           return 0   # NHS future-date clamp
    if days_old <= 1:          return max(RECENCY_WEIGHT - penalty, 0)
    if days_old <= 3:          return max(8 - penalty, 0)
    if days_old <= 5:          return max(6 - penalty, 0)
    if days_old <= 7:          return max(4 - penalty, 0)
    return 0
```

Update **both** callers: module-level `score_job()` at `skill_matcher.py:236` AND `JobScorer.score()` at line 299. Pass `job.date_posted`.

Commit: `feat(scoring): recency fallback to first_seen with -2 confidence penalty`

**Task 2.1 — Update `get_job_age_hours` to prefer `date_posted`**

`backend/src/utils/time_buckets.py:51-63`. Signature becomes `(date_posted, first_seen, date_found=None)` — `date_found` accepted for backwards-compat but ignored. Update `bucket_jobs()` to pass `date_posted`. Update all 33 tests in `backend/tests/test_time_buckets.py` factory.

Commit: `refactor(time_buckets): prefer date_posted over first_seen`

**Task 2.3 — Persist new fields in `insert_job`**

`backend/src/storage/database.py:114-132`. Extend INSERT column list + value tuple. Test round-trip: insert job with `date_posted` + `discovered_at`, SELECT confirms values stored.

Commit: `feat(db): persist date_posted and discovered_at on insert`

**Task 2.4 — CLI view**

`backend/src/cli_view.py:36,82,122`. Change ORDER BY to `COALESCE(date_posted, first_seen) DESC`. `format_relative_time` and `parse_date_safe` all prefer `date_posted`. Add handling for the "Posted date unknown" bucket.

Commit: `refactor(cli_view): prefer date_posted for bucketing`

(The former "Task 2.4 — Dashboard" covered a Python-side UI module that no longer exists in the codebase; Task 2.4 has been folded into the CLI-view task.)

**Task 2.6 — API response model + routes**

`backend/src/api/models.py`: add `date_posted: Optional[str]`, `discovered_at: Optional[str]` to `JobResponse`. **Keep `date_found` for frontend compat.**

`backend/src/api/routes/jobs.py:17-39` — consolidate `_compute_bucket` into a thin wrapper around `time_buckets.get_job_age_hours`. Update `_row_to_job_response` (line 58) to pass `date_posted`. Update hours filter (line 127) and bucket filter (line 148) to use `date_posted` with `first_seen` fallback.

Commit: `feat(api): expose date_posted; consolidate bucket logic`

**Task 2.7 — Frontend contract update**

Create `frontend/src/lib/jobDates.ts`:
```typescript
import type { JobResponse } from './types';
export const displayedPostedDate = (j: JobResponse): string => j.date_posted ?? j.date_found;
export const isApproximateDate  = (j: JobResponse): boolean => !j.date_posted;
```

Update `frontend/src/lib/types.ts:15`:
```typescript
date_found: string;              // Legacy — prefer displayedPostedDate(job)
date_posted: string | null;
discovered_at: string | null;
```

Update the three consumers:
- `frontend/src/components/jobs/JobCard.tsx:100` — `timeAgo(displayedPostedDate(job))` with optional `~` prefix from `isApproximateDate`
- `frontend/src/app/dashboard/page.tsx:38` — `hoursSince(displayedPostedDate(j))`
- `frontend/src/app/jobs/[id]/page.tsx:224` — `relativeDate(displayedPostedDate(job))`

Commit: `feat(frontend): prefer date_posted via displayedPostedDate helper`

**Task 2.8 — CSV exports + reports**

`backend/src/storage/csv_export.py:10,41` — add `date_posted`, `discovered_at` columns after existing `date_found`. Keep `date_found` for downstream spreadsheet compat (public contract).

`backend/src/notifications/email_notify.py:28` + `backend/src/notifications/report_generator.py:26,76,122` — propagate `date_posted` through the dict builders and Job reconstructions.

`backend/src/api/routes/jobs.py:74,94` — same column addition in the API `/jobs/export` endpoint.

One commit per file pair (csv_export + notifications + api export) = 3 commits.

### Phase 3 — Disappearance tracking

**Task 3.1 — Per-run `run_hash`**

`backend/src/main.py` `run_search()` — generate `run_hash = uuid.uuid4().hex` at start. Add `run_hash: Optional[str] = None` to `Job` dataclass. After each source fetch, set `job.run_hash = run_hash` on every returned Job.

Commit: `feat(pipeline): generate per-run run_hash`

**Task 3.2 — Convert `insert_job` to upsert (CORRECTED SQL)**

`backend/src/storage/database.py:114-132` — replace `INSERT OR IGNORE` with `ON CONFLICT DO UPDATE`. **The asymmetry matters** (H2):

```sql
INSERT INTO jobs (...)
VALUES (..., NULL)  -- disappeared_at starts null
ON CONFLICT(normalized_company, normalized_title) DO UPDATE SET
    last_seen_at   = excluded.last_seen_at,
    run_hash       = excluded.run_hash,
    disappeared_at = NULL,
    match_score    = excluded.match_score,                      -- latest profile wins
    description = CASE
        WHEN length(excluded.description) > length(jobs.description) THEN excluded.description
        ELSE jobs.description
    END,                                                        -- keep longest
    salary_min = COALESCE(jobs.salary_min, excluded.salary_min),
    salary_max = COALESCE(jobs.salary_max, excluded.salary_max),
    apply_url  = jobs.apply_url,                                -- stable bookmarks
    source     = jobs.source,                                   -- stable attribution
    date_posted = CASE
        WHEN jobs.date_posted IS NULL THEN excluded.date_posted
        WHEN excluded.date_posted IS NULL THEN jobs.date_posted
        WHEN excluded.date_posted < jobs.date_posted THEN excluded.date_posted
        ELSE jobs.date_posted
    END
```

**Regression test (Phase 3.2.1)** in `backend/tests/test_deduplicator.py`: Reed rich → LinkedIn empty (higher score) → Reed-only. Assert final row has Reed's 2000-char description, Reed's `apply_url`, `source="reed"`, Reed's `salary_min`, Run 3's `match_score`.

**Safety smoke-test before committing:**
```bash
cp backend/data/jobs.db /tmp/jobs_backup.db
cp backend/data/jobs.db /tmp/jobs_upsert_test.db
python -m src.cli run --db-path /tmp/jobs_upsert_test.db --dry-run
sqlite3 /tmp/jobs_backup.db       "SELECT COUNT(*), AVG(match_score), MAX(match_score) FROM jobs"
sqlite3 /tmp/jobs_upsert_test.db  "SELECT COUNT(*), AVG(match_score), MAX(match_score) FROM jobs"
```

Red flags: row count drops, score inflation, AVG drift, new NULLs. If any, do not commit.

Commit: `feat(db): upsert with stable attribution; advance last_seen and run_hash`

**Task 3.3 — `mark_disappeared` with alternate-source guard (CORRECTED)**

`backend/src/storage/database.py` new method:

```python
async def mark_disappeared(self, source: str, current_run_hash: str) -> int:
    """Flag jobs whose source matches but whose key was NOT seen this run by any source."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = await self._conn.execute(
        """
        UPDATE jobs
        SET disappeared_at = ?
        WHERE source = ?
          AND run_hash != ?
          AND disappeared_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM jobs j2
              WHERE j2.normalized_company = jobs.normalized_company
                AND j2.normalized_title   = jobs.normalized_title
                AND j2.run_hash = ?
          )
        """,
        (now, source, current_run_hash, current_run_hash),
    )
    return cursor.rowcount
```

**Regression test (Phase 3.3.1)** in `backend/tests/test_disappearance.py`: LinkedIn wins run 1. Reed-only run 2 (Reed sees same job). Assert row still has `disappeared_at IS NULL`. Run 3 neither source returns → assert flagged.

Commit: `feat(db): mark_disappeared respects alternate-source sightings`

**Task 3.4 — Wire into orchestrator**

`backend/src/main.py` after each source's jobs are inserted AND the source returned >0 jobs (guard against network-error mass-disappearance):

```python
if source_jobs:
    await db.mark_disappeared(source=source_name, current_run_hash=run_hash)
```

Commit: `feat(pipeline): mark disappeared jobs per source`

**Task 3.5 — Purge logic**

`backend/src/storage/database.py:183-190`. Replace with:

```python
async def purge_old_jobs(self, days: int = 90, grace_days: int = 14) -> int:
    """Delete disappeared jobs after grace period; hard ceiling at `days`."""
    cutoff_grace    = (datetime.now(timezone.utc) - timedelta(days=grace_days)).isoformat()
    cutoff_absolute = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = await self._conn.execute(
        """
        DELETE FROM jobs
        WHERE (disappeared_at IS NOT NULL AND disappeared_at < ?)
           OR (first_seen < ?)
        """,
        (cutoff_grace, cutoff_absolute),
    )
    await self._conn.commit()
    return cursor.rowcount
```

Default raised from 30 → 90 days so long-lived Greenhouse jobs aren't nuked just because we first saw them a month ago.

Commit: `refactor(db): purge disappeared jobs with grace period`

**Task 3.6 — Exclude disappeared from notifications**

In `backend/src/main.py` new-jobs assembly for email/Slack/Discord, filter `disappeared_at IS NOT NULL`. Test in `backend/tests/test_notifications.py` with a disappeared fixture.

Commit: `fix(notifications): skip disappeared jobs`

### Phase 4 — Semantic fixes

**Task 4.1 — Greenhouse**

`backend/src/sources/ats/greenhouse.py:40` — set `date_posted = None` with a comment: Greenhouse's public API exposes only `updated_at` (last modification time), which is NOT posting date. Phase 2.0's `first_seen` fallback becomes the proxy.

Commit: `fix(sources/greenhouse): updated_at is not posting date; use first_seen proxy`

**Task 4.2 — NHS Jobs**

`backend/src/sources/feeds/nhs_jobs.py:66` — parse `<pubDate>` from the RSS item instead of `<closingDate>`. The Phase 2.0 sanity clamp (`days_old < 0 → 0`) is the safety net if a closing date ever leaks through again.

Commit: `fix(sources/nhs_jobs): parse pubDate (posting), not closingDate (deadline)`

---

## 7. Phases 5-8 (Optional)

### Phase 5 — Ghost detection (dependency-free)

Add `ghost_flag INTEGER DEFAULT 0` column. `flag_ghosts(threshold_days=45)` updates rows where `disappeared_at IS NULL AND first_seen < cutoff`. The frontend (and API default) exclude `ghost_flag=1` jobs by default with a toggle to show. **Do NOT pursue the ChromaDB/embedding path** until this proves insufficient — that's +500 MB of deps for marginal gain.

### Phase 6 — BambooHR

Probe `https://{company}.bamboohr.com/careers/list` with `Accept: application/json` header to verify JSON availability. Base on `backend/src/sources/ats/workable.py` pattern. Add companies to `backend/src/config/companies.py` `BAMBOOHR_COMPANIES` list. Update SOURCE_REGISTRY / rate limits / test counts.

### Phase 7 — HiringCafe internal API

Use reverse-engineered `POST /api/search-jobs` with `dateFetchedPastNDays: 7` for native freshness filtering. Wrap in a circuit breaker — API is undocumented and can change without notice. Full schema from `umur957/hiring-cafe-job-scraper`.

### Phase 8 — Company slug expansion (104 → 500+)

**Discovery script** `scripts/discover_companies.py` (one-off, outside `backend/src/`):

1. Pull `https://yc-oss.github.io/api/companies/all.json` (~5,500 companies).
2. For each `company.url`, HEAD-probe `boards.greenhouse.io/{slug}`, `jobs.lever.co/{slug}`, `jobs.ashbyhq.com/{slug}`, `apply.workable.com/{slug}`, `{slug}.bamboohr.com/careers`.
3. Write verified hits to `backend/data/discovered_companies.json`.
4. **Manual review gate** — drop agencies, offshore staffing, scams (the HiringCafe / Ali pattern).
5. Append to `backend/src/config/companies.py`.

**Supplementation via Google dorks:** `site:boards.greenhouse.io "London"`, `site:jobs.lever.co "United Kingdom"`, etc. Extract slugs from result URLs.

**Rate-limit audit:** Workable caps at 10 req/10s — if BambooHR adds more slugs, audit that the 15-minute pipeline budget still holds.

**Target yield:** 300-600 jobs/run (up from 86), 500-1200 active DB rows after dedup.

---

## 8. Freshness Target & Infrastructure

**Realistic ceiling: 4 hours.** HiringCafe scrapes 30K companies 3x/day (8-hour floor) at zero cost. Job360 on local cron at 4-hour cadence beats that. Sub-minute requires webhooks + persistent hosting = not free.

| Mode | Delay | Cost | Verdict |
|---|---|---|---|
| Webhooks | <1 min | $20-100/mo | Out of scope |
| 15-min polling | 15 min | $5-20/mo | Overkill |
| **4-hour cron** | 4 hr | **$0** | **Target** |
| 8-hour (HiringCafe) | 8 hr | $0 | Acceptable fallback |
| Daily (current) | 24 hr | $0 | Too stale |

**Four-layer source strategy:**
- **Layer 1 (80%):** ATS public APIs — Greenhouse, Lever, Ashby, Workable, Workday, Recruitee, SmartRecruiters, Personio, SuccessFactors, BambooHR
- **Layer 2 (10%):** Free JSON — Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs
- **Layer 3 (5%):** RSS — FindAJob, NHS, jobs.ac.uk, WeWorkRemotely, BioSpace
- **Layer 4 (5%):** Keyed free-tier — Reed, Adzuna, JSearch, Jooble, Google Jobs, Careerjet, Findwork

All free. Bottleneck is cron cadence, not cost.

**Free cron options:**
- **Local cron** (current) — `cron_setup.sh`. Simple. Requires machine on.
- **GitHub Actions free tier** — 2000 min/month private, unlimited public. 10-min pipeline × 6/day = 1800 min/month. Persist `backend/data/jobs.db` via `actions/upload-artifact` + `actions/download-artifact` across runs. Workflow file: `.github/workflows/scrape.yml` with `cron: '0 */4 * * *'`.
- **Railway/Render free tier** — 500 hours/month compute + persistent disk.

**What ships after Phases 0-4 + 8 + cron hardening:**
- "Last 24 Hours" contains 80-120 accurately-dated jobs (vs today's 30-40)
- Disappeared filter correctly hides stale listings
- Score reflects current profile (no sticky MAX scores)
- Freshness floor: 4 hours

---

## 9. Open-Source Reference (Tier-Ranked)

| Tier | Repo | Used for | Phase |
|---|---|---|---|
| 1 | [JobSpy](https://github.com/speedyapply/JobSpy) | Direct timestamp extraction patterns (Indeed `datePublished`, LinkedIn `<time datetime>`, Glassdoor `ageInDays`) | 1.1, 1.3, 1.8 |
| 1 | [Levergreen](https://github.com/adgramigna/job-board-scraper) | `run_hash` tagging + dbt-style snapshot diffing for disappearance | 3.1, 3.3 |
| 2 | [Hamed's gist](https://gist.github.com/hamedn/b8bfc56afa91a3f397d8725e74596cf2) | HiringCafe production GPT-4o-mini schema — 16-value cross-domain `category` enum | Future LLM extraction |
| 2 | [Ali's writeup](https://gist.github.com/thoroc/21601e286d9d4fec8505a88d71145ad9) | Manual company verification, 3x/day cadence justification, career-page discovery | 8 |
| 2 | [Lever Postings API](https://github.com/lever/postings-api) | Official `createdAt` ms-epoch field docs | 1.3 |
| 2 | [YC Companies API](https://github.com/yc-oss/api) | 5,500 free company domains for Phase 8 discovery | 8 |
| 3 | [JobFunnel](https://github.com/PaulMcInnis/JobFunnel) | `max_listing_days` filter, `company_block_list` patterns | Future enhancement |
| 3 | [SpeedyApply lists](https://github.com/speedyapply/2026-SWE-College-Jobs) | GitHub Actions free cron template | 8 / infra |
| 3 | [HiringCafe scraper](https://github.com/umur957/hiring-cafe-job-scraper) | HiringCafe internal API schema + `dateFetchedPastNDays` primitive | 7 |

**Warnings on what NOT to copy:**
- JobSpy's `datetime.now()` / `fromtimestamp()` are naive (no `tz=utc`). Always pass `tz=timezone.utc` when adapting.
- Levergreen's spider base class sets `created_at = time.time()` — that's the same bug Job360 has today. Copy the `run_hash` pattern, not the date logic.

---

## 10. Out of Scope

- Ollama/Mistral local LLM integration (Priority 7 in original brief) — separate initiative.
- LLM salary extraction — depends on Ollama.
- Workday CXS fix — **already working** (verified §2.4).
- Elasticsearch / migrating off SQLite — forbidden per memory.
- Search engine UI — forbidden per brief.

**Previously out-of-scope, NOW in scope** (per user 2026-04-11):
- Company slug expansion — Phase 8.
- YC API + Apollo free tier + Google dorks for discovery.

---

## 11. Execution Handoff

**Two options:**

1. **Subagent-driven** (recommended) — fresh subagent per task, review between. Best for Phase 1's 46-source parallelism.
2. **Inline** via `superpowers:executing-plans` — batched with checkpoints. Best for Phases 0/2/3/4 where DB state matters.

**Recommended split:**
- Phases 0, 2, 3, 4 → inline
- Phase 1 → subagent-driven (batched by parser type into 3 commits, not 46)
- Phases 5-8 → deferred until critical path is green

**Hard rules (from CLAUDE.md):**
- Never touch `normalized_key()` without verifying deduplicator (Rule 1) — preserved by upsert
- Never change `BaseJobSource` constructor/core methods (Rule 2) — only adding `_discovered_at` helper
- HTTP always mocked in tests (Rule 4)
- Run relevant test suite after every change (Rule 5)
- Update `SOURCE_REGISTRY` count assertions in both `test_cli.py` AND `test_api.py` (Rule 8)
- Scoring changes verified via `test_scorer.py` + `test_profile.py` (Rule 9)
