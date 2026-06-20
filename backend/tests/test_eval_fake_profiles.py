"""TDD for the multi-profile eval harness (pooling + aggregation)."""
from __future__ import annotations

from scripts.eval_fake_profiles import aggregate, pool_jobs


def test_pool_jobs_unions_top_k_from_each_engine():
    rankings = {
        "keyword": [1, 2, 3, 4, 5],
        "hybrid": [3, 6, 7, 1, 8],
        "bm25": [9, 2, 3, 10, 11],
    }
    pool = pool_jobs(rankings, k=2)        # top-2 of each
    # keyword{1,2} hybrid{3,6} bm25{9,2} -> union preserving first-seen
    assert pool == [1, 2, 3, 6, 9]


def test_pool_jobs_dedups_and_handles_short_lists():
    rankings = {"a": [1], "b": [1, 2], "c": []}
    assert pool_jobs(rankings, k=5) == [1, 2]


def test_aggregate_ranks_by_mean_ndcg_and_reports_worst_case():
    results = [
        {"profile": "p1", "config": "E3+E4", "ndcg": 0.95, "spearman": 0.80},
        {"profile": "p2", "config": "E3+E4", "ndcg": 0.97, "spearman": 0.85},
        {"profile": "p1", "config": "E1", "ndcg": 0.90, "spearman": 0.60},
        {"profile": "p2", "config": "E1", "ndcg": 0.70, "spearman": 0.40},  # E1 fragile
    ]
    agg = aggregate(results)
    # E3+E4 has higher mean AND higher worst-case → ranked first
    assert agg[0]["config"] == "E3+E4"
    assert abs(agg[0]["mean_ndcg"] - 0.96) < 1e-9
    assert abs(agg[0]["worst_ndcg"] - 0.95) < 1e-9
    assert agg[0]["n_profiles"] == 2
    # E1's worst case (0.70) exposes its fragility
    e1 = next(r for r in agg if r["config"] == "E1")
    assert abs(e1["worst_ndcg"] - 0.70) < 1e-9
