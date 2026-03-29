"""Pipeline diagnostics collector for observability and quality analysis.

Accumulates metrics throughout the pipeline run and serializes to structured
JSON for logging, reporting, and iterative quality improvement.  Every method
is fail-safe — diagnostics never crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("job360.diagnostics")

# Score histogram bucket boundaries (inclusive lower, exclusive upper except last)
_BUCKETS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50),
            (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
_BUCKET_LABELS = ["0-9", "10-19", "20-29", "30-39", "40-49",
                  "50-59", "60-69", "70-79", "80-89", "90-100"]

_DIMENSIONS = ["role", "skill", "seniority", "experience",
               "credentials", "location", "recency", "semantic"]

_DIM_MAXES = {"role": 20, "skill": 25, "seniority": 10, "experience": 10,
              "credentials": 5, "location": 10, "recency": 10, "semantic": 10}


@dataclass
class DimensionStats:
    """Per-dimension aggregate statistics."""
    avg: float = 0.0
    max_val: int = 0
    zero_count: int = 0
    total_count: int = 0


class PipelineDiagnostics:
    """Accumulates pipeline metrics for structured logging and reporting.

    Usage::

        diag = PipelineDiagnostics()
        diag.start_phase("scoring")
        # ... do scoring ...
        diag.end_phase("scoring")
        diag.record_scores(all_jobs)
        logger.info("PIPELINE_DIAGNOSTICS: %s", diag.to_json_line())
    """

    def __init__(self) -> None:
        # Phase timing
        self._phase_starts: dict[str, float] = {}
        self.timings: dict[str, float] = {}

        # Score distribution
        self.score_histogram: dict[str, int] = {label: 0 for label in _BUCKET_LABELS}
        self.total_scored: int = 0
        self.avg_score: float = 0.0

        # Per-dimension stats
        self.dimension_stats: dict[str, DimensionStats] = {
            dim: DimensionStats() for dim in _DIMENSIONS
        }

        # Pipeline funnel (ordered stages)
        self.funnel: list[tuple[str, int]] = []

        # Dedup stats
        self.dedup_before: int = 0
        self.dedup_after: int = 0
        self.dedup_removed_by_key: int = 0
        self.dedup_removed_by_similarity: int = 0

        # LLM stats
        self.llm_cache_hits: int = 0
        self.llm_api_calls: int = 0
        self.llm_providers_used: list[str] = []
        self.llm_score_deltas: list[int] = []
        self.llm_provider_call_counts: dict[str, int] = {}
        self.llm_provider_failures: dict[str, int] = {}

        # Feedback stats
        self.feedback_liked_count: int = 0
        self.feedback_rejected_count: int = 0
        self.feedback_adjustments_made: int = 0
        self.feedback_total_adj: int = 0

        # Reranker stats
        self.rerank_count: int = 0
        self.rerank_avg_score: float = 0.0
        self.rerank_avg_boost: float = 0.0

        # Data quality
        self.quality_pct_salary: float = 0.0
        self.quality_pct_description: float = 0.0
        self.quality_pct_location: float = 0.0
        self.quality_pct_visa: float = 0.0
        self.quality_total: int = 0

        # Skill gaps (top missing required skills)
        self.skill_gaps: list[tuple[str, int]] = []

    # ------------------------------------------------------------------
    # Phase timing
    # ------------------------------------------------------------------

    def start_phase(self, phase: str) -> None:
        """Record the start of a pipeline phase."""
        try:
            self._phase_starts[phase] = time.time()
        except Exception:
            pass

    def end_phase(self, phase: str) -> None:
        """Record the end of a pipeline phase and compute duration."""
        try:
            start = self._phase_starts.pop(phase, None)
            if start is not None:
                self.timings[phase] = round(time.time() - start, 2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Score distribution + per-dimension stats
    # ------------------------------------------------------------------

    def record_scores(self, jobs: list) -> None:
        """Compute score histogram and per-dimension stats from scored jobs."""
        try:
            if not jobs:
                return

            total_score = 0
            dim_sums: dict[str, int] = {d: 0 for d in _DIMENSIONS}
            dim_maxes: dict[str, int] = {d: 0 for d in _DIMENSIONS}
            dim_zeros: dict[str, int] = {d: 0 for d in _DIMENSIONS}
            count = 0

            for job in jobs:
                score = getattr(job, "match_score", 0) or 0
                total_score += score

                # Histogram
                for i, (lo, hi) in enumerate(_BUCKETS):
                    if lo <= score < hi:
                        self.score_histogram[_BUCKET_LABELS[i]] += 1
                        break

                # Per-dimension from match_data
                md_str = getattr(job, "match_data", "")
                if md_str:
                    try:
                        md = json.loads(md_str)
                    except (json.JSONDecodeError, TypeError):
                        md = {}
                else:
                    md = {}

                for dim in _DIMENSIONS:
                    val = md.get(dim, 0) or 0
                    dim_sums[dim] += val
                    if val > dim_maxes[dim]:
                        dim_maxes[dim] = val
                    if val == 0:
                        dim_zeros[dim] += 1

                count += 1

            self.total_scored = count
            self.avg_score = round(total_score / count, 1) if count else 0.0

            for dim in _DIMENSIONS:
                stats = self.dimension_stats[dim]
                stats.avg = round(dim_sums[dim] / count, 1) if count else 0.0
                stats.max_val = dim_maxes[dim]
                stats.zero_count = dim_zeros[dim]
                stats.total_count = count
        except Exception as exc:
            logger.debug("record_scores failed: %s", exc)

    # ------------------------------------------------------------------
    # Pipeline funnel
    # ------------------------------------------------------------------

    def record_funnel(self, stage: str, count: int) -> None:
        """Add a pipeline funnel stage with its job count."""
        try:
            self.funnel.append((stage, count))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dedup stats
    # ------------------------------------------------------------------

    def record_dedup(self, before: int, after: int,
                     removed_by_key: int = 0,
                     removed_by_similarity: int = 0) -> None:
        """Record deduplication statistics."""
        try:
            self.dedup_before = before
            self.dedup_after = after
            self.dedup_removed_by_key = removed_by_key
            self.dedup_removed_by_similarity = removed_by_similarity
        except Exception:
            pass

    # ------------------------------------------------------------------
    # LLM stats
    # ------------------------------------------------------------------

    def record_llm_stats(self, cache_hits: int = 0, api_calls: int = 0,
                         providers_used: Optional[list[str]] = None,
                         score_deltas: Optional[list[int]] = None,
                         call_counts: Optional[dict[str, int]] = None,
                         failures: Optional[dict[str, int]] = None) -> None:
        """Record LLM enrichment statistics."""
        try:
            self.llm_cache_hits = cache_hits
            self.llm_api_calls = api_calls
            self.llm_providers_used = providers_used or []
            self.llm_score_deltas = score_deltas or []
            self.llm_provider_call_counts = call_counts or {}
            self.llm_provider_failures = failures or {}
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Feedback stats
    # ------------------------------------------------------------------

    def record_feedback(self, liked_count: int = 0, rejected_count: int = 0,
                        adjustments_made: int = 0, total_adj: int = 0) -> None:
        """Record feedback loop statistics."""
        try:
            self.feedback_liked_count = liked_count
            self.feedback_rejected_count = rejected_count
            self.feedback_adjustments_made = adjustments_made
            self.feedback_total_adj = total_adj
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Reranker stats
    # ------------------------------------------------------------------

    def record_rerank(self, reranked_count: int = 0,
                      avg_score: float = 0.0,
                      avg_boost: float = 0.0) -> None:
        """Record cross-encoder reranking statistics."""
        try:
            self.rerank_count = reranked_count
            self.rerank_avg_score = round(avg_score, 2)
            self.rerank_avg_boost = round(avg_boost, 2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Data quality
    # ------------------------------------------------------------------

    def record_data_quality(self, jobs: list) -> None:
        """Compute data quality percentages from job list."""
        try:
            if not jobs:
                return
            n = len(jobs)
            self.quality_total = n
            has_salary = sum(1 for j in jobs
                            if getattr(j, "salary_min", None) or getattr(j, "salary_max", None))
            has_desc = sum(1 for j in jobs
                          if len(getattr(j, "description", "") or "") > 50)
            has_loc = sum(1 for j in jobs
                         if (getattr(j, "location", "") or "").strip())
            has_visa = sum(1 for j in jobs if getattr(j, "visa_flag", False))
            self.quality_pct_salary = round(has_salary / n * 100, 1)
            self.quality_pct_description = round(has_desc / n * 100, 1)
            self.quality_pct_location = round(has_loc / n * 100, 1)
            self.quality_pct_visa = round(has_visa / n * 100, 1)
        except Exception as exc:
            logger.debug("record_data_quality failed: %s", exc)

    # ------------------------------------------------------------------
    # Skill gaps
    # ------------------------------------------------------------------

    def record_skill_gaps(self, jobs: list, top_n: int = 15) -> None:
        """Aggregate most common missing required skills across all jobs."""
        try:
            counter: Counter = Counter()
            for job in jobs:
                md_str = getattr(job, "match_data", "")
                if not md_str:
                    continue
                try:
                    md = json.loads(md_str)
                except (json.JSONDecodeError, TypeError):
                    continue
                for skill in md.get("missing_required", []):
                    counter[skill.lower()] += 1
            self.skill_gaps = counter.most_common(top_n)
        except Exception as exc:
            logger.debug("record_skill_gaps failed: %s", exc)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize all diagnostics to a dictionary."""
        try:
            llm_avg_delta = (
                round(sum(self.llm_score_deltas) / len(self.llm_score_deltas), 1)
                if self.llm_score_deltas else 0.0
            )
            return {
                "timings": self.timings,
                "scores": {
                    "histogram": self.score_histogram,
                    "total_scored": self.total_scored,
                    "avg_score": self.avg_score,
                },
                "dimensions": {
                    dim: {
                        "avg": s.avg,
                        "max": s.max_val,
                        "zero_count": s.zero_count,
                        "max_possible": _DIM_MAXES.get(dim, 0),
                    }
                    for dim, s in self.dimension_stats.items()
                },
                "funnel": self.funnel,
                "dedup": {
                    "before": self.dedup_before,
                    "after": self.dedup_after,
                    "removed_by_key": self.dedup_removed_by_key,
                    "removed_by_similarity": self.dedup_removed_by_similarity,
                },
                "llm": {
                    "cache_hits": self.llm_cache_hits,
                    "api_calls": self.llm_api_calls,
                    "providers_used": self.llm_providers_used,
                    "avg_score_delta": llm_avg_delta,
                    "call_counts": self.llm_provider_call_counts,
                    "failures": self.llm_provider_failures,
                },
                "feedback": {
                    "liked_count": self.feedback_liked_count,
                    "rejected_count": self.feedback_rejected_count,
                    "adjustments_made": self.feedback_adjustments_made,
                    "total_adj": self.feedback_total_adj,
                },
                "reranker": {
                    "reranked_count": self.rerank_count,
                    "avg_rerank_score": self.rerank_avg_score,
                    "avg_boost": self.rerank_avg_boost,
                },
                "data_quality": {
                    "total_jobs": self.quality_total,
                    "pct_salary": self.quality_pct_salary,
                    "pct_description": self.quality_pct_description,
                    "pct_location": self.quality_pct_location,
                    "pct_visa": self.quality_pct_visa,
                },
                "skill_gaps": self.skill_gaps,
            }
        except Exception:
            return {"error": "diagnostics serialization failed"}

    def to_json_line(self) -> str:
        """Serialize to a single JSON string for log output."""
        try:
            return json.dumps(self.to_dict(), separators=(",", ":"))
        except Exception:
            return "{}"
