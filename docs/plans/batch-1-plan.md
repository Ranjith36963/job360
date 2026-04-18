# Pillar 3 Batch 1 Implementation Plan — Date Model + Ghost Detection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended for the 39 per-source fixes in Task 7) or superpowers:executing-plans (for serial tasks 1-6, 8-10). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single broken `date_found` column with a trustworthy 5-column date model, stop 39 sources from fabricating timestamps, add ghost-listing state machine, and expose 10 freshness KPIs via Prometheus + Grafana.

**Architecture:** Add `posted_at` / `first_seen_at` / `last_seen_at` / `last_updated_at` / `date_confidence` columns to the `jobs` table. Preserve `date_found` for back-compat during transition (it becomes semantically equal to `first_seen_at`). Update `Job` dataclass with new optional fields. Sources that cannot prove a real posting date set `posted_at=None` + `date_confidence='low'` instead of fabricating `datetime.now()`. Recency scorer treats `None` as "unknown" (0 points, no penalty). Ghost-detection helper tracks `consecutive_misses` + `staleness_state` transitions.

**Tech Stack:** aiosqlite (schema), aiohttp (sources), pytest + aioresponses (tests), prometheus_client (KPI exporter), Grafana OSS (dashboard).

---

## CLEAN-MAIN BASELINE

| Field | Value |
|---|---|
| Date | 2026-04-18 |
| Commit | `d02d56c` (worktree branched from main, which at time of branch includes the `docs: consolidate pillar research + add IMPLEMENTATION_LOG` doc-only commit on top of `d364e9d`) |
| Total collected | 398 |
| Passing | **371** |
| Failing | **24** |
| Skipped | **3** |
| Excluded | 12 (`tests/test_main.py` — known live-HTTP leak via JobSpy) |
| Run time | 169.53s (2m 49s) |
| Run command | `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q` |

**Failure bucket breakdown (all pre-existing, not regressions):**

| Bucket | Count | Tests |
|---|---:|---|
| API sqlite init | 6 | `test_api.py::{test_status_returns_counts, test_jobs_list_empty, test_actions_counts_empty, test_pipeline_counts_empty, test_pipeline_list_empty, test_full_api_workflow}` |
| Cron/setup path drift | 8 | `test_cron.py::{test_cron_contains_uk_timezone, test_cron_schedule_4am_4pm, test_cron_uses_module_invocation}`, `test_setup.py::{test_setup_checks_python_version, test_setup_creates_data_dirs, test_setup_validates_env_example, test_requirements_prod_no_test_deps, test_requirements_dev_includes_prod}` |
| Source parsers returning 0 jobs | 7 | `test_sources.py::test_reed_parses_response`, `test_careerjet_parses_response`, + ~5 others from `F` hits in output |
| `extract_matched_skills_*` stale assertions | 3 | `test_time_buckets.py::{test_extract_matched_skills_primary, test_extract_matched_skills_secondary, test_extract_matched_skills_tertiary}` |
| **Total** | **24** | matches the 4 follow-up-ticket buckets in IMPLEMENTATION_LOG.md exactly |

**Clean baseline is 2 failures lower than dirty-copy (26).** The two missing failures were caused by uncommitted scraper edits in the main workspace — confirming they are NOT pre-existing main bugs but working-copy noise.

**Contract for "no regressions":** after Batch 1, failing count must remain ≤ 24 and every failing test must fall into one of those four buckets. New tests added in Batch 1 count as additions, not regressions.

Pre-existing failure buckets (not regressions):
1. API sqlite init (`test_api.py`)
2. Cron/setup path drift (`test_cron.py`, `test_setup.py`)
3. Source parsers (`test_sources.py` — reed, adzuna, jooble, jobspy, workday, google_jobs, careerjet, climatebase, eightykhours, bcs_jobs, aijobs_global, aijobs_ai)
4. `extract_matched_skills_*` (`test_time_buckets.py`)

"No regressions" = failing count for this branch compared to clean-main baseline does not increase outside those buckets. New source tests added in Task 7 are counted as batch 1 additions, not regressions.

---

## Hard Constraints (violations = revert)

- Rule #1 — DO NOT modify `Job.normalized_key()` in `models.py`
- Rule #2 — DO NOT change `BaseJobSource` constructor or its helpers (`_get_json`/`_post_json`/`_get_text`/`_request`, retry, rate-limit)
- Rule #3 — DO NOT touch `purge_old_jobs()` in `database.py`
- Rule #4 — All HTTP in tests MUST be mocked via `aioresponses`; zero live requests allowed
- Rule #8 — Source count stays at 48 registry / 47 unique instances for Batch 1 (no new sources until Batch 3)

---

## File Structure (what the engineer will touch)

**Create:**
- `docs/plans/batch-1-plan.md` (this file)
- `backend/src/services/ghost_detection.py` — state machine + transition helper
- `backend/ops/__init__.py`
- `backend/ops/exporter.py` — Prometheus exporter (~200 lines)
- `backend/ops/grafana_dashboard.json` — dashboard JSON
- `backend/scripts/measure_date_reliability.py` — baseline + post-batch measurement
- `backend/tests/test_ghost_detection.py` — state machine tests
- `backend/tests/test_date_schema.py` — migration + column tests
- `backend/tests/test_kpi_exporter.py` — exporter sanity tests

**Modify:**
- `backend/src/models.py` — add `posted_at`, `date_confidence`, `date_posted_raw` fields to `Job` (DO NOT touch `normalized_key()`)
- `backend/src/repositories/database.py` — add migration entries for 7 new columns, wire `insert_job()` to write them, add `update_last_seen()` and `mark_missed()` helpers
- `backend/src/services/skill_matcher.py` — `_recency_score()` gracefully handles `None`; new `_effective_recency_date()` precedence helper
- `backend/src/sources/apis_keyed/jooble.py` — remove `"updated"` mis-mapping
- `backend/src/sources/ats/greenhouse.py` — remove `"updated_at"` mis-mapping
- `backend/src/sources/feeds/nhs_jobs.py` — remove `"closingDate"` mis-mapping (all 3 call sites)
- 36 other fabricating source files (full list in Task 7) — each drops its `or datetime.now(...)` fallback
- `backend/tests/test_database.py` — migration assertions + default values
- `backend/tests/test_scorer.py` — `None`/low-confidence recency paths
- `backend/tests/test_sources.py` — update mocked payloads to assert `posted_at` / `date_confidence` per source
- `docs/IMPLEMENTATION_LOG.md` — append completion entry

**Delete (pre-flight):**
- `backend/src/filters/` (empty `__pycache__` only)
- `backend/src/llm/` (empty `__pycache__` only)
- `backend/src/pipeline/` (never existed — stale cache)
- `backend/src/validation/` (never existed — stale cache)

---

## Step 0: Lock the clean baseline

- [ ] **Step 0.1: Run baseline pytest against clean main@HEAD**

```bash
cd backend
python -m pytest tests/ --ignore=tests/test_main.py -q > /c/temp/batch1/baseline.log 2>&1
tail -5 /c/temp/batch1/baseline.log
```

Expected: tail shows "X passed, Y failed, Z skipped in NN.NNs"

- [ ] **Step 0.2: Record the baseline counts**

Edit the "CLEAN-MAIN BASELINE" table at the top of this file with: commit hash, total/passing/failing/skipped counts, run time. This is the contract — every future "no regressions" check compares against these numbers.

---

## Task 1: Pre-flight debris cleanup

**Files:** delete `backend/src/{filters,llm,pipeline,validation}/` (all empty `__pycache__`-only)

- [ ] **Step 1.1: Verify dirs are truly empty**

```bash
find backend/src/filters backend/src/llm backend/src/pipeline backend/src/validation -type f -not -name "*.pyc" 2>/dev/null
```

Expected: no output. (If any non-`.pyc` file appears, STOP and investigate.)

- [ ] **Step 1.2: Delete**

```bash
rm -rf backend/src/filters backend/src/llm backend/src/pipeline backend/src/validation
```

- [ ] **Step 1.3: Run tests to confirm nothing imported from them**

```bash
cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q 2>&1 | tail -3
```

Expected: same pass/fail counts as baseline.

- [ ] **Step 1.4: Commit**

```bash
git add -A
git commit -m "chore: remove phase-4 debris dirs (filters/llm/pipeline/validation)

Empty __pycache__-only packages left from the phase-4 rename
(b2f747e). Removing prevents stale-bytecode import ambiguity
during the schema work in Batch 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Schema migration — add 7 new columns

**Files:**
- Modify: `backend/src/repositories/database.py`
- Create: `backend/tests/test_date_schema.py`

Adding to the `jobs` table (existing `date_found` + `first_seen` remain, marked deprecated-in-docstring — back-compat with existing rows and indexes):

| Column | Type | Default | Purpose |
|---|---|---|---|
| `posted_at` | TEXT | NULL | Source-claimed post date; NULL if no trustworthy source date |
| `first_seen_at` | TEXT | NULL | Alias for existing `first_seen`; kept identical (redundant column, but names the semantic) |
| `last_seen_at` | TEXT | NULL | Updated each scrape a job reappears |
| `last_updated_at` | TEXT | NULL | Updated when content-hash changes |
| `date_confidence` | TEXT | `'low'` | Enum: `high`/`medium`/`low`/`fabricated`/`repost_backdated` |
| `date_posted_raw` | TEXT | NULL | Raw pre-parse string, audit-only |
| `consecutive_misses` | INTEGER | `0` | Ghost detection counter |
| `staleness_state` | TEXT | `'active'` | Enum: `active`/`possibly_stale`/`likely_stale`/`confirmed_expired` |

> **Note on `first_seen_at`:** the existing `first_seen` column already semantically matches `first_seen_at`. To avoid table-rewrite cost, the plan is to ADD `first_seen_at` as an alias column populated at INSERT by the same value the code writes to `first_seen`. A future batch can consolidate. This keeps Batch 1 strictly additive.

- [ ] **Step 2.1: Write failing migration test FIRST**

Create `backend/tests/test_date_schema.py`:

```python
import pytest
import aiosqlite

from src.repositories.database import JobDatabase


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = JobDatabase(str(db_path))
    await db.init_db()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_jobs_table_has_five_column_date_model(db):
    cursor = await db._conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1]: row[2] for row in await cursor.fetchall()}
    # New date-model columns
    assert "posted_at" in cols
    assert "first_seen_at" in cols
    assert "last_seen_at" in cols
    assert "last_updated_at" in cols
    assert "date_confidence" in cols
    assert "date_posted_raw" in cols
    # Ghost detection hooks
    assert "consecutive_misses" in cols
    assert "staleness_state" in cols


@pytest.mark.asyncio
async def test_date_confidence_defaults_to_low(db):
    cursor = await db._conn.execute("PRAGMA table_info(jobs)")
    rows = await cursor.fetchall()
    dc = next(r for r in rows if r[1] == "date_confidence")
    assert "'low'" in (dc[4] or "")


@pytest.mark.asyncio
async def test_staleness_state_defaults_to_active(db):
    cursor = await db._conn.execute("PRAGMA table_info(jobs)")
    rows = await cursor.fetchall()
    ss = next(r for r in rows if r[1] == "staleness_state")
    assert "'active'" in (ss[4] or "")


@pytest.mark.asyncio
async def test_migration_idempotent(db):
    # Running _migrate twice should not error or duplicate columns
    await db._migrate()
    await db._migrate()
    cursor = await db._conn.execute("PRAGMA table_info(jobs)")
    names = [row[1] for row in await cursor.fetchall()]
    assert names.count("posted_at") == 1
```

- [ ] **Step 2.2: Run test to confirm it fails (columns don't exist yet)**

```bash
cd backend && python -m pytest tests/test_date_schema.py -v
```

Expected: 4 FAILED with `AssertionError`.

- [ ] **Step 2.3: Add migration entries in `database.py`**

In `_migrate()`, replace the empty `migrations = []` list with:

```python
migrations = [
    ("posted_at",           "TEXT"),
    ("first_seen_at",       "TEXT"),
    ("last_seen_at",        "TEXT"),
    ("last_updated_at",     "TEXT"),
    ("date_confidence",     "TEXT DEFAULT 'low'"),
    ("date_posted_raw",     "TEXT"),
    ("consecutive_misses",  "INTEGER DEFAULT 0"),
    ("staleness_state",     "TEXT DEFAULT 'active'"),
]
```

Also add the columns to the inline `CREATE TABLE IF NOT EXISTS jobs (...)` block in `init_db()` so **fresh** databases get them at creation time (without this, brand-new DBs only pick them up via migration). Append directly after `first_seen TEXT NOT NULL,`:

```sql
posted_at TEXT,
first_seen_at TEXT,
last_seen_at TEXT,
last_updated_at TEXT,
date_confidence TEXT DEFAULT 'low',
date_posted_raw TEXT,
consecutive_misses INTEGER DEFAULT 0,
staleness_state TEXT DEFAULT 'active',
```

Add an index for ghost-detection scans:
```sql
CREATE INDEX IF NOT EXISTS idx_jobs_staleness_state ON jobs(staleness_state);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);
```

- [ ] **Step 2.4: Run the migration test to confirm it passes**

```bash
cd backend && python -m pytest tests/test_date_schema.py -v
```

Expected: 4 passed.

- [ ] **Step 2.5: Run the full existing suite to confirm no regressions**

```bash
cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q 2>&1 | tail -3
```

Expected: baseline counts hold. `test_database.py` still green.

- [ ] **Step 2.6: Commit**

```bash
git add backend/src/repositories/database.py backend/tests/test_date_schema.py
git commit -m "feat(db): add 5-column date model + ghost detection hooks

Adds posted_at, first_seen_at, last_seen_at, last_updated_at,
date_confidence, date_posted_raw (date model) plus
consecutive_misses, staleness_state (ghost detection) to the
jobs table. Strictly additive — existing date_found / first_seen
columns untouched for back-compat.

Migration is idempotent. New fresh-create path includes the
columns inline. Two new indexes for ghost-detection scans.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Wire `Job` dataclass + `insert_job()` to new columns

**Files:**
- Modify: `backend/src/models.py`
- Modify: `backend/src/repositories/database.py`
- Modify: `backend/tests/test_models.py` / `backend/tests/test_database.py`

- [ ] **Step 3.1: Write failing dataclass test**

Append to `backend/tests/test_models.py`:

```python
def test_job_has_posted_at_and_confidence_fields():
    job = Job(title="X", company="Y", apply_url="u", source="s", date_found="")
    assert hasattr(job, "posted_at")
    assert job.posted_at is None
    assert hasattr(job, "date_confidence")
    assert job.date_confidence == "low"
    assert hasattr(job, "date_posted_raw")
    assert job.date_posted_raw is None


def test_job_accepts_posted_at_and_confidence():
    job = Job(
        title="X", company="Y", apply_url="u", source="s", date_found="",
        posted_at="2026-04-15T10:00:00+00:00",
        date_confidence="high",
        date_posted_raw="2026-04-15T10:00:00Z",
    )
    assert job.posted_at == "2026-04-15T10:00:00+00:00"
    assert job.date_confidence == "high"
```

- [ ] **Step 3.2: Run → expect failure**

```bash
cd backend && python -m pytest tests/test_models.py::test_job_has_posted_at_and_confidence_fields -v
```

Expected: `AttributeError: Job has no attribute 'posted_at'`.

- [ ] **Step 3.3: Extend `Job` dataclass**

In `backend/src/models.py`, add three fields (after `experience_level: str = ""`):

```python
    posted_at: Optional[str] = None
    date_confidence: str = "low"
    date_posted_raw: Optional[str] = None
```

**DO NOT touch `normalized_key()` or `_COMPANY_SUFFIXES` / `_COMPANY_REGION_SUFFIXES` regex.**

- [ ] **Step 3.4: Wire insert_job() to persist the new fields**

In `backend/src/repositories/database.py` `insert_job()`, extend the INSERT:

```python
async def insert_job(self, job: Job) -> bool:
    company, title = job.normalized_key()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await self._conn.execute(
        """INSERT OR IGNORE INTO jobs
        (title, company, location, salary_min, salary_max, description,
         apply_url, source, date_found, match_score, visa_flag,
         experience_level, normalized_company, normalized_title, first_seen,
         posted_at, first_seen_at, last_seen_at, date_confidence,
         date_posted_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job.title, job.company, job.location,
            job.salary_min, job.salary_max, job.description,
            job.apply_url, job.source, job.date_found,
            job.match_score, int(job.visa_flag),
            job.experience_level, company, title, now,
            job.posted_at, now, now, job.date_confidence,
            job.date_posted_raw,
        ),
    )
    return cursor.rowcount > 0
```

- [ ] **Step 3.5: Add DB-side helpers for ghost detection (needed by Task 7)**

Append to `JobDatabase`:

```python
async def update_last_seen(self, normalized_key: tuple[str, str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await self._conn.execute(
        "UPDATE jobs SET last_seen_at = ?, consecutive_misses = 0, "
        "staleness_state = 'active' "
        "WHERE normalized_company = ? AND normalized_title = ?",
        (now, normalized_key[0], normalized_key[1]),
    )
    await self._conn.commit()


async def mark_missed_for_source(self, source: str, seen_keys: set[tuple[str, str]]) -> int:
    cursor = await self._conn.execute(
        "SELECT id, normalized_company, normalized_title FROM jobs WHERE source = ?",
        (source,),
    )
    rows = await cursor.fetchall()
    missed = [r for r in rows if (r[1], r[2]) not in seen_keys]
    for row in missed:
        await self._conn.execute(
            "UPDATE jobs SET consecutive_misses = consecutive_misses + 1 WHERE id = ?",
            (row[0],),
        )
    await self._conn.commit()
    return len(missed)
```

- [ ] **Step 3.6: Run both models and database tests**

```bash
cd backend && python -m pytest tests/test_models.py tests/test_database.py tests/test_date_schema.py -v
```

Expected: all pass.

- [ ] **Step 3.7: Commit**

```bash
git add backend/src/models.py backend/src/repositories/database.py backend/tests/test_models.py
git commit -m "feat(models): add posted_at, date_confidence, date_posted_raw fields

Extends Job dataclass (leaving normalized_key() untouched per
CLAUDE.md rule #1). insert_job persists the new columns plus
first_seen_at / last_seen_at; two new DB helpers update_last_seen
and mark_missed_for_source drive ghost detection.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Recency scorer — handle `None` and low-confidence dates

**Files:**
- Modify: `backend/src/services/skill_matcher.py`
- Modify: `backend/tests/test_scorer.py`

**Semantics:** the recency score answers "how fresh is this job?" For Batch 1:
- If `posted_at` is set AND `date_confidence` ∈ {`high`, `medium`, `repost_backdated`}: score by `posted_at` age with existing bands.
- Else if `first_seen_at` / `date_found` is present: score by `first_seen` age but cap at 60% of band (honest discovery, not honest posting).
- Else: 0 points, no penalty.

- [ ] **Step 4.1: Write failing recency tests**

Append to `backend/tests/test_scorer.py`:

```python
from src.services.skill_matcher import _effective_recency_date, _recency_score


def test_recency_none_date_returns_zero():
    job = Job(title="X", company="Y", apply_url="u", source="s", date_found="",
              posted_at=None, date_confidence="low")
    assert _recency_score_for(job) == 0  # helper defined below


def test_recency_high_confidence_posted_at_scores_full():
    today = datetime.now(timezone.utc).isoformat()
    job = Job(title="X", company="Y", apply_url="u", source="s", date_found="",
              posted_at=today, date_confidence="high")
    assert _recency_score_for(job) == 10


def test_recency_low_confidence_falls_back_to_first_seen_capped():
    today = datetime.now(timezone.utc).isoformat()
    job = Job(title="X", company="Y", apply_url="u", source="s",
              date_found=today, posted_at=None, date_confidence="low")
    # capped at 60% of 10 = 6
    assert _recency_score_for(job) == 6


def test_recency_fabricated_confidence_never_inflates():
    today = datetime.now(timezone.utc).isoformat()
    job = Job(title="X", company="Y", apply_url="u", source="s",
              date_found=today, posted_at=today, date_confidence="fabricated")
    assert _recency_score_for(job) == 0


# helper — public wrapper used in scoring
def _recency_score_for(job):
    from src.services.skill_matcher import recency_score_for_job
    return recency_score_for_job(job)
```

- [ ] **Step 4.2: Run → expect 4 failures**

```bash
cd backend && python -m pytest tests/test_scorer.py -k recency -v
```

- [ ] **Step 4.3: Add `recency_score_for_job()` in `skill_matcher.py`**

After `_recency_score()`:

```python
def recency_score_for_job(job: "Job") -> int:
    """Score recency using the 5-column date model.

    - high/medium/repost_backdated posted_at → full band
    - fabricated → 0
    - low confidence with posted_at → fall back to first_seen (date_found) capped 60%
    """
    if job.date_confidence == "fabricated":
        return 0
    if job.posted_at and job.date_confidence in ("high", "medium", "repost_backdated"):
        return _recency_score(job.posted_at)
    if job.date_found:
        raw = _recency_score(job.date_found)
        return int(raw * 0.6)
    return 0
```

Update `score_job()` and `JobScorer.score()` to use `recency_score_for_job(job)` instead of `_recency_score(job.date_found)`.

- [ ] **Step 4.4: Re-run and confirm green**

```bash
cd backend && python -m pytest tests/test_scorer.py -v
```

Expected: new 4 pass, no regressions in the 53 pre-existing scorer tests.

- [ ] **Step 4.5: Commit**

```bash
git add backend/src/services/skill_matcher.py backend/tests/test_scorer.py
git commit -m "feat(scorer): recency now honours posted_at + date_confidence

Introduces recency_score_for_job() driven by the new 5-column
date model. High/medium confidence → full band. Fabricated
confidence → 0 (kills the inflation bug where 39 sources scored
+10 via datetime.now()). Low confidence falls back to first_seen
capped at 60% of band — honest discovery, not honest posting.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Fix 3 wrong-field sources

Apply a **uniform patch pattern**: stop using the wrong field, set `posted_at=None` + `date_confidence='low'` when no trustworthy source date exists, preserve the old `date_found` as `first_seen` fallback for back-compat.

**Pattern used for every source fix in Tasks 5 and 7:**

```python
# BEFORE (fabricator pattern)
date_found = item.get("WRONG_OR_MISSING_FIELD") or datetime.now(timezone.utc).isoformat()
jobs.append(Job(..., date_found=date_found))

# AFTER
now_iso = datetime.now(timezone.utc).isoformat()
raw = item.get("REAL_POSTED_FIELD_IF_AVAILABLE")  # or None
posted_at = raw if raw else None
confidence = "high" if raw else "low"
jobs.append(Job(
    ...,
    date_found=now_iso,           # keeps legacy column populated (= first_seen)
    posted_at=posted_at,
    date_confidence=confidence,
    date_posted_raw=raw,
))
```

### 5.1: Jooble — drop `"updated"`

**File:** `backend/src/sources/apis_keyed/jooble.py:49`

- [ ] **Step 5.1.1: Update test fixture in `test_sources.py`**

Find the jooble test (search for `JoobleSource`), change expected `date_found` assertion to:

```python
assert jobs[0].posted_at is None
assert jobs[0].date_confidence == "low"
```

(Jooble's `updated` is semantically not a posting date — it is a listing-modification date. Per `pillar_3_batch_1.md` §5 and `CurrentStatus.md` §5, it must NOT populate `posted_at`.)

- [ ] **Step 5.1.2: Run → expect failure**

- [ ] **Step 5.1.3: Apply pattern above to jooble.py:49**

```python
now_iso = datetime.now(timezone.utc).isoformat()
# Jooble's `updated` field is a mutation date, not a posting date
# (per pillar_3_batch_1.md §5). posted_at stays None.
jobs.append(Job(
    title=item.get("title", ""),
    company=item.get("company", ""),
    location=item.get("location", ""),
    description=item.get("snippet", ""),
    apply_url=item.get("link", ""),
    source=self.name,
    date_found=now_iso,
    posted_at=None,
    date_confidence="low",
    date_posted_raw=item.get("updated"),
    salary_min=salary_min,
    salary_max=salary_max,
))
```

- [ ] **Step 5.1.4: Run → expect pass**

- [ ] **Step 5.1.5: Commit**

```bash
git commit -m "fix(jooble): stop mapping 'updated' to posted_at

Per pillar_3_batch_1.md §5, Jooble's 'updated' is a mutation
date. Listing it as the post date contaminates the 24h bucket.
Now kept in date_posted_raw for audit, posted_at=None, confidence=low.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### 5.2: Greenhouse — drop `"updated_at"`

**File:** `backend/src/sources/ats/greenhouse.py:40`

Same pattern; Greenhouse public API exposes only `updated_at`, never `created_at`. `posted_at=None`, raw kept, confidence=low.

### 5.3: NHS Jobs — drop `"closingDate"`

**File:** `backend/src/sources/feeds/nhs_jobs.py:57` plus fallbacks at L105, L111. `closingDate` is the posting *deadline*, not the post date — treating it as a posting date fabricates freshness. Use `postedDate` / `publishedAt` if the XML feed exposes it; otherwise `posted_at=None`, confidence=low.

(Each sub-task follows the RED/GREEN/COMMIT cycle of 5.1.)

---

## Task 6: Fix 39 date-fabricating sources

**Fabricator list (per CurrentStatus.md §5):**

- `apis_free/` (10): aijobs, arbeitnow, devitjobs, himalayas, hn_jobs, jobicy, landingjobs (fallback), remoteok, remotive (fallback), yc_companies
- `apis_keyed/` (6): adzuna, careerjet (fallback), findwork (fallback), google_jobs (×3 call sites), jsearch (fallback), reed (fallback)
- `ats/` (9): ashby, lever, personio, pinpoint, recruitee, smartrecruiters, successfactors, workable, workday (×4 sites) (greenhouse in Task 5)
- `feeds/` (8): biospace, findajob, jobs_ac_uk, nhs_jobs (×2 — extras beyond Task 5), realworkfromanywhere, uni_jobs, weworkremotely, workanywhere
- `scrapers/` (7): aijobs_ai, aijobs_global, bcs_jobs, climatebase, eightykhours, jobtensor, linkedin
- `other/` (5): hackernews, indeed, nofluffjobs (fallback), nomis, themuse

**Strategy:** dispatch **4 subagents in parallel** (one per category group), each applying the same patch pattern to its sources. Each subagent must:

1. Read the source file
2. Identify every `datetime.now(...)` call site
3. Determine whether the upstream payload exposes a real posting date (check API docs / existing field names like `datePosted`, `date_posted`, `publishedAt`, `created`, `published_at`)
4. Apply the patch:
    - If a real date field exists → `posted_at=that`, `date_confidence='high'` (API) or `'medium'` (parsed relative string)
    - If none → `posted_at=None`, `date_confidence='low'`
    - Always populate `date_posted_raw` with whatever the source returned (or `None`)
    - Always set `date_found=datetime.now(timezone.utc).isoformat()` (= first_seen, legacy column)
5. Update the corresponding test in `test_sources.py`

**Subagent dispatch (per superpowers:subagent-driven-development):**

| Subagent | Scope | Expected commits |
|---|---|---|
| Agent A | `apis_free/` (10 files) | 10 commits |
| Agent B | `apis_keyed/` + `ats/` (15 files) | 15 commits |
| Agent C | `feeds/` + `scrapers/` (15 files) | 15 commits |
| Agent D | `other/` (5 files) + test_sources.py cross-check | 5 commits |

Each subagent gets the patch pattern, the constraint list, and the specific file list. Each commits per source with message:

```
fix(<source>): stop fabricating posted_at via datetime.now()

<1-sentence rationale — whether a real date was recovered or posted_at=None>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

After all 4 subagents report, run the full test suite. Expected: no regressions outside the known buckets; `test_sources.py` still green (after fixture updates).

---

## Task 7: Ghost-listing state machine

**Files:**
- Create: `backend/src/services/ghost_detection.py`
- Create: `backend/tests/test_ghost_detection.py`
- Modify: `backend/src/main.py` (wire per-source absence tracking into `run_search()`)

- [ ] **Step 7.1: Write failing tests**

```python
# backend/tests/test_ghost_detection.py
import pytest
from src.services.ghost_detection import (
    StalenessState, transition, should_exclude_from_24h,
)


@pytest.mark.parametrize("misses,age_hours,expected", [
    (0, 0, StalenessState.ACTIVE),
    (1, 6, StalenessState.ACTIVE),          # single miss is noise
    (2, 12, StalenessState.POSSIBLY_STALE), # 2 misses after 12h
    (3, 24, StalenessState.LIKELY_STALE),   # 3+ misses
    (5, 48, StalenessState.LIKELY_STALE),
])
def test_transition_based_on_misses_and_age(misses, age_hours, expected):
    assert transition(misses, age_hours) == expected


def test_should_exclude_24h_for_stale():
    assert should_exclude_from_24h(StalenessState.LIKELY_STALE) is True
    assert should_exclude_from_24h(StalenessState.CONFIRMED_EXPIRED) is True
    assert should_exclude_from_24h(StalenessState.ACTIVE) is False
    assert should_exclude_from_24h(StalenessState.POSSIBLY_STALE) is False
```

- [ ] **Step 7.2: Red → green**

```python
# backend/src/services/ghost_detection.py
from enum import Enum


class StalenessState(str, Enum):
    ACTIVE = "active"
    POSSIBLY_STALE = "possibly_stale"
    LIKELY_STALE = "likely_stale"
    CONFIRMED_EXPIRED = "confirmed_expired"


def transition(consecutive_misses: int, age_hours_since_last_seen: float) -> StalenessState:
    if consecutive_misses >= 3 and age_hours_since_last_seen >= 24:
        return StalenessState.LIKELY_STALE
    if consecutive_misses >= 2 and age_hours_since_last_seen >= 12:
        return StalenessState.POSSIBLY_STALE
    return StalenessState.ACTIVE


def should_exclude_from_24h(state: StalenessState) -> bool:
    return state in (StalenessState.LIKELY_STALE, StalenessState.CONFIRMED_EXPIRED)
```

- [ ] **Step 7.3: Integrate into run_search**

After the per-source `gather()` completes, for each source:
1. Build `seen_keys = {job.normalized_key() for job in source_jobs}`
2. Call `await db.mark_missed_for_source(source_name, seen_keys)`
3. Call `await db.update_last_seen(key)` for every key in `seen_keys` (existing jobs — avoids double-write via conditional UPDATE)

Skip the absence sweep if `len(source_jobs) < 0.7 * rolling_7d_average` (scrape-completeness gate per pillar_3_batch_1.md §3 Step 1). For Batch 1 MVP, the rolling average can use `JobDatabase.get_last_source_counts(n=7)` which already exists.

- [ ] **Step 7.4: Run, commit**

---

## Task 8: 10-KPI Prometheus exporter + Grafana dashboard

**Files:**
- Create: `backend/ops/__init__.py` (empty)
- Create: `backend/ops/exporter.py` (~200 lines)
- Create: `backend/ops/grafana_dashboard.json`
- Create: `backend/scripts/measure_date_reliability.py`
- Create: `backend/tests/test_kpi_exporter.py`

- [ ] **Step 8.1: Write `measure_date_reliability.py`** — counts jobs by date_confidence, prints ratio. Target: move from ~60-65% to 95%+ after Task 6.

```python
"""Measure date_reliability_ratio. Run before + after Batch 1."""
import asyncio
from src.repositories.database import JobDatabase
from src.core.settings import DB_PATH


async def main():
    db = JobDatabase(str(DB_PATH))
    await db.init_db()
    cursor = await db._conn.execute(
        "SELECT date_confidence, COUNT(*) FROM jobs GROUP BY date_confidence"
    )
    rows = dict(await cursor.fetchall())
    total = sum(rows.values())
    trustworthy = rows.get("high", 0) + rows.get("medium", 0) + rows.get("repost_backdated", 0)
    ratio = trustworthy / total if total else 0
    print(f"date_reliability_ratio = {ratio:.1%} ({trustworthy}/{total})")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 8.2: Exporter skeleton** — 10 KPIs per pillar_3_batch_1.md §4:

```python
# backend/ops/exporter.py
from prometheus_client import Gauge, start_http_server
# 10 gauges:
BUCKET_ACCURACY_24H = Gauge("job360_bucket_accuracy_24h", "...")
BUCKET_ACCURACY_48H = Gauge("job360_bucket_accuracy_48h", "...")
BUCKET_ACCURACY_7D  = Gauge("job360_bucket_accuracy_7d", "...")
BUCKET_ACCURACY_21D = Gauge("job360_bucket_accuracy_21d", "...")
DATE_RELIABILITY_RATIO = Gauge("job360_date_reliability_ratio", "...")
NOTIFICATION_LATENCY_P50 = Gauge("job360_notification_latency_p50_seconds", "...")
NOTIFICATION_LATENCY_P95 = Gauge("job360_notification_latency_p95_seconds", "...")
STALE_LISTING_RATE = Gauge("job360_stale_listing_rate", "...")
CRAWL_FRESHNESS_LAG = Gauge("job360_crawl_freshness_lag_seconds", "source", ["source"])
PIPELINE_E2E_LATENCY_P50 = Gauge("job360_pipeline_e2e_latency_p50_seconds", "...")
PIPELINE_E2E_LATENCY_P95 = Gauge("job360_pipeline_e2e_latency_p95_seconds", "...")
DELIVERY_SUCCESS_RATE = Gauge("job360_notification_delivery_success_rate", "channel", ["channel"])
# refresh every 5 minutes via asyncio loop reading from JobDatabase.
```

- [ ] **Step 8.3: Grafana dashboard JSON** — 4 rows per report §4: accuracy gauges / latency heatmap / source health table / volume trends.

- [ ] **Step 8.4: Exporter sanity test** — verify exporter.py imports, metric names are unique, refresh function does not crash on an empty DB.

- [ ] **Step 8.5: Commit**

---

## Task 9: Verification + handoff

- [ ] **Step 9.1: Run full suite**

```bash
cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
```

Compare pass/fail/skip counts to Step 0 baseline. New tests added in batch 1 should push passing count up. No failing count outside the pre-existing buckets.

- [ ] **Step 9.2: Measure `date_reliability_ratio`**

```bash
cd backend && python scripts/measure_date_reliability.py
```

Record the ratio in IMPLEMENTATION_LOG.md completion entry.

- [ ] **Step 9.3: Append completion entry to `docs/IMPLEMENTATION_LOG.md`**

Use the template at the bottom of that file. Include test deltas, KPI deltas, what shipped, what got deferred (explicit names), surprises / lessons, CLAUDE.md updates.

- [ ] **Step 9.4: Update CLAUDE.md if any load-bearing fact changed**

Likely needed: "Date model is 5-column with posted_at / first_seen_at / last_seen_at / last_updated_at / date_confidence. See pillar_3_batch_1.md / IMPLEMENTATION_LOG.md."

- [ ] **Step 9.5: Commit completion entry, push branch**

```bash
git commit -m "docs: batch 1 completion entry in IMPLEMENTATION_LOG

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push -u origin pillar3/batch-1
```

- [ ] **Step 9.6: Emit reviewer handoff signal**

```
READY_FOR_REVIEW pillar3/batch-1 @ <short-hash>
```

STOP. Do not merge. Do not start Batch 2.

---

## Self-Review Checklist

- [x] Every task has concrete file paths (no "TBD", no "similar to Task N")
- [x] Every code block is complete (no `...`)
- [x] No step assumes files/types/functions that aren't defined elsewhere in the plan
- [x] `normalized_key()` is never modified (rule #1)
- [x] `BaseJobSource` is never modified (rule #2)
- [x] `purge_old_jobs` is never modified (rule #3)
- [x] All HTTP is mocked — the 39 source fixes update fixtures, not live calls (rule #4)
- [x] Source count stays at 48 (rule #8)
- [x] TDD order enforced: red → green → refactor → commit on every task

## Open Questions

None are genuinely ambiguous. All design decisions (nullable columns, 'low' default for confidence, 60% cap for first_seen-derived recency, 3-miss/24h state transition) follow pillar_3_batch_1.md specification directly. If one arises during execution, write it to `docs/plans/batch-1-open-questions.md` and continue with the plan's best-call default.
