"""LLM matcher (funnel -> judge).

All LLM traffic is mocked via the injected ``llm_extract_validated_fn``
(CLAUDE.md rule #4 — suite must run offline).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from src.models import Job
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
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
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
            match_score INTEGER DEFAULT 0
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
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
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
            match_score INTEGER DEFAULT 0
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
