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
import math
import re
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
# BM25 — pure lexical ranker (the BM25 leg of the hybrid engine)
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lower-case word/number tokens. Shared by BM25 query + docs."""
    return _TOKEN_RE.findall((text or "").lower())


def bm25_rank(
    query: str,
    documents: list[tuple[Any, str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[Any, float]]:
    """Rank ``documents`` by Okapi BM25 relevance to ``query``.

    Pure, dependency-free lexical ranker — distinct from engine 1's weighted
    keyword scorer in that it applies IDF (rare query terms weigh more),
    term-frequency saturation (``k1``) and length normalisation (``b``).

    Args:
        query: free text (the user's profile titles/skills/summary).
        documents: ``(item_id, text)`` pairs; ids are returned verbatim.
        k1: term-frequency saturation constant (Robertson default 1.5).
        b: length-normalisation strength in [0, 1] (default 0.75).

    Returns:
        ``[(item_id, score)]`` for documents with score > 0, sorted
        descending. Stable tiebreak by first-appearance order. An empty
        query, empty corpus, or a query matching no document → ``[]``.
    """
    q_terms = set(_tokenize(query))
    if not q_terms or not documents:
        return []

    doc_tokens = [_tokenize(text) for _id, text in documents]
    doc_lens = [len(toks) for toks in doc_tokens]
    n_docs = len(documents)
    avgdl = sum(doc_lens) / n_docs if n_docs else 0.0

    # Document frequency per query term.
    df: dict[str, int] = {}
    for toks in doc_tokens:
        present = set(toks)
        for term in q_terms & present:
            df[term] = df.get(term, 0) + 1

    # Robust BM25 IDF — the +1 keeps weights positive even for common terms.
    idf = {
        term: math.log(1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        for term in q_terms
    }

    scored: list[tuple[int, Any, float]] = []
    for idx, ((item_id, _text), toks, dl) in enumerate(zip(documents, doc_tokens, doc_lens)):
        if dl == 0:
            continue
        tf: dict[str, int] = {}
        for t in toks:
            if t in q_terms:
                tf[t] = tf.get(t, 0) + 1
        norm = k1 * (1 - b + b * (dl / avgdl)) if avgdl else k1
        score = sum(idf[term] * (freq * (k1 + 1)) / (freq + norm) for term, freq in tf.items())
        if score > 0:
            scored.append((idx, item_id, score))

    scored.sort(key=lambda t: (-t[2], t[0]))
    return [(item_id, score) for _idx, item_id, score in scored]


# ---------------------------------------------------------------------------
# Retrieval orchestrator
# ---------------------------------------------------------------------------


def retrieve_for_user(
    profile: Any,
    *,
    k: int = 100,
    keyword_fn: Optional[Callable[[Any, int], list[int]]] = None,
    semantic_fn: Optional[Callable[[Any, int], list[int]]] = None,
    rrf_k: int = 60,
    bm25_fn: Optional[Callable[[Any, int], list[int]]] = None,
    rerank_fn: Optional[Callable[[list[int]], list[int]]] = None,
) -> list[int]:
    """Return the top-`k` fused job ids for a user profile — the single
    orchestration seam for the hybrid engine (engine 3).

    Sync-friendly (pure orchestration) — every upstream is injected, so this
    is fully unit-testable offline. Callers from FastAPI or ARQ pass wrappers
    that hit SQLite / SQLite-BM25 / ChromaDB / the cross-encoder.

    Pipeline: keyword anchor → (optional) BM25 + semantic legs fused via RRF
    → (optional) cross-encoder rerank → truncate to ``k``.

    Args:
        profile: a ``UserProfile`` — passed through to every fetcher.
        k: how many ids to return after rerank.
        keyword_fn: ``(profile, limit) -> list[int]`` of top keyword-matched
            job ids. Required — this is the always-available anchor.
        semantic_fn: ``(profile, limit) -> list[int]`` of semantically closest
            ids. Optional — None / [] / exception degrades that leg silently.
        rrf_k: RRF smoothing constant (default 60).
        bm25_fn: ``(profile, limit) -> list[int]`` BM25-ranked ids. Optional —
            None / [] / exception degrades that leg silently. Pure-Python, no
            heavy deps, so it can run even when the semantic stack is absent.
        rerank_fn: ``(list[int]) -> list[int]`` cross-encoder rerank applied to
            the fused list BEFORE truncation, so a strong rerank can promote a
            candidate above the ``k`` cut-off. Optional.

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

    # Stage A — keyword top 500 (always-available anchor).
    keyword_ids = keyword_fn(profile, 500)
    if not keyword_ids:
        # Nothing to fuse. Bail early — extra legs alone would produce
        # rankings with no keyword corroboration.
        if tel is not None:
            tel.keyword_calls += 1
            tel.fallback_reason = "empty_index"
        return []

    # Stage A2 — BM25 lexical leg (optional; pure, no heavy deps).
    bm25_ids: list[int] = []
    if bm25_fn is not None:
        try:
            bm25_ids = bm25_fn(profile, 500) or []
        except Exception as e:
            logger.warning("BM25 retrieval failed — skipping that leg: %s", e)
            bm25_ids = []

    # Stage B — semantic top 500 (optional; heavy, may be unavailable).
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

    # Stage C — fuse whatever extra signals we have with the keyword anchor.
    extra_lists = [lst for lst in (bm25_ids, semantic_ids) if lst]
    if not extra_lists:
        if tel is not None:
            tel.keyword_calls += 1
            tel.fallback_reason = fallback_reason or "empty_index"
        result_ids = list(keyword_ids)
    else:
        if tel is not None:
            tel.hybrid_calls += 1
            # Telemetry's hybrid/keyword split tracks the SEMANTIC leg only;
            # a BM25-only fusion still records the semantic fallback reason.
            tel.fallback_reason = None if semantic_ids else fallback_reason
        fused = reciprocal_rank_fusion([keyword_ids, *extra_lists], k=rrf_k)
        result_ids = [item for item, _score in fused]

    # Stage D — optional cross-encoder rerank, BEFORE truncation.
    if rerank_fn is not None:
        result_ids = rerank_fn(result_ids)

    return result_ids[:k]


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
        from sentence_transformers import CrossEncoder
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

    # `Any`: the encoder is a duck-typed CrossEncoder-compatible object (the
    # real class is lazily imported, tests inject a stub) exposing `.predict`.
    encoder: Any = encoder_factory() if encoder_factory else _load_cross_encoder()
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
