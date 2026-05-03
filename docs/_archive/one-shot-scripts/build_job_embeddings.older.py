"""Pillar 2 Batch 2.6 — one-shot backfill: embed every enriched job.

Usage (after ``pip install '.[semantic]'`` and with
``SEMANTIC_ENABLED=true`` in the environment)::

    cd backend
    python ../scripts/build_job_embeddings.py --db-path data/jobs.db

The script:

    1. Opens the SQLite DB and ChromaDB persistent store.
    2. Iterates jobs that have a ``job_enrichment`` row but are missing
       from ``job_embeddings``.
    3. Encodes each via ``services/embeddings.encode_job()``.
    4. Writes the vector to Chroma and logs an audit row to the
       ``job_embeddings`` table.

Idempotent: re-running skips jobs whose audit row already records the
current ``MODEL_NAME``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add backend/ to sys.path so we can import the services.
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import aiosqlite  # noqa: E402

from src.models import Job  # noqa: E402
from src.services.embeddings import MODEL_NAME, encode_job  # noqa: E402
from src.services.job_enrichment import load_enrichment  # noqa: E402
from src.services.vector_index import VectorIndex  # noqa: E402

logger = logging.getLogger("job360.scripts.build_job_embeddings")


async def _jobs_needing_embedding(conn: aiosqlite.Connection) -> list[tuple]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        """
        SELECT j.id, j.title, j.company, j.location, j.description
          FROM jobs j
          JOIN job_enrichment e ON e.job_id = j.id
          LEFT JOIN job_embeddings em
                 ON em.job_id = j.id AND em.model_version = ?
         WHERE em.job_id IS NULL
        """,
        (MODEL_NAME,),
    )
    return await cur.fetchall()


async def _run(db_path: str, limit: int | None) -> int:
    index = VectorIndex()
    encoded = 0
    async with aiosqlite.connect(db_path) as conn:
        rows = await _jobs_needing_embedding(conn)
        if limit is not None:
            rows = rows[:limit]

        for row in rows:
            job = Job(
                title=row["title"] or "",
                company=row["company"] or "",
                apply_url="",
                source="",
                date_found="",
                location=row["location"] or "",
                description=row["description"] or "",
            )
            enrichment = await load_enrichment(conn, row["id"])
            vector = encode_job(job, enrichment)
            index.upsert(row["id"], vector, metadata={"job_id": row["id"]})
            await conn.execute(
                """
                INSERT OR REPLACE INTO job_embeddings(job_id, model_version)
                VALUES (?, ?)
                """,
                (row["id"], MODEL_NAME),
            )
            encoded += 1
            if encoded % 100 == 0:
                await conn.commit()
                logger.info("Embedded %d jobs so far", encoded)
        await conn.commit()
    logger.info("Done. %d new embeddings written.", encoded)
    return encoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="data/jobs.db")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional cap on jobs to embed this run.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(args.db_path, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
