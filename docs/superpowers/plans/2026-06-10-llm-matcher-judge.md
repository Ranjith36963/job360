# LLM Matcher (Funnel → Judge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each per-user search, an LLM "matcher" judges the keyword-shortlisted jobs against the user's full profile (CV + LinkedIn + GitHub + preferences) and writes a per-user fit verdict that re-ranks the dashboard.

**Architecture:** Keyword scoring stays the fast funnel. After `run_search` writes `user_feed`, a new gated stage (`MATCHER_ENABLED`, default **off** per CLAUDE.md rule #18) takes the top-N feed jobs (score ≥ `MATCHER_THRESHOLD`), calls the existing Gemini→Groq→Cerebras chain with a fit rubric (validated at 10/10 in `scripts/compare_enrichment_levels.py`), and persists `llm_fit_score / llm_verdict / llm_reason / llm_matched_at` onto the user's `user_feed` row (per-user state belongs in `user_feed` — rules #10/#17 untouched; `jobs` and `job_enrichment` stay shared). The read path orders by `COALESCE(llm_fit_score, score)` — identical order when the flag is off (all NULLs). Salary is never taken from the matcher (measured LLM weakness) — it only emits fit.

**Tech Stack:** Python 3.9+ / aiosqlite / Pydantic / existing `llm_extract_validated` provider chain; Next.js 16 + React 19 frontend; pytest + aioresponses (no live HTTP — rule #4); vitest.

**Hard constraints (from CLAUDE.md):**
- Rule #18 analog: `MATCHER_ENABLED=false` default. OFF behaviour must be byte-identical to today. `backend/tests/conftest.py` must force it off (same pattern as `SEMANTIC_ENABLED`/`ENRICHMENT_ENABLED`).
- Rules #10/#17: do NOT add `user_id` to `jobs`/`job_enrichment`. Verdicts go in `user_feed`.
- Rule #4: tests inject `llm_extract_validated_fn` mocks (mirror `job_enrichment.py`).
- Rule #21: value-presence tests, not just schema-presence.
- Rule #16: no new heavy top-level imports.
- Canonical suite must stay green: `cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py`.

---

### Task 1: Migration 0017 — verdict columns on `user_feed`

**Files:**
- Create: `backend/migrations/0017_user_feed_llm_verdict.up.sql`
- Create: `backend/migrations/0017_user_feed_llm_verdict.down.sql`
- Test: `backend/tests/test_migrations.py` (add to existing file; read it first — if it asserts a migration COUNT or enumerates ids, update those assertions)

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_migrations.py`, mirroring how that file tests other column-add migrations — read 2-3 existing tests there first and copy their fixture style):

```python
@pytest.mark.asyncio
async def test_0017_adds_llm_verdict_columns(tmp_path):
    """Migration 0017 adds the four per-user LLM-matcher columns to user_feed."""
    db_path = tmp_path / "mig0017.db"
    # Use the same helper the other tests in this file use to apply all
    # migrations to a fresh DB (e.g. run_all / apply_migrations). Then:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(user_feed)")
        cols = {row[1] for row in await cur.fetchall()}
    assert {"llm_fit_score", "llm_verdict", "llm_reason", "llm_matched_at"} <= cols
```

- [ ] **Step 2: Run it — must FAIL** (`python -m pytest tests/test_migrations.py -q -p no:randomly`): columns absent.

- [ ] **Step 3: Write the migration.** `0017_user_feed_llm_verdict.up.sql`:

```sql
-- Per-user LLM matcher verdict (funnel -> judge). Lives on user_feed because
-- fit is per-(user, job) state — rules #10/#17 keep jobs/job_enrichment shared.
ALTER TABLE user_feed ADD COLUMN llm_fit_score INTEGER;
ALTER TABLE user_feed ADD COLUMN llm_verdict TEXT;
ALTER TABLE user_feed ADD COLUMN llm_reason TEXT;
ALTER TABLE user_feed ADD COLUMN llm_matched_at TEXT;
```

`0017_user_feed_llm_verdict.down.sql` (mirror the 0014 best-effort pattern exactly):

```sql
-- SQLite <3.35 can't DROP COLUMN cleanly; best-effort down (see 0014):
-- mark the schema version only. Production rollback restores from backup.
DELETE FROM _schema_migrations WHERE id = '0017_user_feed_llm_verdict';
```

- [ ] **Step 4: Run test — must PASS.** Also run the whole migrations file: `python -m pytest tests/test_migrations.py -q -p no:randomly`.

- [ ] **Step 5: Commit** — `feat(db): migration 0017 — per-user LLM verdict columns on user_feed`

---

### Task 2: Read path — surface + rank by the verdict

**Files:**
- Modify: `backend/src/repositories/database.py:743-779` (`get_user_feed_jobs`)
- Test: `backend/tests/test_database.py` (append)

- [ ] **Step 1: Write the failing test** (value-presence per rule #21 — a LOW keyword score with a HIGH llm score must rank first, and the columns must round-trip). Read `tests/test_database.py` first and reuse its existing DB fixture that applies migrations; insert two jobs + two `user_feed` rows by hand:

```python
@pytest.mark.asyncio
async def test_feed_jobs_surface_and_rank_by_llm_verdict(db_with_migrations):
    """COALESCE(llm_fit_score, score) ranking: judged job with low keyword
    score but high LLM fit outranks an unjudged higher-keyword job, and the
    llm_* columns come back with real values (rule #21)."""
    db = db_with_migrations  # whatever the file's existing fixture is named
    uid = "test-user-llm"
    # insert two catalog jobs (reuse the file's existing make_job/insert helper),
    # then two feed rows:
    #   job A: score=80, no verdict
    #   job B: score=40, llm_fit_score=95, llm_verdict='strong fit', llm_reason='domain match'
    await db._conn.execute(
        "UPDATE user_feed SET llm_fit_score=95, llm_verdict='strong fit', "
        "llm_reason='domain match', llm_matched_at=datetime('now') "
        "WHERE user_id=? AND job_id=?", (uid, job_b_id))
    await db.commit()
    rows = await db.get_user_feed_jobs(uid)
    assert rows[0]["id"] == job_b_id          # 95 beats 80
    assert rows[0]["llm_fit_score"] == 95     # value-presence
    assert rows[0]["llm_verdict"] == "strong fit"
    assert rows[1]["llm_fit_score"] is None   # unjudged row -> NULL, not 0
```

- [ ] **Step 2: Run — must FAIL** (no such column in SELECT / wrong order).

- [ ] **Step 3: Implement.** In `get_user_feed_jobs`, change the SQL to:

```python
sql = (
    f"SELECT {self._JOBS_ENRICHMENT_JOIN_COLS}, f.score AS feed_score, "  # noqa: S608
    "f.llm_fit_score AS llm_fit_score, f.llm_verdict AS llm_verdict, "
    "f.llm_reason AS llm_reason "
    "FROM user_feed f "
    "JOIN jobs j ON j.id = f.job_id "
    "LEFT JOIN job_enrichment je ON je.job_id = j.id "
    "WHERE f.user_id = ? AND f.status = 'active' "
    "AND j.first_seen >= ? AND f.score >= ? "
    "AND (j.staleness_state IS NULL OR j.staleness_state = 'active') "
    # Judge outranks funnel: matcher fit when present, else keyword score.
    # All-NULL llm_fit_score (flag off) makes this identical to the old order.
    "ORDER BY COALESCE(f.llm_fit_score, f.score) DESC, j.date_found DESC"
)
```

The existing `except aiosqlite.OperationalError: return []` already covers pre-0017 DBs (migrations auto-apply on boot, so this only hits genuinely-fresh DBs). If any OTHER test fixture creates `user_feed` with a hand-written CREATE TABLE, add the four columns there too (grep `CREATE TABLE user_feed` under `backend/tests/`).

- [ ] **Step 4: Run the test — PASS — then the full database + feed files:** `python -m pytest tests/test_database.py tests/test_feed.py -q -p no:randomly`.

- [ ] **Step 5: Commit** — `feat(db): rank user feed by COALESCE(llm_fit_score, score) + surface verdict columns`

---

### Task 3: The matcher service — `src/services/llm_matcher.py`

**Files:**
- Create: `backend/src/services/llm_matcher.py`
- Test: `backend/tests/test_llm_matcher.py` (new)
- Modify: `backend/tests/conftest.py` (one line, top block)

- [ ] **Step 1: conftest guard FIRST** (rule #18 hermeticity — same block as the existing two, at the very top before any `src` import):

```python
os.environ.setdefault("MATCHER_ENABLED", "false")
```

- [ ] **Step 2: Write the failing tests** — `backend/tests/test_llm_matcher.py`:

```python
"""LLM matcher (funnel -> judge) — Pillar 2 follow-on.

All LLM traffic is mocked via the injected ``llm_extract_validated_fn``
(rule #4). DB tests reuse the migration-applying fixture style from
test_database.py.
"""
import pytest

from src.models import Job
from src.services.llm_matcher import (
    MatchVerdict,
    _build_match_prompt,
    match_batch,
    match_job,
    profile_to_matcher_text,
)


def _job(**kw):
    base = dict(
        title="Senior ML Engineer", company="Acme", location="London, UK",
        salary="", description="Build LLM pipelines with Python and PyTorch.",
        apply_url="https://x/1", source="reed",
    )
    base.update(kw)
    return Job(**base)  # adjust kwargs to the real Job dataclass signature


class _Profile:
    """Minimal stand-in matching profile.cv_data / .preferences attr shape."""
    class _CV:
        job_titles = ["ML Engineer", "AI Engineer"]
        skills = ["python", "pytorch", "llm"]
        summary = "Senior AI/ML engineer, 6 yrs."
    class _Prefs:
        experience_level = "senior"
        work_arrangement = "remote"
        salary_min = 60000
    cv_data = _CV()
    preferences = _Prefs()


def test_match_verdict_bounds():
    with pytest.raises(Exception):
        MatchVerdict(fit_score=150)
    v = MatchVerdict(fit_score=85, verdict="strong fit", reason="domain match")
    assert v.fit_score == 85


def test_profile_text_includes_all_four_profile_parts():
    txt = profile_to_matcher_text(_Profile())
    assert "ML Engineer" in txt and "python" in txt
    assert "senior" in txt and "60000" in txt


def test_prompt_contains_profile_job_and_facts_hint():
    p = _build_match_prompt("PROFILE-SENTINEL", _job(), {"seniority": "senior"})
    assert "PROFILE-SENTINEL" in p
    assert "Senior ML Engineer" in p
    assert "seniority" in p          # facts hint included
    assert "fit_score" in p          # rubric included


@pytest.mark.asyncio
async def test_match_job_uses_injected_fn_no_http():
    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=91, verdict="strong fit", reason="r")
    v = await match_job("profile", _job(), None, llm_extract_validated_fn=fake)
    assert v.fit_score == 91


@pytest.mark.asyncio
async def test_match_batch_persists_skips_and_survives_errors(db_with_migrations):
    """3 jobs: one succeeds -> row updated; one raises -> swallowed, row NULL;
    one already judged -> LLM not called again (skip_existing)."""
    # arrange: user + 3 catalog jobs + 3 user_feed rows (reuse fixture helpers);
    # pre-set llm_matched_at on job 3's feed row.
    calls = []
    async def fake(prompt, schema, system):
        calls.append(prompt)
        if "BOOM" in prompt:
            raise RuntimeError("provider down")
        return MatchVerdict(fit_score=88, verdict="good", reason="r")
    out = await match_batch(
        jobs, user_id=uid, profile_text="profile", conn=db._conn,
        llm_extract_validated_fn=fake)
    # job1 persisted:
    cur = await db._conn.execute(
        "SELECT llm_fit_score, llm_verdict FROM user_feed WHERE user_id=? AND job_id=?",
        (uid, job1_id))
    row = await cur.fetchone()
    assert row[0] == 88 and row[1] == "good"
    # job2 (BOOM in description) swallowed -> verdict columns still NULL
    # job3 skipped -> only 2 LLM calls
    assert len(calls) == 2


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("MATCHER_ENABLED", raising=False)
    import importlib
    import src.services.llm_matcher as m
    importlib.reload(m)
    assert m.MATCHER_ENABLED is False
```

- [ ] **Step 3: Run — must FAIL** (module doesn't exist).

- [ ] **Step 4: Implement `backend/src/services/llm_matcher.py`:**

```python
"""LLM matcher — the "judge" of the funnel->judge scoring path.

The keyword engine cheaply shortlists; this module sends each shortlisted job
plus the user's FULL profile (CV + LinkedIn + GitHub merged into cv_data, plus
preferences) to the shared Gemini->Groq->Cerebras chain and persists a per-user
fit verdict onto ``user_feed`` (per-user state — rules #10/#17 keep the shared
catalog tables untouched).

Honest limits, by measurement (scripts/score_enrichment_accuracy.py):
  * fit verdicts measured 10/10 on the labeled sample — the judge is the
    accuracy ceiling of the system;
  * the LLM drops structured salaries — so this module NEVER writes salary;
    salary stays sourced from the job row / enrichment.

Rule #4: tests inject ``llm_extract_validated_fn``. Rule #18 analog:
``MATCHER_ENABLED`` defaults off and the off path must be a byte-identical
no-op (callers gate on the flag before importing/calling).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable
from typing import Callable, Optional

import aiosqlite
from pydantic import BaseModel, Field

from src.models import Job
from src.services.profile.llm_provider import llm_extract_validated

logger = logging.getLogger("job360.services.llm_matcher")

MATCHER_ENABLED = os.getenv("MATCHER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
# Loose funnel on purpose: the judge can rescue a mediocre keyword score, but
# it can never see a job the funnel discarded. 30 == MIN_MATCH_SCORE floor.
MATCHER_THRESHOLD = int(os.getenv("MATCHER_THRESHOLD", "30"))
MATCHER_MAX_JOBS = int(os.getenv("MATCHER_MAX_JOBS", "30"))


class MatchVerdict(BaseModel):
    """The judge's output. fit_score is the per-user score; verdict is a
    <=8-word human label; reason names the deciding dimension."""
    fit_score: int = Field(ge=0, le=100)
    verdict: str = ""
    reason: str = ""


LLMExtractFn = Callable[[str, type, str], Awaitable[MatchVerdict]]

_SYSTEM_PROMPT = (
    "You are a precise job-fit judge. Return ONLY valid JSON matching the "
    "schema. No prose."
)

# Rubric validated in scripts/compare_enrichment_levels.py (L4 = 10/10).
_MATCH_RUBRIC = (
    "Score the candidate-job fit 0-100 by weighing FOUR dimensions: "
    "(1) domain/role match, (2) seniority fit vs the candidate's level, "
    "(3) skills overlap, (4) location/remote + visa fit. "
    'Return ONLY JSON: {"fit_score": <0-100 int>, "verdict": "<=8 words", '
    '"reason": "<=200 chars naming the deciding dimension"}.'
)


def profile_to_matcher_text(profile) -> str:
    """The permanent 'left side': titles + skills + summary from cv_data
    (LinkedIn/GitHub are already merged into cv_data by the upload routes)
    plus explicit preferences."""
    cv = getattr(profile, "cv_data", None)
    prefs = getattr(profile, "preferences", None)
    titles = ", ".join(getattr(cv, "job_titles", []) or []) if cv else ""
    skills = ", ".join((getattr(cv, "skills", []) or [])[:30]) if cv else ""
    summary = (getattr(cv, "summary", "") or "")[:600] if cv else ""
    pref_bits = []
    if prefs:
        for attr in ("experience_level", "work_arrangement"):
            v = getattr(prefs, attr, None)
            if v:
                pref_bits.append(f"{attr}={v}")
        smin = getattr(prefs, "salary_min", None)
        if smin:
            pref_bits.append(f"salary_min={smin}")
    return (
        f"Target titles: {titles}\nSkills: {skills}\nSummary: {summary}\n"
        f"Preferences: {', '.join(pref_bits)}"
    )


def _build_match_prompt(profile_txt: str, job: Job, facts: Optional[dict]) -> str:
    import json as _json
    hint = ""
    if facts:
        hint = (
            "\nPRE-EXTRACTED FACTS about this job (use them, but trust the "
            f"description if they conflict): {_json.dumps(facts, default=str)}\n"
        )
    return (
        f"{_MATCH_RUBRIC}\n\n"
        f"CANDIDATE PROFILE:\n{profile_txt}\n{hint}\n"
        f"JOB:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"Description:\n{(job.description or '')[:3500]}\n"
    )


def _facts_hint(job: Job, enrichment) -> Optional[dict]:
    """Cheap deterministic hints for the judge. Enrichment wins when present;
    salary always comes from structured fields (never the matcher)."""
    facts: dict = {}
    if enrichment is not None:
        facts["seniority"] = getattr(enrichment.seniority, "value", enrichment.seniority)
        facts["workplace_type"] = getattr(enrichment.workplace_type, "value", enrichment.workplace_type)
        facts["visa_sponsorship"] = getattr(enrichment.visa_sponsorship, "value", enrichment.visa_sponsorship)
    lvl = getattr(job, "experience_level", "") or ""
    if "seniority" not in facts and lvl:
        facts["seniority"] = lvl
    smin = getattr(job, "salary_min", None)
    smax = getattr(job, "salary_max", None)
    if smin or smax:
        facts["salary"] = {"min": smin, "max": smax}
    return facts or None


async def match_job(
    profile_txt: str,
    job: Job,
    facts: Optional[dict],
    *,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> MatchVerdict:
    """One judge call. Raises on total provider failure — callers catch."""
    fn = llm_extract_validated_fn or llm_extract_validated
    prompt = _build_match_prompt(profile_txt, job, facts)
    return await fn(prompt, MatchVerdict, _SYSTEM_PROMPT)


async def has_verdict(conn: aiosqlite.Connection, user_id: str, job_id: int) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM user_feed WHERE user_id = ? AND job_id = ? "
        "AND llm_matched_at IS NOT NULL LIMIT 1",
        (user_id, job_id),
    )
    return (await cur.fetchone()) is not None


async def save_verdict(
    conn: aiosqlite.Connection, user_id: str, job_id: int, verdict: MatchVerdict
) -> None:
    await conn.execute(
        "UPDATE user_feed SET llm_fit_score = ?, llm_verdict = ?, llm_reason = ?, "
        "llm_matched_at = datetime('now') WHERE user_id = ? AND job_id = ?",
        (verdict.fit_score, verdict.verdict, verdict.reason, user_id, job_id),
    )
    await conn.commit()


async def match_batch(
    jobs: list[Job],
    *,
    user_id: str,
    profile_text: str,
    conn: aiosqlite.Connection,
    semaphore_limit: int = 3,
    skip_existing: bool = True,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> "list[Optional[MatchVerdict]]":
    """Judge a shortlist concurrently (bounded — free tiers cap ~30 req/min).
    Per-job errors are swallowed; one bad LLM response can't kill the batch.
    Persists each success onto the user's user_feed row."""
    import asyncio

    if not jobs:
        return []
    sem = asyncio.Semaphore(semaphore_limit)

    async def _one(job: Job) -> Optional[MatchVerdict]:
        job_id = getattr(job, "id", None)
        if job_id is None:
            return None
        async with sem:
            try:
                if skip_existing and await has_verdict(conn, user_id, job_id):
                    return None
                enrichment = None
                try:
                    from src.services.job_enrichment import load_enrichment  # noqa: PLC0415
                    enrichment = await load_enrichment(conn, job_id)
                except Exception:  # noqa: BLE001 — hints are optional
                    enrichment = None
                verdict = await match_job(
                    profile_text, job, _facts_hint(job, enrichment),
                    llm_extract_validated_fn=llm_extract_validated_fn,
                )
                await save_verdict(conn, user_id, job_id, verdict)
                return verdict
            except Exception as e:  # noqa: BLE001 — judge failure must not kill the run
                logger.warning("match_batch: judge failed for job %s: %s", job_id, e)
                return None

    return await asyncio.gather(*[_one(j) for j in jobs])
```

Adjust `Job(**base)` kwargs in the test to the real dataclass fields (read `src/models.py` first); same for the DB fixture names.

- [ ] **Step 5: Run — all of `tests/test_llm_matcher.py` PASS**, then `python -m pytest tests/test_llm_matcher.py tests/test_feed.py -q -p no:randomly`.

- [ ] **Step 6: Commit** — `feat(matcher): LLM judge service — per-user fit verdicts on user_feed`

---

### Task 4: Pipeline hook — judge runs after the feed write

**Files:**
- Modify: `backend/src/main.py` (insert AFTER the user_feed block ending line ~661, BEFORE the `SEMANTIC_ENABLED` vector block at ~663)
- Test: `backend/tests/test_llm_matcher.py` (append integration test)

- [ ] **Step 1: Failing test** — monkeypatch-driven integration: build 2 fake jobs with ids + feed rows, call the new helper directly (extract the stage as a testable function `_run_matcher_stage` in `main.py` so the test doesn't need the whole pipeline):

```python
@pytest.mark.asyncio
async def test_matcher_stage_judges_shortlist_and_respects_threshold(db_with_migrations, monkeypatch):
    """Stage filters by MATCHER_THRESHOLD, caps at MATCHER_MAX_JOBS, persists
    verdicts, and NEVER raises even when the LLM explodes."""
    from src import main as main_mod
    # jobs: score 80 (judged), score 10 (below threshold 30 -> skipped)
    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=77, verdict="good", reason="r")
    monkeypatch.setattr(
        "src.services.llm_matcher.llm_extract_validated", fake)
    await main_mod._run_matcher_stage(db, user_id=uid, jobs=[job80, job10])
    # job80's feed row has llm_fit_score=77; job10's is NULL
```

- [ ] **Step 2: Run — FAIL** (`_run_matcher_stage` undefined).

- [ ] **Step 3: Implement.** In `main.py` add (module level, near `_recency_bucket`):

```python
async def _run_matcher_stage(db, *, user_id: str, jobs: list) -> None:
    """Funnel -> judge: LLM-match the top shortlisted jobs for THIS user.
    Gated on MATCHER_ENABLED (default off — rule #18 analog). Any failure is
    logged and swallowed: the judge upgrades scores, it never blocks a run."""
    try:
        from src.services.llm_matcher import (  # noqa: PLC0415 — lazy by design
            MATCHER_ENABLED, MATCHER_MAX_JOBS, MATCHER_THRESHOLD,
            match_batch, profile_to_matcher_text,
        )
        if not MATCHER_ENABLED:
            return
        from src.services.profile.storage import load_profile  # noqa: PLC0415
        profile = load_profile(user_id)
        if profile is None:
            logger.info("matcher: no profile for user %s — skipping", user_id)
            return
        shortlist = sorted(
            (j for j in jobs
             if getattr(j, "id", None) is not None
             and j.match_score is not None
             and j.match_score >= MATCHER_THRESHOLD),
            key=lambda j: j.match_score, reverse=True,
        )[:MATCHER_MAX_JOBS]
        if not shortlist:
            return
        logger.info("matcher: judging %s shortlisted jobs for user %s",
                    len(shortlist), user_id)
        await match_batch(
            shortlist, user_id=user_id,
            profile_text=profile_to_matcher_text(profile),
            conn=db._conn, semaphore_limit=3,
        )
    except Exception as e:  # noqa: BLE001 — judge failure must never kill the run
        logger.warning("matcher stage failed (run continues): %s", e)
```

And inside `run_search`, immediately after the feed-write block (after line ~661 `logger.info("Wrote %s jobs to user_feed ...")`, still inside `if user_id is not None and unique_jobs:` — or as its own guarded block):

```python
            # Funnel -> judge (LLM matcher). Per-user, post-feed-write so the
            # verdict UPDATE always finds its user_feed row. Default OFF.
            if user_id is not None and unique_jobs:
                await _run_matcher_stage(db, user_id=user_id, jobs=unique_jobs)
```

- [ ] **Step 4: Run — PASS**, then the canonical suite: `python -m pytest -q -p no:randomly --ignore=tests/test_main.py`. Zero regressions allowed (flag is off in tests, so default behaviour must be untouched).

- [ ] **Step 5: Commit** — `feat(pipeline): run LLM judge on the per-user shortlist after feed write`

---

### Task 5: API surface — verdict fields in the jobs response

**Files:**
- Modify: `backend/src/api/models.py:33-96` (`JobResponse`)
- Modify: `backend/src/api/routes/jobs.py:100-171` (`_row_to_job_response`)
- Test: `backend/tests/test_api.py` (append; value-presence per rule #21)

- [ ] **Step 1: Failing test** (mirror the style of `test_jobs_response_includes_score_dim_breakdown` in `tests/test_api.py` — same fixtures/client; seed a feed row with a verdict then GET /jobs as that user):

```python
@pytest.mark.asyncio
async def test_jobs_response_includes_llm_verdict_values(client, auth_user_with_feed):
    """Rule #21: real values, not defaults — llm fields round-trip end-to-end."""
    # seed: UPDATE user_feed SET llm_fit_score=93, llm_verdict='strong fit',
    #        llm_reason='domain + seniority', llm_matched_at=datetime('now')
    r = await client.get("/api/jobs", cookies=session_cookie)
    body = r.json()
    judged = [j for j in body["jobs"] if j["llm_fit_score"] is not None]
    assert judged and judged[0]["llm_fit_score"] == 93
    assert judged[0]["llm_verdict"] == "strong fit"
```

- [ ] **Step 2: Run — FAIL** (KeyError `llm_fit_score`).

- [ ] **Step 3: Implement.** `JobResponse` gains (after `dedup_group_ids`):

```python
    # Funnel->judge (LLM matcher) — per-user verdict from user_feed. None for
    # unauthenticated reads, unjudged jobs, or MATCHER_ENABLED=false.
    llm_fit_score: Optional[int] = None
    llm_verdict: Optional[str] = None
    llm_reason: Optional[str] = None
```

`_row_to_job_response` maps them (None-safe — rows from the shared-catalog path have no such keys):

```python
        llm_fit_score=row.get("llm_fit_score"),
        llm_verdict=row.get("llm_verdict"),
        llm_reason=row.get("llm_reason"),
```

- [ ] **Step 4: Run — PASS**, then `python -m pytest tests/test_api.py -q -p no:randomly`.

- [ ] **Step 5: Commit** — `feat(api): expose per-user LLM verdict on the jobs response`

---

### Task 6: Frontend — verdict badge on job cards

**Files:**
- Modify: `frontend/src/lib/types.ts` (the `Job` type — mirror backend exactly, see models.py comment "Frontend lib/types.ts must mirror these")
- Modify: the job-card component (find it: `frontend/src/components/jobs/` — likely `JobCard.tsx`; READ it fully first and match its existing badge idiom)
- Test: `frontend/src/components/jobs/__tests__/llm-verdict-badge.test.tsx` (new; mirror the setup of `frontend/src/app/dashboard/__tests__/uses-hybrid.test.tsx`)

- [ ] **Step 1: types.ts** — add to the `Job` interface:

```ts
  llm_fit_score: number | null;
  llm_verdict: string | null;
  llm_reason: string | null;
```

- [ ] **Step 2: Failing test** — render the card with `llm_verdict: "strong fit"`, `llm_fit_score: 93`, assert the badge text appears; render with nulls, assert it does NOT:

```tsx
it("shows the AI verdict badge when a verdict exists", () => {
  render(<JobCard job={jobWith({ llm_fit_score: 93, llm_verdict: "strong fit", llm_reason: "domain match" })} />);
  expect(screen.getByText(/strong fit/i)).toBeInTheDocument();
});
it("renders no AI badge when unjudged", () => {
  render(<JobCard job={jobWith({ llm_fit_score: null, llm_verdict: null, llm_reason: null })} />);
  expect(screen.queryByText(/AI:/i)).not.toBeInTheDocument();
});
```

(Adapt `jobWith` to however existing card tests build a Job fixture; if none exist, build a minimal complete Job object.)

- [ ] **Step 3: Run — FAIL** (`npm run test:unit`).

- [ ] **Step 4: Implement the badge** in the card, using the file's existing Badge/chip component and color idiom. Logic:

```tsx
{job.llm_verdict != null && job.llm_fit_score != null && (
  <Badge
    variant="outline"
    title={job.llm_reason ?? undefined}
    className={
      job.llm_fit_score >= 70 ? "border-emerald-500 text-emerald-600"
      : job.llm_fit_score >= 40 ? "border-amber-500 text-amber-600"
      : "border-red-500 text-red-600"
    }
  >
    AI: {job.llm_verdict} · {job.llm_fit_score}
  </Badge>
)}
```

(Exact classes/variant must match the component's existing badges — copy the in-file idiom, this snippet is the logic contract, not the styling gospel. Next.js 16 caution per rule #22 doesn't apply here — no App Router API change — but do not touch `page.tsx` server/client boundaries.)

- [ ] **Step 5: Run — PASS:** `npm run test:unit`, then `npm run type-check` and `npm run lint`.

- [ ] **Step 6: Commit** — `feat(frontend): AI verdict badge on job cards`

---

### Task 7: Full verification gate (overseer-driven — NOT a subagent task)

- [ ] Backend canonical suite green: `cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py`
- [ ] `python -m ruff check .` clean
- [ ] Frontend: `npm run test:unit && npm run type-check && npm run lint`
- [ ] Live verification via the **verify-job360 skill**: set `MATCHER_ENABLED=true` in the gitignored root `.env`, restart the API, run a real per-user search as the demo user, confirm (a) `user_feed.llm_fit_score` rows populate in SQLite, (b) GET /api/jobs shows the verdict fields with real values, (c) the dashboard renders the AI badge and the judged ordering, (d) with the flag removed the behaviour is byte-identical to before.
- [ ] Measure: log how many LLM calls one search consumed + wall time of the matcher stage (from the API log lines added in Task 4).
