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
