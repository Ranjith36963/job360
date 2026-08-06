"""The enrichment self-heal cron (2026-08-05): coverage of the candidate pool
must converge WITHOUT a human or a laptop — measured prod gap at ship time was
738/6,589 active candidates enriched (11%), the rest depending on manual
sweeps. Pins: budget + prioritization, E2-off no-op, empty-coverage no-op."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from migrations import runner
from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase

_NOW = datetime.now(timezone.utc).isoformat()


async def _full_db(path: str) -> JobDatabase:
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path)
    db = JobDatabase(path)
    await db.init_db()
    return db


def _seed_user(db_path: str) -> str:
    user_id = str(uuid.uuid4())
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?,?,?)",
            (user_id, f"{user_id}@t.example", "hash"),
        )
    return user_id


def _mk(i: int) -> Job:
    return Job(
        title=f"Engineer {i}", company=f"Co{i}", apply_url=f"https://x/{i}",
        source="reed", date_found=_NOW, location="London, UK",
        description="Python role.",
    )


@pytest.mark.asyncio
async def test_sweep_enriches_highest_value_candidates_within_budget(tmp_path, monkeypatch):
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", True, raising=False)
    monkeypatch.setenv("ENRICHMENT_SWEEP_PER_TICK", "2")

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        for i in range(4):
            await db.insert_job(_mk(i))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs ORDER BY id")
        ids = [r[0] for r in await cur.fetchall()]
        # Active feed rows with distinct scores; job ids[3] highest value.
        for jid, score in zip(ids, (10, 20, 30, 90)):
            await conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, jid, score, "older", _NOW, _NOW),
            )
        await db.commit()

        captured: list[int] = []

        async def fake_enrich_batch(jobs, semaphore_limit=3, conn=None, skip_existing=True, **kw):
            captured.extend(j.id for j in jobs)
            return [object()] * len(jobs)

        with patch("src.services.job_enrichment.enrich_batch", new=fake_enrich_batch):
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["enriched"] == 2, result
        assert set(captured) == {ids[3], ids[2]}, (
            f"must pick the 2 HIGHEST-value candidates, got {captured}"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sweep_noop_when_e2_off(tmp_path, monkeypatch):
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", False, raising=False)
    monkeypatch.setattr(tasks_mod, "ENRICHMENT_ENABLED", False)

    called = {"n": 0}

    async def fake_enrich_batch(*a, **kw):
        called["n"] += 1
        return []

    with patch("src.services.job_enrichment.enrich_batch", new=fake_enrich_batch):
        result = await tasks_mod.enrichment_sweep({"db": None})

    assert result == {"enriched": 0, "reason": "e2_off"}
    assert called["n"] == 0, "E2 off must mean zero LLM calls (rule #18)"


@pytest.mark.asyncio
async def test_sweep_reports_complete_when_nothing_missing(tmp_path, monkeypatch):
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", True, raising=False)

    db = await _full_db(str(tmp_path / "t2.db"))
    try:
        result = await tasks_mod.enrichment_sweep({"db": db._db})
        assert result["reason"] == "coverage_complete"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sweep_repairs_hollow_enrichment_when_text_arrives(tmp_path, monkeypatch):
    """REPAIR phase (Pillar-2 sim finding): enrichment that ran on an empty
    description extracted all-unknowns and skip_existing froze it forever.
    Once the job HAS text, leftover budget must re-enrich it in place."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", True, raising=False)
    monkeypatch.setenv("ENRICHMENT_SWEEP_PER_TICK", "5")

    db = await _full_db(str(tmp_path / "t3.db"))
    try:
        await db.insert_job(Job(
            title="ML Engineer", company="Co", apply_url="https://x/1",
            source="reed", date_found=_NOW, location="London, UK",
            description="A real, substantial description " * 20,
        ))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t3.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await conn.execute(
            "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
            "VALUES (?,?,?,?,'active',?,?)", (user_id, jid, 50, "older", _NOW, _NOW))
        # A hollow enrichment row — the empty-text artifact.
        await conn.execute(
            "INSERT INTO job_enrichment(job_id, title_canonical, category, visa_sponsorship, "
            "seniority, workplace_type, enriched_at) VALUES (?,?,?,?,?,?,?)",
            (jid, "ML Engineer", "software_engineering", "unknown", "unknown", "unknown", _NOW))
        await db.commit()

        calls = []

        async def fake_enrich_batch(jobs, semaphore_limit=3, conn=None, skip_existing=True, **kw):
            calls.append((sorted(j.id for j in jobs), skip_existing))
            return [object()] * len(jobs)

        with patch("src.services.job_enrichment.enrich_batch", new=fake_enrich_batch):
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result.get("repaired") == 1, result
        # The repair call must run with skip_existing=False so it overwrites.
        assert any(ids == [jid] and skip is False for ids, skip in calls), calls
    finally:
        await db.close()
