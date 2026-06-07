"""Step-1 B8 — integration tests for ?mode=hybrid wiring.

Three guard-rails:

1. ``test_mode_hybrid_empty_index_falls_back`` — fresh DB / empty index ⇒
   keyword path served, WARNING log emitted, no 500. Works without the
   semantic stack installed (the helper degrades early on import errors).

2. ``test_mode_hybrid_populated_index_fuses`` — populated vector index +
   sentence-transformers stack reorders results. Skipped when the
   semantic stack isn't installed.

3. ``test_mode_keyword_unaffected`` — the keyword-mode path is untouched
   by the B8 wiring (regression guard).
"""

from __future__ import annotations

import importlib.util
import logging

import pytest

from src.api.routes import jobs as jobs_route

_SEMANTIC_STACK = (
    importlib.util.find_spec("sentence_transformers") is not None and importlib.util.find_spec("chromadb") is not None
)


@pytest.fixture(autouse=True)
def _isolate_chroma(monkeypatch, tmp_path):
    """Give every test its own EMPTY chroma store.

    These tests otherwise share the real ``data/chroma/`` persist dir, so one
    test's upserts leak into another's "empty index" assertion. (This stayed
    hidden while VectorIndex.upsert was silently failing on newer chromadb;
    once upsert works, the leak surfaces.) Point the default persist dir at a
    per-test tmp path so isolation is guaranteed.
    """
    monkeypatch.setattr(
        "src.services.vector_index._DEFAULT_PATH", tmp_path / "chroma", raising=False
    )


# ---------------------------------------------------------------------------
# 1. Empty-index fallback — no semantic stack needed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_hybrid_empty_index_falls_back(authenticated_async_context, monkeypatch, caplog):
    """?mode=hybrid against a fresh DB returns 200 + warns + matches keyword."""
    # Force SEMANTIC_ENABLED=true so the helper doesn't skip silently.
    from src.core import settings

    monkeypatch.setattr(settings, "SEMANTIC_ENABLED", True, raising=True)

    # When the chromadb stack isn't installed the helper warns about the
    # import failure; when it IS installed it warns about the empty index.
    # Either way the request must 200 and degrade.
    caplog.set_level(logging.WARNING, logger="job360.api.jobs")

    async with authenticated_async_context() as client:
        resp_hybrid = await client.get("/api/jobs?mode=hybrid")
        resp_keyword = await client.get("/api/jobs?mode=keyword")

    assert resp_hybrid.status_code == 200, resp_hybrid.text
    assert resp_keyword.status_code == 200, resp_keyword.text

    # Same DB → same payload total. Both 0 here (empty fixture DB).
    assert resp_hybrid.json()["total"] == resp_keyword.json()["total"]
    assert resp_hybrid.json()["jobs"] == resp_keyword.json()["jobs"]

    # A WARNING line about hybrid degradation must be present.
    warned = [rec for rec in caplog.records if rec.name == "job360.api.jobs" and rec.levelno >= logging.WARNING]
    assert warned, (
        "Expected a WARNING log line about hybrid fallback; got none."
        f" caplog records: {[(r.name, r.levelname, r.message) for r in caplog.records]}"
    )
    msgs = " | ".join(r.getMessage() for r in warned)
    assert "hybrid" in msgs.lower(), msgs


# ---------------------------------------------------------------------------
# 3. Keyword-mode regression guard — no behaviour change from B8.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_keyword_unaffected(authenticated_async_context):
    """The keyword path (no ?mode, or ?mode=keyword) is unchanged by B8."""
    async with authenticated_async_context() as client:
        resp_default = await client.get("/api/jobs")
        resp_keyword = await client.get("/api/jobs?mode=keyword")

    assert resp_default.status_code == 200
    assert resp_keyword.status_code == 200
    # Both must serve identical results — ?mode=keyword is the explicit
    # spelling of the default behaviour.
    assert resp_default.json() == resp_keyword.json()


# ---------------------------------------------------------------------------
# 2. Populated-index fusion — requires the semantic stack.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _SEMANTIC_STACK,
    reason="sentence_transformers / chromadb not installed",
)
def test_mode_hybrid_populated_index_fuses(monkeypatch):
    """Populated vector index reorders results vs keyword-only.

    Unit-test against the helper directly so we don't need to seed Chroma
    through the full FastAPI lifecycle. The helper is the load-bearing
    piece — if it correctly reorders, the route correctly reorders.
    """
    from src.core import settings
    from src.services import vector_index as vix_mod

    monkeypatch.setattr(settings, "SEMANTIC_ENABLED", True, raising=True)

    # Build a fake VectorIndex that returns a deterministic semantic ranking.
    # We make job 5 the semantic top hit — the fused order should pull it up.
    class _FakeIndex:
        def __init__(self, *args, **kwargs):
            pass

        def count(self):
            return 5

        def query(self, vector, k=10, filter_metadata=None):
            # Reverse keyword order — last keyword id first.
            return [(5, 0.1), (4, 0.2), (3, 0.3), (2, 0.4), (1, 0.5)]

        def upsert(self, *a, **kw):
            pass

    monkeypatch.setattr(vix_mod, "VectorIndex", _FakeIndex)

    # Patch encode_job to return a fixed vector so we never touch a real
    # sentence-transformers model.
    from src.services import embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "encode_job", lambda job, enrichment: [0.0] * 384)

    rows = [{"id": i, "title": f"Job {i}", "description": "desc", "match_score": 100 - i} for i in (1, 2, 3, 4, 5)]

    reordered = jobs_route._maybe_apply_hybrid_reorder(rows, profile=None)

    # Fusion of [1,2,3,4,5] (keyword) with [5,4,3,2,1] (semantic) via RRF
    # places items appearing in BOTH lists at top — but every id appears in
    # both. The middle items (3) accumulate the most balanced rank
    # contribution; ids at the extremes (1 and 5) get one strong + one weak.
    # Either way the order MUST differ from the pure keyword [1,2,3,4,5].
    keyword_order = [r["id"] for r in rows]
    fused_order = [r["id"] for r in reordered]
    assert fused_order != keyword_order, f"Hybrid mode should reorder; got identical order {fused_order}"
    # All rows preserved (no losses).
    assert sorted(fused_order) == sorted(keyword_order)
