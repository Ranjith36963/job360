"""Pillar 2 Batch 2.6 — job embeddings.

Encodes a Job + its enrichment into a 384-dim vector using
`sentence-transformers/all-MiniLM-L6-v2`. Handles the short-profile →
long-description asymmetry the research report flags: when the
`requirements_summary` exceeds 300 tokens, split into 300-token windows
with 50-token overlap and store `max(chunk_similarities)` as the
job-level score.

CLAUDE.md rule #11 compliance — `sentence_transformers` is imported
lazily inside the functions that use it. Tests inject an
`encoder_factory` that returns a fake encoder so no real 80 MB model
is downloaded during pytest.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Callable, Optional

logger = logging.getLogger("job360.services.embeddings")


def _semantic_enabled() -> bool:
    """Read ``SEMANTIC_ENABLED`` at call time so tests can monkey-patch the env."""

    return os.getenv("SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Chunking policy — plan §4 Batch 2.6 calls for 300-token windows with
# 50-token overlap on `requirements_summary`. Implemented here as word-count
# splits so we avoid shipping a dedicated tokenizer inside library code.
_CHUNK_SIZE_WORDS = 300
_CHUNK_OVERLAP_WORDS = 50


# Loaded on first call, reused thereafter. Tests override via the
# ``encoder_factory`` parameter, never touching this cache.
_ENCODER: Optional[object] = None


def _load_encoder() -> object:
    """Lazy-load the sentence-transformers encoder. CLAUDE.md rule #11."""
    global _ENCODER
    if _ENCODER is not None:
        return _ENCODER
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "sentence-transformers is not installed — run " "`pip install '.[semantic]'` and retry."
        ) from e
    _ENCODER = SentenceTransformer(MODEL_NAME)
    return _ENCODER


def _encode_text(text: str, encoder: object):
    """Wrap a single-text .encode() call. Returns a list[float] for JSON safety."""
    import numpy as np  # Local import — same CLAUDE.md rule pattern.

    vec = encoder.encode(text)
    if isinstance(vec, np.ndarray):
        vec = vec.tolist()
    elif isinstance(vec, list) and vec and isinstance(vec[0], np.ndarray):
        vec = vec[0].tolist()
    return vec


def _chunk_words(text: str, size: int, overlap: int) -> list[str]:
    """Split a string on word boundaries into overlapping windows."""
    words = text.split()
    if len(words) <= size:
        return [text]
    chunks: list[str] = []
    step = size - overlap
    if step <= 0:
        step = size
    for start in range(0, len(words), step):
        window = words[start : start + size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + size >= len(words):
            break
    return chunks


def _pool_chunk_vectors(vectors: Iterable[list[float]]) -> list[float]:
    """Element-wise max-pool — the research report's recommendation for
    matching a short query against long documents. Preserves the
    strongest signal per dimension so a single chunk's relevance doesn't
    wash out in a mean."""
    vecs = list(vectors)
    if not vecs:
        return []
    if len(vecs) == 1:
        return list(vecs[0])
    dim = len(vecs[0])
    out = [float("-inf")] * dim
    for v in vecs:
        for i, x in enumerate(v):
            if x > out[i]:
                out[i] = x
    return out


def encode_job(
    job,
    enrichment,
    *,
    encoder_factory: Optional[Callable[[], object]] = None,
) -> list[float]:
    """Produce a 384-dim embedding for (job, enrichment).

    Base text: ``title + " | " + requirements_summary + " | " +
    " ".join(required_skills)``. When the full job description exceeds
    ``_CHUNK_SIZE_WORDS`` words, chunk the description with overlap and
    max-pool the per-chunk vectors — the research report's asymmetric
    search trick (short query, long document).

    Args:
        job: a `Job` dataclass — `title` and `description` are read.
        enrichment: a `JobEnrichment` — may be None (degraded mode:
            encode the title alone).
        encoder_factory: optional callable returning a sentence-transformers-
            compatible encoder (anything with `.encode(str) -> list[float]`).
            Tests inject a fake here to avoid the 80 MB model download.

    Returns:
        A `list[float]` of length 384 (plain Python to keep the DB write
        path JSON-safe).
    """
    started_ns = time.perf_counter_ns()
    try:
        encoder = encoder_factory() if encoder_factory else _load_encoder()

        title = (getattr(job, "title", None) or "").strip()
        description = (getattr(job, "description", "") or "").strip()

        if enrichment is None:
            # Degraded mode: title + description (chunked if long), no enriched
            # fields to round things out.
            if not description or len(description.split()) <= _CHUNK_SIZE_WORDS:
                return _encode_text(
                    (f"{title} | {description}").strip(" |") or "job",
                    encoder,
                )
            chunks = _chunk_words(description, _CHUNK_SIZE_WORDS, _CHUNK_OVERLAP_WORDS)
            return _pool_chunk_vectors([_encode_text(f"{title} | {c}", encoder) for c in chunks])

        summary = (getattr(enrichment, "requirements_summary", "") or "").strip()
        required = getattr(enrichment, "required_skills", []) or []
        required_joined = " ".join(required)

        base_text = f"{title} | {summary} | {required_joined}".strip(" |")

        # Chunk the long description (unbounded) rather than the 250-char summary.
        desc_words = description.split()
        if not description or len(desc_words) <= _CHUNK_SIZE_WORDS:
            return _encode_text(base_text or title or "job", encoder)

        chunks = _chunk_words(description, _CHUNK_SIZE_WORDS, _CHUNK_OVERLAP_WORDS)
        chunk_texts = [f"{title} | {summary} | {required_joined} | {c}".strip(" |") for c in chunks]
        chunk_vecs = [_encode_text(t, encoder) for t in chunk_texts]
        return _pool_chunk_vectors(chunk_vecs)
    finally:
        # Step-1 S3 — record encode duration. Stays inert when SEMANTIC_ENABLED
        # is false (CLAUDE.md rule #18). Local import keeps the path cheap.
        if _semantic_enabled():
            from src.utils.telemetry import embeddings_telemetry

            tel = embeddings_telemetry()
            tel.encode_duration_ms += int(max(0, (time.perf_counter_ns() - started_ns) // 1_000_000))


def reset_cache_for_testing() -> None:
    """Discard the cached encoder — tests that swap models call this."""
    global _ENCODER
    _ENCODER = None
