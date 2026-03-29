"""Semantic embeddings for job-profile similarity scoring.

Uses sentence-transformers (all-MiniLM-L6-v2, 384-dim) with lazy singleton
loading. Model is downloaded on first use (~33 MB) and cached by the library.
Falls back gracefully if sentence-transformers is not installed.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger("job360.embeddings")

# Sentinel: None = not yet attempted, False = unavailable
_model: object = None
_available: Optional[bool] = None

# Model name — small, fast, good quality for semantic similarity
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _load_model():
    """Lazy-load sentence-transformers model (singleton)."""
    global _model, _available
    if _available is not None:
        return _model

    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
        _available = True
        logger.info("Loaded embedding model: %s", MODEL_NAME)
    except ImportError:
        _model = None
        _available = False
        logger.info("sentence-transformers not installed — semantic scoring disabled")
    except Exception as e:
        _model = None
        _available = False
        logger.warning("Failed to load embedding model: %s", e)

    return _model


def is_available() -> bool:
    """Check if embedding model is available (triggers lazy load)."""
    _load_model()
    return _available is True


def encode(text: str) -> Optional[np.ndarray]:
    """Encode text into a 384-dim embedding vector. Returns None if unavailable."""
    model = _load_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
        return vec / (np.linalg.norm(vec) + 1e-10)  # L2-normalize
    except Exception as e:
        logger.warning("Embedding encode failed: %s", e)
        return None


def encode_batch(texts: list[str]) -> Optional[np.ndarray]:
    """Encode a batch of texts. Returns (N, 384) array or None."""
    model = _load_model()
    if model is None or not texts:
        return None
    try:
        vecs = model.encode(texts, convert_to_numpy=True, show_progress_bar=False,
                            batch_size=64)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-10
        normalized = vecs / norms
        logger.debug("encode_batch: %d texts → shape %s", len(texts), normalized.shape)
        return normalized
    except Exception as e:
        logger.warning("Batch embedding failed: %s", e)
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalized vectors."""
    return float(np.dot(a, b))


def build_profile_embedding(
    job_titles: list[str],
    primary_skills: list[str],
    secondary_skills: list[str],
    relevance_keywords: list[str],
    about_me: str = "",
) -> Optional[np.ndarray]:
    """Build a single embedding vector representing the user's professional profile.

    Combines job titles + about_me + skills + keywords into a weighted text
    representation, then encodes it into a single 384-dim vector.
    """
    parts = []
    # Job titles get repeated for emphasis (they're the strongest signal)
    for t in job_titles[:5]:
        parts.append(t)
        parts.append(t)
    # About me — personal career narrative (rich semantic signal)
    if about_me:
        parts.append(about_me)
    # Primary skills — single mention each
    for s in primary_skills[:15]:
        parts.append(s)
    # Secondary skills — single mention
    for s in secondary_skills[:10]:
        parts.append(s)
    # Top relevance keywords
    for kw in relevance_keywords[:20]:
        parts.append(kw)

    if not parts:
        return None

    text = " . ".join(parts)
    return encode(text)


def score_semantic_similarity(
    profile_embedding: Optional[np.ndarray],
    job_text: str,
    max_points: int = 10,
    job_embedding: Optional[np.ndarray] = None,
) -> int:
    """Score semantic similarity between profile embedding and job text.

    Args:
        profile_embedding: Pre-computed profile vector (384-dim, L2-normalized).
        job_text: Job title + description concatenated.
        max_points: Maximum points to award (default 10 = DIM_SEMANTIC).
        job_embedding: Optional pre-computed job vector. Skips re-encoding if provided.

    Returns:
        Integer score 0 to max_points.
    """
    if profile_embedding is None:
        return 0

    if job_embedding is None:
        job_embedding = encode(job_text)
    if job_embedding is None:
        return 0

    sim = cosine_similarity(profile_embedding, job_embedding)
    # Similarity typically ranges 0.1-0.8 for relevant pairs.
    # Map: <0.2 → 0, 0.2-0.6 → linear 0-max, >0.6 → max
    if sim < 0.2:
        return 0
    if sim > 0.6:
        return max_points
    scaled = (sim - 0.2) / 0.4  # 0.0 to 1.0
    return min(int(scaled * max_points + 0.5), max_points)
