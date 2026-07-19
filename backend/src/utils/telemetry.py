"""Step-1 S3 — telemetry dataclasses + per-source timer.

Stdlib-only (no prometheus / OpenTelemetry deps; CLAUDE.md rule #16) — the
counters here are populated by the existing pipeline code and read at the end
of ``run_search`` for persistence into ``run_log``.

Three telemetry surfaces:

* :class:`EnrichmentTelemetry` — populated by
  :func:`src.services.job_enrichment.enrich_batch`. Counters MUST stay zero
  when ``ENRICHMENT_ENABLED`` is false (rule #18).

* :class:`EmbeddingsTelemetry` — populated by
  :func:`src.services.embeddings.encode_job`. Counters MUST stay zero when
  ``SEMANTIC_ENABLED`` is false (rule #18).

* :class:`HybridTelemetry` — populated by
  :func:`src.services.retrieval.retrieve_for_user`. ``fallback_reason`` is
  set when the hybrid path degrades to keyword-only ("empty_index" |
  "stack_unavailable" | "exception").

* :class:`MatcherTelemetry` — populated by
  :func:`src.services.llm_matcher.match_batch`. Tracks judged / skipped /
  failed counts and the fit_score spread (min / avg / max) per run. Counters
  MUST stay zero when ``MATCHER_ENABLED`` is false (rule #18 analog).

Plus :func:`source_timer` — a context manager that times a per-source fetch
and emits a structured log line at exit.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("job360.utils.telemetry")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentTelemetry:
    """Counters for the LLM enrichment pipeline."""

    llm_calls: int = 0
    cache_hits: int = 0
    validation_failures: int = 0
    timeouts: int = 0


@dataclass
class EmbeddingsTelemetry:
    """Counters for the vector-index upsert path."""

    upserts_ok: int = 0
    upserts_failed: int = 0
    encode_duration_ms: int = 0


@dataclass
class HybridTelemetry:
    """Counters for the hybrid retrieval path.

    ``fallback_reason`` is one of ``"empty_index"``, ``"stack_unavailable"``,
    ``"exception"``, or ``None`` when hybrid succeeded.
    """

    hybrid_calls: int = 0
    keyword_calls: int = 0
    fallback_reason: Optional[str] = None


@dataclass
class MatcherTelemetry:
    """Counters for the LLM judge (funnel→judge), populated by
    :func:`src.services.llm_matcher.match_batch`.

    Counters stay zero when ``MATCHER_ENABLED`` is false (the stage that calls
    ``match_batch`` is flag-gated, rule #18 analog). ``fit_min`` / ``fit_max``
    are ``None`` until the first verdict is recorded.
    """

    judged: int = 0
    skipped_existing: int = 0
    failed: int = 0
    fit_sum: int = 0
    fit_min: Optional[int] = None
    fit_max: Optional[int] = None

    def record_verdict(self, fit_score: int) -> None:
        """Fold one successful verdict's fit_score into the spread counters."""
        self.judged += 1
        self.fit_sum += fit_score
        self.fit_min = fit_score if self.fit_min is None else min(self.fit_min, fit_score)
        self.fit_max = fit_score if self.fit_max is None else max(self.fit_max, fit_score)

    @property
    def avg_fit(self) -> float:
        """Mean fit_score across judged jobs (0.0 when none judged)."""
        return (self.fit_sum / self.judged) if self.judged else 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialize for persistence into the ``run_log.matcher_stats`` column
        (backlog #9 — judge telemetry). Empty-run friendly (all zeros)."""
        return {
            "judged": self.judged,
            "skipped_existing": self.skipped_existing,
            "failed": self.failed,
            "avg_fit": round(self.avg_fit, 1),
            "fit_min": self.fit_min,
            "fit_max": self.fit_max,
        }


# ---------------------------------------------------------------------------
# Module-level singletons (process-wide; reset between test runs as needed)
# ---------------------------------------------------------------------------


_enrichment_tel = EnrichmentTelemetry()
_embeddings_tel = EmbeddingsTelemetry()
_hybrid_tel = HybridTelemetry()
_matcher_tel = MatcherTelemetry()


def enrichment_telemetry() -> EnrichmentTelemetry:
    """Return the process-wide :class:`EnrichmentTelemetry` singleton."""

    return _enrichment_tel


def embeddings_telemetry() -> EmbeddingsTelemetry:
    """Return the process-wide :class:`EmbeddingsTelemetry` singleton."""

    return _embeddings_tel


def hybrid_telemetry() -> HybridTelemetry:
    """Return the process-wide :class:`HybridTelemetry` singleton."""

    return _hybrid_tel


def matcher_telemetry() -> MatcherTelemetry:
    """Return the process-wide :class:`MatcherTelemetry` singleton."""

    return _matcher_tel


def reset_for_testing() -> None:
    """Zero every counter — tests call between cases for isolation."""

    global _enrichment_tel, _embeddings_tel, _hybrid_tel, _matcher_tel
    _enrichment_tel = EnrichmentTelemetry()
    _embeddings_tel = EmbeddingsTelemetry()
    _hybrid_tel = HybridTelemetry()
    _matcher_tel = MatcherTelemetry()


# ---------------------------------------------------------------------------
# Per-source timer
# ---------------------------------------------------------------------------


@dataclass
class Timer:
    """Lightweight stopwatch used by :func:`source_timer`."""

    name: str
    start_ns: int = field(default_factory=time.perf_counter_ns)
    duration_ms: int = 0


@contextlib.contextmanager
def source_timer(name: str) -> Iterator[Timer]:
    """Time a per-source operation; emit a structured log line on exit.

    Usage::

        with source_timer("greenhouse") as t:
            await source.fetch_jobs()
        # t.duration_ms is now populated

    The log line goes through the standard ``job360`` logger so the
    ``run_uuid`` ContextVar (S1) appears alongside.
    """

    timer = Timer(name=name)
    try:
        yield timer
    finally:
        elapsed_ns = time.perf_counter_ns() - timer.start_ns
        timer.duration_ms = max(0, elapsed_ns // 1_000_000)
        logger.debug("source_timer source=%s duration_ms=%s", timer.name, timer.duration_ms)
