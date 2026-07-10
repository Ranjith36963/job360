"""TDD tests for backend/scripts/accuracy_audit.py.

All pure-function tests.  No DB, no network, no heavy imports.
Run from backend/ with:
    python -m pytest -q -p no:randomly tests/test_accuracy_audit.py
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

# --------------------------------------------------------------------------- #
#  Import the module under test                                                #
# --------------------------------------------------------------------------- #
from scripts.accuracy_audit import (
    bucket_precision_recall,
    build_prompt,
    ndcg,
    spearman,
    summary_stats,
    top_mismatches,
)

# =========================================================================== #
#  spearman()                                                                  #
# =========================================================================== #


class TestSpearman:
    def test_perfect_positive(self) -> None:
        pairs: List[Tuple[float, float]] = [(1, 10), (2, 20), (3, 30)]
        assert spearman(pairs) == pytest.approx(1.0, abs=1e-9)

    def test_perfect_negative(self) -> None:
        pairs: List[Tuple[float, float]] = [(1, 30), (2, 20), (3, 10)]
        assert spearman(pairs) == pytest.approx(-1.0, abs=1e-9)

    def test_none_on_fewer_than_two(self) -> None:
        assert spearman([]) is None
        assert spearman([(1, 2)]) is None

    def test_none_on_zero_variance(self) -> None:
        # All app scores the same → zero variance on one side → undefined
        pairs: List[Tuple[float, float]] = [(5, 10), (5, 20), (5, 30)]
        assert spearman(pairs) is None

    def test_tied_ranks_handled(self) -> None:
        # Ties get average rank.  Three items with the same app score: ranks 1,2,3
        # → average = 2 for all.  On the gold side monotone → expected correlation
        # must be 0 (flat app rank against varying gold rank → r=0).
        pairs: List[Tuple[float, float]] = [(5, 10), (5, 20), (5, 30)]
        # zero-variance on app side → None
        result = spearman(pairs)
        assert result is None

    def test_unordered_pairs(self) -> None:
        # A moderately positive correlation but not perfect.
        pairs: List[Tuple[float, float]] = [(3, 30), (1, 10), (2, 25)]
        r = spearman(pairs)
        assert r is not None
        assert 0.0 < r <= 1.0

    def test_partial_inversion(self) -> None:
        # Reverse the middle two elements — correlation should be <1 and >−1
        pairs: List[Tuple[float, float]] = [(1, 10), (2, 30), (3, 20), (4, 40)]
        r = spearman(pairs)
        assert r is not None
        assert -1.0 < r < 1.0


# =========================================================================== #
#  ndcg()                                                                      #
# =========================================================================== #


class TestNdcg:
    def test_perfect_ranking(self) -> None:
        # App ranks are already sorted by gold desc → NDCG = 1.0
        golds = [90.0, 70.0, 50.0, 20.0]
        assert ndcg(golds) == pytest.approx(1.0, abs=1e-9)

    def test_reversed_ranking(self) -> None:
        # Worst possible ranking: app puts lowest gold first
        golds = [20.0, 50.0, 70.0, 90.0]
        score = ndcg(golds)
        assert score is not None
        assert score < 1.0
        assert score >= 0.0

    def test_k_truncation(self) -> None:
        # With k=2 only the first two golds matter.
        # Perfect first two → score should be 1.0
        golds = [90.0, 80.0, 10.0, 5.0]
        assert ndcg(golds, k=2) == pytest.approx(1.0, abs=1e-9)

    def test_single_item(self) -> None:
        # One item is trivially perfect
        assert ndcg([50.0]) == pytest.approx(1.0, abs=1e-9)

    def test_none_on_empty(self) -> None:
        assert ndcg([]) is None

    def test_known_case(self) -> None:
        # golds in app order: [10, 80, 50]
        # ideal order: [80, 50, 10]
        # DCG(actual)  = 10/log2(2) + 80/log2(3) + 50/log2(4)
        #              = 10 + 80/1.585 + 50/2  ≈ 10 + 50.47 + 25 = 85.47
        # IDCG(ideal)  = 80/log2(2) + 50/log2(3) + 10/log2(4)
        #              = 80 + 31.55 + 5 = 116.55
        # NDCG ≈ 85.47 / 116.55 ≈ 0.733
        golds = [10.0, 80.0, 50.0]
        score = ndcg(golds)
        assert score is not None
        assert 0.70 < score < 0.78


# =========================================================================== #
#  bucket_precision_recall()                                                   #
# =========================================================================== #

Row = dict  # {app: Optional[float], gold: float, ...}


class TestBucketPrecisionRecall:
    def _rows(self, pairs: List[Tuple[Optional[float], float]]) -> List[Row]:
        return [{"app": a, "gold": g} for a, g in pairs]

    def test_all_true_positives(self) -> None:
        rows = self._rows([(80, 80), (90, 90), (70, 75)])
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        assert r["tp"] == 3
        assert r["fp"] == 0
        assert r["fn"] == 0
        assert r["precision"] == pytest.approx(1.0)
        assert r["recall"] == pytest.approx(1.0)
        assert r["f1"] == pytest.approx(1.0)

    def test_all_false_positives(self) -> None:
        # App thinks all are good, gold says all are bad
        rows = self._rows([(80, 20), (90, 30)])
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        assert r["tp"] == 0
        assert r["fp"] == 2
        assert r["fn"] == 0
        assert r["precision"] == pytest.approx(0.0)
        assert r["recall"] is None  # no positive ground truth → recall undefined

    def test_all_false_negatives(self) -> None:
        # App thinks all are bad, gold says all are good
        rows = self._rows([(20, 80), (30, 90)])
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        assert r["tp"] == 0
        assert r["fp"] == 0
        assert r["fn"] == 2
        assert r["recall"] == pytest.approx(0.0)
        assert r["precision"] is None  # no positive prediction → precision undefined

    def test_mixed(self) -> None:
        rows = self._rows([(80, 80), (80, 20), (20, 80), (20, 20)])
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        assert r["tp"] == 1
        assert r["fp"] == 1
        assert r["fn"] == 1
        assert r["precision"] == pytest.approx(0.5)
        assert r["recall"] == pytest.approx(0.5)

    def test_none_app_rows_skipped(self) -> None:
        rows = [{"app": None, "gold": 80}, {"app": 80, "gold": 80}]
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        assert r["tp"] == 1

    def test_f1_harmonic_mean(self) -> None:
        rows = self._rows([(80, 80), (80, 80), (80, 20)])
        r = bucket_precision_recall(rows, app_threshold=60, gold_threshold=60)
        # precision = 2/3, recall = 2/2 = 1.0 → f1 = 2*(2/3*1)/(2/3+1) = 0.8
        assert r["f1"] == pytest.approx(0.8, abs=1e-6)


# =========================================================================== #
#  top_mismatches()                                                            #
# =========================================================================== #


class TestTopMismatches:
    def _rows(self, items: List[Tuple[int, int, int, str, str]]) -> List[dict]:
        return [
            {"job_id": jid, "title": t, "app": a, "gold": g, "reason": r}
            for jid, t, a, g, r in items
        ]

    def test_returns_largest_gap_first(self) -> None:
        rows = self._rows(
            [
                (1, "Job A", 80, 20, "bad domain"),  # gap=60
                (2, "Job B", 20, 80, "great fit"),   # gap=60
                (3, "Job C", 50, 55, "close"),        # gap=5
                (4, "Job D", 30, 90, "very off"),     # gap=60
                (5, "Job E", 10, 70, "different"),    # gap=60
            ]
        )
        result = top_mismatches(rows, n=3)
        assert len(result) == 3
        # All top 3 should have gap >= 60
        for item in result:
            assert item["gap"] >= 60

    def test_n_respected(self) -> None:
        rows = self._rows(
            [(i, f"J{i}", 10 * i, 100 - 10 * i, "reason") for i in range(1, 11)]
        )
        assert len(top_mismatches(rows, n=5)) == 5
        assert len(top_mismatches(rows, n=1)) == 1

    def test_gap_field_present(self) -> None:
        rows = self._rows([(1, "J", 30, 80, "reason")])
        result = top_mismatches(rows, n=1)
        assert result[0]["gap"] == 50

    def test_skips_none_app(self) -> None:
        rows = [
            {"job_id": 1, "title": "J1", "app": None, "gold": 80, "reason": "r"},
            {"job_id": 2, "title": "J2", "app": 20, "gold": 80, "reason": "r"},
        ]
        result = top_mismatches(rows, n=5)
        # Row with None app is skipped
        assert len(result) == 1
        assert result[0]["job_id"] == 2

    def test_empty_input(self) -> None:
        assert top_mismatches([], n=5) == []


# =========================================================================== #
#  summary_stats()                                                             #
# =========================================================================== #


class TestSummaryStats:
    def test_empty(self) -> None:
        s = summary_stats([])
        assert s["count"] == 0

    def test_basic(self) -> None:
        golds = [80.0, 50.0, 30.0]
        s = summary_stats(golds)
        assert s["count"] == 3
        assert s["mean"] == pytest.approx(160.0 / 3, abs=1e-3)
        assert s["min"] == 30.0
        assert s["max"] == 80.0

    def test_buckets(self) -> None:
        golds = [80.0, 75.0, 60.0, 45.0, 20.0, 10.0]
        s = summary_stats(golds)
        # strong >= 70: 80, 75  → 2
        # moderate 40-69: 60, 45 → 2
        # weak < 40: 20, 10 → 2
        assert s["dist"]["strong"] == 2
        assert s["dist"]["moderate"] == 2
        assert s["dist"]["weak"] == 2

    def test_boundary_70_is_strong(self) -> None:
        s = summary_stats([70.0])
        assert s["dist"]["strong"] == 1

    def test_boundary_40_is_moderate(self) -> None:
        s = summary_stats([40.0])
        assert s["dist"]["moderate"] == 1

    def test_boundary_39_is_weak(self) -> None:
        s = summary_stats([39.0])
        assert s["dist"]["weak"] == 1


# =========================================================================== #
#  build_prompt()                                                              #
# =========================================================================== #


class TestBuildPrompt:
    def _profile(self) -> dict:
        return {
            "titles": ["Senior ML Engineer", "Data Scientist"],
            "skills": ["Python", "PyTorch", "AWS"],
            "seniority": "senior",
            "locations": ["London", "Remote"],
            "notes": "Prefers fully remote or hybrid roles",
        }

    def _job(self) -> dict:
        return {
            "job_id": 42,
            "title": "Machine Learning Engineer",
            "company": "Acme Corp",
            "location": "London, UK",
            "description": "We need a strong ML engineer with Python and PyTorch...",
        }

    def test_candidate_fields_appear(self) -> None:
        prompt = build_prompt(self._profile(), self._job())
        assert "Senior ML Engineer" in prompt
        assert "Python" in prompt
        assert "senior" in prompt
        assert "London" in prompt

    def test_job_fields_appear(self) -> None:
        prompt = build_prompt(self._profile(), self._job())
        assert "Machine Learning Engineer" in prompt
        assert "Acme Corp" in prompt
        assert "London, UK" in prompt
        assert "PyTorch" in prompt

    def test_json_format_instruction_present(self) -> None:
        prompt = build_prompt(self._profile(), self._job())
        # Must ask for JSON output with fit and reason
        assert '"fit"' in prompt or "'fit'" in prompt or "fit" in prompt
        assert '"reason"' in prompt or "'reason'" in prompt or "reason" in prompt
        # Must ask for 0-100 range
        assert "0-100" in prompt or "0 to 100" in prompt or "0–100" in prompt

    def test_deterministic(self) -> None:
        p = self._profile()
        j = self._job()
        assert build_prompt(p, j) == build_prompt(p, j)

    def test_empty_profile_graceful(self) -> None:
        prompt = build_prompt({}, self._job())
        # Should not raise; job fields still present
        assert "Machine Learning Engineer" in prompt

    def test_empty_job_graceful(self) -> None:
        prompt = build_prompt(self._profile(), {})
        # Should not raise; candidate fields still present
        assert "Python" in prompt

    def test_description_included(self) -> None:
        prompt = build_prompt(self._profile(), self._job())
        assert "ML engineer with Python" in prompt
