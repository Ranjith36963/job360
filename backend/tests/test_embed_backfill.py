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
        # "Already embedded" now means "has a VECTOR", not "has an audit row" —
        # that distinction IS the fix (a Chroma-era audit row with no vector
        # must be re-embedded, never skipped). Seed whichever the DB supports.
        from src.services.pg_vector_index import vector_column_available
        if vector_column_available(str(tmp_path / "t.db")):
            await conn.execute(
                "INSERT INTO job_embeddings(job_id, model_version, embedding) "
                # `public.vector`, never a bare `vector`: pgvector installs into
                # public, and Postgres resolves a TYPE name through search_path
                # just like a table name. Test schemas do not include public
                # (that fallback let a "private" test read the shared catalog),
                # so an unqualified cast fails with `type "vector" does not
                # exist`. Production code already qualifies every one of these
                # — see pg_vector_index.py:118,147-149.
                "VALUES (?, 'm', ?::public.vector)",
                (ids[0], "[" + ",".join(["0.1"] * 384) + "]"),
            )
        else:
            await conn.execute(
                "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, 'm')",
                (ids[0],),
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

        with patch("src.services.pg_vector_index.PgVectorIndex", return_value=_FakeVix()), \
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

        with patch("src.services.pg_vector_index.PgVectorIndex", return_value=_FakeVix()), \
             patch("src.services.embeddings.encode_job", new=flaky_encode), \
             patch("src.services.job_enrichment.load_enrichment", new=fake_load_enrichment):
            n = await main_mod._embed_backfill_budget(db, conn, budget=10)

        assert n == 2, "the poison row must be skipped, the rest embedded"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_missing_semantic_stack_stops_the_sweep_loudly(tmp_path, caplog):
    """ONE missing package must not read as N unrelated job failures.

    The worker image is built from Dockerfile.worker with a plain
    ``pip install .`` — no ``[semantic]`` extra — while the API image installs
    torch + ``.[semantic]``. Confirmed in the live Railway build log. The worker
    runs ``refresh_catalog``, so the process ingesting the most jobs is the one
    that cannot embed them: 687 vectors for 9,977 jobs in production.

    It survived because ``sentence_transformers`` is imported lazily (rule #16),
    so the module import succeeds and the failure only appears per job as
    "embed backfill: job <id> failed" — a warning naming neither the cause nor
    the remedy, repeated for every row.

    The escalation is deliberately NOT an upfront probe. The tests above inject
    their own encoder and never need the real package; an upfront probe wrongly
    abandoned those runs, which is how CI caught the first version of this fix.
    It fires only after a real failure, when the stack is genuinely absent.
    """
    import builtins
    import logging

    from src import main as main_mod

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        for i in range(3):
            await db.insert_job(_mk(i))
        conn = db._db
        await db.commit()

        real_import = builtins.__import__

        def _no_sentence_transformers(name, *args, **kwargs):
            if name == "sentence_transformers" or name.startswith(
                "sentence_transformers."
            ):
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        class _FakeVix:
            def upsert(self, **kw):
                pass

        def encode_that_needs_the_missing_package(job, enrichment=None):
            # What the real encoder does on the worker: raises because the
            # package it lazily imports is not installed.
            raise RuntimeError(
                "sentence-transformers is not installed — run "
                "`pip install '.[semantic]'` and retry."
            )

        async def fake_load_enrichment(conn_, jid):
            return None

        with patch("src.services.pg_vector_index.PgVectorIndex", return_value=_FakeVix()), \
             patch("src.services.embeddings.encode_job",
                   new=encode_that_needs_the_missing_package), \
             patch("src.services.job_enrichment.load_enrichment", new=fake_load_enrichment), \
             patch.object(builtins, "__import__", _no_sentence_transformers), \
             caplog.at_level(logging.WARNING, logger="job360"):
            n = await main_mod._embed_backfill_budget(db, conn, budget=10)

        assert n == 0

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, (
            "a completely un-embeddable process logged no ERROR — this is the "
            "silence that hid 6.9% coverage behind green instruments"
        )
        text = " ".join(r.getMessage() for r in errors).lower()
        assert "sentence-transformers" in text and "semantic_enabled" in text, (
            f"the error does not name the cause or the flag: {text[:200]}"
        )

        # It STOPPED rather than repeating itself once per row. Three jobs were
        # queued; a per-job warning storm is exactly what this replaces.
        per_job = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "job" in r.getMessage()
            and "failed" in r.getMessage()
        ]
        assert not per_job, (
            f"still emitting per-job warnings ({len(per_job)}) instead of one "
            "actionable error"
        )
    finally:
        await db.close()
