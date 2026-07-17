"""Tests for src/services/rescore.py and JobDatabase.get_catalog_jobs_for_rescore."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import JobScorer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _make_job(**overrides):
    defaults = dict(
        title="ML Engineer",
        company="Acme Corp",
        apply_url="https://example.com/job",
        source="reed",
        date_found=_NOW,
        location="London, UK",
        description="Python machine learning role.",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _make_scorer():
    config = SearchConfig(
        job_titles=["ML Engineer", "AI Engineer"],
        primary_skills=["python", "machine learning"],
        secondary_skills=["pytorch"],
    )
    return JobScorer(config)


# ---------------------------------------------------------------------------
# score_catalog_row tests
# ---------------------------------------------------------------------------


def test_score_catalog_row_returns_non_none_match_score():
    """score_catalog_row produces a ScoreBreakdown with a non-None match_score."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    row = {
        "title": "ML Engineer",
        "company": "Acme",
        "apply_url": "https://example.com/1",
        "source": "reed",
        "date_found": _NOW,
        "location": "London, UK",
        "description": "Python machine learning role with pytorch.",
        "salary_min": None,
        "salary_max": None,
        "posted_at": None,
        "date_confidence": "low",
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown is not None
    assert breakdown.match_score is not None
    assert isinstance(breakdown.match_score, int)


def test_score_catalog_row_matching_job_scores_above_threshold():
    """A clearly matching row should score well above 0."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    row = {
        "title": "Senior ML Engineer",
        "company": "DeepMind",
        "apply_url": "https://example.com/2",
        "source": "greenhouse",
        "date_found": _NOW,
        "location": "London, UK",
        "description": "We need an ML Engineer with deep Python and machine learning skills.",
        "salary_min": 70000,
        "salary_max": 110000,
        "posted_at": None,
        "date_confidence": "medium",
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown.match_score > 0


def test_score_catalog_row_tolerates_missing_optional_fields():
    """score_catalog_row must not raise when optional fields are absent in the row."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    # Only required fields
    row = {
        "title": "AI Engineer",
        "company": "Corp",
        "apply_url": "https://example.com/3",
        "source": "lever",
        "date_found": _NOW,
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown is not None


# ---------------------------------------------------------------------------
# get_catalog_jobs_for_rescore loader tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_loader_returns_inserted_jobs(db):
    """Insert 3 jobs, assert loader returns 3 dicts."""
    for i in range(3):
        await db.insert_job(_make_job(
            title=f"Engineer {i}",
            company=f"Corp{i}",
            apply_url=f"https://example.com/{i}",
        ))
    rows = await db.get_catalog_jobs_for_rescore()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_catalog_loader_respects_limit(db):
    """Insert 3 jobs, limit=2 must return exactly 2."""
    for i in range(3):
        await db.insert_job(_make_job(
            title=f"Role {i}",
            company=f"Firm{i}",
            apply_url=f"https://example.com/limit/{i}",
        ))
    rows = await db.get_catalog_jobs_for_rescore(limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_catalog_loader_returns_expected_keys(db):
    """Each returned dict must contain all keys needed by score_catalog_row."""
    await db.insert_job(_make_job())
    rows = await db.get_catalog_jobs_for_rescore()
    assert len(rows) == 1
    required_keys = {
        "id", "title", "company", "apply_url", "source", "date_found",
        "location", "description", "salary_min", "salary_max",
        "posted_at", "date_confidence",
    }
    assert required_keys <= set(rows[0].keys())


@pytest.mark.asyncio
async def test_catalog_loader_empty_when_no_jobs(db):
    """No jobs in DB → empty list, no error."""
    rows = await db.get_catalog_jobs_for_rescore()
    assert rows == []


# ---------------------------------------------------------------------------
# rescore_user_feed tests
# ---------------------------------------------------------------------------
# These need a full DB with migrations (user_feed, user_profiles,
# user_profile_versions tables). We use a tmp_path file-based DB and apply
# migrations exactly like conftest._bootstrap_async_db does, then monkeypatch
# DB_PATH on the storage module so save_profile / current_profile_version_id
# hit the test DB rather than the production one.


def _bootstrap_full_db(db_path: str) -> None:
    """Init schema + apply all migrations on a file-based test DB."""
    from migrations import runner

    async def _run():
        database = JobDatabase(db_path)
        await database.init_db()
        await database.close()
        await runner.up(db_path)

    asyncio.run(_run())


def _seed_user(db_path: str) -> str:
    """Insert a bare-minimum users row and return its id."""
    import uuid
    user_id = str(uuid.uuid4())
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?,?,?)",
            (user_id, f"{user_id}@test.example", "hash"),
        )
    return user_id


def _seed_profile(db_path: str, user_id: str) -> int:
    """Insert a profile + version row and return the version id."""
    import json
    from dataclasses import asdict

    from src.services.profile.models import CVData, UserPreferences

    cv = CVData(
        raw_text="Experienced Senior Python Engineer with ML skills.",
        skills=["python", "machine learning", "pytorch"],
        job_titles=["Senior Python Engineer", "ML Engineer"],
    )
    prefs = UserPreferences(
        target_job_titles=["Senior Python Engineer"],
        additional_skills=["python"],
        experience_level="senior",
    )
    cv_json = json.dumps(asdict(cv), default=str)
    pref_json = json.dumps(asdict(prefs), default=str)
    now = datetime.now(timezone.utc).isoformat()
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO user_profiles(user_id, cv_data, preferences, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "cv_data=excluded.cv_data, preferences=excluded.preferences, "
            "updated_at=excluded.updated_at",
            (user_id, cv_json, pref_json, now),
        )
        cur = conn.execute(
            "INSERT INTO user_profile_versions(user_id, created_at, source_action, cv_data, preferences) "
            "VALUES (?,?,?,?,?)",
            (user_id, now, "test_seed", cv_json, pref_json),
        )
        version_id = cur.lastrowid
    return version_id


@pytest.fixture
def full_db(tmp_path):
    """File-based DB with full migration set applied."""
    db_path = str(tmp_path / "rescore_test.db")
    _bootstrap_full_db(db_path)
    return db_path


@pytest.mark.asyncio
async def test_rescore_user_feed_scores_matching_jobs(full_db, monkeypatch):
    """Seed 3 catalog jobs + a senior-python profile; rescore writes feed rows
    for matching jobs, each stamped with the profile_version id."""
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)

    user_id = _seed_user(full_db)
    version_id = _seed_profile(full_db, user_id)

    # Seed 3 catalog jobs — commit before closing so the second connection sees them
    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await db.insert_job(Job(
            title="Senior Python Engineer",
            company="Acme",
            apply_url="https://example.com/j1",
            source="reed",
            date_found=_NOW,
            location="London, UK",
            description="Python machine learning senior role. python pytorch.",
        ))
        await db.insert_job(Job(
            title="Marketing Intern",
            company="CorpB",
            apply_url="https://example.com/j2",
            source="reed",
            date_found=_NOW,
            location="London, UK",
            description="Marketing internship position. Social media campaigns.",
        ))
        await db.insert_job(Job(
            title="Sales Representative",
            company="CorpC",
            apply_url="https://example.com/j3",
            source="reed",
            date_found=_NOW,
            location="London, UK",
            description="Sales representative role. B2B sales and account management.",
        ))
        await db._conn.commit()  # flush before rescore opens a second connection
    finally:
        await db.close()

    from src.services.rescore import rescore_user_feed
    result = await rescore_user_feed(user_id, db_path=full_db)

    assert result["rescored"] > 0, f"Expected >0 rescored, got {result}"
    assert result["version"] == version_id

    # Verify the python-matching job got a feed row stamped with the version
    with pgsync.connect(full_db) as conn:
        rows = conn.execute(
            "SELECT j.title, f.score, f.profile_version "
            "FROM user_feed f JOIN jobs j ON j.id = f.job_id "
            "WHERE f.user_id = ? ORDER BY f.score DESC",
            (user_id,),
        ).fetchall()

    assert len(rows) > 0, "Expected at least one feed row"
    # All feed rows must be stamped with the profile_version
    for title, _score, pv in rows:
        assert pv == version_id, f"Row '{title}' has profile_version={pv}, expected {version_id}"
    # The senior python role should have the highest score
    assert rows[0][0] == "Senior Python Engineer", (
        f"Expected 'Senior Python Engineer' at top, got '{rows[0][0]}'"
    )

    # Score for the top row must equal what score_catalog_row returns
    from src.services.profile.keyword_generator import generate_search_config
    from src.services.profile.storage import load_profile
    from src.services.rescore import score_catalog_row
    profile = load_profile(user_id)
    config = generate_search_config(profile)
    scorer = JobScorer(config, user_preferences=profile.preferences, enrichment_lookup=lambda j: None)
    db2 = JobDatabase(full_db)
    await db2.init_db()
    try:
        catalog_rows = await db2.get_catalog_jobs_for_rescore()
    finally:
        await db2.close()
    top_catalog = next(r for r in catalog_rows if r["title"] == "Senior Python Engineer")
    expected_score = int(score_catalog_row(scorer, top_catalog).match_score)
    assert rows[0][1] == expected_score


@pytest.mark.asyncio
async def test_rescore_clears_verdicts(full_db, monkeypatch):
    """Any pre-existing llm_matched_at must be NULL after rescore when MATCHER_ENABLED=True.

    FIX 1: clear_user_verdicts only runs when MATCHER_ENABLED is True (the flag is
    off by default in tests via conftest.py, so we must enable it explicitly here).
    We also stub _run_matcher_stage so no real LLM calls are made.
    """
    import src.services.llm_matcher as matcher_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    # FIX 1: enable the matcher so clear_user_verdicts actually runs
    monkeypatch.setattr(matcher_mod, "MATCHER_ENABLED", True)
    monkeypatch.setattr(matcher_mod, "MATCHER_THRESHOLD", 30)

    # Stub _run_matcher_stage so we never call a real LLM
    async def _fake_matcher_stage(db, *, user_id, jobs):
        pass

    monkeypatch.setattr("src.main._run_matcher_stage", _fake_matcher_stage, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    # Insert a catalog job and a feed row with a fake verdict
    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await db.insert_job(Job(
            title="Senior Python Engineer",
            company="VerdictCorp",
            apply_url="https://example.com/verdict/j1",
            source="reed",
            date_found=_NOW,
            location="London, UK",
            description="Python machine learning senior role.",
        ))
        # Get its id
        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE company = 'VerdictCorp' LIMIT 1"
        )
        row = await cur.fetchone()
        job_id = row[0]
        # Insert feed row with a verdict (llm_matched_at set)
        now = datetime.now(timezone.utc).isoformat()
        await db._conn.execute(
            "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at, "
            "llm_fit_score, llm_verdict, llm_reason, llm_matched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, job_id, 50, "24h", "active", now, now, 88, "strong fit", "python overlap", now),
        )
        await db._conn.commit()  # flush before rescore opens a second connection
    finally:
        await db.close()

    from src.services.rescore import rescore_user_feed
    await rescore_user_feed(user_id, db_path=full_db)

    with pgsync.connect(full_db) as conn:
        r = conn.execute(
            "SELECT llm_matched_at FROM user_feed WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        ).fetchone()
    assert r is not None, "Feed row must still exist after rescore"
    assert r[0] is None, f"llm_matched_at should be NULL after rescore (MATCHER_ENABLED=True), got {r[0]}"


@pytest.mark.asyncio
async def test_rescore_no_profile_returns_early(full_db, monkeypatch):
    """A user with no profile (or incomplete profile) → returns no_profile without DB writes."""
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)

    user_id = _seed_user(full_db)
    # No profile seeded intentionally

    from src.services.rescore import rescore_user_feed
    result = await rescore_user_feed(user_id, db_path=full_db)

    assert result == {"rescored": 0, "reason": "no_profile"}

    # No feed rows written
    with pgsync.connect(full_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM user_feed WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    assert count == 0, f"Expected 0 feed rows for profileless user, got {count}"


# ---------------------------------------------------------------------------
# FIX 1 — MATCHER_ENABLED=False -> verdict path is a complete no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescore_matcher_off_never_clears_verdicts(full_db, monkeypatch):
    """FIX 1: with MATCHER_ENABLED=False (default in tests), clear_user_verdicts
    is NEVER imported or called — the verdict path is a complete no-op."""
    import src.services.llm_matcher as matcher_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    # Explicitly confirm the flag is off (conftest sets it, but be explicit)
    monkeypatch.setattr(matcher_mod, "MATCHER_ENABLED", False)

    clear_calls = []

    async def fake_clear(conn, uid):
        clear_calls.append(uid)
        return 0

    monkeypatch.setattr(matcher_mod, "clear_user_verdicts", fake_clear)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    from src.services.rescore import rescore_user_feed
    result = await rescore_user_feed(user_id, db_path=full_db)

    assert clear_calls == [], (
        "FIX 1 violated: clear_user_verdicts was called even though MATCHER_ENABLED=False"
    )
    assert "rescored" in result


# ---------------------------------------------------------------------------
# FIX 3 — per-row try/except: one bad row does not abort the whole re-score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescore_bad_row_does_not_abort(full_db, monkeypatch):
    """FIX 3: if score_catalog_row raises for one job, other jobs are still processed."""
    import src.services.llm_matcher as matcher_mod
    import src.services.profile.storage as storage_mod
    from src.services import rescore as rescore_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(matcher_mod, "MATCHER_ENABLED", False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    # Seed two catalog jobs
    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await db.insert_job(Job(
            title="Python Engineer",
            company="GoodCo",
            apply_url="https://example.com/good",
            source="test",
            date_found=_NOW,
            location="London, UK",
            description="Senior Python software engineer role.",
        ))
        await db.insert_job(Job(
            title="Another Role",
            company="OtherCo",
            apply_url="https://example.com/other",
            source="test",
            date_found=_NOW,
            location="London, UK",
            description="Some other job description with python skills.",
        ))
        await db._conn.commit()
    finally:
        await db.close()

    call_count = {"n": 0}
    orig = rescore_mod.score_catalog_row

    def patched_score(scorer, row):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("FIX 3 — simulated corrupt row")
        return orig(scorer, row)

    monkeypatch.setattr(rescore_mod, "score_catalog_row", patched_score)

    result = await rescore_mod.rescore_user_feed(user_id, db_path=full_db)

    # Both rows were attempted
    assert call_count["n"] == 2, f"Expected 2 score_catalog_row calls, got {call_count['n']}"
    # The result is returned normally (no exception propagated)
    assert "rescored" in result
    # At least one row succeeded
    assert result["rescored"] >= 1, (
        "Expected at least one row to succeed after the first row raised"
    )


# ---------------------------------------------------------------------------
# FIX 4 — per-user asyncio.Lock keeps the dict populated after a normal run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescore_per_user_lock_is_present_and_released(full_db, monkeypatch):
    """FIX 4: after rescore_user_feed completes, the per-user Lock exists in
    _user_rescore_locks and is NOT held (released cleanly)."""
    import asyncio as _asyncio

    import src.services.llm_matcher as matcher_mod
    import src.services.profile.storage as storage_mod
    from src.services import rescore as rescore_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(matcher_mod, "MATCHER_ENABLED", False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    # Clear the lock dict so we start fresh
    rescore_mod._user_rescore_locks.clear()

    await rescore_mod.rescore_user_feed(user_id, db_path=full_db)

    assert user_id in rescore_mod._user_rescore_locks, (
        "FIX 4: per-user Lock was never added to _user_rescore_locks"
    )
    lock = rescore_mod._user_rescore_locks[user_id]
    assert isinstance(lock, _asyncio.Lock)
    assert not lock.locked(), "FIX 4: Lock is still held after rescore_user_feed returned"


# ---------------------------------------------------------------------------
# FIX 5 — score_catalog_row sets job.id from the row dict
# ---------------------------------------------------------------------------


def test_score_catalog_row_sets_job_id_from_row():
    """FIX 5: the job passed to enrichment_lookup must have .id == row['id'].

    enrichment_lookup is only called when user_preferences is not None
    (see skill_matcher.py line 519). We therefore supply a UserPreferences so
    the scorer actually reaches the lookup call.
    """
    from src.services.profile.models import SearchConfig, UserPreferences
    from src.services.rescore import score_catalog_row
    from src.services.skill_matcher import JobScorer

    captured = []

    config = SearchConfig(
        job_titles=["ML Engineer"],
        primary_skills=["python"],
        secondary_skills=[],
    )

    def capturing_lookup(job):
        captured.append(getattr(job, "id", "MISSING"))
        return None

    scorer_with_lookup = JobScorer(
        config,
        # Must pass user_preferences so the scorer reaches the enrichment_lookup branch
        user_preferences=UserPreferences(target_job_titles=["ML Engineer"]),
        enrichment_lookup=capturing_lookup,
    )

    row = {
        "id": 99,
        "title": "ML Engineer",
        "company": "TestCo",
        "apply_url": "https://example.com/99",
        "source": "test",
        "date_found": _NOW,
        "location": "London, UK",
        "description": "Python machine learning role.",
        "salary_min": None,
        "salary_max": None,
        "posted_at": None,
        "date_confidence": "low",
    }
    score_catalog_row(scorer_with_lookup, row)

    assert len(captured) >= 1, "enrichment_lookup was never called"
    assert captured[0] == 99, (
        f"FIX 5: expected job.id == 99 inside enrichment_lookup, got {captured[0]!r}"
    )


def test_rescore_logger_is_under_job360_namespace():
    """Regression guard: the rescore logger MUST be under the 'job360' namespace
    so setup_logging()'s handlers (stdout/file/JSON) actually emit its records.
    A bare __name__ logger lands on the root logger (no job360 handler, WARNING
    default) and its INFO lines vanish — proven live during verification."""
    import src.services.rescore as rescore_mod

    assert rescore_mod.logger.name == "job360.services.rescore"
