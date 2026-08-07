"""PgVectorIndex — the job vector store, in Postgres.

WHY IT EXISTS (2026-08-07): the Chroma-on-local-disk store made semantic
search structurally impossible in this deployment — the worker (which runs the
only scheduled pipeline) has no semantic extra, backend and worker have
separate disks, and worst, `job_embeddings` claimed coverage the on-disk index
might not have, so the backfill skipped jobs forever. Measured: catalog grew
7,761 -> 8,184 overnight while embeddings stayed at exactly 284, with
SEMANTIC_ENABLED=true throughout.

These tests pin the contract that makes those failures impossible: the vector
lives in the same row as its bookkeeping.
"""
from __future__ import annotations

import asyncio

import pytest

from migrations import runner
from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase

_NOW = "2026-08-07T00:00:00+00:00"


def _pgvector_available() -> bool:
    """Migration 0027 is tolerant, so a Postgres without pgvector simply has
    no `embedding` column and this store degrades to empty results. Skip
    rather than fail there — CI runs the pgvector image (ci.yml /
    ci-offline.yml) and prod has pgvector 0.8.3, so these still gate the real
    environments."""
    try:
        from src.core.settings import DB_PATH

        with pgsync.connect(str(DB_PATH)) as c:
            row = c.execute(
                "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
            ).fetchone()
            return bool(row and row[0])
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _pgvector_available(), reason="pgvector not available on this Postgres"
)


def _vec(*leading: float) -> list[float]:
    v = list(leading)
    return v + [0.0] * (384 - len(v))


@pytest.fixture
def vec_db(tmp_path):
    path = str(tmp_path / "vec.db")

    async def _boot():
        db = JobDatabase(path)
        await db.init_db()
        for i in range(3):
            await db.insert_job(Job(
                title=f"Job {i}", company=f"Co{i}", apply_url=f"https://x/{i}",
                source="reed", date_found=_NOW, location="London, UK",
                description="text",
            ))
        await db.close()
        await runner.up(path)

    asyncio.run(_boot())
    return path


def _job_ids(path):
    with pgsync.connect(path) as c:
        return [r[0] for r in c.execute("SELECT id FROM jobs ORDER BY id").fetchall()]


def test_upsert_query_roundtrip_ranks_by_cosine_distance(vec_db):
    from src.services.pg_vector_index import PgVectorIndex

    ids = _job_ids(vec_db)
    vix = PgVectorIndex(vec_db)
    vix.upsert(ids[0], _vec(1.0))          # identical to the query
    vix.upsert(ids[1], _vec(0.0, 1.0))     # orthogonal
    res = vix.query(_vec(1.0), k=5)

    assert res[0][0] == ids[0], "nearest neighbour is not first"
    assert res[0][1] == pytest.approx(0.0, abs=1e-4)
    assert res[1][1] > res[0][1], "distances must ascend"


def test_upsert_is_idempotent_and_replaces(vec_db):
    from src.services.pg_vector_index import PgVectorIndex

    ids = _job_ids(vec_db)
    vix = PgVectorIndex(vec_db)
    vix.upsert(ids[0], _vec(1.0))
    vix.upsert(ids[0], _vec(0.0, 1.0))  # replace
    assert vix.count() == 1
    # The replacement vector is what answers the query now.
    res = vix.query(_vec(0.0, 1.0), k=1)
    assert res[0][0] == ids[0] and res[0][1] == pytest.approx(0.0, abs=1e-4)


def test_count_reports_vectors_not_audit_rows(vec_db):
    """THE BUG THIS STORE REMOVES. Under Chroma-on-disk, an audit row could
    say "embedded" while the vector was gone with the container — and the
    backfill skipped those jobs forever. Here an audit row without a vector
    must NOT be counted as coverage."""
    from src.services.pg_vector_index import PgVectorIndex

    ids = _job_ids(vec_db)
    with pgsync.connect(vec_db) as c:
        c.execute(
            "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, 'legacy')",
            (ids[2],),
        )
    vix = PgVectorIndex(vec_db)
    assert vix.count() == 0, "an audit row with no vector was counted as coverage"

    vix.upsert(ids[2], _vec(1.0))
    assert vix.count() == 1


def test_query_skips_rows_without_a_vector(vec_db):
    from src.services.pg_vector_index import PgVectorIndex

    ids = _job_ids(vec_db)
    with pgsync.connect(vec_db) as c:
        c.execute(
            "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, 'legacy')",
            (ids[1],),
        )
    vix = PgVectorIndex(vec_db)
    vix.upsert(ids[0], _vec(1.0))
    res = vix.query(_vec(1.0), k=10)
    assert [j for j, _ in res] == [ids[0]]


def test_delete_clears_the_vector_but_keeps_the_row(vec_db):
    from src.services.pg_vector_index import PgVectorIndex

    ids = _job_ids(vec_db)
    vix = PgVectorIndex(vec_db)
    vix.upsert(ids[0], _vec(1.0))
    vix.delete(ids[0])
    assert vix.count() == 0
    with pgsync.connect(vec_db) as c:
        row = c.execute(
            "SELECT model_version FROM job_embeddings WHERE job_id = ?", (ids[0],)
        ).fetchone()
    assert row is not None, "the audit row should survive so re-embedding is tracked"
