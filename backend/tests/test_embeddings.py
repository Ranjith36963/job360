"""Pillar 2 Batch 2.6 — tests for the embedding encoder + ChromaDB wrapper.

CLAUDE.md rule #11 — no real sentence-transformers or ChromaDB imports
during pytest. All tests inject a fake encoder / client.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import Job
from src.services.embeddings import (
    EMBEDDING_DIM,
    _chunk_words,
    _pool_chunk_vectors,
    encode_job,
    reset_cache_for_testing,
)
from src.services.job_enrichment_schema import (
    JobCategory,
    JobEnrichment,
    SalaryBand,
)
from src.services.vector_index import VectorIndex


# ---------------------------------------------------------------------------
# Fake encoder — deterministic, fast, no downloads.
# ---------------------------------------------------------------------------


class _FakeEncoder:
    """Produces a 384-dim vector whose values depend only on the input text
    hash — so deterministic, and `encode("foo") == encode("foo")`."""

    def __init__(self):
        self.calls: list[str] = []

    def encode(self, text: str):
        self.calls.append(text)
        # Hash stable per text — produces ints in 0..255
        h = hash(text)
        return [((h >> (i % 32)) & 0xFF) / 255.0 for i in range(EMBEDDING_DIM)]


def _fake_factory():
    enc = _FakeEncoder()
    return lambda: enc, enc


def _sample_job() -> Job:
    return Job(
        title="ML Engineer",
        company="Acme",
        apply_url="https://example.com",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK",
        description="",
    )


def _sample_enrichment(summary="Build ML systems.", required=None, **kw) -> JobEnrichment:
    return JobEnrichment(
        title_canonical="ML Engineer",
        category=JobCategory.MACHINE_LEARNING,
        requirements_summary=summary,
        required_skills=required or ["Python"],
        **kw,
    )


# ---------------------------------------------------------------------------
# _chunk_words + _pool_chunk_vectors — pure helpers
# ---------------------------------------------------------------------------


def test_chunk_words_short_text_returns_single_chunk():
    assert _chunk_words("a b c d e", 300, 50) == ["a b c d e"]


def test_chunk_words_long_text_splits_with_overlap():
    words = [f"w{i}" for i in range(700)]
    text = " ".join(words)
    chunks = _chunk_words(text, 300, 50)
    # 700 words → 300-window step 250 → windows at 0, 250, 500.
    assert len(chunks) == 3
    assert chunks[0].startswith("w0 ")
    # Overlap: chunk[1] must begin at word 250, not 300.
    assert chunks[1].startswith("w250 ")


def test_pool_chunk_vectors_max_per_dim():
    pooled = _pool_chunk_vectors([
        [0.1, 0.5, 0.2],
        [0.3, 0.4, 0.9],
    ])
    assert pooled == [0.3, 0.5, 0.9]


def test_pool_chunk_vectors_single_vector_returned_asis():
    v = [0.1, 0.2, 0.3]
    assert _pool_chunk_vectors([v]) == v


def test_pool_chunk_vectors_empty_returns_empty():
    assert _pool_chunk_vectors([]) == []


# ---------------------------------------------------------------------------
# encode_job — determinism + chunking integration
# ---------------------------------------------------------------------------


def test_encode_job_is_deterministic():
    reset_cache_for_testing()
    enc = _FakeEncoder()
    job = _sample_job()
    enrichment = _sample_enrichment()
    v1 = encode_job(job, enrichment, encoder_factory=lambda: enc)
    v2 = encode_job(job, enrichment, encoder_factory=lambda: enc)
    assert v1 == v2
    assert len(v1) == EMBEDDING_DIM


def test_encode_job_uses_title_summary_and_skills():
    enc = _FakeEncoder()
    job = _sample_job()
    enrichment = _sample_enrichment(summary="Ship LLMs.", required=["Python", "PyTorch"])
    encode_job(job, enrichment, encoder_factory=lambda: enc)
    assert any("ML Engineer" in call for call in enc.calls)
    assert any("Ship LLMs" in call for call in enc.calls)
    assert any("Python" in call and "PyTorch" in call for call in enc.calls)


def test_encode_job_chunks_long_description():
    """Description >300 words triggers multiple .encode() calls (one per
    chunk). Batch 2.6 — the 250-char requirements_summary is always
    short, so chunking happens on the unbounded job.description."""
    enc = _FakeEncoder()
    job = _sample_job()
    job.description = " ".join([f"word{i}" for i in range(800)])
    enrichment = _sample_enrichment(summary="Short summary.")
    vec = encode_job(job, enrichment, encoder_factory=lambda: enc)
    # 800-word description at 300-window step-250 → ≥ 3 chunks.
    assert len(enc.calls) >= 2
    assert len(vec) == EMBEDDING_DIM


def test_encode_job_no_enrichment_degraded_to_title_only():
    """Missing enrichment → still produces a vector, just using the title."""
    enc = _FakeEncoder()
    job = _sample_job()
    vec = encode_job(job, None, encoder_factory=lambda: enc)
    assert len(vec) == EMBEDDING_DIM
    assert any("ML Engineer" in call for call in enc.calls)


def test_encode_job_empty_title_falls_back_to_placeholder():
    """Defensive: even an empty title produces a non-empty vector."""
    enc = _FakeEncoder()
    job = _sample_job()
    job.title = ""
    vec = encode_job(job, None, encoder_factory=lambda: enc)
    assert len(vec) == EMBEDDING_DIM


# ---------------------------------------------------------------------------
# VectorIndex — fake Chroma client
# ---------------------------------------------------------------------------


class _FakeCollection:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def upsert(self, ids, embeddings, metadatas):
        for i, e, m in zip(ids, embeddings, metadatas):
            self._rows[i] = {"embedding": list(e), "meta": dict(m or {})}

    def query(self, query_embeddings, n_results, where=None):
        # Toy distance = L2 squared, no filter implemented beyond passthrough.
        q = query_embeddings[0]
        scored: list[tuple[str, float]] = []
        for row_id, row in self._rows.items():
            if where and not all(row["meta"].get(k) == v for k, v in where.items()):
                continue
            dist = sum((a - b) ** 2 for a, b in zip(q, row["embedding"]))
            scored.append((row_id, dist))
        scored.sort(key=lambda x: x[1])
        chosen = scored[:n_results]
        return {
            "ids": [[i for i, _ in chosen]],
            "distances": [[d for _, d in chosen]],
        }

    def delete(self, ids):
        for i in ids:
            self._rows.pop(i, None)

    def count(self):
        return len(self._rows)


class _FakeClient:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, name):
        return self._collections.setdefault(name, _FakeCollection())


def test_vector_index_upsert_and_query_round_trip():
    idx = VectorIndex(client=_FakeClient())
    idx.upsert(1, [0.1] * EMBEDDING_DIM, {"job_id": 1})
    idx.upsert(2, [0.9] * EMBEDDING_DIM, {"job_id": 2})
    results = idx.query([0.1] * EMBEDDING_DIM, k=2)
    assert results[0][0] == 1   # closest to [0.1]*n


def test_vector_index_upsert_replaces_existing():
    idx = VectorIndex(client=_FakeClient())
    idx.upsert(1, [0.1] * EMBEDDING_DIM)
    idx.upsert(1, [0.9] * EMBEDDING_DIM)
    assert idx.count() == 1


def test_vector_index_delete():
    idx = VectorIndex(client=_FakeClient())
    idx.upsert(1, [0.1] * EMBEDDING_DIM)
    idx.upsert(2, [0.2] * EMBEDDING_DIM)
    idx.delete(1)
    assert idx.count() == 1


def test_vector_index_query_empty_returns_empty_list():
    idx = VectorIndex(client=_FakeClient())
    assert idx.query([0.5] * EMBEDDING_DIM, k=5) == []


def test_vector_index_respects_metadata_filter():
    idx = VectorIndex(client=_FakeClient())
    idx.upsert(1, [0.1] * EMBEDDING_DIM, {"domain": "tech"})
    idx.upsert(2, [0.2] * EMBEDDING_DIM, {"domain": "healthcare"})
    results = idx.query([0.0] * EMBEDDING_DIM, k=5, filter_metadata={"domain": "tech"})
    assert [rid for rid, _ in results] == [1]
