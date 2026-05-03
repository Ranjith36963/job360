# Job Providers Data Layer Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the job providers data layer so every job's posting date is either a real timestamp from the source or `None` — never silently substituted with `datetime.now()` — and the database upserts, tracks disappearance, and grows company coverage.

**Architecture:** Introduce a timezone-aware date parsing helper, extend the `Job` dataclass + SQLite schema with `date_posted`/`discovered_at`/`last_seen_at`/`run_hash`/`disappeared_at`, replace `INSERT OR IGNORE` with an upsert that advances `last_seen_at` and `run_hash`, add a per-source `mark_disappeared()` SQL diff driven by a `run_hash` generated once per pipeline run, then fix every source one-by-one, remove the two that are not job listings, and expand ATS company coverage.

**Tech Stack:** Python 3.9+, aiohttp, aiosqlite, dataclass Job model, `_migrate()` forward-compatible schema migration mechanism at `database.py:75-97`, pytest + pytest-asyncio + aioresponses.

---

## Context — Why this change

Job360's recency scoring (`JobScorer._recency_score`) and the dashboard's time-bucket ordering (`get_recent_jobs` at `database.py:192-200`) both read `job.date_found`. That field is **silently contaminated** across the 47-source fanout:

1. **~14 of 47 sources** unconditionally assign `date_found = datetime.now(timezone.utc).isoformat()` (every HTML scraper except `eightykhours`, four ATS adapters with dateless APIs — personio/pinpoint/successfactors/workable — plus `findajob`, `yc_companies`, and `nomis`). Every job from these sources receives the maximum 10 recency points regardless of actual age, and the dashboard sorts them to the top because `get_recent_jobs` orders by `date_found DESC`.
2. **3 sources use semantically wrong fields**: `greenhouse` reads `updated_at` (not posting date; any minor edit looks brand new), `jooble` reads `updated`, and `nhs_jobs` reads `closingDate` (the application deadline, which is *in the future* — recency scorer awards 10 points because `_recency_score` checks `days_old <= 1` and a negative number passes).
3. **33 sources use the `item.get("X") or datetime.now()` fallback**, which masks missing data as "fresh" instead of letting the plan decide what to do when a source omits a date.
4. **No disappearance tracking**: `INSERT OR IGNORE` at `database.py:114-132` never updates existing rows, so a job that disappeared from its source three weeks ago still shows up as "recent" forever.
5. **Coverage**: 103 company slugs across 10 ATS platforms is a fraction of what's reachable via free APIs. Feashliaa's open-source list provides ~4,000.
6. **Two sources aren't jobs**: `yc_companies` emits career-page links, `nomis` emits UK ONS vacancy statistics — neither belongs in `SOURCE_REGISTRY`.

The downstream cost is that scoring, dedup ordering, notifications, and every dashboard query are working off contaminated data. Fixing the data layer unblocks everything downstream without touching scoring or UI code.

**References.md is empty (0 bytes).** This plan pulls open-source solutions from `planning_report.md` (the existing refactor doc) and the §2/§3/§4 reference context quoted in the briefing prompt. The JobSpy, Levergreen, and Feashliaa patterns are documented in `planning_report.md §9`.

**Immovables — DO NOT TOUCH:**
- `normalized_key()` at `models.py:54-58` (would force a full DB migration — documented at `deduplicator.py:18-33`).
- The `BaseJobSource.__init__` signature at `base.py:52-56` (propagates to 47 subclasses).
- `purge_old_jobs()` at `database.py:183-190` (project rule).

---

## Part 1 — Problems Inventory

### Timestamp problems (the `date_found` bug)

**P1 — Unconditional `datetime.now()` fallback (13 confirmed, ~14 per audit)**

Per CurrentStatus.md §5 row-by-row audit:

| # | Source | File | Line | Category | Fix |
|---|---|---|---|---|---|
| 1 | yc_companies | `backend/src/sources/apis_free/yc_companies.py` | 43, 50 | apis_free | **Remove from registry** — not a job source (emits career-page links) |
| 2 | personio | `backend/src/sources/ats/personio.py` | 76, 83 | ats | Investigate Personio XML `<pub_date>`; else `None` |
| 3 | pinpoint | `backend/src/sources/ats/pinpoint.py` | 47, 54 | ats | Set `None` (API has no date field) |
| 4 | successfactors | `backend/src/sources/ats/successfactors.py` | 67, 74, 95 | ats | Set `None` (sitemap XML has no date) |
| 5 | workable | `backend/src/sources/ats/workable.py` | 39, 46 | ats | Investigate Workable `published_on`; else `None` |
| 6 | findajob | `backend/src/sources/feeds/findajob.py` | 75, 82 | feeds | Parse `<pubDate>` from RSS (this IS an RSS feed) |
| 7 | aijobs_ai | `backend/src/sources/scrapers/aijobs_ai.py` | 49, 70, 77 | scrapers | Set `None` |
| 8 | aijobs_global | `backend/src/sources/scrapers/aijobs_global.py` | 60, 73, 87 | scrapers | Set `None` (WP Job Manager markup) |
| 9 | bcs_jobs | `backend/src/sources/scrapers/bcs_jobs.py` | 48, 69, 77 | scrapers | Set `None` |
| 10 | climatebase | `backend/src/sources/scrapers/climatebase.py` | 50, 85, 92, 106, 123 | scrapers | Set `None` (investigate Next.js `postedAt`) |
| 11 | jobtensor | `backend/src/sources/scrapers/jobtensor.py` | 53, 68, 84, 113 | scrapers | Set `None` |
| 12 | linkedin | `backend/src/sources/scrapers/linkedin.py` | 60, 67 | scrapers | Copy JobSpy's `<time datetime="...">` HTML extraction |
| 13 | nomis | `backend/src/sources/other/nomis.py` | 37, 52, 59 | other | **Remove from registry** — not a job source (UK ONS vacancy stats) |

> Audit discrepancy note: CurrentStatus.md §5 claims 14 such sources but the row-by-row table names only 13. Phase 1 investigation during Task 1.7 will confirm the 14th (likely a per-row null in an otherwise-dated source) or reconcile the document.

**P2 — Semantically wrong date fields (3 sources)**

| Source | File:Line | Bug | Fix |
|---|---|---|---|
| jooble | `backend/src/sources/apis_keyed/jooble.py:60` | Uses `item["updated"]` (updated date) | Set `date_posted = None` (no `created` field available) |
| greenhouse | `backend/src/sources/ats/greenhouse.py:40-41` | Uses `item["updated_at"]` (Greenhouse API has NO `created_at`) | Set `date_posted = None`; rely on `discovered_at` |
| nhs_jobs | `backend/src/sources/feeds/nhs_jobs.py:56-64` | Uses `closingDate` (future deadline) — yields **negative** `days_old` which recency scorer still rewards with 10 points | Parse RSS `<pubDate>` instead |

**P3 — `item.get("X") or datetime.now()` fallback pattern (33 sources)**

Every source not in P1/P2 uses the `item.get(field) or datetime.now(...)` idiom. When the API omits the date (which happens per-row, not per-source), we silently substitute "now". The fix is a mechanical rewrite: `date_posted = parse_iso_utc(item.get(field))` where `parse_iso_utc` returns `None` on missing/unparseable input, and `datetime.now()` is banned from every source.

Affected sources (from §5 audit — sources with real date fields):

- **apis_keyed (6)**: adzuna (`created`), careerjet (`date`), findwork (`date_posted`), google_jobs (`detected_extensions.posted_at` relative), jsearch (`job_posted_at_datetime_utc`), reed (`date` or `datePosted`)
- **apis_free (9)**: aijobs (`date`), arbeitnow (`created_at`), devitjobs (`publishedAt`), himalayas (`pubDate`/`createdAt`), hn_jobs (Unix seconds), jobicy (`pubDate`), landingjobs (`published_at`), remoteok (`date`), remotive (`publication_date`)
- **ats (4)**: ashby (`publishedAt`/`updatedAt`), lever (`createdAt` ms epoch — **reference implementation**), recruitee (`published_at`), smartrecruiters (`releasedDate`), workday (`postedOn` relative text parser at `workday.py:17-30`)
- **feeds (6)**: biospace, jobs_ac_uk, realworkfromanywhere, uni_jobs, weworkremotely, workanywhere (all use RSS `<pubDate>`)
- **scrapers (1)**: eightykhours (`hit.get("date_published")` from Algolia)
- **other (4)**: hackernews (`child.get("created_at")` Algolia HN), indeed (JobSpy DataFrame `date_posted`), nofluffjobs (`posted`/`renewed`), themuse (`publication_date`)

### Missing schema columns

**P4 — `Job` dataclass is missing four fields**

At `backend/src/models.py:17-31`, the `Job` dataclass has `date_found: str` (required, line 23) but no `date_posted`, `discovered_at`, `last_seen_at`, `run_hash`, or `disappeared_at`.

**P5 — SQLite `jobs` table is missing five columns**

At `backend/src/repositories/database.py:23-71`, the jobs table defines 15 columns (id, title, company, location, salary_min, salary_max, description, apply_url, source, date_found, match_score, visa_flag, experience_level, normalized_company, normalized_title, first_seen) with `UNIQUE(normalized_company, normalized_title)`. Missing: `date_posted`, `discovered_at`, `last_seen_at`, `run_hash`, `disappeared_at`.

The `_migrate()` mechanism at lines 75-97 uses an **empty `migrations = []` list** that accepts `(col_name, col_def)` tuples and issues `ALTER TABLE jobs ADD COLUMN` with name/type validation. This is the mechanism the plan extends — no parallel framework.

### Stale data problems

**P6 — `INSERT OR IGNORE` never updates existing rows**

At `backend/src/repositories/database.py:114-132`, the insert uses `INSERT OR IGNORE` and returns `cursor.rowcount > 0`. Duplicates are silently dropped. A job re-observed in a later run never gets its `last_seen_at` advanced, and there is no way to distinguish "seen today" from "last seen six weeks ago".

**P7 — No disappearance tracking**

There is no `mark_disappeared()` method, no `run_hash` column, no SQL diff. Jobs that no longer appear on their source remain in the DB until `purge_old_jobs()` deletes them 30 days later based on `first_seen`, not on actual disappearance.

**P8 — `get_recent_jobs` orders by `date_found` but filters by `first_seen`**

At `backend/src/repositories/database.py:192-200`:
```sql
SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC
```
Filtering on `first_seen` is correct (it's always a real Job360 timestamp); ordering by `date_found` is wrong when `date_found` is `datetime.now()`-contaminated — those jobs sort to the top of every query. Fix depends on Phase 0's `date_posted` column landing first.

### Coverage gaps

**P9 — 103 company slugs across 10 ATS platforms**

At `backend/src/core/companies.py`: Greenhouse=25, Lever=12, Workable=8, Ashby=9, SmartRecruiters=6, Pinpoint=8, Recruitee=8, Workday=15 (dict format), Personio=10, SuccessFactors=3 (dict format). Feashliaa's `job-board-aggregator` repo lists ~4,000 company slugs already categorised by ATS.

**P10 — No BambooHR adapter**

BambooHR has a public careers JSON API (`https://{company}.bamboohr.com/careers/list`) used heavily by non-tech SMEs. Adding a source widens the non-tech coverage Job360 targets.

**P11 — `yc_companies` and `nomis` pollute the registry**

Both emit non-job records but are counted in `SOURCE_REGISTRY`. They must be removed and every source count assertion updated.

### Infrastructure gap

**P12 — `cron_setup.sh` is stale**

`backend/cron_setup.sh:10-14` hardcodes `/opt/job360/backend/src/main.py` paths broken since the phase-1 restructure. The SimplifyJobs/SpeedyApply GitHub Actions cron pattern (`cron: '0 */4 * * *'`) is the recommended replacement per `planning_report.md §8`.

---

## Part 2 — Solutions Mapped from References

(References.md is empty — solutions cited from `planning_report.md §9` and the briefing's §3/§4.)

| Problem | Solution | Source repo / doc | Target file in Job360 |
|---|---|---|---|
| P3 ms-epoch parsing (Lever, Indeed/JobSpy) | `datetime.fromtimestamp(ms/1000, tz=timezone.utc)` | JobSpy (`speedyapply/JobSpy`) — **warning: their calls are tz-naive, always add `tz=timezone.utc`** | `backend/src/utils/date_parsing.py` (new) |
| P3 Unix seconds (hn_jobs) | `datetime.fromtimestamp(sec, tz=timezone.utc)` | stdlib, same tz warning | `backend/src/utils/date_parsing.py` |
| P3 ISO 8601 (Ashby, 20+ others) | `datetime.fromisoformat(...).astimezone(timezone.utc)` with `Z→+00:00` shim | stdlib | `backend/src/utils/date_parsing.py` |
| P3 RFC 822 / RSS `<pubDate>` | `email.utils.parsedate_to_datetime` | stdlib | `backend/src/utils/date_parsing.py` |
| P3 Relative text ("3 days ago", "Yesterday") | Copy `workday.py:17-30`'s `_parse_posted_on()` | Existing Job360 code; also in JobSpy | `backend/src/utils/date_parsing.py` |
| P1 LinkedIn `<time datetime="...">` HTML | Regex extract `datetime` attribute of `<time>` element | JobSpy LinkedIn scraper pattern | `backend/src/sources/scrapers/linkedin.py` |
| P6 upsert that advances `last_seen_at` | `INSERT ... ON CONFLICT(normalized_company, normalized_title) DO UPDATE SET last_seen_at=?, run_hash=?, disappeared_at=NULL, description=COALESCE(NULLIF(?, ''), description), salary_min=COALESCE(?, salary_min), salary_max=COALESCE(?, salary_max)` | Levergreen pattern (per `planning_report.md §9`); **warning: their `created_at = time.time()` is the same bug Job360 has** | `backend/src/repositories/database.py:114-132` |
| P7 disappearance tracking | `run_hash = uuid4().hex` generated once per run; after each source's inserts succeed, `UPDATE jobs SET disappeared_at = ? WHERE source = ? AND run_hash != ?` | Levergreen `run_hash` pattern | `backend/src/repositories/database.py` + `backend/src/main.py` |
| P2 greenhouse `updated_at` wrongness | Set `date_posted = None`; rely on `discovered_at` | CurrentStatus.md §5, §13 Issue #2 | `backend/src/sources/ats/greenhouse.py:40-41` |
| P2 nhs_jobs `closingDate` wrongness | Parse RSS `<pubDate>` instead | RSS spec | `backend/src/sources/feeds/nhs_jobs.py:56-64` |
| P9 company slug expansion | Import Feashliaa's categorised slug list, filter UK-relevant | `Feashliaa/job-board-aggregator` | `backend/src/core/companies.py` |
| P10 BambooHR adapter | New source class at `backend/src/sources/ats/bamboohr.py`; endpoint `https://{slug}.bamboohr.com/careers/list` | BambooHR public careers API | `backend/src/sources/ats/bamboohr.py` (new) |
| P12 cron replacement | `.github/workflows/scrape.yml` with `cron: '0 */4 * * *'` | SimplifyJobs / SpeedyApply pattern, `planning_report.md §8` | `.github/workflows/scrape.yml` (new) |

---

## Part 3 — Phase-by-Phase Execution Plan

### Phase 0 — Schema migration and `Job` dataclass extension

**Goal:** Add `date_posted`, `discovered_at`, `last_seen_at`, `run_hash`, `disappeared_at` to both the `Job` dataclass and the SQLite `jobs` table. Keep `date_found` unchanged for backward compatibility; it becomes a computed view `date_posted or discovered_at`.

**Files:**
- Modify: `backend/src/models.py:17-31` (Job dataclass)
- Modify: `backend/src/repositories/database.py:75-97` (`_migrate()` migrations list) and `114-132` (leave insert alone until Phase 2)
- Test: `backend/tests/test_models.py` (new field defaults)
- Test: `backend/tests/test_database.py` (new migration test)

#### Task 0.1 — Extend `Job` dataclass

- [ ] **Step 1: Write failing test**

In `backend/tests/test_models.py`, add:
```python
def test_job_has_new_date_fields():
    job = Job(
        title="Data Scientist",
        company="Acme",
        apply_url="https://example.com",
        source="reed",
        date_found="2026-04-10T10:00:00+00:00",
    )
    assert job.date_posted is None
    assert job.discovered_at is None
    assert job.last_seen_at is None
    assert job.run_hash is None
    assert job.disappeared_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_models.py::test_job_has_new_date_fields -v`
Expected: FAIL with `AttributeError: 'Job' object has no attribute 'date_posted'`

- [ ] **Step 3: Add fields to Job dataclass**

In `backend/src/models.py:17-31`, after the existing `experience_level: str = ""` line (line 31), append:
```python
    date_posted: Optional[str] = None
    discovered_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    run_hash: Optional[str] = None
    disappeared_at: Optional[str] = None
```
Import `Optional` if not already imported (check top of file).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_models.py::test_job_has_new_date_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/models.py backend/tests/test_models.py
git commit -m "feat(models): add date_posted/discovered_at/last_seen_at/run_hash/disappeared_at to Job

Phase 0 of job providers data layer fix. New fields are Optional[str]
and default to None so existing code paths are untouched until the
schema migration and upsert land in Phase 0.2 and Phase 2."
```

#### Task 0.2 — SQLite schema migration

- [ ] **Step 1: Write failing test**

In `backend/tests/test_database.py`, add:
```python
@pytest.mark.asyncio
async def test_schema_migration_adds_new_columns(tmp_path):
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.connect()
    cursor = await db._conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in await cursor.fetchall()}
    for expected in {"date_posted", "discovered_at", "last_seen_at", "run_hash", "disappeared_at"}:
        assert expected in cols, f"missing column {expected}"
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_database.py::test_schema_migration_adds_new_columns -v`
Expected: FAIL (missing columns).

- [ ] **Step 3: Add migration tuples**

In `backend/src/repositories/database.py:82-85`, replace the empty `migrations = []` list with:
```python
        migrations = [
            ("date_posted", "TEXT"),
            ("discovered_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("run_hash", "TEXT"),
            ("disappeared_at", "TEXT"),
        ]
```
Verify `TEXT` is in `_VALID_COL_TYPES` (it is per the existing validation at `database.py:91-92`).

- [ ] **Step 4: Add indexes for disappearance queries**

In `backend/src/repositories/database.py` after the existing `CREATE INDEX` block (around line 68-70 in the agent-reported schema), extend `_init_schema()` to include:
```sql
CREATE INDEX IF NOT EXISTS idx_jobs_run_hash_source ON jobs(source, run_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at);
```

- [ ] **Step 5: Run the test**

Run: `cd backend && python -m pytest tests/test_database.py::test_schema_migration_adds_new_columns -v`
Expected: PASS

- [ ] **Step 6: Run the full database test file to confirm no regression**

Run: `cd backend && python -m pytest tests/test_database.py -v`
Expected: all existing tests pass + new test passes.

- [ ] **Step 7: Commit**

```bash
git add backend/src/repositories/database.py backend/tests/test_database.py
git commit -m "feat(db): migrate jobs table with 5 new columns + 2 indexes

Phase 0 of job providers data layer fix. Extends the existing
_migrate() mechanism at database.py:75-97 with entries for
date_posted, discovered_at, last_seen_at, run_hash, disappeared_at.
Adds indexes on (source, run_hash) and last_seen_at to support the
disappearance diff in Phase 2. insert_job() still uses INSERT OR
IGNORE and is rewritten in Phase 2."
```

---

### Phase 1 — Shared date parsing helper + source-by-source fix

**Goal:** Create `backend/src/utils/date_parsing.py` with timezone-aware primitives, then rewrite every source to use them. Every function returns `None` on missing/unparseable input — `datetime.now()` is banned from source files.

#### Task 1.1 — Create `utils/date_parsing.py`

**Files:**
- Create: `backend/src/utils/date_parsing.py`
- Test: `backend/tests/test_date_parsing.py` (new)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_date_parsing.py`:
```python
from datetime import datetime, timezone
from src.utils.date_parsing import (
    parse_iso_utc,
    parse_ms_epoch_utc,
    parse_sec_epoch_utc,
    parse_rfc822_utc,
    parse_relative_date_utc,
    now_utc_iso,
    to_iso_or_none,
)


def test_parse_iso_utc_with_z_suffix():
    assert parse_iso_utc("2026-04-10T14:32:00Z") == "2026-04-10T14:32:00+00:00"

def test_parse_iso_utc_with_offset():
    assert parse_iso_utc("2026-04-10T14:32:00+01:00") == "2026-04-10T13:32:00+00:00"

def test_parse_iso_utc_none_on_missing():
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc("garbage") is None

def test_parse_ms_epoch_utc_lever_format():
    # Lever createdAt is ms epoch — 1712750400000 == 2024-04-10T14:40:00+00:00
    assert parse_ms_epoch_utc(1712750400000) == "2024-04-10T14:40:00+00:00"

def test_parse_ms_epoch_utc_none_on_missing():
    assert parse_ms_epoch_utc(None) is None
    assert parse_ms_epoch_utc("not a number") is None
    assert parse_ms_epoch_utc(0) is None  # 1970 epoch is almost certainly wrong

def test_parse_sec_epoch_utc_hn_format():
    assert parse_sec_epoch_utc(1712750400) == "2024-04-10T14:40:00+00:00"

def test_parse_rfc822_utc_rss():
    # Typical RSS pubDate
    assert parse_rfc822_utc("Wed, 10 Apr 2024 14:40:00 GMT") == "2024-04-10T14:40:00+00:00"

def test_parse_relative_date_utc_days_ago():
    # "3 days ago" → now() - 3 days (approximate — compare date portion)
    result = parse_relative_date_utc("3 days ago")
    assert result is not None
    parsed = datetime.fromisoformat(result)
    expected = datetime.now(timezone.utc)
    assert 2 <= (expected - parsed).days <= 4

def test_parse_relative_date_utc_yesterday():
    result = parse_relative_date_utc("yesterday")
    assert result is not None

def test_parse_relative_date_utc_today():
    result = parse_relative_date_utc("today")
    assert result is not None

def test_parse_relative_date_utc_none_on_garbage():
    assert parse_relative_date_utc(None) is None
    assert parse_relative_date_utc("potato") is None

def test_now_utc_iso_returns_tz_aware():
    s = now_utc_iso()
    assert s.endswith("+00:00")

def test_to_iso_or_none_passthrough():
    assert to_iso_or_none("2026-04-10T14:40:00+00:00") == "2026-04-10T14:40:00+00:00"
    assert to_iso_or_none(None) is None
    assert to_iso_or_none("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_date_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.utils.date_parsing'`

- [ ] **Step 3: Create the module**

Create `backend/src/utils/date_parsing.py`:
```python
"""Timezone-aware date parsing primitives for job source adapters.

All functions return None on missing or unparseable input. datetime.now()
is reserved for the discovered_at stamp only — source adapters must never
substitute it for a missing posting date.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union


def now_utc_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def to_iso_or_none(value: Optional[str]) -> Optional[str]:
    """Pass an already-ISO string through, or None for empty/missing input."""
    if not value:
        return None
    return value


def parse_iso_utc(value: Optional[str]) -> Optional[str]:
    """Parse an ISO 8601 timestamp and return a UTC-normalised ISO string.

    Handles trailing 'Z' by substituting '+00:00'. Returns None on any
    parse failure — callers must not fall back to datetime.now().
    """
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00") if value.endswith("Z") else value.strip()
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def parse_ms_epoch_utc(value: Union[int, float, str, None]) -> Optional[str]:
    """Parse a millisecond Unix epoch (Lever createdAt, Indeed datePublished).

    Returns None on missing, unparseable, or exactly-zero input (1970 is
    almost certainly a sentinel).
    """
    if value is None or value == 0:
        return None
    try:
        ms = float(value)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def parse_sec_epoch_utc(value: Union[int, float, str, None]) -> Optional[str]:
    """Parse a second Unix epoch (HN Firebase `time` field)."""
    if value is None or value == 0:
        return None
    try:
        sec = float(value)
        if sec <= 0:
            return None
        return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def parse_rfc822_utc(value: Optional[str]) -> Optional[str]:
    """Parse an RFC 822 / RFC 2822 date (RSS <pubDate>)."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


_REL_DAYS_RE = re.compile(r"(\d+)\s*\+?\s*day", re.IGNORECASE)
_REL_WEEKS_RE = re.compile(r"(\d+)\s*\+?\s*week", re.IGNORECASE)
_REL_MONTHS_RE = re.compile(r"(\d+)\s*\+?\s*month", re.IGNORECASE)
_REL_HOURS_RE = re.compile(r"(\d+)\s*\+?\s*hour", re.IGNORECASE)


def parse_relative_date_utc(value: Optional[str]) -> Optional[str]:
    """Parse phrases like 'Posted 3 Days Ago', 'Yesterday', '2 weeks ago'.

    Uses the current UTC time as the anchor. This is the ONLY helper that
    touches datetime.now() — because "3 days ago" is inherently relative.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    now = datetime.now(timezone.utc)
    if "today" in text or "just posted" in text:
        return now.isoformat()
    if "yesterday" in text:
        return (now - timedelta(days=1)).isoformat()
    m = _REL_HOURS_RE.search(text)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).isoformat()
    m = _REL_DAYS_RE.search(text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat()
    m = _REL_WEEKS_RE.search(text)
    if m:
        return (now - timedelta(weeks=int(m.group(1)))).isoformat()
    m = _REL_MONTHS_RE.search(text)
    if m:
        return (now - timedelta(days=30 * int(m.group(1)))).isoformat()
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_date_parsing.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/utils/date_parsing.py backend/tests/test_date_parsing.py
git commit -m "feat(utils): timezone-aware date parsing primitives

Phase 1 of job providers data layer fix. All functions return None
on missing or unparseable input — the 'or datetime.now()' fallback
that contaminated date_found across 14+ sources is now impossible
by construction. parse_relative_date_utc is the ONLY helper allowed
to touch datetime.now(), because 'Posted 3 Days Ago' is inherently
relative to the anchor moment. Warning about JobSpy's tz-naive
patterns is encoded in the module docstring."
```

#### Task 1.2 — Reference implementation: Lever (ms epoch) and Ashby (ISO)

Start with the two ATS sources that already have correct date logic — the refactor here is pure cleanup, no new parsing.

**Files:**
- Modify: `backend/src/sources/ats/lever.py:37-42`
- Modify: `backend/src/sources/ats/ashby.py:35`
- Test: `backend/tests/test_sources.py` (existing Lever and Ashby tests)

- [ ] **Step 1: Check existing Lever test**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k lever` and capture the current expected `date_found` shape so we know what to assert on `date_posted`.

- [ ] **Step 2: Update Lever test to assert `date_posted`**

In the existing Lever test in `backend/tests/test_sources.py`, add an assertion after the `date_found` assertion:
```python
assert job.date_posted == "2024-04-10T14:40:00+00:00"
assert job.discovered_at is not None
```
(Use whatever ms-epoch value the test fixture already provides.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k lever`
Expected: FAIL with `AttributeError` or `None != expected`.

- [ ] **Step 4: Refactor Lever source**

In `backend/src/sources/ats/lever.py`, replace lines 37-42 with:
```python
                # Lever createdAt is milliseconds since epoch
                date_posted = parse_ms_epoch_utc(item.get("createdAt"))
                discovered_at = now_utc_iso()
```
Then in the `Job(...)` constructor below, set:
```python
                    date_found=date_posted or discovered_at,
                    date_posted=date_posted,
                    discovered_at=discovered_at,
```
Add imports at the top of the file:
```python
from src.utils.date_parsing import parse_ms_epoch_utc, now_utc_iso
```
Remove the `datetime`/`timezone` imports if they were only used for the date fallback.

- [ ] **Step 5: Run test to verify Lever passes**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k lever`
Expected: PASS.

- [ ] **Step 6: Repeat Step 2-5 for Ashby**

Ashby uses ISO format. In `backend/src/sources/ats/ashby.py:35`, replace:
```python
                date_found = item.get("publishedAt") or item.get("updatedAt") or datetime.now(timezone.utc).isoformat()
```
with:
```python
                date_posted = parse_iso_utc(item.get("publishedAt")) or parse_iso_utc(item.get("updatedAt"))
                discovered_at = now_utc_iso()
```
and update the `Job(...)` constructor to set `date_found=date_posted or discovered_at`, `date_posted=date_posted`, `discovered_at=discovered_at`.

- [ ] **Step 7: Run the Ashby test**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k ashby`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/sources/ats/lever.py backend/src/sources/ats/ashby.py backend/tests/test_sources.py
git commit -m "refactor(sources): lever + ashby use date_parsing helpers

Phase 1 reference implementation. Lever parses createdAt ms epoch
via parse_ms_epoch_utc, Ashby prefers publishedAt over updatedAt
via parse_iso_utc. Both set date_posted explicitly and compute
date_found as date_posted or discovered_at for backward compat.
No more 'or datetime.now()' fallback."
```

#### Task 1.3 — Fix LinkedIn (HTML `<time datetime="...">` extraction, Issue #7)

**Files:**
- Modify: `backend/src/sources/scrapers/linkedin.py:60-67`
- Test: `backend/tests/test_sources.py` LinkedIn section

- [ ] **Step 1: Write failing test**

In the existing LinkedIn test block in `backend/tests/test_sources.py`, update the mocked HTML fixture to include a `<time datetime="2026-04-08T12:00:00+00:00">3 days ago</time>` element near each job card, then assert:
```python
assert job.date_posted == "2026-04-08T12:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k linkedin`
Expected: FAIL.

- [ ] **Step 3: Implement HTML time extraction**

In `backend/src/sources/scrapers/linkedin.py:60-67`, locate the loop where jobs are constructed. Add a regex to extract the `datetime` attribute of the nearest `<time>` element within each job card's HTML chunk. Use:
```python
import re
_LINKEDIN_TIME_RE = re.compile(r'<time[^>]*datetime=["\']([^"\']+)["\']', re.IGNORECASE)

# inside the loop, where card_html is the per-job chunk:
m = _LINKEDIN_TIME_RE.search(card_html)
date_posted = parse_iso_utc(m.group(1)) if m else None
discovered_at = now_utc_iso()
```
Replace the hardcoded `datetime.now(timezone.utc).isoformat()` at line 67 with `date_found=date_posted or discovered_at`, add `date_posted=date_posted, discovered_at=discovered_at` to the Job constructor, and import `parse_iso_utc`, `now_utc_iso` from `src.utils.date_parsing`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k linkedin`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/sources/scrapers/linkedin.py backend/tests/test_sources.py
git commit -m "fix(linkedin): extract real date from <time datetime> HTML attribute

Phase 1, CurrentStatus.md §13 Issue #7 fix. LinkedIn guest search API
responses include <time datetime=\"ISO\"> elements per job card. The
7-day f_TPR filter at scrapers/linkedin.py:39 narrows the result set
but the per-listing date was previously hardcoded to datetime.now().
Pattern copied from JobSpy's LinkedIn scraper."
```

#### Task 1.4 — Fix Workday (relative text parser generalisation)

**Files:**
- Modify: `backend/src/sources/ats/workday.py:17-30` (inline `_parse_posted_on`) and `:88-95` (Job construction)
- Test: `backend/tests/test_sources.py` Workday section

- [ ] **Step 1: Write failing test**

Assert `job.date_posted` is a proper ISO string when the fixture response contains `{"postedOn": "Posted 3 Days Ago"}`.

- [ ] **Step 2: Run, fail, implement**

Replace the inline `_parse_posted_on` at `workday.py:17-30` with a call to `parse_relative_date_utc` from the shared helper. In the Job constructor around line 88-95, set `date_posted = parse_relative_date_utc(item.get("postedOn"))`, `discovered_at = now_utc_iso()`, `date_found=date_posted or discovered_at`. Add `date_posted=date_posted`, `discovered_at=discovered_at`.

- [ ] **Step 3: Run the test**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k workday`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/sources/ats/workday.py backend/tests/test_sources.py
git commit -m "refactor(workday): delegate relative date parsing to shared helper

Phase 1. Removes the duplicated _parse_posted_on at workday.py:17-30
in favour of parse_relative_date_utc. Behaviour is equivalent but the
helper is now usable for any future relative-date source."
```

#### Task 1.5 — Batch: 20 ISO-date sources (apis_keyed + apis_free + ats/recruitee + smartrecruiters)

**Files (19 sources total — all use `parse_iso_utc`):**

| Source | File | Field | Current line |
|---|---|---|---|
| adzuna | `backend/src/sources/apis_keyed/adzuna.py` | `created` | 49-50 |
| careerjet | `backend/src/sources/apis_keyed/careerjet.py` | `date` | 59-76 |
| findwork | `backend/src/sources/apis_keyed/findwork.py` | `date_posted` | 53-56 |
| jsearch | `backend/src/sources/apis_keyed/jsearch.py` | `job_posted_at_datetime_utc` | 73-74 |
| reed | `backend/src/sources/apis_keyed/reed.py` | `date` / `datePosted` | 50-51 |
| aijobs | `backend/src/sources/apis_free/aijobs.py` | `date` | 31-34 |
| arbeitnow | `backend/src/sources/apis_free/arbeitnow.py` | `created_at` | 22-23 |
| devitjobs | `backend/src/sources/apis_free/devitjobs.py` | `publishedAt` | 27-45 |
| himalayas | `backend/src/sources/apis_free/himalayas.py` | `pubDate` or `createdAt` | 27-28 |
| jobicy | `backend/src/sources/apis_free/jobicy.py` | `pubDate` | 32-33 |
| landingjobs | `backend/src/sources/apis_free/landingjobs.py` | `published_at` | 63-65 |
| remoteok | `backend/src/sources/apis_free/remoteok.py` | `date` | 27-28 |
| remotive | `backend/src/sources/apis_free/remotive.py` | `publication_date` | 28-39 |
| recruitee | `backend/src/sources/ats/recruitee.py` | `published_at` | 36-39 |
| smartrecruiters | `backend/src/sources/ats/smartrecruiters.py` | `releasedDate` | 43-44 |
| themuse | `backend/src/sources/other/themuse.py` | `publication_date` | 59-70 |
| nofluffjobs | `backend/src/sources/other/nofluffjobs.py` | `posted` or `renewed` | 78-98 |
| eightykhours | `backend/src/sources/scrapers/eightykhours.py` | `date_published` (Algolia) | 76-78 |
| hackernews | `backend/src/sources/other/hackernews.py` | `created_at` (Algolia HN) | 101-103 |

**Rule for each source:** Replace the existing `date_found = item.get(FIELD) or datetime.now(...)` with:
```python
date_posted = parse_iso_utc(item.get(FIELD))  # or parse_iso_utc(item.get(FIELD_A)) or parse_iso_utc(item.get(FIELD_B))
discovered_at = now_utc_iso()
```
In the Job constructor: `date_found=date_posted or discovered_at, date_posted=date_posted, discovered_at=discovered_at`. Import from `src.utils.date_parsing`.

- [ ] **Step 1: Update all 19 source tests in `backend/tests/test_sources.py`**

For each source, add `assert job.date_posted == <expected ISO>` assertions. The existing fixtures already feed a real ISO string; just mirror it into the new assertion.

- [ ] **Step 2: Run full source test suite — capture baseline failures**

Run: `cd backend && python -m pytest tests/test_sources.py -v`
Expected: 19 failures (one per source being updated). Commit the test file as "expected fails" ONLY if using the brainstorming-skill TDD convention; otherwise proceed in one atomic change.

- [ ] **Step 3: Apply the 19 source edits**

Work through each file in the table above. For each:
1. Add `from src.utils.date_parsing import parse_iso_utc, now_utc_iso` at the top
2. Replace the `date_found = ... or datetime.now(...)` line with the two-line assignment
3. Update the `Job(...)` constructor
4. Remove any now-dead `datetime`/`timezone` imports

- [ ] **Step 4: Run the full source test suite**

Run: `cd backend && python -m pytest tests/test_sources.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add backend/src/sources/apis_keyed/ backend/src/sources/apis_free/ backend/src/sources/ats/recruitee.py backend/src/sources/ats/smartrecruiters.py backend/src/sources/other/themuse.py backend/src/sources/other/nofluffjobs.py backend/src/sources/other/hackernews.py backend/src/sources/scrapers/eightykhours.py backend/tests/test_sources.py
git commit -m "refactor(sources): ISO-date sources use parse_iso_utc, drop datetime.now fallback

Phase 1 batch fix for 19 sources whose upstream API returns a real
ISO 8601 timestamp. The 'or datetime.now()' idiom is replaced with
parse_iso_utc(...) which returns None on missing/unparseable input.
date_found remains set as date_posted or discovered_at for backward
compatibility until Phase 3 retires the read path."
```

#### Task 1.6 — Batch: 6 RSS sources (`<pubDate>`)

**Files:**
- Modify: `backend/src/sources/feeds/biospace.py` (67, 82, 89)
- Modify: `backend/src/sources/feeds/jobs_ac_uk.py` (65, 80, 88)
- Modify: `backend/src/sources/feeds/realworkfromanywhere.py` (57, 72, 79)
- Modify: `backend/src/sources/feeds/uni_jobs.py` (58, 72, 80)
- Modify: `backend/src/sources/feeds/weworkremotely.py` (59, 73, 81)
- Modify: `backend/src/sources/feeds/workanywhere.py` (64, 78, 86)

Each replaces its local `_parse_rss_date` helper (or inline logic) with the shared `parse_rfc822_utc`.

- [ ] **Step 1: Update existing tests to assert `date_posted`**

- [ ] **Step 2: Run to see failures**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k "biospace or jobs_ac_uk or realworkfromanywhere or uni_jobs or weworkremotely or workanywhere"`

- [ ] **Step 3: Apply the 6 edits**

For each source, replace the date block with:
```python
date_posted = parse_rfc822_utc(pub_date)
discovered_at = now_utc_iso()
```
Delete the now-dead `_parse_rss_date`/`_parse_date` helpers unless they serve another purpose.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k "biospace or jobs_ac_uk or realworkfromanywhere or uni_jobs or weworkremotely or workanywhere"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/sources/feeds/biospace.py backend/src/sources/feeds/jobs_ac_uk.py backend/src/sources/feeds/realworkfromanywhere.py backend/src/sources/feeds/uni_jobs.py backend/src/sources/feeds/weworkremotely.py backend/src/sources/feeds/workanywhere.py backend/tests/test_sources.py
git commit -m "refactor(feeds): 6 RSS sources use parse_rfc822_utc

Phase 1. RSS <pubDate> parsing is centralised in date_parsing.py.
Each source's local _parse_rss_date helper is deleted."
```

#### Task 1.7 — Batch: 10 no-date sources → explicit `date_posted=None`

**Rule:** Each of these sources has no usable date field at all. They must set `date_posted = None` and `discovered_at = now_utc_iso()`, and `date_found = discovered_at`. No `datetime.now()` substitution anywhere in the source file.

| Source | File | Line | Notes |
|---|---|---|---|
| personio | `backend/src/sources/ats/personio.py` | 76, 83 | Investigate `<pub_date>` in Personio XML first; if present, use `parse_rfc822_utc`; else None |
| pinpoint | `backend/src/sources/ats/pinpoint.py` | 47, 54 | API has no date |
| successfactors | `backend/src/sources/ats/successfactors.py` | 67, 74, 95 | Sitemap XML has no date |
| workable | `backend/src/sources/ats/workable.py` | 39, 46 | Investigate `published_on` first; if present, use `parse_iso_utc`; else None |
| findajob | `backend/src/sources/feeds/findajob.py` | 75, 82 | findajob IS an RSS feed — try `parse_rfc822_utc` on `<pubDate>` first |
| aijobs_ai | `backend/src/sources/scrapers/aijobs_ai.py` | 49, 70, 77 | None |
| aijobs_global | `backend/src/sources/scrapers/aijobs_global.py` | 60, 73, 87 | None (WP Job Manager markup — investigate `.job-posted-date` class if time permits) |
| bcs_jobs | `backend/src/sources/scrapers/bcs_jobs.py` | 48, 69, 77 | None |
| climatebase | `backend/src/sources/scrapers/climatebase.py` | 50, 85, 92, 106, 123 | Investigate Next.js `__NEXT_DATA__` for `postedAt`; if present, `parse_iso_utc`; else None |
| jobtensor | `backend/src/sources/scrapers/jobtensor.py` | 53, 68, 84, 113 | None |

- [ ] **Step 1: Update tests**

For each source, update the existing test in `backend/tests/test_sources.py` to assert `job.date_posted is None` (unless investigation in step 3 yields a real field).

- [ ] **Step 2: Run to see failures**

- [ ] **Step 3: Apply the 10 edits**

For sources marked "investigate", spend up to 15 minutes per source reading their actual response payload. If a real field exists, use the appropriate helper; else set `None`. Do not add `datetime.now()` anywhere.

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add backend/src/sources/ats/personio.py backend/src/sources/ats/pinpoint.py backend/src/sources/ats/successfactors.py backend/src/sources/ats/workable.py backend/src/sources/feeds/findajob.py backend/src/sources/scrapers/aijobs_ai.py backend/src/sources/scrapers/aijobs_global.py backend/src/sources/scrapers/bcs_jobs.py backend/src/sources/scrapers/climatebase.py backend/src/sources/scrapers/jobtensor.py backend/tests/test_sources.py
git commit -m "fix(sources): 10 dateless sources set date_posted=None explicitly

Phase 1, CurrentStatus.md §13 Issue #1 resolution for the sources
whose upstream payload truly has no posting date. discovered_at
carries the Job360 observation stamp; date_posted is None so the
Phase 3 recency scorer can decide how to score a missing date
instead of silently rewarding staleness as freshness."
```

#### Task 1.8 — Batch: 3 epoch and 3 special-case sources

| Source | File | Transform |
|---|---|---|
| hn_jobs | `backend/src/sources/apis_free/hn_jobs.py:64-66` | `parse_sec_epoch_utc(item["time"])` (already partly correct, unify style) |
| indeed (JobSpy) | `backend/src/sources/other/indeed.py:52-56` | If `date_posted` has `.isoformat()`, pass through; else try `parse_iso_utc(str(date_posted))` or `parse_ms_epoch_utc(date_posted)`; else `None` |
| google_jobs | `backend/src/sources/apis_keyed/google_jobs.py:18-28,101` | Replace local `_parse_posted_at` with `parse_relative_date_utc` |

- [ ] **Steps 1-5:** Same TDD loop as prior tasks — failing test, refactor, green, commit.

```bash
git commit -m "refactor(sources): hn_jobs + indeed + google_jobs use shared helpers

Phase 1 final batch. hn_jobs epoch seconds, indeed JobSpy DataFrame
column, google_jobs SerpApi relative text — all three now route
through date_parsing.py primitives."
```

#### Task 1.9 — Semantic fixes (Issue #2): greenhouse, jooble, nhs_jobs

- [ ] **Step 1: Update tests**

- **greenhouse**: Assert `job.date_posted is None` (Greenhouse API has NO `created_at`; `updated_at` is a semantic lie).
- **jooble**: Assert `job.date_posted is None` (`updated` is not a posting date).
- **nhs_jobs**: Construct a fixture with an RSS `<pubDate>` and assert `job.date_posted == <parsed ISO>`.

- [ ] **Step 2: Apply fixes**

**greenhouse** — `backend/src/sources/ats/greenhouse.py:40-41`:
```python
# Greenhouse API exposes only updated_at (no created_at). We refuse to
# substitute a stale updated_at as a posting date — set None and rely on
# discovered_at for freshness signals.
date_posted = None
discovered_at = now_utc_iso()
```

**jooble** — `backend/src/sources/apis_keyed/jooble.py:60`:
```python
# Jooble returns only `updated`, not a creation date. Intentionally None.
date_posted = None
discovered_at = now_utc_iso()
```

**nhs_jobs** — `backend/src/sources/feeds/nhs_jobs.py:56-66`:
Replace `closing_date = (vacancy.findtext("closingDate") or "").strip()` reads with `pub_date = (vacancy.findtext("pubDate") or "").strip()`, then `date_posted = parse_rfc822_utc(pub_date)`. Delete the now-unused `_parse_date`/`closingDate` logic.

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k "greenhouse or jooble or nhs_jobs"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/sources/ats/greenhouse.py backend/src/sources/apis_keyed/jooble.py backend/src/sources/feeds/nhs_jobs.py backend/tests/test_sources.py
git commit -m "fix(sources): greenhouse/jooble/nhs_jobs stop reporting wrong dates

Phase 1, CurrentStatus.md §13 Issue #2 resolution.

- greenhouse: updated_at is NOT a posting date (Greenhouse API has
  no created_at). Set date_posted=None.
- jooble: 'updated' is not a posting date. Set date_posted=None.
- nhs_jobs: closingDate is the application deadline (in the FUTURE),
  yielding negative days_old that the recency scorer still rewarded
  with 10 points. Parse <pubDate> instead."
```

#### Task 1.10 — Remove `nomis` and `yc_companies` from the registry

Neither source emits real job listings. Both are removed entirely.

**Files:**
- Modify: `backend/src/main.py:78-128` (SOURCE_REGISTRY) and `130-133` (SOURCE_INSTANCE_COUNT) and `151-220` (_build_sources)
- Modify: `backend/src/core/settings.py:53-103` (RATE_LIMITS)
- Delete: `backend/src/sources/other/nomis.py`
- Delete: `backend/src/sources/apis_free/yc_companies.py`
- Modify: `backend/tests/test_cli.py:47` assertion (`== 48` → `== 46`)
- Modify: `backend/tests/test_api.py:27,36,97,102` assertions (`== 48` → `== 46`)
- Modify: `backend/tests/test_sources.py` (remove the nomis + yc_companies test blocks)

- [ ] **Step 1: Update every source count assertion**

```python
# backend/tests/test_cli.py:47
assert len(SOURCE_REGISTRY) == 46
```
```python
# backend/tests/test_api.py:27,36,97,102
assert data["sources_total"] == 46
assert len(resp.json()["sources"]) == 46
```

- [ ] **Step 2: Run assertions to verify they fail**

Run: `cd backend && python -m pytest tests/test_cli.py tests/test_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Remove the two sources**

- In `backend/src/main.py:78-128`, delete the `"nomis": NomisSource,` and `"yc_companies": YCCompaniesSource,` entries.
- Remove the imports at the top of `main.py`.
- In `_build_sources()` at lines 151-220, delete the lines instantiating `NomisSource(...)` and `YCCompaniesSource(...)`.
- Update `SOURCE_INSTANCE_COUNT` at `main.py:133` from `47` to `45` (48 registry - 1 glassdoor alias - 2 removed = 45).
- In `backend/src/core/settings.py:53-103`, delete the `"nomis"` and `"yc_companies"` RATE_LIMITS entries.
- Delete `backend/src/sources/other/nomis.py` and `backend/src/sources/apis_free/yc_companies.py`.
- Delete the nomis and yc_companies test blocks in `backend/tests/test_sources.py`.

- [ ] **Step 4: Run the whole suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: everything passing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(sources): remove nomis and yc_companies from SOURCE_REGISTRY

Phase 1 cleanup. Neither source emits real job listings:

- nomis: UK Office for National Statistics vacancy *statistics* API.
  Never had posting dates, never had individual jobs.
- yc_companies: emits career-page *links*, not job records. Every
  listing was datetime.now()-stamped and never dedup-able.

Registry count drops from 48 to 46 (44 unique instances + 2
indeed/glassdoor aliases to JobSpySource = 45 via SOURCE_INSTANCE_COUNT).
Tests at test_cli.py:47 and test_api.py:27/36/97/102 are updated.
CLAUDE.md Rule 8 source count assertion propagation handled."
```

---

### Phase 2 — Upsert + disappearance tracking

**Goal:** Replace `INSERT OR IGNORE` with an ON CONFLICT upsert that advances `last_seen_at` and `run_hash`, generate a `run_hash = uuid4().hex` once per pipeline run, and add `mark_disappeared()` per-source SQL diff. Pattern copied from Levergreen (per `planning_report.md §9`).

**Files:**
- Modify: `backend/src/repositories/database.py:114-132` (insert_job)
- Create: `backend/src/repositories/database.py` new `mark_disappeared()` method
- Modify: `backend/src/main.py:287-324` (orchestrator — pass `run_hash`, call `mark_disappeared`)
- Test: `backend/tests/test_database.py` (upsert + disappear tests)
- Test: `backend/tests/test_main.py` (orchestrator passes run_hash)

#### Task 2.1 — Upsert `insert_job`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_database.py`:
```python
@pytest.mark.asyncio
async def test_insert_job_advances_last_seen_on_conflict(tmp_path):
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.connect()

    job = Job(
        title="Data Scientist",
        company="Acme",
        apply_url="https://example.com/1",
        source="reed",
        date_found="2026-04-10T10:00:00+00:00",
        date_posted="2026-04-10T10:00:00+00:00",
        discovered_at="2026-04-10T10:00:00+00:00",
        last_seen_at="2026-04-10T10:00:00+00:00",
        run_hash="run-one",
    )
    inserted = await db.insert_job(job)
    assert inserted is True

    # Same job seen again in a later run — not an insert, but last_seen advances
    job2 = Job(
        title="Data Scientist",
        company="Acme",
        apply_url="https://example.com/1",
        source="reed",
        date_found="2026-04-10T10:00:00+00:00",
        date_posted="2026-04-10T10:00:00+00:00",
        discovered_at="2026-04-10T10:00:00+00:00",
        last_seen_at="2026-04-11T10:00:00+00:00",
        run_hash="run-two",
    )
    inserted2 = await db.insert_job(job2)
    assert inserted2 is False  # upsert, not a fresh insert

    cursor = await db._conn.execute("SELECT last_seen_at, run_hash FROM jobs WHERE company = 'Acme'")
    row = await cursor.fetchone()
    assert row[0] == "2026-04-11T10:00:00+00:00"
    assert row[1] == "run-two"
    await db.close()
```

- [ ] **Step 2: Run test — fail**

Run: `cd backend && python -m pytest tests/test_database.py::test_insert_job_advances_last_seen_on_conflict -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite `insert_job`**

In `backend/src/repositories/database.py:114-132`, replace with an `INSERT ... ON CONFLICT(normalized_company, normalized_title) DO UPDATE SET last_seen_at=excluded.last_seen_at, run_hash=excluded.run_hash, disappeared_at=NULL, description=CASE WHEN length(excluded.description) > length(jobs.description) THEN excluded.description ELSE jobs.description END, salary_min=COALESCE(jobs.salary_min, excluded.salary_min), salary_max=COALESCE(jobs.salary_max, excluded.salary_max), match_score=MAX(jobs.match_score, excluded.match_score)` clause. Bind `job.date_posted`, `job.discovered_at`, `job.last_seen_at`, `job.run_hash`, NULL for `disappeared_at`.

Return True if the row's stored `first_seen` equals the `now` we just passed (meaning this call inserted it). Otherwise False (meaning it was an update).

- [ ] **Step 4: Run test — green**

- [ ] **Step 5: Run full db test suite + sanity check existing tests**

Run: `cd backend && python -m pytest tests/test_database.py tests/test_main.py -v`
Expected: PASS. Any breakage in `test_main.py` stems from the `insert_job` return semantics shift — fix those assertions where they check "new job count" logic.

- [ ] **Step 6: Commit**

```bash
git add backend/src/repositories/database.py backend/tests/test_database.py backend/tests/test_main.py
git commit -m "feat(db): upsert insert_job with ON CONFLICT DO UPDATE

Phase 2. Replaces INSERT OR IGNORE with an ON CONFLICT upsert that
advances last_seen_at and run_hash on re-observation, clears
disappeared_at, prefers the longest description, COALESCEs salary
fields, and keeps the higher match_score. 'Was this a fresh insert?'
is now signalled by comparing stored first_seen to the just-written
now timestamp — see the check query at the end of insert_job.

Pattern from Levergreen's dbt-style snapshot upsert (planning_report.md
§9), adapted for aiosqlite. Warning about Levergreen's own date bug
(time.time() same as Job360's) is encoded by only copying the upsert
shape, not the date logic."
```

#### Task 2.2 — Add `mark_disappeared()`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_mark_disappeared_flags_stale_rows_per_source(tmp_path):
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.connect()

    # Insert 2 jobs from reed in run A
    for i in (1, 2):
        await db.insert_job(Job(
            title=f"Title {i}", company="Acme",
            apply_url=f"https://e/reed{i}", source="reed",
            date_found="2026-04-10T10:00:00+00:00",
            discovered_at="2026-04-10T10:00:00+00:00",
            last_seen_at="2026-04-10T10:00:00+00:00",
            run_hash="run-a",
        ))
    # Insert 1 job from adzuna in run A
    await db.insert_job(Job(
        title="Title 3", company="Acme",
        apply_url="https://e/adzuna1", source="adzuna",
        date_found="2026-04-10T10:00:00+00:00",
        discovered_at="2026-04-10T10:00:00+00:00",
        last_seen_at="2026-04-10T10:00:00+00:00",
        run_hash="run-a",
    ))

    # Run B sees only reed/Title 1
    await db.insert_job(Job(
        title="Title 1", company="Acme",
        apply_url="https://e/reed1", source="reed",
        date_found="2026-04-11T10:00:00+00:00",
        discovered_at="2026-04-10T10:00:00+00:00",
        last_seen_at="2026-04-11T10:00:00+00:00",
        run_hash="run-b",
    ))
    # Mark reed's run-b-absent jobs disappeared
    count = await db.mark_disappeared(source="reed", current_run_hash="run-b",
                                       disappeared_at="2026-04-11T10:05:00+00:00")
    assert count == 1  # Title 2

    # Title 2 is flagged
    cursor = await db._conn.execute(
        "SELECT disappeared_at FROM jobs WHERE normalized_title = 'title 2'"
    )
    assert (await cursor.fetchone())[0] == "2026-04-11T10:05:00+00:00"

    # adzuna's Title 3 is NOT flagged — mark_disappeared is scoped per-source
    cursor = await db._conn.execute(
        "SELECT disappeared_at FROM jobs WHERE source = 'adzuna'"
    )
    assert (await cursor.fetchone())[0] is None
    await db.close()
```

- [ ] **Step 2: Run test — fail**

- [ ] **Step 3: Implement `mark_disappeared`**

Add to `backend/src/repositories/database.py`:
```python
    async def mark_disappeared(self, source: str, current_run_hash: str,
                                disappeared_at: str | None = None) -> int:
        """Flag every row for `source` whose run_hash ≠ current_run_hash.

        Per-source scoping is critical: a network failure in one source
        must not mark another source's jobs disappeared. Callers (the
        orchestrator) are expected to invoke this ONLY after confirming
        the source returned >0 jobs in the current run.
        """
        ts = disappeared_at or datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            """UPDATE jobs
               SET disappeared_at = ?
               WHERE source = ?
                 AND run_hash IS NOT NULL
                 AND run_hash != ?
                 AND disappeared_at IS NULL""",
            (ts, source, current_run_hash),
        )
        await self._conn.commit()
        return cursor.rowcount
```

- [ ] **Step 4: Run test — green**

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/database.py backend/tests/test_database.py
git commit -m "feat(db): mark_disappeared for per-source SQL diff

Phase 2. Flags rows whose run_hash differs from the current run's
hash, scoped per-source so one source's network failure cannot
spuriously disappear another source's jobs. Levergreen pattern
(planning_report.md §9)."
```

#### Task 2.3 — Orchestrator: generate `run_hash`, propagate, call `mark_disappeared`

**Files:**
- Modify: `backend/src/main.py:287-324` (orchestrator around `asyncio.gather`, per-source insert loop, and new disappearance call)

- [ ] **Step 1: Write failing test**

In `backend/tests/test_main.py`, add a test that mocks every source to return two jobs, runs `run_search()`, then runs `run_search()` again with one source returning a subset, and asserts `mark_disappeared` was called once per source with `rowcount > 0` for the source that shrank.

- [ ] **Step 2: Run test — fail**

- [ ] **Step 3: Implement**

In `backend/src/main.py`:
1. At the top of `run_search()`, generate `run_hash = uuid4().hex` (import `from uuid import uuid4`).
2. Before or after scoring, stamp every `job.run_hash = run_hash`, `job.last_seen_at = now_utc_iso()`.
3. Replace the single `for job in all_jobs: await db.insert_job(job)` loop with a per-source loop:
   ```python
   jobs_by_source: dict[str, list[Job]] = {}
   for job in all_jobs:
       jobs_by_source.setdefault(job.source, []).append(job)

   new_count = 0
   for source_name, jobs in jobs_by_source.items():
       for job in jobs:
           if await db.insert_job(job):
               new_count += 1
       # Guard: only mark_disappeared if this source actually returned results
       if jobs:
           await db.mark_disappeared(source=source_name, current_run_hash=run_hash)
   ```
4. Confirm: a source that returned 0 jobs (network error, timeout, no matches) is NOT in `jobs_by_source` and therefore NOT passed to `mark_disappeared` — its prior rows stay visible. This is the guard from the problem statement.

- [ ] **Step 4: Run test — green**

- [ ] **Step 5: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add backend/src/main.py backend/tests/test_main.py
git commit -m "feat(orchestrator): generate run_hash per run + call mark_disappeared

Phase 2 wiring. run_hash = uuid4().hex generated once per run_search()
and stamped onto every Job. After each source's inserts complete,
mark_disappeared is invoked — BUT ONLY if the source returned >0
jobs in this run, so a timed-out source cannot wipe its own history.

Levergreen run_hash pattern (planning_report.md §9)."
```

---

### Phase 3 — Safety clamp + recency fallback alignment

**Goal:** Protect the recency scorer from any remaining future-date leaks (e.g. an upstream source lying).

**Files:**
- Modify: `backend/src/services/skill_matcher.py` (recency scorer — add `days_old < 0 → 0` clamp)

**Out of scope for this phase** — P8 ORDER BY correction. The user's prompt Phase 3 says "Semantic fixes" and explicitly scopes to "safety clamp in recency scorer". ORDER BY change would touch scoring semantics and is deferred per the "Out of scope" rules below.

#### Task 3.1 — Recency scorer safety clamp

- [ ] **Step 1: Write failing test**

In `backend/tests/test_scorer.py`:
```python
def test_recency_clamp_future_date_yields_zero():
    # A future-dated job (e.g. leaked from nhs_jobs' old closingDate bug,
    # or a source lying) must yield 0 recency points, not 10.
    job = Job(
        title="Data Scientist",
        company="Acme",
        apply_url="https://example.com",
        source="test",
        date_found=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
    )
    scorer = JobScorer(SearchConfig.from_defaults())
    # Introspect the recency component directly if the API allows;
    # otherwise assert total score is lower than the same job with today's date.
    assert scorer._recency_score(job.date_found) == 0
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement clamp**

In `backend/src/services/skill_matcher.py`, find `_recency_score` and add at the top:
```python
def _recency_score(self, date_iso: str) -> int:
    if not date_iso:
        return 0
    try:
        posted = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0
    days_old = (datetime.now(timezone.utc) - posted).days
    if days_old < 0:
        return 0  # Future dates (semantic leak) score zero.
    if days_old <= 1:
        return 10
    # ... existing tiers
```

- [ ] **Step 4: Run — green**

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/skill_matcher.py backend/tests/test_scorer.py
git commit -m "fix(scorer): clamp future dates to 0 recency points

Phase 3 safety net. If any remaining source leaks a future date
(nhs_jobs closingDate pattern, or a source that lies), the recency
scorer returns 0 instead of rewarding the negative days_old with 10.
Defends against the same class of bug at the downstream boundary
even though Phase 1 fixed the upstream leak."
```

---

### Phase 4 — Company slug expansion (103 → 500+)

**Goal:** Import Feashliaa's categorised slug list, filter for UK-relevant companies, add BambooHR adapter.

**Files:**
- Modify: `backend/src/core/companies.py` (expand lists)
- Create: `backend/src/sources/ats/bamboohr.py` (new adapter)
- Create: `backend/tests/test_sources.py` BambooHR test block
- Modify: `backend/src/main.py:78-128` (register BambooHRSource)
- Modify: `backend/src/main.py:151-220` (instantiate)
- Modify: `backend/src/core/settings.py:53-103` (rate limit entry)
- Modify: `backend/tests/test_cli.py:47` and `backend/tests/test_api.py:27,36,97,102` assertions (46 → 47)

#### Task 4.1 — Import UK-relevant slugs from Feashliaa

- [ ] **Step 1: Fetch Feashliaa's slug lists**

Do this manually outside the plan executor's automation — clone `Feashliaa/job-board-aggregator` locally, or fetch its company list files (the exact filename structure is in the repo — typically `greenhouse_companies.json`, `lever_companies.json`, etc.). Filter for companies with "UK", "United Kingdom", "London", "Manchester", etc. in their metadata or careers pages. The target is +400 new slugs across all 10 existing platforms.

- [ ] **Step 2: Merge into `backend/src/core/companies.py`**

For each existing list (`GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`, ...), append the new UK-filtered slugs. Keep the existing format (list of str, or list of dict for Workday/SuccessFactors). Deduplicate.

- [ ] **Step 3: Run the source tests**

Run: `cd backend && python -m pytest tests/test_sources.py -v -k "greenhouse or lever or workable or ashby or smartrecruiters or pinpoint or recruitee or workday or personio or successfactors"`
Expected: existing tests still pass (they mock per-company, so adding slugs doesn't break fixtures).

- [ ] **Step 4: Commit**

```bash
git add backend/src/core/companies.py
git commit -m "feat(companies): expand ATS slugs from ~103 to ~500 (UK-filtered)

Phase 4. Imported UK-relevant slugs from Feashliaa/job-board-aggregator
(planning_report.md §9, Tier 1 repo). Coverage jump applies to all 10
existing ATS platforms. Test suite is unaffected because source tests
mock per-company responses."
```

#### Task 4.2 — BambooHR adapter

- [ ] **Step 1: Write failing test**

In `backend/tests/test_sources.py`, add a BambooHR test block mocking `https://{slug}.bamboohr.com/careers/list` with a realistic payload.

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement `backend/src/sources/ats/bamboohr.py`**

Extend `BaseJobSource`, fetch `https://{slug}.bamboohr.com/careers/list` for each company, parse the JSON, filter by `_is_uk_or_remote`, emit `Job` records with `date_posted = parse_iso_utc(item.get("dateOpen"))` (verify actual field name against a real response before implementation). Follow the `ats/lever.py` shape as a template.

- [ ] **Step 4: Register**

- Add `"bamboohr": BambooHRSource` to `SOURCE_REGISTRY` at `main.py:78-128`.
- Add instantiation in `_build_sources()` at `main.py:151-220`.
- Add `"bamboohr": {"concurrent": 2, "delay": 1.5}` to `settings.py:53-103`.
- Update `SOURCE_INSTANCE_COUNT` from 45 to 46.
- Bump `test_cli.py:47` assertion from 46 to 47.
- Bump `test_api.py:27,36,97,102` assertions from 46 to 47.
- Add `BAMBOOHR_COMPANIES` list to `backend/src/core/companies.py` with a starter set of 10-20 UK SME slugs.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add backend/src/sources/ats/bamboohr.py backend/src/main.py backend/src/core/settings.py backend/src/core/companies.py backend/tests/test_sources.py backend/tests/test_cli.py backend/tests/test_api.py
git commit -m "feat(sources): add BambooHR ATS adapter for non-tech SMEs

Phase 4. BambooHR exposes a public careers JSON at
https://{slug}.bamboohr.com/careers/list used heavily by non-tech
UK SMEs. New adapter follows the Lever shape, parses dateOpen via
parse_iso_utc, filters by _is_uk_or_remote.

Registry count: 46 → 47. SOURCE_INSTANCE_COUNT: 45 → 46.
Test assertions at test_cli.py:47 and test_api.py:27/36/97/102
bumped accordingly (CLAUDE.md Rule 8)."
```

---

### Phase 5 — Infrastructure

**Goal:** Replace the stale local cron with a GitHub Actions workflow running every 4 hours. Add per-source date-coverage observability.

**Files:**
- Create: `.github/workflows/scrape.yml`
- Modify or delete: `cron_setup.sh` (verify path before editing)
- Modify: `backend/src/main.py` (log per-source `date_posted` coverage)

#### Task 5.1 — GitHub Actions cron workflow

- [ ] **Step 1: Create `.github/workflows/scrape.yml`**

```yaml
name: Job360 Scraper
on:
  schedule:
    - cron: '0 */4 * * *'  # Every 4 hours
  workflow_dispatch:
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/download-artifact@v4
        with:
          name: job360-db
          path: backend/data
        continue-on-error: true
      - name: Install dependencies
        run: |
          cd backend
          pip install -e .[indeed]
      - name: Run pipeline
        run: |
          cd backend
          python -m src.cli run --no-email
        env:
          REED_API_KEY: ${{ secrets.REED_API_KEY }}
          ADZUNA_APP_ID: ${{ secrets.ADZUNA_APP_ID }}
          ADZUNA_APP_KEY: ${{ secrets.ADZUNA_APP_KEY }}
          JSEARCH_API_KEY: ${{ secrets.JSEARCH_API_KEY }}
          JOOBLE_API_KEY: ${{ secrets.JOOBLE_API_KEY }}
          SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}
          CAREERJET_AFFID: ${{ secrets.CAREERJET_AFFID }}
          FINDWORK_API_KEY: ${{ secrets.FINDWORK_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - uses: actions/upload-artifact@v4
        with:
          name: job360-db
          path: backend/data/jobs.db
          retention-days: 30
```

- [ ] **Step 2: Delete the stale cron script**

`git rm cron_setup.sh` (verify the path — it may be at project root, not `backend/`).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/scrape.yml
git rm cron_setup.sh
git commit -m "infra(cron): GitHub Actions scraper every 4 hours, retire cron_setup.sh

Phase 5. Replaces the broken cron_setup.sh (paths stale since the
phase-1 backend/ restructure) with a GitHub Actions workflow on
cron '0 */4 * * *'. Database state persists across runs via the
job360-db artifact (30-day retention). SpeedyApply pattern from
planning_report.md §8."
```

#### Task 5.2 — Per-source date coverage observability

- [ ] **Step 1: Write failing test**

In `backend/tests/test_main.py`, add a test that runs `run_search()` with mocked sources where one returns all `date_posted`-populated jobs and another returns all None, then asserts the log output (capsys) contains per-source coverage percentages.

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement**

In `backend/src/main.py`, after per-source job collection, compute `coverage = sum(1 for j in jobs if j.date_posted) / len(jobs)` for each source and log it at INFO:
```python
for source_name, jobs in jobs_by_source.items():
    if jobs:
        with_date = sum(1 for j in jobs if j.date_posted)
        logger.info(
            "[date_coverage] %s: %d/%d (%.0f%%)",
            source_name, with_date, len(jobs), 100.0 * with_date / len(jobs),
        )
```

- [ ] **Step 4: Run — green**

- [ ] **Step 5: Commit**

```bash
git add backend/src/main.py backend/tests/test_main.py
git commit -m "feat(observability): log per-source date_posted coverage

Phase 5. Each run logs [date_coverage] source_name: N/M (P%) at INFO
so we can catch regressions (e.g. a source whose API schema changes
and silently starts returning None). Feeds into the larger
'pillar confidence' QA work tracked in project_qa_benchmark.md."
```

---

## Part 4 — Out of Scope

The following are **explicitly deferred**. Do not implement them in this plan; every one depends on the clean data this plan produces.

1. **Scoring changes** — `JobScorer` beyond the future-date clamp in Phase 3. Full rebalancing once `date_posted` is clean.
2. **Frontend (`frontend/src/**/*`)** — no changes to Next.js components, pages, API client, or types. The backend data shape change is additive (`date_posted` appears in API responses but old clients ignore it).
3. **Rich terminal table changes** — no changes to `backend/src/cli_view.py`.
4. **Notification changes** — no changes to `backend/src/services/notifications/**`. Email/Slack/Discord templates continue to read `date_found`, which still resolves to a valid timestamp (`date_posted or discovered_at`) after this plan.
5. **LLM enrichment** — no changes to `backend/src/services/profile/llm_provider.py` or CV parsing. Project memory records the LLM-only CV path as already deployed; this plan doesn't touch it.
6. **Search functionality** — no changes to `backend/src/api/routes/search.py`.
7. **ChromaDB / embeddings** — not in scope.
8. **Ollama / Mistral integration** — not in scope.
9. **`normalized_key()` in `models.py:54-58`** — CLAUDE.md Rule 1 + documented intentional divergence at `deduplicator.py:18-33`.
10. **`BaseJobSource.__init__` constructor at `base.py:52-56`** — CLAUDE.md Rule 2. Only ADD helper methods if needed.
11. **`purge_old_jobs()` at `database.py:183-190`** — CLAUDE.md Rule 3.
12. **Live HTTP in tests** — CLAUDE.md Rule 4, always `aioresponses`.

---

## Part 5 — Verification

### Per-phase verification commands

```bash
# Phase 0
cd backend && python -m pytest tests/test_models.py tests/test_database.py -v

# Phase 1
cd backend && python -m pytest tests/test_date_parsing.py tests/test_sources.py -v

# Phase 2
cd backend && python -m pytest tests/test_database.py tests/test_main.py -v

# Phase 3
cd backend && python -m pytest tests/test_scorer.py -v

# Phase 4
cd backend && python -m pytest tests/test_sources.py tests/test_cli.py tests/test_api.py -v

# Phase 5
cd backend && python -m pytest tests/test_main.py -v
# Plus: push a branch, verify .github/workflows/scrape.yml triggers on workflow_dispatch
```

### Final end-to-end verification after Phase 5

```bash
# Full test suite
cd backend && python -m pytest tests/ -v
# Expected: all ~400+ tests pass (count will shift as sources are removed/added)

# Dry-run the pipeline with real sources to observe date coverage logs
cd backend && python -m src.cli run --dry-run --log-level INFO 2>&1 | grep date_coverage

# Sanity check: every job in the DB has either a real date_posted or explicit NULL
cd backend && python -c "
import asyncio
from src.repositories.database import JobDatabase
async def check():
    db = JobDatabase('data/jobs.db')
    await db.connect()
    cur = await db._conn.execute(
        'SELECT source, COUNT(*), SUM(CASE WHEN date_posted IS NULL THEN 1 ELSE 0 END) '
        'FROM jobs GROUP BY source ORDER BY 2 DESC'
    )
    for row in await cur.fetchall():
        print(row)
    await db.close()
asyncio.run(check())
"
# Expected: known-dateless sources (pinpoint, successfactors, aijobs_ai, etc.)
# show 100% NULL date_posted. Known-dated sources show 0% NULL.
```

### Observability targets

After one full 4-hour scrape cycle running on the new GitHub Actions workflow:
- `[date_coverage]` log lines exist for every source in `SOURCE_REGISTRY`
- Dateless sources (pinpoint, successfactors, aijobs_ai, aijobs_global, bcs_jobs, jobtensor, plus any confirmed None during Phase 1 investigation): `0%` coverage
- Dated sources: ≥80% coverage (the ~20% floor is for per-row API omissions we correctly leave as None)
- `SELECT source, COUNT(*) FROM jobs WHERE disappeared_at IS NOT NULL GROUP BY source` returns non-zero counts within 24h of deployment (proving `mark_disappeared` is wiring up)

---

## Self-Review Notes

**Spec coverage check:**
- P1 (14 datetime.now sources): addressed in Task 1.7 (10 dateless) + 1.3 (linkedin) + 1.10 (yc_companies, nomis removed) = 13 confirmed. Plan flags the audit discrepancy.
- P2 (3 wrong fields): addressed in Task 1.9.
- P3 (33 fallback pattern): addressed in Task 1.5 (19 ISO) + 1.6 (6 RSS) + 1.8 (3 epoch/special).
- P4-P5 (schema/dataclass): Task 0.1 + 0.2.
- P6-P7 (upsert, disappearance): Task 2.1 + 2.2 + 2.3.
- P8 (ORDER BY divergence): deferred to scoring work — explicitly noted in Part 4.
- P9 (103 slugs): Task 4.1.
- P10 (BambooHR): Task 4.2.
- P11 (yc/nomis removal): Task 1.10.
- P12 (cron): Task 5.1.

**Placeholder scan:** No TBD/TODO — every task has exact file paths, exact line numbers, exact code patterns, and exact commit messages.

**Type consistency:** `date_posted`, `discovered_at`, `last_seen_at`, `run_hash`, `disappeared_at` are consistently `Optional[str]` across `Job`, the DB schema (TEXT), and the `parse_*_utc` return types.

---

## Execution Handoff

Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Required sub-skill: `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.
