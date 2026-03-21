"""Cross-encoder reranking for top-N job candidates.

Uses cross-encoder/ms-marco-MiniLM-L-6-v2 to rerank the top candidates
by joint attention over (profile_text, job_text) pairs. This is much
more accurate than bi-encoder cosine similarity but ~50x slower, so we
only apply it to the top-50 candidates from initial scoring.

Falls back gracefully if sentence-transformers is not installed.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Sentinel: None = not yet attempted, False = unavailable
_model: object = None
_available: Optional[bool] = None

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _load_model():
    """Lazy-load cross-encoder model (singleton)."""
    global _model, _available
    if _available is not None:
        return _model

    try:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(MODEL_NAME)
        _available = True
        logger.info("Loaded cross-encoder: %s", MODEL_NAME)
    except ImportError:
        _model = None
        _available = False
        logger.info("sentence-transformers not installed — cross-encoder reranking disabled")
    except Exception as e:
        _model = None
        _available = False
        logger.warning("Failed to load cross-encoder: %s", e)

    return _model


def is_available() -> bool:
    """Check if cross-encoder model is available (triggers lazy load)."""
    _load_model()
    return _available is True


def rerank(
    profile_text: str,
    candidates: list[dict],
    text_key: str = "description",
    title_key: str = "title",
    top_n: int = 50,
) -> list[dict]:
    """Rerank candidate jobs using cross-encoder scoring.

    Args:
        profile_text: User's professional profile summary (job titles + skills).
        candidates: List of job dicts (must have text_key and title_key).
        text_key: Key for job description text in each dict.
        title_key: Key for job title in each dict.
        top_n: Max candidates to rerank (pass-through rest unchanged).

    Returns:
        Reranked list of job dicts with 'rerank_score' added.
        If cross-encoder is unavailable, returns candidates unchanged.
    """
    model = _load_model()
    if model is None or not candidates or not profile_text:
        return candidates

    # Only rerank top_n candidates
    to_rerank = candidates[:top_n]
    rest = candidates[top_n:]

    # Build (profile, job_text) pairs
    pairs = []
    for job in to_rerank:
        job_text = f"{job.get(title_key, '')} {job.get(text_key, '')}"
        # Truncate to avoid model max length issues (512 tokens ≈ 2000 chars)
        job_text = job_text[:2000]
        profile_truncated = profile_text[:500]
        pairs.append((profile_truncated, job_text))

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning("Cross-encoder scoring failed: %s", e)
        return candidates

    # Attach scores and sort
    for job, score in zip(to_rerank, scores):
        job["rerank_score"] = float(score)

    to_rerank.sort(key=lambda j: j.get("rerank_score", 0), reverse=True)

    return to_rerank + rest


def build_profile_text(
    job_titles: list[str],
    primary_skills: list[str],
    secondary_skills: list[str],
) -> str:
    """Build a concise profile summary for cross-encoder input."""
    parts = []
    if job_titles:
        parts.append("Target roles: " + ", ".join(job_titles[:5]))
    if primary_skills:
        parts.append("Key skills: " + ", ".join(primary_skills[:10]))
    if secondary_skills:
        parts.append("Additional skills: " + ", ".join(secondary_skills[:8]))
    return ". ".join(parts)
