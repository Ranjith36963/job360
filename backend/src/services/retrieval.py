"""Pillar 2 Batch 2.7 — hybrid retrieval with Reciprocal Rank Fusion.

Two signals combined via RRF:

  Stage A — keyword: existing SQL scorer's top-500 by ``match_score``.
  Stage B — semantic: ChromaDB nearest-neighbour on the user profile text.

Stage C — ``reciprocal_rank_fusion(ranked_lists, k=60)`` merges the
per-source rankings without needing score calibration between the two.

When embeddings are unavailable (Chroma empty or ``sentence_transformers``
not installed), the retrieval gracefully falls back to keyword-only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Callable, Optional

logger = logging.getLogger("job360.services.retrieval")


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion — pure function
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    ranked_lists: Iterable[list[Any]],
    k: int = 60,
) -> list[tuple[Any, float]]:
    """Fuse multiple ranked lists into one ranked list via RRF.

    For each list, an item at rank `i` (0-indexed) contributes
    ``1 / (k + i + 1)`` to its running RRF score. Items appearing in
    multiple lists accumulate; the output is sorted by descending score.

    Args:
        ranked_lists: iterable of lists — each list is ordered best → worst.
        k: smoothing constant. Plan §4 Batch 2.7 pins ``k=60`` (the Cormack
            2009 default).

    Returns:
        List of ``(item, score)`` tuples in descending score order.
        Preserves first-appearance-order across ranked_lists as a stable
        tiebreaker when two items score identically.
    """
    if k <= 0:
        raise ValueError("k must be positive")

    scores: dict[Any, float] = {}
    first_seen_index: dict[Any, tuple[int, int]] = {}
    for list_index, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
            if item not in first_seen_index:
                first_seen_index[item] = (list_index, rank)

    # Sort descending by score, tiebreaker = first appearance (stable).
    return sorted(
        scores.items(),
        key=lambda kv: (-kv[1], first_seen_index[kv[0]]),
    )


# ---------------------------------------------------------------------------
# Retrieval orchestrator
# ---------------------------------------------------------------------------


def retrieve_for_user(
    profile,
    *,
    k: int = 100,
    keyword_fn: Optional[Callable[[Any, int], list[int]]] = None,
    semantic_fn: Optional[Callable[[Any, int], list[int]]] = None,
    rrf_k: int = 60,
) -> list[int]:
    """Return the top-`k` fused job ids for a user profile.

    This is sync-friendly (pure orchestration) — the upstream fetchers are
    injected via ``keyword_fn`` and ``semantic_fn``. Callers from FastAPI
    or ARQ pass their own wrappers that hit SQLite + ChromaDB.

    Args:
        profile: a ``UserProfile`` — passed through to both fetchers.
        k: how many ids to return after fusion.
        keyword_fn: ``(profile, limit) -> list[int]`` of top keyword-matched
            job ids. Required — this is the always-available path.
        semantic_fn: ``(profile, limit) -> list[int]`` of semantically
            closest job ids. Optional — when None or it returns [],
            retrieval degrades to keyword-only.
        rrf_k: RRF smoothing constant (default 60).

    Returns:
        List of job ids, best first. Length up to ``k``.
    """
    if keyword_fn is None:
        raise ValueError("keyword_fn is required")

    # Step-1 S3 — telemetry. Local import + flag check so the counter stays
    # inert when SEMANTIC_ENABLED is false (CLAUDE.md rule #18).
    import os  # noqa: PLC0415 — local for env-var read at call time

    from src.utils.telemetry import hybrid_telemetry  # noqa: PLC0415

    semantic_on = os.getenv("SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    tel = hybrid_telemetry() if semantic_on else None

    # Stage A — keyword top 500.
    keyword_ids = keyword_fn(profile, 500)
    if not keyword_ids:
        # Nothing to fuse. Bail early — semantic results alone would
        # produce rankings with no keyword corroboration.
        if tel is not None:
            tel.keyword_calls += 1
            tel.fallback_reason = "empty_index"
        return []

    # Stage B — semantic top 500 (when available).
    semantic_ids: list[int] = []
    fallback_reason: str | None = None
    if semantic_fn is None:
        fallback_reason = "stack_unavailable"
    else:
        try:
            semantic_ids = semantic_fn(profile, 500)
        except Exception as e:
            logger.warning("Semantic retrieval failed — falling back to keyword: %s", e)
            semantic_ids = []
            fallback_reason = "exception"

    if not semantic_ids:
        if tel is not None:
            tel.keyword_calls += 1
            tel.fallback_reason = fallback_reason or "empty_index"
        return keyword_ids[:k]

    # Stage C — RRF fusion.
    if tel is not None:
        tel.hybrid_calls += 1
        tel.fallback_reason = None
    fused = reciprocal_rank_fusion([keyword_ids, semantic_ids], k=rrf_k)
    return [item for item, _score in fused[:k]]


def is_hybrid_available(vector_index_count: int) -> bool:
    """Return True if the hybrid path has a populated vector index.

    API routes use this to decide whether ``?mode=hybrid`` is the default
    or whether to fall back to ``?mode=keyword`` when Chroma is empty.
    """
    return vector_index_count > 0


# ---------------------------------------------------------------------------
# Pillar 2 Batch 2.8 — cross-encoder rerank
# ---------------------------------------------------------------------------


CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# Plan §4 Batch 2.8 — rerank the top-50 survivors from RRF.
_DEFAULT_RERANK_TOP_N = 50

# Module-level cached CrossEncoder instance (like embeddings._ENCODER).
_CROSS_ENCODER: Optional[object] = None


def _load_cross_encoder() -> object:
    """Lazy import (CLAUDE.md rule #11 spirit)."""
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "sentence-transformers is not installed — run " "`pip install '.[semantic]'` and retry."
        ) from e
    _CROSS_ENCODER = CrossEncoder(CROSS_ENCODER_MODEL)
    return _CROSS_ENCODER


def reset_cross_encoder_for_testing() -> None:
    """Discard the cached cross-encoder — tests call this between swaps."""
    global _CROSS_ENCODER
    _CROSS_ENCODER = None


def cross_encoder_rerank(
    query: str,
    candidates: list[tuple[Any, str]],
    *,
    top_n: int = _DEFAULT_RERANK_TOP_N,
    encoder_factory: Optional[Callable[[], object]] = None,
) -> list[tuple[Any, float]]:
    """Rerank `candidates` by cross-encoder relevance to `query`.

    Args:
        query: the user's query text (profile summary / target role).
        candidates: a list of ``(item_id, candidate_text)`` pairs, ordered
            by the upstream RRF fusion (best first). Only the first
            ``top_n`` are rescored — the research report's standard
            rerank-on-top-K pattern.
        top_n: rerank budget (default 50 per plan).
        encoder_factory: test-only override returning a CrossEncoder-
            compatible object with ``.predict(list[tuple[str, str]]) ->
            list[float]``.

    Returns:
        ``[(item_id, rerank_score)]`` sorted descending by the new score.
        Items beyond ``top_n`` keep their original order at the tail with
        a sentinel score of -inf (so they always sit below the rescored
        ones).
    """
    if not candidates:
        return []

    head = candidates[:top_n]
    tail = candidates[top_n:]

    encoder = encoder_factory() if encoder_factory else _load_cross_encoder()
    pairs = [(query, text) for _id, text in head]
    scores = encoder.predict(pairs)
    # Normalise to a plain Python list[float] in case a numpy array comes back.
    try:
        scores = [float(s) for s in scores]
    except TypeError:
        scores = [float(scores)]

    reranked_head = sorted(
        ((item_id, score) for (item_id, _text), score in zip(head, scores)),
        key=lambda kv: -kv[1],
    )
    reranked_tail = [(item_id, float("-inf")) for item_id, _text in tail]
    return reranked_head + reranked_tail
