"""Hybrid retrieval: FTS5 full-text search + vector similarity + RRF fusion.

Combines two independent ranking signals:
- FTS5: lexical/keyword matching (excels at exact terms like "CISSP", "Python")
- Vector: semantic similarity (catches paraphrases like "cloud infra" ↔ "AWS DevOps")

Fused using Reciprocal Rank Fusion (RRF): score = Σ 1/(k + rank_i)
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# RRF constant — standard value from the original paper (Cormack et al. 2009)
RRF_K = 60


def serialize_embedding(vec: np.ndarray) -> str:
    """Serialize a numpy vector to base64 string for SQLite storage."""
    return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")


def deserialize_embedding(data: str) -> Optional[np.ndarray]:
    """Deserialize a base64 string back to numpy vector."""
    if not data:
        return None
    try:
        raw = base64.b64decode(data)
        return np.frombuffer(raw, dtype=np.float32).copy()
    except Exception:
        return None


def rrf_fuse(ranked_lists: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion over multiple ranked ID lists.

    Args:
        ranked_lists: List of ranked ID lists (each ordered by relevance).
        k: RRF constant (default 60).

    Returns:
        List of (job_id, rrf_score) tuples sorted by descending fused score.
    """
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, job_id in enumerate(ranked):
            scores[job_id] = scores.get(job_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


class HybridRetriever:
    """Hybrid search over the jobs database using FTS5 + vector similarity."""

    def __init__(self, conn):
        """Accept an aiosqlite connection (from JobDatabase)."""
        self._conn = conn

    async def fts5_search(self, query: str, limit: int = 50) -> list[int]:
        """Full-text search using FTS5. Returns ranked job IDs (rowid)."""
        if not query.strip():
            return []
        try:
            # FTS5 implicit AND: split words are ANDed by default.
            # Wrap individual terms with OR for broader matching.
            terms = [t for t in query.strip().split() if t]
            safe_query = " OR ".join(terms) if len(terms) > 1 else terms[0]
            cursor = await self._conn.execute(
                """SELECT rowid FROM jobs_fts
                   WHERE jobs_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.debug("FTS5 search failed (table may not exist): %s", e)
            return []

    async def vector_search(
        self, query_embedding: np.ndarray, limit: int = 50
    ) -> list[int]:
        """Vector similarity search over stored embeddings. Returns ranked job IDs."""
        if query_embedding is None:
            return []

        try:
            cursor = await self._conn.execute(
                "SELECT id, embedding FROM jobs WHERE embedding != '' AND embedding IS NOT NULL"
            )
            rows = await cursor.fetchall()
        except Exception as e:
            logger.debug("Vector search failed: %s", e)
            return []

        if not rows:
            return []

        scored: list[tuple[int, float]] = []
        for row in rows:
            vec = deserialize_embedding(row[1])
            if vec is not None:
                sim = float(np.dot(query_embedding, vec))
                scored.append((row[0], sim))

        scored.sort(key=lambda x: -x[1])
        return [job_id for job_id, _ in scored[:limit]]

    async def hybrid_search(
        self,
        query: str,
        profile_embedding: Optional[np.ndarray] = None,
        limit: int = 50,
    ) -> list[tuple[int, float]]:
        """Hybrid search combining FTS5 + vector similarity via RRF.

        Args:
            query: Text query for FTS5 search.
            profile_embedding: Pre-computed profile vector for semantic search.
            limit: Max results.

        Returns:
            List of (job_id, rrf_score) sorted by fused relevance.
        """
        ranked_lists = []

        # FTS5 ranking
        fts_results = await self.fts5_search(query, limit=limit * 2)
        if fts_results:
            ranked_lists.append(fts_results)

        # Vector ranking
        if profile_embedding is not None:
            vec_results = await self.vector_search(profile_embedding, limit=limit * 2)
            if vec_results:
                ranked_lists.append(vec_results)

        if not ranked_lists:
            return []

        # If only one signal, use it directly
        if len(ranked_lists) == 1:
            return [(job_id, 1.0 / (RRF_K + rank + 1))
                    for rank, job_id in enumerate(ranked_lists[0][:limit])]

        return rrf_fuse(ranked_lists)[:limit]

    async def search_with_details(
        self,
        query: str,
        profile_embedding: Optional[np.ndarray] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search and return full job rows with RRF scores.

        Returns list of job dicts with an extra 'rrf_score' key.
        """
        results = await self.hybrid_search(query, profile_embedding, limit)
        if not results:
            return []

        job_ids = [r[0] for r in results]
        score_map = {r[0]: r[1] for r in results}

        # Fetch job details in bulk
        placeholders = ",".join("?" * len(job_ids))
        cursor = await self._conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})",
            job_ids,
        )
        rows = await cursor.fetchall()
        jobs = {row[0]: dict(row) for row in rows}

        # Return in RRF-ranked order
        output = []
        for job_id in job_ids:
            if job_id in jobs:
                job = jobs[job_id]
                job["rrf_score"] = score_map[job_id]
                output.append(job)

        return output
