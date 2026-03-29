"""Feedback loop: adjust job scores based on user liked/rejected signals.

Computes a preference adjustment (+/- 10 points) by comparing new jobs
against the user's historical liked/not_interested actions. Uses embedding
similarity when available, falls back to keyword overlap.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger("job360.feedback")

# Max adjustment from feedback signal (doubled from ±5 for meaningful impact)
FEEDBACK_BONUS = 10
FEEDBACK_PENALTY = -10


async def load_feedback_signals(conn) -> dict:
    """Load liked/not_interested job data from the database.

    Returns dict with keys:
        'liked_texts': list of (title + description) strings for liked jobs
        'rejected_texts': list of (title + description) strings for rejected jobs
        'liked_embeddings': list of numpy vectors (or empty)
        'rejected_embeddings': list of numpy vectors (or empty)
    """
    signals = {
        "liked_texts": [],
        "rejected_texts": [],
        "liked_embeddings": [],
        "rejected_embeddings": [],
    }

    try:
        cursor = await conn.execute("""
            SELECT j.title, j.description, j.embedding, ua.action
            FROM user_actions ua
            JOIN jobs j ON j.id = ua.job_id
            WHERE ua.action IN ('liked', 'not_interested')
        """)
        rows = await cursor.fetchall()
    except Exception:
        return signals

    from src.filters.hybrid_retriever import deserialize_embedding

    for row in rows:
        text = f"{row[0]} {row[1]}"
        action = row[3]
        emb = deserialize_embedding(row[2]) if row[2] else None

        if action == "liked":
            signals["liked_texts"].append(text)
            if emb is not None:
                signals["liked_embeddings"].append(emb)
        elif action == "not_interested":
            signals["rejected_texts"].append(text)
            if emb is not None:
                signals["rejected_embeddings"].append(emb)

    return signals


def build_preference_vector(signals: dict) -> Optional[np.ndarray]:
    """Build a preference vector from liked/rejected embeddings.

    Uses: preference = mean(liked) - 0.5 * mean(rejected), then L2-normalize.
    Returns None if no embeddings available.
    """
    liked = signals.get("liked_embeddings", [])
    rejected = signals.get("rejected_embeddings", [])

    if not liked and not rejected:
        return None

    vec = np.zeros(384, dtype=np.float32)

    if liked:
        liked_mean = np.mean(liked, axis=0)
        vec += liked_mean

    if rejected:
        rejected_mean = np.mean(rejected, axis=0)
        vec -= 0.5 * rejected_mean

    norm = np.linalg.norm(vec)
    if norm < 1e-10:
        return None

    logger.debug(
        "Preference vector built: %d liked embeddings, %d rejected embeddings",
        len(liked), len(rejected),
    )

    return vec / norm


def compute_feedback_adjustment(
    job_text: str,
    signals: dict,
    preference_vector: Optional[np.ndarray] = None,
    job_embedding: Optional[np.ndarray] = None,
) -> int:
    """Compute feedback adjustment for a job (-5 to +5).

    Strategy:
    1. If embeddings available: cosine similarity with preference vector
    2. Fallback: keyword overlap with liked/rejected texts

    Args:
        job_text: Job title + description.
        signals: Output from load_feedback_signals().
        preference_vector: Pre-computed from build_preference_vector().
        job_embedding: Pre-computed embedding for this job.

    Returns:
        Integer adjustment from FEEDBACK_PENALTY to FEEDBACK_BONUS.
    """
    if not signals.get("liked_texts") and not signals.get("rejected_texts"):
        return 0

    # Strategy 1: embedding similarity
    if preference_vector is not None and job_embedding is not None:
        sim = float(np.dot(preference_vector, job_embedding))
        # Map similarity: >0.3 → bonus, <-0.1 → penalty, else graduated
        if sim > 0.3:
            logger.debug("Feedback +%d for '%.40s' (sim=%.3f)", FEEDBACK_BONUS, job_text, sim)
            return FEEDBACK_BONUS
        if sim > 0.15:
            logger.debug("Feedback +6 for '%.40s' (sim=%.3f)", job_text, sim)
            return 6
        if sim < -0.1:
            logger.debug("Feedback %d for '%.40s' (sim=%.3f)", FEEDBACK_PENALTY, job_text, sim)
            return FEEDBACK_PENALTY
        if sim < 0.0:
            logger.debug("Feedback -6 for '%.40s' (sim=%.3f)", job_text, sim)
            return -6
        return 0

    # Strategy 2: keyword overlap fallback
    return _keyword_feedback(job_text, signals)


def _keyword_feedback(job_text: str, signals: dict) -> int:
    """Simple keyword-based feedback using liked/rejected text overlap."""
    job_words = set(job_text.lower().split())

    liked_score = 0
    for liked_text in signals.get("liked_texts", []):
        liked_words = set(liked_text.lower().split())
        overlap = len(job_words & liked_words)
        if overlap > 5:
            liked_score += 1

    rejected_score = 0
    for rejected_text in signals.get("rejected_texts", []):
        rejected_words = set(rejected_text.lower().split())
        overlap = len(job_words & rejected_words)
        if overlap > 5:
            rejected_score += 1

    if liked_score > rejected_score:
        return min(liked_score * 3, FEEDBACK_BONUS)
    if rejected_score > liked_score:
        return max(-rejected_score * 3, FEEDBACK_PENALTY)
    return 0
