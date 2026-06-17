"""TDD tests for backend/scripts/engine_ablation.py.

Pure-function tests for the engine ablation harness — it ranks jobs by each
engine (and combinations) and scores each ranking against a Claude-graded gold.
No DB, no network. Run from backend/.
"""
from __future__ import annotations

from scripts.engine_ablation import (
    combine_rrf,
    compare_configs,
    dim_only_score,
    evaluate_config,
    evaluate_ranking,
    evaluate_ranking_strong,
    precision_at_k,
    ranking_to_scores,
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


# --------------------------------------------------------------------------- #
#  ranking_to_scores — lets a pre-ordered ranking (e.g. the full hybrid) slot  #
#  into the score-map model                                                    #
# --------------------------------------------------------------------------- #


def test_ranking_to_scores_preserves_order():
    s = ranking_to_scores([7, 3, 9])
    assert s[7] > s[3] > s[9]
    assert scores_to_ranking(s) == [7, 3, 9]


def test_ranking_to_scores_empty():
    assert ranking_to_scores([]) == {}


def test_ranking_to_scores_feeds_evaluate_config():
    """A hybrid ranking expressed as pseudo-scores evaluates like any engine."""
    engine_scores = {"hybrid": ranking_to_scores([3, 1, 2])}
    golds = {"1": 80.0, "2": 20.0, "3": 90.0}
    out = evaluate_config({"name": "E3", "engines": ["hybrid"]}, engine_scores, golds)
    assert out["ndcg"] == 1.0   # ranking 3,1,2 == gold order 90,80,20


# --------------------------------------------------------------------------- #
#  dim_only_score — Engine 2 measured as the enrichment dims ONLY              #
#  (seniority+salary+visa+workplace), NOT the full match_score                 #
# --------------------------------------------------------------------------- #


class _FakeBreakdown:
    def __init__(self, sen, sal, visa, wp, match):
        self.seniority_score = sen
        self.salary_score = sal
        self.visa_score = visa
        self.workplace_score = wp
        self.match_score = match


def test_dim_only_score_sums_the_four_enrichment_dims():
    bd = _FakeBreakdown(8, 10, 6, 6, match=99)
    assert dim_only_score(bd) == 30.0   # 8+10+6+6 — NOT match_score (99)


def test_dim_only_score_zero_when_no_dims():
    bd = _FakeBreakdown(0, 0, 0, 0, match=45)
    assert dim_only_score(bd) == 0.0


# --------------------------------------------------------------------------- #
#  evaluate_ranking_strong — point estimates + bootstrap 95% CIs              #
#  + NDCG@5 / NDCG@10 (top-focus)                                             #
# --------------------------------------------------------------------------- #


def test_evaluate_ranking_strong_perfect_order():
    golds = {"1": 90.0, "2": 70.0, "3": 40.0, "4": 10.0}
    out = evaluate_ranking_strong([1, 2, 3, 4], golds, n_resamples=200, seed=1)
    assert out["n"] == 4
    assert out["ndcg"]["point"] == 1.0          # best jobs already on top
    assert out["spearman"]["point"] == 1.0
    assert "ndcg@5" in out and "ndcg@10" in out


def test_evaluate_ranking_strong_has_ci_bounds():
    golds = {"1": 90.0, "2": 10.0, "3": 70.0, "4": 40.0, "5": 20.0}
    out = evaluate_ranking_strong([3, 1, 4, 5, 2], golds, n_resamples=300, seed=7)
    for m in ("ndcg", "ndcg@5", "spearman"):
        lo, hi = out[m]["ci"]
        assert lo <= hi                          # CI is an interval
        assert lo <= out[m]["point"] <= hi or abs(out[m]["point"] - hi) < 0.2


def test_evaluate_ranking_strong_is_deterministic_with_seed():
    golds = {"1": 90.0, "2": 10.0, "3": 70.0, "4": 40.0}
    a = evaluate_ranking_strong([1, 3, 4, 2], golds, n_resamples=200, seed=42)
    b = evaluate_ranking_strong([1, 3, 4, 2], golds, n_resamples=200, seed=42)
    assert a["ndcg"]["ci"] == b["ndcg"]["ci"]    # same seed → same CI


# --------------------------------------------------------------------------- #
#  compare_configs — significance test (is the gap real or noise?)            #
# --------------------------------------------------------------------------- #


def test_compare_configs_detects_clearly_better():
    golds = {"1": 95.0, "2": 80.0, "3": 65.0, "4": 45.0, "5": 25.0, "6": 5.0}
    good = [1, 2, 3, 4, 5, 6]      # perfect
    bad = [6, 5, 4, 3, 2, 1]       # reversed
    out = compare_configs(good, bad, golds, metric="ndcg", n_resamples=500, seed=3)
    assert out["diff"] > 0
    assert out["significant"] is True            # CI of the diff excludes 0


def test_compare_configs_identical_not_significant():
    golds = {"1": 90.0, "2": 70.0, "3": 40.0, "4": 10.0}
    r = [1, 2, 3, 4]
    out = compare_configs(r, r, golds, metric="ndcg", n_resamples=400, seed=3)
    assert abs(out["diff"]) < 1e-9
    assert out["significant"] is False
