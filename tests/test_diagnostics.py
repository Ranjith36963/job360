"""Tests for the PipelineDiagnostics collector."""

import json
import time
from dataclasses import dataclass
from typing import Optional

import pytest

from src.diagnostics import PipelineDiagnostics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeJob:
    title: str = "Software Engineer"
    company: str = "Acme"
    match_score: int = 65
    match_data: str = ""
    description: str = "Build software systems using Python and AWS."
    location: str = "London"
    salary_min: Optional[float] = 50000
    salary_max: Optional[float] = 70000
    visa_flag: bool = False
    job_type: str = "Full-time"
    experience_level: str = "mid"


def _make_job(score: int = 65, **overrides) -> _FakeJob:
    md = {
        "role": min(20, score // 5),
        "skill": min(25, score // 4),
        "seniority": 5,
        "experience": 5,
        "credentials": 2,
        "location": 8,
        "recency": 7,
        "semantic": 4,
        "matched": ["Python", "AWS"],
        "missing_required": ["Kubernetes"],
        "missing_preferred": ["Docker"],
        "transferable": ["Terraform"],
    }
    md.update(overrides.pop("match_data_overrides", {}))
    defaults = {
        "match_score": score,
        "match_data": json.dumps(md),
    }
    defaults.update(overrides)
    return _FakeJob(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineDiagnostics:
    def test_init_defaults(self):
        diag = PipelineDiagnostics()
        assert diag.total_scored == 0
        assert diag.avg_score == 0.0
        assert all(v == 0 for v in diag.score_histogram.values())
        assert diag.funnel == []

    def test_phase_timing(self):
        diag = PipelineDiagnostics()
        diag.start_phase("scoring")
        time.sleep(0.01)
        diag.end_phase("scoring")
        assert "scoring" in diag.timings
        assert diag.timings["scoring"] >= 0.01

    def test_phase_timing_missing_start(self):
        """end_phase without start_phase should not crash."""
        diag = PipelineDiagnostics()
        diag.end_phase("unknown")
        assert "unknown" not in diag.timings

    def test_record_scores_histogram(self):
        diag = PipelineDiagnostics()
        jobs = [_make_job(s) for s in [15, 35, 55, 55, 75, 95]]
        diag.record_scores(jobs)
        assert diag.total_scored == 6
        assert diag.score_histogram["10-19"] == 1
        assert diag.score_histogram["30-39"] == 1
        assert diag.score_histogram["50-59"] == 2
        assert diag.score_histogram["70-79"] == 1
        assert diag.score_histogram["90-100"] == 1
        assert diag.avg_score == round(sum([15, 35, 55, 55, 75, 95]) / 6, 1)

    def test_record_scores_empty(self):
        diag = PipelineDiagnostics()
        diag.record_scores([])
        assert diag.total_scored == 0

    def test_record_scores_dimension_stats(self):
        diag = PipelineDiagnostics()
        jobs = [
            _make_job(50, match_data_overrides={"role": 15, "skill": 0}),
            _make_job(60, match_data_overrides={"role": 10, "skill": 20}),
        ]
        diag.record_scores(jobs)
        assert diag.dimension_stats["role"].avg == 12.5
        assert diag.dimension_stats["role"].max_val == 15
        assert diag.dimension_stats["role"].zero_count == 0
        assert diag.dimension_stats["skill"].zero_count == 1

    def test_record_funnel(self):
        diag = PipelineDiagnostics()
        diag.record_funnel("raw_fetched", 1000)
        diag.record_funnel("after_foreign_filter", 800)
        diag.record_funnel("after_dedup", 500)
        assert len(diag.funnel) == 3
        assert diag.funnel[0] == ("raw_fetched", 1000)

    def test_record_dedup(self):
        diag = PipelineDiagnostics()
        diag.record_dedup(before=1000, after=750, removed_by_key=150, removed_by_similarity=100)
        assert diag.dedup_before == 1000
        assert diag.dedup_after == 750
        assert diag.dedup_removed_by_key == 150
        assert diag.dedup_removed_by_similarity == 100

    def test_record_llm_stats(self):
        diag = PipelineDiagnostics()
        diag.record_llm_stats(
            cache_hits=10, api_calls=5,
            providers_used=["groq", "gemini"],
            score_deltas=[3, -2, 5],
            call_counts={"groq": 3, "gemini": 2},
            failures={"groq": 1},
        )
        assert diag.llm_cache_hits == 10
        assert diag.llm_api_calls == 5
        assert diag.llm_providers_used == ["groq", "gemini"]

    def test_record_feedback(self):
        diag = PipelineDiagnostics()
        diag.record_feedback(liked_count=5, rejected_count=3, adjustments_made=8, total_adj=20)
        assert diag.feedback_liked_count == 5
        assert diag.feedback_total_adj == 20

    def test_record_rerank(self):
        diag = PipelineDiagnostics()
        diag.record_rerank(reranked_count=50, avg_score=0.85, avg_boost=3.2)
        assert diag.rerank_count == 50
        assert diag.rerank_avg_score == 0.85
        assert diag.rerank_avg_boost == 3.2

    def test_record_data_quality(self):
        diag = PipelineDiagnostics()
        jobs = [
            _make_job(50),
            _make_job(60, salary_min=None, salary_max=None, visa_flag=True),
        ]
        diag.record_data_quality(jobs)
        assert diag.quality_total == 2
        assert diag.quality_pct_salary == 50.0
        assert diag.quality_pct_visa == 50.0

    def test_record_data_quality_empty(self):
        diag = PipelineDiagnostics()
        diag.record_data_quality([])
        assert diag.quality_total == 0

    def test_record_skill_gaps(self):
        diag = PipelineDiagnostics()
        jobs = [
            _make_job(50, match_data_overrides={"missing_required": ["Kubernetes", "Docker"]}),
            _make_job(60, match_data_overrides={"missing_required": ["Kubernetes"]}),
            _make_job(70, match_data_overrides={"missing_required": ["Terraform"]}),
        ]
        diag.record_skill_gaps(jobs)
        assert len(diag.skill_gaps) > 0
        # Kubernetes should be most common (appears 2 times)
        assert diag.skill_gaps[0][0] == "kubernetes"
        assert diag.skill_gaps[0][1] == 2

    def test_to_dict_structure(self):
        diag = PipelineDiagnostics()
        diag.record_funnel("test", 100)
        d = diag.to_dict()
        assert "timings" in d
        assert "scores" in d
        assert "dimensions" in d
        assert "funnel" in d
        assert "dedup" in d
        assert "llm" in d
        assert "feedback" in d
        assert "reranker" in d
        assert "data_quality" in d
        assert "skill_gaps" in d

    def test_to_json_line_valid(self):
        diag = PipelineDiagnostics()
        diag.record_funnel("test", 100)
        line = diag.to_json_line()
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
        assert parsed["funnel"] == [["test", 100]]

    def test_fail_safety_bad_job(self):
        """Diagnostics should not crash on malformed job objects."""
        diag = PipelineDiagnostics()

        class _BadJob:
            match_score = "not_a_number"
            match_data = "{invalid json"
            description = None
            location = None
            salary_min = None
            salary_max = None
            visa_flag = None

        # These should not raise
        diag.record_scores([_BadJob()])
        diag.record_data_quality([_BadJob()])
        diag.record_skill_gaps([_BadJob()])
        # Should still produce valid output
        assert isinstance(diag.to_dict(), dict)

    def test_llm_avg_delta(self):
        diag = PipelineDiagnostics()
        diag.record_llm_stats(score_deltas=[4, -2, 6])
        d = diag.to_dict()
        # avg of [4, -2, 6] = 2.67 rounded to 2.7
        assert d["llm"]["avg_score_delta"] == 2.7

    def test_llm_avg_delta_empty(self):
        diag = PipelineDiagnostics()
        diag.record_llm_stats(score_deltas=[])
        d = diag.to_dict()
        assert d["llm"]["avg_score_delta"] == 0.0
