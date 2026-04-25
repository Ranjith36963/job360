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

Plus :func:`source_timer` — a context manager that times a per-source fetch
and emits a structured log line at exit.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Optional

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


# ---------------------------------------------------------------------------
# Module-level singletons (process-wide; reset between test runs as needed)
# ---------------------------------------------------------------------------


_enrichment_tel = EnrichmentTelemetry()
_embeddings_tel = EmbeddingsTelemetry()
_hybrid_tel = HybridTelemetry()


def enrichment_telemetry() -> EnrichmentTelemetry:
    """Return the process-wide :class:`EnrichmentTelemetry` singleton."""

    return _enrichment_tel


def embeddings_telemetry() -> EmbeddingsTelemetry:
    """Return the process-wide :class:`EmbeddingsTelemetry` singleton."""

    return _embeddings_tel


def hybrid_telemetry() -> HybridTelemetry:
    """Return the process-wide :class:`HybridTelemetry` singleton."""

    return _hybrid_tel


def reset_for_testing() -> None:
    """Zero every counter — tests call between cases for isolation."""

    global _enrichment_tel, _embeddings_tel, _hybrid_tel
    _enrichment_tel = EnrichmentTelemetry()
    _embeddings_tel = EmbeddingsTelemetry()
    _hybrid_tel = HybridTelemetry()


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
