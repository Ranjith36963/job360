"""Engine 3 enhancement — offline tests for the pure hybrid-reorder core.

`_hybrid_reorder_rows` fuses the keyword row order + a BM25 ranking (pure,
no heavy deps) + an injected semantic id list, then applies an optional
injected rerank. This lets us test the full engine-3 ordering logic without
sentence-transformers / chromadb installed.
"""
from __future__ import annotations

from src.api.routes.jobs import _hybrid_reorder_rows


def _rows(*specs):
    """specs = (id, title, description) tuples → list[dict] in keyword order."""
    return [{"id": i, "title": t, "description": d} for (i, t, d) in specs]


def test_hybrid_reorder_empty_rows_returns_same():
    assert _hybrid_reorder_rows([], "python") == []


def test_hybrid_reorder_no_query_keeps_keyword_order():
    """Empty query → no BM25 leg, no semantic → keyword order unchanged."""
    rows = _rows((1, "Sales Manager", ""), (2, "Python Engineer", ""), (3, "Cook", ""))
    result = _hybrid_reorder_rows(rows, "")
    assert [r["id"] for r in result] == [1, 2, 3]


def test_hybrid_reorder_bm25_promotes_strong_text_match():
    """A lower keyword row whose text strongly matches the query is promoted."""
    rows = _rows(
        (1, "Sales Manager", "retail targets"),
        (2, "Python Engineer", "python python backend"),
    )
    result = _hybrid_reorder_rows(rows, "python")
    assert [r["id"] for r in result] == [2, 1]


def test_hybrid_reorder_fuses_injected_semantic_ids():
    """An injected semantic ranking fuses with keyword order and lifts a row."""
    rows = _rows((1, "A", ""), (2, "B", ""), (3, "C", ""))
    # 3 is keyword-last but semantic-only-hit → fusion lifts it to the top.
    result = _hybrid_reorder_rows(rows, "", semantic_ids=[3])
    assert {r["id"] for r in result} == {1, 2, 3}
    assert result[0]["id"] == 3


def test_hybrid_reorder_applies_injected_rerank():
    """An injected rerank_fn reorders the final list."""
    rows = _rows((1, "A", ""), (2, "B", ""), (3, "C", ""))

    def rr(ids):
        return list(reversed(ids))

    result = _hybrid_reorder_rows(rows, "", rerank_fn=rr)
    assert [r["id"] for r in result] == [3, 2, 1]


def test_hybrid_reorder_never_loses_rows_on_stale_semantic_id():
    """A semantic id not present in rows (stale index) must not drop rows."""
    rows = _rows((1, "A", ""), (2, "B", ""))
    result = _hybrid_reorder_rows(rows, "", semantic_ids=[99, 2, 1])
    assert {r["id"] for r in result} == {1, 2}
