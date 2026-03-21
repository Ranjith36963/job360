"""Tests for hybrid retrieval: FTS5 + vector + RRF fusion."""

import numpy as np
import pytest

from src.filters.hybrid_retriever import (
    serialize_embedding,
    deserialize_embedding,
    rrf_fuse,
    HybridRetriever,
)
from src.storage.database import JobDatabase
from src.models import Job
from datetime import datetime, timezone


# ── Serialization tests ──


class TestSerialization:
    def test_roundtrip(self):
        vec = np.random.randn(384).astype(np.float32)
        serialized = serialize_embedding(vec)
        restored = deserialize_embedding(serialized)
        assert np.allclose(vec, restored)

    def test_serialize_produces_string(self):
        vec = np.zeros(384, dtype=np.float32)
        result = serialize_embedding(vec)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_deserialize_empty_returns_none(self):
        assert deserialize_embedding("") is None
        assert deserialize_embedding(None) is None

    def test_deserialize_invalid_returns_none(self):
        assert deserialize_embedding("not-valid-base64!!!") is None


# ── RRF fusion tests ──


class TestRRFFuse:
    def test_single_list(self):
        result = rrf_fuse([[10, 20, 30]])
        ids = [r[0] for r in result]
        assert ids == [10, 20, 30]

    def test_two_lists_agreement(self):
        """Both lists rank job 1 first — it should be top."""
        result = rrf_fuse([[1, 2, 3], [1, 3, 2]])
        assert result[0][0] == 1

    def test_two_lists_disagreement(self):
        """Item in both lists beats item in only one."""
        result = rrf_fuse([[10, 20, 30], [20, 40, 50]])
        ids = [r[0] for r in result]
        # 20 appears in both lists (rank 2 + rank 1) — should be top
        assert ids[0] == 20

    def test_empty_lists(self):
        assert rrf_fuse([]) == []
        assert rrf_fuse([[]]) == []

    def test_disjoint_lists(self):
        """Items only in one list still appear."""
        result = rrf_fuse([[1, 2], [3, 4]])
        ids = {r[0] for r in result}
        assert ids == {1, 2, 3, 4}

    def test_scores_are_positive(self):
        result = rrf_fuse([[1, 2, 3], [3, 2, 1]])
        for _, score in result:
            assert score > 0


# ── FTS5 + vector search integration tests ──


@pytest.fixture
async def test_db(tmp_path):
    """Create a temporary database with FTS5 and sample jobs."""
    db_path = str(tmp_path / "test_hybrid.db")
    db = JobDatabase(db_path)
    await db.init_db()

    now = datetime.now(timezone.utc).isoformat()
    jobs = [
        Job(title="Python Developer", company="TechCo", apply_url="https://a.com",
            source="test", date_found=now, description="Python Django REST API PostgreSQL"),
        Job(title="Data Scientist", company="DataCo", apply_url="https://b.com",
            source="test", date_found=now, description="Machine Learning Python pandas scikit-learn"),
        Job(title="Nurse Practitioner", company="NHS Trust", apply_url="https://c.com",
            source="test", date_found=now, description="Patient care clinical assessment NMC registered"),
        Job(title="Quantity Surveyor", company="BuildCo", apply_url="https://d.com",
            source="test", date_found=now, description="NRM cost management construction RICS"),
        Job(title="Finance Analyst", company="BankCo", apply_url="https://e.com",
            source="test", date_found=now, description="Financial modelling Excel VBA budgeting"),
    ]
    for job in jobs:
        await db.insert_job(job)

    yield db
    await db.close()


@pytest.mark.asyncio
async def test_fts5_search_python(test_db):
    retriever = test_db.retriever
    results = await retriever.fts5_search("Python", limit=10)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_fts5_search_no_results(test_db):
    retriever = test_db.retriever
    results = await retriever.fts5_search("zzznonexistentxxx", limit=10)
    assert results == []


@pytest.mark.asyncio
async def test_fts5_search_empty_query(test_db):
    retriever = test_db.retriever
    results = await retriever.fts5_search("", limit=10)
    assert results == []


@pytest.mark.asyncio
async def test_vector_search_with_embeddings(test_db):
    conn = test_db._conn
    cursor = await conn.execute("SELECT id, title, description FROM jobs")
    rows = await cursor.fetchall()

    for row in rows:
        rng = np.random.RandomState(hash(f"{row[1]} {row[2]}") % (2**31))
        vec = rng.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        await conn.execute(
            "UPDATE jobs SET embedding = ? WHERE id = ?",
            (serialize_embedding(vec), row[0]),
        )
    await conn.commit()

    # Query vector identical to first job's embedding
    rng = np.random.RandomState(hash("Python Developer Python Django REST API PostgreSQL") % (2**31))
    query_vec = rng.randn(384).astype(np.float32)
    query_vec = query_vec / np.linalg.norm(query_vec)

    retriever = test_db.retriever
    results = await retriever.vector_search(query_vec, limit=5)
    assert len(results) > 0
    assert results[0] == 1  # First inserted job


@pytest.mark.asyncio
async def test_vector_search_none_embedding(test_db):
    retriever = test_db.retriever
    results = await retriever.vector_search(None, limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_hybrid_search_fts_only(test_db):
    retriever = test_db.retriever
    results = await retriever.hybrid_search("Python", limit=5)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_search_with_details(test_db):
    results = await test_db.search_jobs("Python", limit=5)
    assert len(results) >= 1
    for job in results:
        assert "title" in job
        assert "rrf_score" in job
        assert job["rrf_score"] > 0


@pytest.mark.asyncio
async def test_fts5_search_nursing(test_db):
    retriever = test_db.retriever
    results = await retriever.fts5_search("clinical assessment NMC", limit=10)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_fts5_search_finance(test_db):
    retriever = test_db.retriever
    results = await retriever.fts5_search("financial modelling budgeting", limit=10)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_db_schema_has_fts5(test_db):
    tables = await test_db.get_tables()
    assert "jobs_fts" in tables


@pytest.mark.asyncio
async def test_db_schema_has_embedding_column(test_db):
    cursor = await test_db._conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "embedding" in cols
