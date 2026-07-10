"""TDD tests for engine_health pure functions.

All tests are deterministic — no live HTTP, no DB access.
Tests import directly from scripts.engine_health (pure functions only).
"""
from __future__ import annotations

from typing import List, Optional

from scripts.engine_health import (
    actions_precision,
    evaluate_targets,
    freshness_stats,
    ghost_rate,
    judge_agreement,
    percentiles,
)

# ---------------------------------------------------------------------------
# percentiles
# ---------------------------------------------------------------------------


class TestPercentiles:
    def test_empty_list_returns_empty(self) -> None:
        result = percentiles([], [50, 95, 99])
        # either {} or all None values — both acceptable; we just need no crash
        # and every key is either absent or None
        for v in result.values():
            assert v is None

    def test_known_list_1_to_100(self) -> None:
        samples = list(range(1, 101))  # 1..100 inclusive
        result = percentiles(samples, [50, 95, 99])
        # Nearest-rank: p50 ≈ 50, p95 ≈ 95, p99 ≈ 99 (±1 for rounding)
        assert abs(result[50] - 50) <= 1
        assert abs(result[95] - 95) <= 1
        assert abs(result[99] - 99) <= 1

    def test_single_element(self) -> None:
        result = percentiles([42.0], [50, 95])
        assert result[50] == 42.0
        assert result[95] == 42.0

    def test_two_elements(self) -> None:
        result = percentiles([10.0, 20.0], [50])
        assert 10.0 <= result[50] <= 20.0

    def test_returns_requested_keys(self) -> None:
        result = percentiles([1.0, 2.0, 3.0], [25, 75])
        assert set(result.keys()) == {25, 75}

    def test_p100_is_max(self) -> None:
        samples = [float(x) for x in range(1, 11)]
        result = percentiles(samples, [100])
        assert result[100] == 10.0

    def test_p0_is_min(self) -> None:
        samples = [float(x) for x in range(1, 11)]
        result = percentiles(samples, [0])
        assert result[0] == 1.0


# ---------------------------------------------------------------------------
# freshness_stats
# ---------------------------------------------------------------------------


class TestFreshnessStats:
    # Fixed "now" in ISO format
    NOW = "2024-06-15T12:00:00+00:00"

    def test_empty_list(self) -> None:
        stats = freshness_stats([], self.NOW)
        assert stats["count"] == 0
        assert stats["with_date"] == 0

    def test_all_none(self) -> None:
        stats = freshness_stats([None, None, None], self.NOW)
        assert stats["count"] == 3
        assert stats["with_date"] == 0
        # medians / pct should be None or 0 (graceful)
        assert stats["median_age_days"] is None or stats["median_age_days"] == 0

    def test_counts_non_null(self) -> None:
        posted = [
            "2024-06-15T11:00:00+00:00",  # 1h ago → within 24h
            "2024-06-14T12:00:00+00:00",  # 24h ago → within 24h boundary
            None,
        ]
        stats = freshness_stats(posted, self.NOW)
        assert stats["count"] == 3
        assert stats["with_date"] == 2

    def test_pct_last_24h(self) -> None:
        posted = [
            "2024-06-15T11:00:00+00:00",  # 1h ago → in 24h window
            "2024-06-15T10:00:00+00:00",  # 2h ago → in 24h window
            "2024-06-08T12:00:00+00:00",  # 7d ago → NOT in 24h window
        ]
        stats = freshness_stats(posted, self.NOW)
        assert stats["with_date"] == 3
        # 2 out of 3 are within 24h
        assert abs(stats["pct_last_24h"] - (2 / 3 * 100)) < 0.1

    def test_pct_last_7d(self) -> None:
        posted = [
            "2024-06-15T11:00:00+00:00",  # 1h ago → in 7d
            "2024-06-08T12:00:01+00:00",  # just under 7d → in 7d
            "2024-06-07T12:00:00+00:00",  # >7d → NOT in 7d
            None,
        ]
        stats = freshness_stats(posted, self.NOW)
        # 2 out of 3 non-null dates are within 7 days
        assert abs(stats["pct_last_7d"] - (2 / 3 * 100)) < 0.1

    def test_median_age_days(self) -> None:
        # ages: 1h (≈0.04d), 1d, 10d
        posted = [
            "2024-06-15T11:00:00+00:00",  # ~0.04 days
            "2024-06-14T12:00:00+00:00",  # ~1 day
            "2024-06-05T12:00:00+00:00",  # ~10 days
        ]
        stats = freshness_stats(posted, self.NOW)
        # Median of [0.04, 1, 10] ≈ 1
        assert abs(stats["median_age_days"] - 1.0) < 0.5

    def test_mixed_none_and_dates(self) -> None:
        posted: List[Optional[str]] = [None, "2024-06-15T11:00:00+00:00", None]
        stats = freshness_stats(posted, self.NOW)
        assert stats["count"] == 3
        assert stats["with_date"] == 1
        assert stats["pct_last_24h"] == 100.0


# ---------------------------------------------------------------------------
# judge_agreement
# ---------------------------------------------------------------------------


class TestJudgeAgreement:
    def test_empty_returns_zero_judged(self) -> None:
        result = judge_agreement([])
        assert result["n_judged"] == 0
        assert result["pearson"] is None
        assert result["top10_goodfit_pct"] is None

    def test_no_llm_scores(self) -> None:
        rows = [{"score": 50, "llm_fit_score": None}] * 5
        result = judge_agreement(rows)
        assert result["n_judged"] == 0
        assert result["pearson"] is None

    def test_strong_positive_correlation(self) -> None:
        # keyword and llm track each other perfectly
        rows = [
            {"score": 10 * i, "llm_fit_score": 10 * i} for i in range(1, 11)
        ]
        result = judge_agreement(rows)
        assert result["n_judged"] == 10
        assert result["pearson"] is not None
        assert result["pearson"] > 0.95

    def test_negative_correlation(self) -> None:
        rows = [
            {"score": i, "llm_fit_score": 100 - i} for i in range(1, 11)
        ]
        result = judge_agreement(rows)
        assert result["pearson"] is not None
        assert result["pearson"] < -0.9

    def test_zero_variance_pearson_is_none(self) -> None:
        # llm_fit_score constant → zero variance → Pearson undefined
        rows = [{"score": i * 10, "llm_fit_score": 50} for i in range(1, 6)]
        result = judge_agreement(rows)
        assert result["pearson"] is None

    def test_top10_goodfit_pct_high(self) -> None:
        # 20 rows: top 10 by score all have llm_fit_score >= 60
        # bottom 10 have llm_fit_score < 60
        rows = []
        for i in range(10):  # high-score rows
            rows.append({"score": 90 + i, "llm_fit_score": 70 + i})
        for i in range(10):  # low-score rows
            rows.append({"score": 10 + i, "llm_fit_score": 20 + i})
        result = judge_agreement(rows)
        assert result["top10_goodfit_pct"] == 100.0

    def test_top10_goodfit_pct_mixed(self) -> None:
        # top 10 by score: 5 good-fit (>=60), 5 poor-fit (<60)
        rows = []
        for i in range(5):
            rows.append({"score": 90 + i, "llm_fit_score": 70})  # good
        for i in range(5):
            rows.append({"score": 80 + i, "llm_fit_score": 40})  # poor
        for i in range(10):
            rows.append({"score": 10 + i, "llm_fit_score": None})  # not judged
        result = judge_agreement(rows)
        # top 10 by score are the first 10 rows (5 good + 5 poor, all judged)
        assert result["top10_goodfit_pct"] == 50.0

    def test_top10_none_if_no_judged_in_top10(self) -> None:
        # Top 10 by score all have llm_fit_score=None
        rows = [{"score": 90 - i, "llm_fit_score": None} for i in range(10)]
        rows += [{"score": 10 - i, "llm_fit_score": 80} for i in range(5)]
        result = judge_agreement(rows)
        # No judged rows in top 10 → None
        assert result["top10_goodfit_pct"] is None

    def test_single_judged_row_pearson_none(self) -> None:
        rows = [{"score": 50, "llm_fit_score": 70}]
        result = judge_agreement(rows)
        assert result["n_judged"] == 1
        assert result["pearson"] is None  # need ≥2 points


# ---------------------------------------------------------------------------
# ghost_rate
# ---------------------------------------------------------------------------


class TestGhostRate:
    def test_empty(self) -> None:
        result = ghost_rate([])
        assert result["total"] == 0
        assert result["ghosts"] == 0
        assert result["pct"] == 0.0

    def test_no_ghosts(self) -> None:
        result = ghost_rate(["active", "active", None, "STALE"])
        assert result["ghosts"] == 0
        assert result["pct"] == 0.0

    def test_all_ghost_states(self) -> None:
        states = ["GHOST", "CONFIRMED_EXPIRED", "GHOST", "active"]
        result = ghost_rate(states)
        assert result["total"] == 4
        assert result["ghosts"] == 3
        assert abs(result["pct"] - 75.0) < 0.1

    def test_ghost_only(self) -> None:
        result = ghost_rate(["GHOST", "GHOST"])
        assert result["pct"] == 100.0

    def test_none_values_not_counted_as_ghost(self) -> None:
        result = ghost_rate([None, None, "GHOST"])
        assert result["ghosts"] == 1
        assert result["total"] == 3

    def test_case_sensitivity(self) -> None:
        # States must match exactly (uppercase)
        result = ghost_rate(["ghost", "confirmed_expired"])
        assert result["ghosts"] == 0


# ---------------------------------------------------------------------------
# actions_precision
# ---------------------------------------------------------------------------


class TestActionsPrecision:
    def test_empty(self) -> None:
        result = actions_precision([])
        assert result["n"] == 0
        assert result["top_decile_engage_rate"] is None
        assert result["bottom_decile_engage_rate"] is None
        assert result["lift"] is None

    def test_no_actions(self) -> None:
        rows = [{"score": i * 10, "action": None} for i in range(20)]
        result = actions_precision(rows)
        assert result["top_decile_engage_rate"] == 0.0 or result["top_decile_engage_rate"] is None

    def test_high_lift_scenario(self) -> None:
        # 20 rows: top 10% (2 rows) are liked, bottom 10% (2 rows) have no action
        rows = []
        # Top scorers: liked/applied
        rows.append({"score": 95, "action": "liked"})
        rows.append({"score": 90, "action": "applied"})
        # Middle scorers: mixed
        for i in range(16):
            rows.append({"score": 50 + i, "action": None})
        # Bottom scorers: not_interested
        rows.append({"score": 5, "action": "not_interested"})
        rows.append({"score": 3, "action": None})

        result = actions_precision(rows)
        assert result["n"] == 20
        # top decile (2 rows): both liked/applied → 100%
        assert result["top_decile_engage_rate"] == 1.0
        # lift should be high or None (if bottom is 0)
        assert result["lift"] is None or result["lift"] > 1

    def test_lift_none_when_bottom_zero(self) -> None:
        rows = [{"score": 80, "action": "liked"}] + [
            {"score": i, "action": None} for i in range(19)
        ]
        result = actions_precision(rows)
        # If bottom decile has 0 engage → lift is None
        if result["bottom_decile_engage_rate"] == 0.0:
            assert result["lift"] is None
        else:
            assert result["lift"] is not None

    def test_returns_n(self) -> None:
        rows = [{"score": i, "action": "liked"} for i in range(15)]
        result = actions_precision(rows)
        assert result["n"] == 15

    def test_engage_actions_are_liked_and_applied(self) -> None:
        # 10 rows: all have score 50+, action alternates liked / not_interested
        rows = [
            {"score": 90, "action": "liked"},
            {"score": 89, "action": "not_interested"},
            {"score": 88, "action": "applied"},
            {"score": 87, "action": "not_interested"},
            {"score": 86, "action": "liked"},
            {"score": 85, "action": None},
            {"score": 84, "action": "applied"},
            {"score": 83, "action": "not_interested"},
            {"score": 82, "action": "liked"},
            {"score": 81, "action": None},
        ]
        result = actions_precision(rows)
        assert result["n"] == 10
        # top decile = top 1 row (10% of 10 = 1): score=90, liked → engage
        # top_decile_engage_rate should be 1.0
        assert result["top_decile_engage_rate"] == 1.0


# ---------------------------------------------------------------------------
# evaluate_targets
# ---------------------------------------------------------------------------


class TestEvaluateTargets:
    def test_all_pass(self) -> None:
        metrics = {
            "feed_keyword_p95_ms": 200.0,
            "feed_hybrid_p95_ms": 1000.0,
            "ghost_rate_pct": 2.0,
            "judge_top10_goodfit_pct": 90.0,
        }
        targets = {
            "feed_keyword_p95_ms": 500,
            "feed_hybrid_p95_ms": 3000,
            "ghost_rate_pct": 5,
            "judge_top10_goodfit_pct": 80,
        }
        rows = evaluate_targets(metrics, targets)
        assert all(r["pass"] is True for r in rows)

    def test_keyword_fail(self) -> None:
        metrics = {
            "feed_keyword_p95_ms": 600.0,  # over 500 limit
            "feed_hybrid_p95_ms": 1000.0,
            "ghost_rate_pct": 2.0,
            "judge_top10_goodfit_pct": 90.0,
        }
        targets = {
            "feed_keyword_p95_ms": 500,
            "feed_hybrid_p95_ms": 3000,
            "ghost_rate_pct": 5,
            "judge_top10_goodfit_pct": 80,
        }
        rows = evaluate_targets(metrics, targets)
        kw_row = next(r for r in rows if r["name"] == "feed_keyword_p95_ms")
        assert kw_row["pass"] is False

    def test_ghost_rate_fail(self) -> None:
        metrics = {"ghost_rate_pct": 10.0}
        targets = {"ghost_rate_pct": 5}
        rows = evaluate_targets(metrics, targets)
        assert rows[0]["pass"] is False

    def test_none_metric_skipped(self) -> None:
        metrics = {"feed_hybrid_p95_ms": None}
        targets = {"feed_hybrid_p95_ms": 3000}
        rows = evaluate_targets(metrics, targets)
        assert rows[0]["pass"] is None

    def test_row_has_required_fields(self) -> None:
        metrics = {"ghost_rate_pct": 2.0}
        targets = {"ghost_rate_pct": 5}
        rows = evaluate_targets(metrics, targets)
        row = rows[0]
        for field in ("name", "value", "target", "pass", "note"):
            assert field in row

    def test_judge_goodfit_pct_pass_and_fail(self) -> None:
        # judge_top10_goodfit_pct is a MIN threshold (must be >= target)
        metrics_pass = {"judge_top10_goodfit_pct": 85.0}
        metrics_fail = {"judge_top10_goodfit_pct": 70.0}
        targets = {"judge_top10_goodfit_pct": 80}
        assert evaluate_targets(metrics_pass, targets)[0]["pass"] is True
        assert evaluate_targets(metrics_fail, targets)[0]["pass"] is False

    def test_empty_metrics_and_targets(self) -> None:
        rows = evaluate_targets({}, {})
        assert rows == []

    def test_metrics_without_matching_target_not_in_output(self) -> None:
        # Only targets that have a matching entry in `targets` dict are evaluated
        metrics = {"ghost_rate_pct": 2.0, "some_extra_metric": 99.0}
        targets = {"ghost_rate_pct": 5}
        rows = evaluate_targets(metrics, targets)
        names = [r["name"] for r in rows]
        assert "some_extra_metric" not in names

    def test_ghost_rate_max_threshold(self) -> None:
        # ghost_rate_pct is a MAX threshold (must be <= target to pass)
        metrics_ok = {"ghost_rate_pct": 4.9}
        metrics_bad = {"ghost_rate_pct": 5.1}
        targets = {"ghost_rate_pct": 5}
        assert evaluate_targets(metrics_ok, targets)[0]["pass"] is True
        assert evaluate_targets(metrics_bad, targets)[0]["pass"] is False
