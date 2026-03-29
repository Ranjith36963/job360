"""Stratified job sampler for validation — picks jobs across sources and score ranges."""

from __future__ import annotations

import aiosqlite
from collections import defaultdict
from typing import Optional


async def sample_jobs(
    db_path: str,
    per_source: int = 3,
    days: int = 7,
    min_score: int = 0,
    source_filter: Optional[str] = None,
) -> list[dict]:
    """Sample jobs from the DB, stratified by source and score range.

    Returns up to ``per_source`` jobs per active source: one high-score,
    one mid-score, and one low-score (when available).
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        where = "WHERE date_found >= datetime('now', ?)"
        params: list = [f"-{days} days"]

        if min_score > 0:
            where += " AND match_score >= ?"
            params.append(min_score)
        if source_filter:
            where += " AND source = ?"
            params.append(source_filter)

        query = f"""
            SELECT id, title, company, location, apply_url, source,
                   date_found, match_score, description, match_data,
                   salary_min, salary_max, visa_flag, job_type,
                   experience_level
            FROM jobs {where}
            ORDER BY source, match_score DESC
        """
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

    # Group by source
    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(dict(row))

    # Stratified pick: high, mid, low per source
    sampled: list[dict] = []
    for source, jobs in sorted(by_source.items()):
        if not jobs:
            continue
        picks: list[dict] = []
        n = len(jobs)
        # Jobs are already sorted by match_score DESC
        picks.append(jobs[0])                    # Highest score
        if n >= 3:
            picks.append(jobs[n // 2])           # Mid score
            picks.append(jobs[-1])               # Lowest score
        elif n == 2:
            picks.append(jobs[1])
        # Deduplicate by id (mid might == high or low)
        seen_ids: set[int] = set()
        for p in picks:
            if p["id"] not in seen_ids and len(seen_ids) < per_source:
                sampled.append(p)
                seen_ids.add(p["id"])

    return sampled
