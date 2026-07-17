"""Step-1 B8 — one-shot backfill: embed every (enriched) job.

Restored from Pillar-2 Batch 2.6 (commit 46f7c62) and adapted to the
post-restructure ``backend/scripts/`` location. Idempotent — skips jobs
that already have a ``job_embeddings`` audit row for the active model.

Usage (after ``pip install '.[semantic]'`` and with
``SEMANTIC_ENABLED=true`` in the environment)::

    cd backend
    python scripts/build_job_embeddings.py --db-path data/jobs.db

Behaviour:

    1. Bails early (exit 0) when ``SEMANTIC_ENABLED`` is false — the
       semantic stack is opt-in (CLAUDE.md rule #18).
    2. Opens the SQLite DB and the persistent ChromaDB store.
    3. Iterates jobs missing an audit row for the current model version.
       Jobs without a ``job_enrichment`` row still get a degraded
       embedding (encoder handles ``enrichment=None``).
    4. Calls ``services.embeddings.encode_job(job, enrichment)`` (lazy-
       imports sentence-transformers).
    5. Writes the vector via ``VectorIndex.upsert(...)`` and logs an
       audit row to ``job_embeddings``.
    6. Prints progress every 100 jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add backend/ to sys.path so ``from src...`` resolves when the script is
# invoked directly (``python scripts/build_job_embeddings.py``).
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent  # scripts/ → backend/
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.core.settings import SEMANTIC_ENABLED  # noqa: E402
from src.models import Job  # noqa: E402
from src.repositories import pg  # noqa: E402

logger = logging.getLogger("job360.scripts.build_job_embeddings")


async def _jobs_needing_embedding(conn: pg.Connection, model_name: str) -> list:
    """Return jobs whose audit row is absent for the active model version."""
    conn.row_factory = pg.Row
    cur = await conn.execute(
        """
        SELECT j.id, j.title, j.company, j.location, j.description,
               j.apply_url, j.source, j.date_found
          FROM jobs j
          LEFT JOIN job_embeddings em
                 ON em.job_id = j.id AND em.model_version = ?
         WHERE em.job_id IS NULL
        """,
        (model_name,),
    )
    return await cur.fetchall()


async def _run(db_path: str, limit: int | None) -> int:
    # Lazy-import the heavy modules — kept off the import path of every CLI
    # invocation per CLAUDE.md rule #16. Only the SEMANTIC_ENABLED-true path
    # touches sentence-transformers / chromadb.
    from src.services.embeddings import MODEL_NAME, encode_job
    from src.services.job_enrichment import load_enrichment
    from src.services.vector_index import VectorIndex

    index = VectorIndex()
    encoded = 0
    async with pg.connect(db_path) as conn:
        rows = await _jobs_needing_embedding(conn, MODEL_NAME)
        if limit is not None:
            rows = rows[:limit]

        total = len(rows)
        logger.info("Found %d jobs needing embeddings (model=%s)", total, MODEL_NAME)

        for row in rows:
            job = Job(
                title=row["title"] or "",
                company=row["company"] or "",
                apply_url=row["apply_url"] or "",
                source=row["source"] or "",
                date_found=row["date_found"] or "",
                location=row["location"] or "",
                description=row["description"] or "",
            )
            try:
                enrichment = await load_enrichment(conn, row["id"])
            except Exception as e:
                logger.warning("load_enrichment failed for job %s: %s", row["id"], e)
                enrichment = None

            try:
                vector = encode_job(job, enrichment)
                index.upsert(
                    row["id"],
                    vector,
                    metadata={
                        "job_id": row["id"],
                        "title": job.title,
                        "company": job.company,
                    },
                )
                await conn.execute(
                    """
                    INSERT OR REPLACE INTO job_embeddings(job_id, model_version)
                    VALUES (?, ?)
                    """,
                    (row["id"], MODEL_NAME),
                )
                encoded += 1
            except Exception as e:
                logger.warning("embed failed for job %s: %s", row["id"], e)
                continue

            if encoded % 100 == 0:
                await conn.commit()
                logger.info("Embedded %d / %d jobs so far", encoded, total)

        await conn.commit()
    logger.info("Done. %d new embeddings written.", encoded)
    return encoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="data/jobs.db")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on jobs to embed this run.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not SEMANTIC_ENABLED:
        logger.info(
            "SEMANTIC_ENABLED is false — skipping embedding backfill. "
            "Set SEMANTIC_ENABLED=true and install '.[semantic]' to enable."
        )
        return 0

    asyncio.run(_run(args.db_path, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
