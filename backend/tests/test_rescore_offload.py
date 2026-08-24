"""The catalog re-score must never run on the event loop thread.

The bug this pins
-----------------
``backfill_feed_from_catalog`` is called on EVERY per-user search
(``src/main.py`` ~:1108). Its Phase-1 loop scored up to 50,000 catalog rows
with regex-heavy, fully synchronous work and ZERO awaits inside the loop. On the
single FastAPI event loop that freezes every other user's request AND the
``/api/search/{id}/status`` poll the UI depends on — the exact "Lost contact with
the server while searching" failure PR #123 already fixed once in ``run_search``
(``src/main.py`` :895 now uses ``asyncio.to_thread``). ``rescore_user_feed`` (run
on every profile save, ``api/routes/profile.py`` :89) had the identical loop.

Why THREAD IDENTITY, not timing
-------------------------------
A timing assertion only fires when the fixture happens to be big enough to be
slow, and conftest makes ``asyncio.sleep`` instant. Asking "which thread ran the
scorer?" is deterministic on three rows and immune to both. The one timing test
at the bottom exists only to reproduce the USER-VISIBLE symptom.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase

_NOW = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Local seed helpers (deliberately NOT imported from tests/test_rescore.py —
# importing a fixture across test modules double-imports it and breaks the
# per-test Postgres schema isolation).
# ---------------------------------------------------------------------------


def _bootstrap_full_db(db_path: str) -> None:
    from migrations import runner

    async def _run():
        database = JobDatabase(db_path)
        await database.init_db()
        await database.close()
        await runner.up(db_path)

    asyncio.run(_run())


def _seed_user(db_path: str) -> str:
    user_id = str(uuid.uuid4())
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?,?,?)",
            (user_id, f"{user_id}@test.example", "hash"),
        )
    return user_id


def _seed_profile(db_path: str, user_id: str) -> int:
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
            "INSERT INTO user_profile_versions(user_id, created_at, source_action, "
            "cv_data, preferences) VALUES (?,?,?,?,?)",
            (user_id, now, "test_seed", cv_json, pref_json),
        )
        version_id = cur.lastrowid
    return version_id


async def _seed_catalog(db_path: str, n: int = 4) -> None:
    db = JobDatabase(db_path)
    await db.init_db()
    try:
        for i in range(n):
            await db.insert_job(
                Job(
                    title=f"Senior Python Engineer {i}",
                    company=f"Acme {i}",
                    apply_url=f"https://example.com/j{i}",
                    source="reed",
                    date_found=_NOW,
                    location="London, UK",
                    description="Python machine learning senior role. python pytorch.",
                )
            )
        await db._conn.commit()
    finally:
        await db.close()


@pytest.fixture
def full_db(tmp_path):
    db_path = str(tmp_path / "rescore_offload_test.db")
    _bootstrap_full_db(db_path)
    return db_path


def _thread_recorder(monkeypatch):
    """Wrap ``rescore.score_catalog_row`` so every call records where it ran.

    Records ``True`` when a running event loop is visible from the calling
    thread — i.e. the scoring is happening ON the loop and every other request
    is frozen for its duration.
    """
    import src.services.rescore as rescore_mod

    real = rescore_mod.score_catalog_row
    on_loop: list[bool] = []

    def _recorder(scorer, row):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(scorer, row)

    monkeypatch.setattr(rescore_mod, "score_catalog_row", _recorder)
    return on_loop


# ---------------------------------------------------------------------------
# T5 / T6 — thread-identity proof for both catalog-scoring paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_scores_off_the_event_loop(full_db, monkeypatch):
    """Every per-user search runs this. It must not score on the loop thread."""
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)
    await _seed_catalog(full_db)

    on_loop = _thread_recorder(monkeypatch)

    from src.services.rescore import backfill_feed_from_catalog

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    assert on_loop, "the scorer never ran — the fixture proves nothing"
    assert all(c is False for c in on_loop), (
        "score_catalog_row executed ON the event loop thread "
        f"({on_loop.count(True)} of {len(on_loop)} rows) — the whole catalog scan "
        "freezes every other request; wrap Phase 1 in asyncio.to_thread"
    )


@pytest.mark.asyncio
async def test_rescore_user_feed_scores_off_the_event_loop(full_db, monkeypatch):
    """The twin path — fired on every profile save (api/routes/profile.py:89)."""
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)
    await _seed_catalog(full_db)

    on_loop = _thread_recorder(monkeypatch)

    from src.services.rescore import rescore_user_feed

    await rescore_user_feed(user_id, db_path=full_db)

    assert on_loop, "the scorer never ran — the fixture proves nothing"
    assert all(c is False for c in on_loop), (
        "score_catalog_row executed ON the event loop thread "
        f"({on_loop.count(True)} of {len(on_loop)} rows) — wrap Phase 1 in "
        "asyncio.to_thread"
    )


# ---------------------------------------------------------------------------
# T7 — the enrichment lookup sits in the SAME frozen window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrichment_lookup_parses_off_loop(tmp_path, monkeypatch):
    """``_build_enrichment_lookup`` deserialises every enrichment row.

    It runs immediately before Phase 1 on BOTH re-score paths and grows toward
    100% catalog coverage by design, so its JSON parsing belongs off the loop
    too.
    """
    from src.repositories import pg
    from src.services import job_enrichment as je
    from src.services.job_enrichment import save_enrichment
    from src.services.job_enrichment_schema import (
        EmploymentType,
        ExperienceLevel,
        JobCategory,
        JobEnrichment,
        SalaryBand,
        SalaryFrequency,
        SeniorityLevel,
        VisaSponsorship,
        WorkplaceType,
    )

    db_path = tmp_path / "enrichment_offload.db"
    repo_root = Path(__file__).resolve().parent.parent
    with pgsync.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, company TEXT, "
            "location TEXT, description TEXT);"
        )
        conn.executescript(
            (repo_root / "migrations" / "0008_job_enrichment.up.sql").read_text()
        )
        conn.commit()

    enrichment = JobEnrichment(
        title_canonical="Machine Learning Engineer",
        category=JobCategory.MACHINE_LEARNING,
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.HYBRID,
        locations=["London, UK"],
        salary=SalaryBand(
            min=60000, max=90000, currency="GBP", frequency=SalaryFrequency.ANNUAL
        ),
        required_skills=["Python"],
        preferred_skills=["MLOps"],
        experience_min_years=3,
        experience_level=ExperienceLevel.MID,
        requirements_summary="Ship ML systems.",
        language="en",
        # `employer_type` was RETIRED 2026-08 (measured on 3,119 live enriched
        # rows) and no longer exists on JobEnrichment. This test predates that,
        # and the field is irrelevant to what it proves — that the lookup
        # deserialises off the event loop — so it is simply dropped.
        visa_sponsorship=VisaSponsorship.YES,
        seniority=SeniorityLevel.MID,
        remote_region=None,
        apply_instructions=None,
        red_flags=[],
    )

    on_loop: list[bool] = []
    real_model = je.JobEnrichment

    def _recording_model(**kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_model(**kwargs)

    conn = await pg.connect(str(db_path))
    try:
        await conn.execute(
            "INSERT INTO jobs (id, title, company) VALUES (1, 'ML Engineer', 'Acme')"
        )
        await save_enrichment(conn, 1, enrichment)
        monkeypatch.setattr(je, "JobEnrichment", _recording_model)
        lookup = await je._build_enrichment_lookup(conn)
    finally:
        await conn.close()

    assert lookup, "the lookup must actually have parsed a row"
    assert on_loop, "no enrichment row was parsed — the fixture proves nothing"
    assert all(c is False for c in on_loop), (
        "_build_enrichment_lookup parsed enrichment rows ON the event loop "
        "thread — it runs in the same frozen window as the catalog re-score"
    )


# ---------------------------------------------------------------------------
# The user-visible symptom, reproduced (timing — the WHY, not the guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_keeps_loop_responsive_under_load(full_db, monkeypatch):
    """While the catalog is scored, the status poll must still get answered.

    Stands in for ``/api/search/{id}/status``: a heartbeat task that only ever
    asks for ``asyncio.sleep(0)`` (a bare yield — unaffected by conftest's
    instant-sleep monkeypatch) and records the worst gap between iterations.
    Before the fix the whole scan ran in ONE loop callback, so the heartbeat did
    not run at all until it finished.
    """
    import src.services.profile.storage as storage_mod
    import src.services.rescore as rescore_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)
    await _seed_catalog(full_db, n=20)

    real = rescore_mod.score_catalog_row

    def _slow(scorer, row):
        # Real time.sleep — conftest patches asyncio.sleep only. 20 rows x 50 ms
        # = ~1 s of CPU-shaped work, the shape of a real catalog scan.
        time.sleep(0.05)
        return real(scorer, row)

    monkeypatch.setattr(rescore_mod, "score_catalog_row", _slow)

    gaps: list[float] = []
    stop = threading.Event()

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)

    from src.services.rescore import backfill_feed_from_catalog

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    stop.set()
    await hb

    worst = max(gaps) if gaps else 0.0
    assert worst < 0.25, (
        f"the event loop went unanswered for {worst*1000:.0f} ms during the "
        "catalog scan — every other user's request and the search-status poll "
        "were frozen for that long"
    )
