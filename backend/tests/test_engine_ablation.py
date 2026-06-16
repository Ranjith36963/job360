"""TDD tests for backend/scripts/engine_ablation.py.

Pure-function tests for the engine ablation harness — it ranks jobs by each
engine (and combinations) and scores each ranking against a Claude-graded gold.
No DB, no network. Run from backend/.
"""
from __future__ import annotations

from scripts.engine_ablation import (
    combine_rrf,
    evaluate_config,
    evaluate_ranking,
    precision_at_k,
    scores_to_ranking,
)


# --------------------------------------------------------------------------- #
#  scores_to_ranking                                                          #
# --------------------------------------------------------------------------- #


def test_scores_to_ranking_sorts_desc_and_drops_none():
    ranking = scores_to_ranking({1: 30, 2: 50, 3: None, 4: 10})
    assert ranking == [2, 1, 4]            # 50 > 30 > 10; job 3 (None) dropped


def test_scores_to_ranking_stable_tiebreak_by_insertion_order():
    ranking = scores_to_ranking({1: 50, 2: 30, 3: 50})
    assert ranking == [1, 3, 2]            # 1 and 3 tie at 50; 1 inserted first


def test_scores_to_ranking_empty():
    assert scores_to_ranking({}) == []


# --------------------------------------------------------------------------- #
#  precision_at_k                                                             #
# --------------------------------------------------------------------------- #


def test_precision_at_k_counts_good_golds_in_top_k():
    golds = {"1": 80.0, "2": 50.0, "3": 90.0, "4": 10.0}
    # top-2 of this ranking = [1, 2]; gold>=60 → only job 1 → 1/2
    assert precision_at_k([1, 2, 3, 4], golds, 2, gold_threshold=60.0) == 0.5


def test_precision_at_k_perfect_when_top_all_good():
    golds = {"1": 80.0, "2": 90.0}
    assert precision_at_k([1, 2], golds, 2, gold_threshold=60.0) == 1.0


def test_precision_at_k_empty_ranking_is_none():
    assert precision_at_k([], {"1": 80.0}, 5) is None


# --------------------------------------------------------------------------- #
#  evaluate_ranking                                                           #
# --------------------------------------------------------------------------- #


def test_evaluate_ranking_perfect_order_scores_top():
    """Ranking already in gold order → NDCG and Spearman both perfect (1.0)."""
    golds = {"1": 80.0, "2": 20.0, "3": 90.0}
    out = evaluate_ranking([3, 1, 2], golds)   # golds in this order = [90, 80, 20]
    assert out["n"] == 3
    assert out["ndcg"] == 1.0
    assert out["spearman"] == 1.0
    # gold>=60 → jobs 3 and 1 → 2/3
    assert abs(out["precision_at_k"] - (2 / 3)) < 1e-9


def test_evaluate_ranking_reversed_order_is_worst():
    golds = {"1": 90.0, "2": 80.0, "3": 10.0}
    good = evaluate_ranking([1, 2, 3], golds)
    bad = evaluate_ranking([3, 2, 1], golds)
    assert good["ndcg"] >= bad["ndcg"]
    assert good["spearman"] > bad["spearman"]


def test_evaluate_ranking_ignores_ids_without_gold():
    golds = {"1": 90.0, "2": 80.0}
    out = evaluate_ranking([1, 99, 2], golds)   # 99 has no gold
    assert out["n"] == 2


def test_evaluate_ranking_no_gold_overlap_returns_none_metrics():
    out = evaluate_ranking([7, 8], {"1": 90.0})
    assert out["n"] == 0
    assert out["ndcg"] is None
    assert out["spearman"] is None


# --------------------------------------------------------------------------- #
#  combine_rrf                                                                #
# --------------------------------------------------------------------------- #


def test_combine_rrf_promotes_items_in_multiple_lists():
    fused = combine_rrf([[1, 2, 3], [3, 2, 4]])
    assert set(fused) == {1, 2, 3, 4}
    assert set(fused[:2]) == {2, 3}        # 2 and 3 appear in both lists


# --------------------------------------------------------------------------- #
#  evaluate_config                                                            #
# --------------------------------------------------------------------------- #


def test_evaluate_config_single_engine():
    engine_scores = {"keyword": {1: 10, 2: 90, 3: 50}}
    golds = {"1": 10.0, "2": 90.0, "3": 50.0}
    out = evaluate_config(
        {"name": "E1", "engines": ["keyword"]}, engine_scores, golds
    )
    assert out["name"] == "E1"
    assert out["ndcg"] == 1.0              # keyword order == gold order here


def test_evaluate_config_combines_engines_via_rrf():
    engine_scores = {
        "keyword": {1: 90, 2: 10, 3: 50},   # ranks 1,3,2
        "judge": {1: 10, 2: 90, 3: 50},     # ranks 2,3,1
    }
    golds = {"1": 50.0, "2": 50.0, "3": 90.0}
    out = evaluate_config(
        {"name": "E1+E4", "engines": ["keyword", "judge"]}, engine_scores, golds
    )
    assert out["name"] == "E1+E4"
    assert out["n"] == 3
    # job 3 is mid in both lists → RRF lifts it; it's also the best gold (90)
    assert out["ndcg"] is not None
