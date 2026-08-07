"""LLM matcher (funnel -> judge).

All LLM traffic is mocked via the injected ``llm_extract_validated_fn``
(CLAUDE.md rule #4 — suite must run offline).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.models import Job
from src.repositories import pg
from src.services.llm_matcher import (
    MatchVerdict,
    _build_match_prompt,
    match_batch,
    match_job,
    profile_to_matcher_text,
)

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc).isoformat()


def _make_job(title: str = "ML Engineer", company: str = "Acme", **kwargs) -> Job:
    """Build a minimal valid Job. id is set after construction."""
    return Job(
        title=title,
        company=company,
        apply_url=f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
        source="greenhouse",
        date_found=_NOW,
        **kwargs,
    )


class _CV:
    job_titles = ["ML Engineer", "AI Engineer"]
    skills = ["python", "pytorch", "llm"]
    summary = "Senior AI/ML engineer, 6 yrs."


class _Prefs:
    experience_level = "senior"
    work_arrangement = "remote"
    salary_min = 60000


class _Profile:
    cv_data = _CV()
    preferences = _Prefs()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_match_verdict_bounds():
    with pytest.raises(ValueError):
        MatchVerdict(fit_score=150)
    v = MatchVerdict(fit_score=85, verdict="strong fit", reason="domain match")
    assert v.fit_score == 85


def test_profile_text_includes_all_profile_parts():
    txt = profile_to_matcher_text(_Profile())
    assert "ML Engineer" in txt and "python" in txt
    assert "senior" in txt and "60000" in txt


def test_prompt_contains_profile_job_facts_and_rubric():
    job = _make_job(title="Senior ML Engineer")
    p = _build_match_prompt("PROFILE-SENTINEL", job, {"seniority": "senior"})
    assert "PROFILE-SENTINEL" in p
    assert "Senior ML Engineer" in p
    assert "seniority" in p
    assert "fit_score" in p


@pytest.mark.asyncio
async def test_match_job_uses_injected_fn_no_http():
    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=91, verdict="strong fit", reason="r")

    job = _make_job(title="Senior AI Engineer")
    v = await match_job("profile", job, None, llm_extract_validated_fn=fake)
    assert v.fit_score == 91


# ---------------------------------------------------------------------------
# In-memory DB fixture for persistence test (mirrors test_database.py approach)
# ---------------------------------------------------------------------------


@pytest.fixture
async def mem_db():
    """Minimal in-memory aiosqlite connection with jobs + user_feed tables."""
    conn = await pg.connect(":memory:")
    conn.row_factory = pg.Row
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            description TEXT DEFAULT '',
            apply_url TEXT NOT NULL,
            source TEXT NOT NULL,
            date_found TEXT NOT NULL,
            match_score INTEGER DEFAULT 0,
            salary_min REAL,
            salary_max REAL
        );
        CREATE TABLE IF NOT EXISTS user_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            bucket TEXT NOT NULL DEFAULT 'top',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            llm_fit_score INTEGER,
            llm_verdict TEXT,
            llm_reason TEXT,
            llm_matched_at TEXT,
            UNIQUE(user_id, job_id)
        );
        CREATE TABLE IF NOT EXISTS job_enrichment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            title_canonical TEXT,
            seniority TEXT,
            workplace_type TEXT,
            visa_sponsorship TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_match_batch_persists_skips_and_survives_errors(mem_db):
    """3 jobs through match_batch:
    - job1 (success): persists onto user_feed with fit_score=88
    - job2 (BOOM in title): LLM raises; row stays NULL; function does NOT raise
    - job3 (pre-judged): skipped — no second LLM call
    """
    conn = mem_db
    uid = "user-test-001"

    # Insert 3 catalog jobs
    cur = await conn.execute(
        "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
        ("Good Job", "CorpA", "https://a.com/1", "greenhouse", _NOW),
    )
    job1_id = cur.lastrowid
    cur = await conn.execute(
        "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
        ("BOOM Job", "CorpB", "https://b.com/2", "greenhouse", _NOW),
    )
    job2_id = cur.lastrowid
    cur = await conn.execute(
        "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
        ("Already Judged Job", "CorpC", "https://c.com/3", "greenhouse", _NOW),
    )
    job3_id = cur.lastrowid
    await conn.commit()

    # Insert user_feed rows for all three
    for jid in (job1_id, job2_id, job3_id):
        await conn.execute(
            "INSERT INTO user_feed(user_id, job_id, score, bucket) VALUES (?,?,?,?)",
            (uid, jid, 50, "top"),
        )
    # Pre-set job3 as already judged
    await conn.execute(
        "UPDATE user_feed SET llm_fit_score=50, llm_verdict='ok', "
        "llm_reason='pre', llm_matched_at=datetime('now') "
        "WHERE user_id=? AND job_id=?",
        (uid, job3_id),
    )
    await conn.commit()

    # Build Job objects with correct .id set
    job1 = _make_job(title="Good Job", company="CorpA")
    job1.id = job1_id  # type: ignore[attr-defined]
    job2 = _make_job(title="BOOM Job", company="CorpB")
    job2.id = job2_id  # type: ignore[attr-defined]
    job3 = _make_job(title="Already Judged Job", company="CorpC")
    job3.id = job3_id  # type: ignore[attr-defined]

    calls = []

    async def fake(prompt, schema, system):
        calls.append(prompt)
        if "BOOM" in prompt:
            raise RuntimeError("provider down")
        return MatchVerdict(fit_score=88, verdict="good", reason="r")

    out = await match_batch(
        [job1, job2, job3],
        user_id=uid,
        profile_text="profile",
        conn=conn,
        llm_extract_validated_fn=fake,
    )

    # Function must not raise even with one failing job
    assert isinstance(out, list)
    assert len(out) == 3

    # job1: persisted with fit_score=88
    cur = await conn.execute(
        "SELECT llm_fit_score, llm_verdict, llm_matched_at FROM user_feed "
        "WHERE user_id=? AND job_id=?",
        (uid, job1_id),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["llm_fit_score"] == 88
    assert row["llm_verdict"] == "good"
    assert row["llm_matched_at"] is not None

    # job2: swallowed error — llm_fit_score stays NULL (was not set)
    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
        (uid, job2_id),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["llm_fit_score"] is None

    # job3: skipped — only 2 LLM calls total
    assert len(calls) == 2


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("MATCHER_ENABLED", raising=False)
    import importlib

    import src.services.llm_matcher as m

    importlib.reload(m)
    assert m.MATCHER_ENABLED is False


# ---------------------------------------------------------------------------
# _run_matcher_stage integration tests (Task 4)
# ---------------------------------------------------------------------------


@pytest.fixture
async def stage_db():
    """Minimal in-memory DB for _run_matcher_stage tests."""
    conn = await pg.connect(":memory:")
    conn.row_factory = pg.Row
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            description TEXT DEFAULT '',
            apply_url TEXT NOT NULL,
            source TEXT NOT NULL,
            date_found TEXT NOT NULL,
            match_score INTEGER DEFAULT 0,
            salary_min REAL,
            salary_max REAL
        );
        CREATE TABLE IF NOT EXISTS user_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            bucket TEXT NOT NULL DEFAULT 'top',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            llm_fit_score INTEGER,
            llm_verdict TEXT,
            llm_reason TEXT,
            llm_matched_at TEXT,
            UNIQUE(user_id, job_id)
        );
        CREATE TABLE IF NOT EXISTS job_enrichment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            title_canonical TEXT,
            seniority TEXT,
            workplace_type TEXT,
            visa_sponsorship TEXT
        );
    """)
    await conn.commit()

    # Minimal DB-wrapper stub so _run_matcher_stage can do db._conn
    class _FakeDB:
        def __init__(self, c):
            self._conn = c

    yield _FakeDB(conn)
    await conn.close()


_UID = "user-stage-001"


async def _seed_job(conn, title: str, company: str, score: int, uid: str) -> Job:
    """Insert a jobs row + user_feed row; return a Job with .id set."""
    cur = await conn.execute(
        "INSERT INTO jobs(title, company, apply_url, source, date_found, match_score) "
        "VALUES (?,?,?,?,?,?)",
        (title, company, f"https://x.com/{title}", "test", _NOW, score),
    )
    jid = cur.lastrowid
    await conn.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket) VALUES (?,?,?,?)",
        (uid, jid, score, "top"),
    )
    await conn.commit()
    j = _make_job(title=title, company=company, match_score=score)
    j.id = jid  # type: ignore[attr-defined]
    return j


@pytest.mark.asyncio
async def test_matcher_stage_judges_shortlist_and_respects_threshold(stage_db, monkeypatch):
    """_run_matcher_stage: judges jobs >= MATCHER_THRESHOLD for the user,
    skips those below, persists verdicts, and never raises."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=77, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    job80 = await _seed_job(conn, "High Score Job", "Corp80", 80, _UID)
    job10 = await _seed_job(conn, "Low Score Job", "Corp10", 10, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job80, job10])

    # job80 should be judged (score >= 30 threshold)
    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
        (_UID, job80.id),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["llm_fit_score"] == 77

    # job10 should be skipped (score < 30 threshold)
    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
        (_UID, job10.id),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["llm_fit_score"] is None


@pytest.mark.asyncio
async def test_matcher_stage_is_noop_when_flag_off(stage_db, monkeypatch):
    """Default OFF: no LLM call, no DB change, no exception (rule #18)."""
    from src import main as main_mod

    called = []

    async def fake(prompt, schema, system):
        called.append(1)
        return MatchVerdict(fit_score=99)

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    job80 = await _seed_job(conn, "Flag Off Job", "CorpF", 80, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job80])
    assert called == []  # flag is off (conftest forces false) -> zero LLM traffic


@pytest.mark.asyncio
async def test_matcher_stage_swallows_total_failure(stage_db, monkeypatch):
    """Even if profile loading explodes, the stage logs and returns — a judge
    failure must never kill the search run."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)

    def boom(uid):
        raise RuntimeError("storage corrupted")

    monkeypatch.setattr("src.services.profile.storage.load_profile", boom)

    conn = stage_db._conn
    job80 = await _seed_job(conn, "Boom Profile Job", "CorpB", 80, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job80])  # must not raise


@pytest.mark.asyncio
async def test_matcher_stage_caches_verdict_no_rejudge(stage_db, monkeypatch):
    """FUNNEL CACHE / speed guarantee (eval verdict B): a job already judged is
    NOT re-judged on a second run — skip_existing via has_verdict means zero
    extra LLM calls. This is what makes the judge-led funnel affordable: the
    expensive engine runs once per (user, job), then is cached."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    calls = []

    async def fake(prompt, schema, system):
        calls.append(1)
        return MatchVerdict(fit_score=77, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    job = await _seed_job(conn, "Cache Job", "CorpC", 80, _UID)

    # First run: the job is judged exactly once.
    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job])
    assert len(calls) == 1
    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?", (_UID, job.id)
    )
    assert (await cur.fetchone())["llm_fit_score"] == 77

    # Second run with the SAME job: the cached verdict must short-circuit the
    # LLM — no extra call. (This is the funnel's speed win.)
    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job])
    assert len(calls) == 1, "cached verdict must not trigger a second LLM call"


# ---------------------------------------------------------------------------
# Task 4 — clear_user_verdicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_user_verdicts_nulls_all_llm_columns(mem_db):
    """save_verdict then clear_user_verdicts: all four llm_ columns go NULL."""
    from src.services.llm_matcher import clear_user_verdicts, has_verdict, save_verdict

    conn = mem_db
    uid = "user-clear-001"

    # Insert a catalog job + feed row
    cur = await conn.execute(
        "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
        ("Clear Test Job", "CorpX", "https://x.com/1", "greenhouse", _NOW),
    )
    jid = cur.lastrowid
    await conn.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket) VALUES (?,?,?,?)",
        (uid, jid, 70, "top"),
    )
    await conn.commit()

    # Save a verdict
    verdict = MatchVerdict(fit_score=80, verdict="strong", reason="domain")
    await save_verdict(conn, uid, jid, verdict)
    assert await has_verdict(conn, uid, jid) is True

    # Clear
    cleared = await clear_user_verdicts(conn, uid)
    assert cleared >= 1

    # has_verdict must now return False
    assert await has_verdict(conn, uid, jid) is False

    # All four llm_ columns must be NULL
    cur = await conn.execute(
        "SELECT llm_fit_score, llm_verdict, llm_reason, llm_matched_at "
        "FROM user_feed WHERE user_id = ? AND job_id = ?",
        (uid, jid),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["llm_fit_score"] is None
    assert row["llm_verdict"] is None
    assert row["llm_reason"] is None
    assert row["llm_matched_at"] is None


@pytest.mark.asyncio
async def test_clear_user_verdicts_returns_zero_when_no_rows(mem_db):
    """No feed rows for this user → returns 0 without error."""
    from src.services.llm_matcher import clear_user_verdicts

    cleared = await clear_user_verdicts(mem_db, "nonexistent-user")
    assert cleared == 0


# ---------------------------------------------------------------------------
# Engine 4 enhancement — judge telemetry (backlog #9)
# ---------------------------------------------------------------------------


def test_matcher_telemetry_accessor_and_reset():
    from src.utils import telemetry

    telemetry.reset_for_testing()
    tel = telemetry.matcher_telemetry()
    assert tel.judged == 0
    assert tel.failed == 0
    assert tel.skipped_existing == 0
    assert tel.avg_fit == 0.0

    tel.record_verdict(88)
    tel.record_verdict(20)
    assert tel.judged == 2
    assert tel.fit_min == 20
    assert tel.fit_max == 88
    assert tel.avg_fit == 54.0

    telemetry.reset_for_testing()
    assert telemetry.matcher_telemetry().judged == 0


@pytest.mark.asyncio
async def test_match_batch_populates_telemetry(mem_db):
    """One success + one failure + one pre-judged → judged/failed/skipped + spread."""
    from src.utils import telemetry

    telemetry.reset_for_testing()
    conn = mem_db
    uid = "user-tel-001"

    ids = []
    for title in ("Tel Good", "Tel BOOM", "Tel Judged"):
        cur = await conn.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
            (title, "C", f"https://x.com/{title}", "greenhouse", _NOW),
        )
        jid = cur.lastrowid
        ids.append(jid)
        await conn.execute(
            "INSERT INTO user_feed(user_id, job_id, score, bucket) VALUES (?,?,?,?)",
            (uid, jid, 50, "top"),
        )
    await conn.execute(
        "UPDATE user_feed SET llm_fit_score=50, llm_matched_at=datetime('now') "
        "WHERE user_id=? AND job_id=?",
        (uid, ids[2]),
    )
    await conn.commit()

    jobs = []
    for title, jid in zip(("Tel Good", "Tel BOOM", "Tel Judged"), ids):
        j = _make_job(title=title)
        j.id = jid  # type: ignore[attr-defined]
        jobs.append(j)

    async def fake(prompt, schema, system):
        if "BOOM" in prompt:
            raise RuntimeError("provider down")
        return MatchVerdict(fit_score=88, verdict="good", reason="r")

    await match_batch(
        jobs, user_id=uid, profile_text="p", conn=conn, llm_extract_validated_fn=fake
    )

    tel = telemetry.matcher_telemetry()
    assert tel.judged == 1
    assert tel.failed == 1
    assert tel.skipped_existing == 1
    assert tel.fit_min == 88
    assert tel.fit_max == 88


@pytest.mark.asyncio
async def test_matcher_stage_logs_fit_spread(stage_db, monkeypatch, caplog):
    """The judge stage logs the fit-score spread (min/avg/max) for visibility."""
    import logging

    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=77, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    job = await _seed_job(conn, "Spread Job", "CorpS", 80, _UID)

    with caplog.at_level(logging.INFO, logger="job360.main"):
        await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[job])

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "fit" in msgs.lower()
    assert "77" in msgs  # single job → min == avg == max == 77


@pytest.mark.asyncio
async def test_match_batch_never_uses_the_shared_connection_concurrently(mem_db):
    """Finding C2: match_batch judges run concurrently but share ONE psycopg
    connection. psycopg3 forbids using one async connection from two coroutines
    at once ("another operation is already in progress"); that error fell into
    match_batch's broad except and was swallowed as "judge failed" — silently
    dropping a PAID verdict.

    A detector wraps the shared connection's execute() and records the MAX
    number of in-flight execute() calls. With the db_lock serialising DB access
    it stays 1; without it, two judges overlap on the connection and it climbs
    past 1. Verified to FAIL against the pre-fix code (reached 2).
    """
    conn = mem_db
    uid = "user-conc-c2"
    N = 6

    job_ids = []
    for i in range(N):
        cur = await conn.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found) VALUES (?,?,?,?,?)",
            (f"Job {i}", "Corp", f"https://x.com/{i}", "greenhouse", _NOW),
        )
        job_ids.append(cur.lastrowid)
    await conn.commit()
    for jid in job_ids:
        await conn.execute(
            "INSERT INTO user_feed(user_id, job_id, score, bucket) VALUES (?,?,?,?)",
            (uid, jid, 50, "top"),
        )
    await conn.commit()

    jobs = []
    for i, jid in enumerate(job_ids):
        j = _make_job(title=f"Job {i}", company="Corp")
        j.id = jid  # type: ignore[attr-defined]
        jobs.append(j)

    in_flight = 0
    max_in_flight = 0
    real_execute = conn.execute

    async def watched_execute(sql, params=()):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            return await real_execute(sql, params)
        finally:
            in_flight -= 1

    conn.execute = watched_execute  # type: ignore[assignment]

    async def fake(prompt, schema, system):
        # Yield control so sibling coroutines get a chance to interleave on the
        # shared connection — this is what exposes the missing serialisation.
        await asyncio.sleep(0)
        return MatchVerdict(fit_score=77, verdict="good", reason="r")

    try:
        out = await match_batch(
            jobs,
            user_id=uid,
            profile_text="p",
            conn=conn,
            semaphore_limit=N,  # max concurrency pressure
            llm_extract_validated_fn=fake,
        )
    finally:
        conn.execute = real_execute  # restore before assertions / teardown

    assert max_in_flight == 1, (
        f"the shared psycopg connection was used by {max_in_flight} coroutines at "
        "once — match_batch is not serialising DB access (finding C2). psycopg3 "
        "raises 'another operation is already in progress', which the batch "
        "swallows as a failed judge, silently dropping a paid verdict."
    )
    # And every verdict was persisted — nothing dropped.
    assert sum(1 for v in out if v is not None) == N
    for jid in job_ids:
        cur = await real_execute(
            "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
            (uid, jid),
        )
        row = await cur.fetchone()
        assert row["llm_fit_score"] == 77


@pytest.mark.asyncio
async def test_matcher_window_judges_backfilled_rows_not_just_fetched(stage_db, monkeypatch):
    """THE VERIFIED WINDOW (2026-08-06 audit finding). The judge used to see
    only jobs passed in from the current fetch — backfilled catalog rows that
    fill the dashboard were never judged (28 of the measured top-100 were
    unjudged junk). The window pass must judge feed rows even when the
    `jobs` argument does not contain them."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=66, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    # A BACKFILLED row: exists in the feed, NOT passed via `jobs`.
    backfilled = await _seed_job(conn, "Backfilled Data Engineer", "CatalogCo", 55, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[])

    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
        (_UID, backfilled.id),
    )
    row = await cur.fetchone()
    assert row is not None and row["llm_fit_score"] == 66, (
        "a backfilled feed row inside the display window was not judged"
    )


def test_matcher_text_includes_every_source_not_just_cv_skills():
    """THE JUDGE'S BLIND SPOT (2026-08-06). profile_to_matcher_text sent
    `job_titles + cv.skills[:30] + summary` and claimed LinkedIn/GitHub were
    "already merged into cv_data" — false for cv.skills. So the highest-signal
    stage in the funnel, and the oracle the accuracy audit measures against,
    scored every job against a CV-only, 30-skill-truncated candidate."""
    class _CV2:
        job_titles = ["AI Engineer"]
        companies = ["Acme"]
        skills = [f"CvSkill{i}" for i in range(45)]
        linkedin_skills = ["Stakeholder Management"]
        github_llm_skills = ["LangGraph"]
        github_frameworks = ["FastAPI"]
        about_me_inferred_skills = ["Mentoring"]
        summary = "Senior AI engineer."
        cv_positions = [{"title": "ML Engineer", "company": "Acme",
                         "dates": "2023-2025", "location": "London", "bullets": []}]
        linkedin_positions = [{"title": "Data Scientist", "company": "Beta",
                               "start": "2021", "end": "2023"}]
        github_repos_brief = [{"name": "fraud", "description": "Fraud detection"}]
        education = ["MSc AI"]
        certifications = ["AWS SA"]

    class _Prefs2:
        target_job_titles = ["ML Engineer"]
        experience_level = "senior"
        work_arrangement = "remote"
        preferred_workplace = "hybrid"
        salary_min = 60000
        salary_max = 90000
        needs_visa = True
        preferred_locations = ["London"]
        about_me = "I want to build agentic systems."

    class _P2:
        cv_data = _CV2()
        preferences = _Prefs2()

    txt = profile_to_matcher_text(_P2())
    # every skill source, labelled by origin
    assert "Stakeholder Management" in txt, "LinkedIn skills hidden from the judge"
    assert "LangGraph" in txt, "GitHub skills hidden from the judge"
    assert "FastAPI" in txt and "Mentoring" in txt
    assert "CvSkill44" in txt, "skills beyond the old 30-item cap were dropped"
    # dated experience — the rubric asks about seniority fit and had no data
    assert "ML Engineer at Acme (2023-2025)" in txt
    assert "Data Scientist at Beta" in txt
    # what they built, credentials, and the full preference set
    assert "Fraud detection" in txt
    assert "MSc AI" in txt and "AWS SA" in txt
    assert "needs_visa_sponsorship=true" in txt
    assert "salary_min=60000" in txt
    assert "agentic systems" in txt


def test_matcher_text_survives_a_bare_profile():
    class _Empty:
        cv_data = None
        preferences = None
    assert profile_to_matcher_text(_Empty()) == ""


@pytest.mark.asyncio
async def test_deep_bench_judges_below_the_window(stage_db, monkeypatch):
    """THE DEEP BENCH (2026-08-07). The window judges what the user sees and
    the rescue lane catches thin-text gems; neither reaches a genuinely good
    job sitting mid-pack for ordinary reasons — the iteration-2 audit still
    found 6 (fit 70-85) at ranks 100-800. A slice below the window must be
    judged each run."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.llm_matcher.MATCHER_WINDOW", 1)
    monkeypatch.setattr("src.services.llm_matcher.MATCHER_DEEP_BENCH", 2)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=71, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    top = await _seed_job(conn, "Window Job", "W", 90, _UID)
    bench1 = await _seed_job(conn, "Bench Job A", "B1", 60, _UID)
    bench2 = await _seed_job(conn, "Bench Job B", "B2", 55, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[])

    for job, label in ((top, "window"), (bench1, "deep bench 1"), (bench2, "deep bench 2")):
        cur = await conn.execute(
            "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
            (_UID, job.id),
        )
        row = await cur.fetchone()
        assert row is not None and row["llm_fit_score"] == 71, f"{label} was not judged"


@pytest.mark.asyncio
async def test_deep_bench_zero_disables_it(stage_db, monkeypatch):
    """0 must mean the window-only behaviour, byte-identical (rule #18 shape)."""
    from src import main as main_mod

    monkeypatch.setattr("src.services.llm_matcher.MATCHER_ENABLED", True)
    monkeypatch.setattr("src.services.llm_matcher.MATCHER_WINDOW", 1)
    monkeypatch.setattr("src.services.llm_matcher.MATCHER_DEEP_BENCH", 0)
    monkeypatch.setattr("src.services.profile.storage.load_profile", lambda uid: _Profile())

    async def fake(prompt, schema, system):
        return MatchVerdict(fit_score=71, verdict="good", reason="r")

    monkeypatch.setattr("src.services.llm_matcher.llm_extract_validated", fake)

    conn = stage_db._conn
    await _seed_job(conn, "Window Job", "W", 90, _UID)
    bench = await _seed_job(conn, "Bench Job", "B", 60, _UID)

    await main_mod._run_matcher_stage(stage_db, user_id=_UID, jobs=[])

    cur = await conn.execute(
        "SELECT llm_fit_score FROM user_feed WHERE user_id=? AND job_id=?",
        (_UID, bench.id),
    )
    assert (await cur.fetchone())["llm_fit_score"] is None, "deep bench ran while disabled"
