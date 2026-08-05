"""Convergence backfill (2026-08-05): each run embeds a budget of EXISTING
catalog jobs missing vectors, so coverage reaches 100% through ordinary
searches — no manual sweep on the prod box. Pins: the budget cap, idempotence
(already-embedded jobs skipped), and per-row fault tolerance."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from migrations import runner
from src.models import Job
from src.repositories.database import JobDatabase


async def _full_db(path: str) -> JobDatabase:
    """init + ALL migrations — job_embeddings arrives in migration 0009; without
    it the schema-per-test isolation silently falls through to public (the
    search_path trap in saved memory)."""
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path)
    db = JobDatabase(path)
    await db.init_db()
    return db

_NOW = datetime.now(timezone.utc).isoformat()


def _mk(i: int) -> Job:
    return Job(
        title=f"Engineer {i}", company=f"Co{i}",
        apply_url=f"https://x/{i}", source="reed",
        date_found=_NOW, location="London, UK",
        description="Python role.",
    )


@pytest.mark.asyncio
async def test_backfill_embeds_only_missing_up_to_budget(tmp_path):
    from src import main as main_mod

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        for i in range(5):
            await db.insert_job(_mk(i))
        conn = db._db
        # Pretend job #1 is already embedded.
        cur = await conn.execute("SELECT id FROM jobs ORDER BY id")
        ids = [r[0] for r in await cur.fetchall()]
        await conn.execute(
            "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, 'm')", (ids[0],)
        )
        await db.commit()

        upserted: list[int] = []

        class _FakeVix:
            def upsert(self, job_id=None, vector=None, metadata=None):
                upserted.append(int(job_id))

        def fake_encode(job, enrichment=None):
            return [0.0] * 3

        async def fake_load_enrichment(conn, jid):
            return None

        with patch("src.services.vector_index.VectorIndex", return_value=_FakeVix()), \
             patch("src.services.embeddings.encode_job", new=fake_encode), \
             patch("src.services.job_enrichment.load_enrichment", new=fake_load_enrichment):
            n = await main_mod._embed_backfill_budget(db, conn, budget=2)

        assert n == 2, "budget must cap the sweep"
        assert ids[0] not in upserted, "an already-embedded job was re-embedded"
        cur = await conn.execute("SELECT count(*) FROM job_embeddings")
        assert (await cur.fetchone())[0] == 3  # 1 pre-existing + 2 new
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_survives_a_poison_row(tmp_path):
    from src import main as main_mod

    db = await _full_db(str(tmp_path / "t2.db"))
    try:
        for i in range(3):
            await db.insert_job(_mk(i))
        conn = db._db
        calls = {"n": 0}

        class _FakeVix:
            def upsert(self, **kw):
                pass

        def flaky_encode(job, enrichment=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("encoder blew up")
            return [0.0] * 3

        async def fake_load_enrichment(conn, jid):
            return None

        with patch("src.services.vector_index.VectorIndex", return_value=_FakeVix()), \
             patch("src.services.embeddings.encode_job", new=flaky_encode), \
             patch("src.services.job_enrichment.load_enrichment", new=fake_load_enrichment):
            n = await main_mod._embed_backfill_budget(db, conn, budget=10)

        assert n == 2, "the poison row must be skipped, the rest embedded"
    finally:
        await db.close()
