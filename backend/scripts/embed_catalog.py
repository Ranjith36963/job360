"""Backfill embeddings for every catalog job that doesn't have one.

Job-understanding goal (2026-08-05): the semantic engine (E3) can only rank
what is embedded, and prod had 284 of 7,309 jobs embedded (3.9%) — so hybrid
retrieval silently degraded to keyword-only. This script closes that gap:

    python scripts/embed_catalog.py            # embed everything missing
    python scripts/embed_catalog.py --limit 50 # bounded probe run

Idempotent: jobs already in the `job_embeddings` audit table are skipped, so
re-runs only touch new arrivals. Uses the same `encode_job` + `VectorIndex`
path the pipeline uses (model all-MiniLM-L6-v2, enrichment-aware text build),
so the vectors are indistinguishable from ingest-time ones.

Heavy deps (sentence-transformers, chromadb) load lazily per rule #16 — the
script needs the `[semantic]` extra installed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if os.name == "nt":
    # psycopg cannot run async on the Windows Proactor loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main(limit: int | None) -> int:
    from src.core.settings import DB_PATH
    from src.models import Job
    from src.repositories import pg
    from src.services.embeddings import MODEL_NAME, encode_job
    from src.services.job_enrichment import load_enrichment
    from src.services.vector_index import VectorIndex

    db = await pg.connect(str(DB_PATH))
    try:
        cur = await db.execute(
            """
            SELECT j.id, j.title, j.company, j.location, j.description,
                   j.apply_url, j.source, j.date_found
            FROM jobs j
            LEFT JOIN job_embeddings e ON e.job_id = j.id
            WHERE e.job_id IS NULL
            ORDER BY j.id
            """
        )
        rows = await cur.fetchall()
        if limit:
            rows = rows[:limit]
        total = len(rows)
        print(f"jobs missing embeddings: {total}")
        if not total:
            return 0

        vix = VectorIndex()
        done = failed = 0
        for r in rows:
            jid = r["id"]
            try:
                job = Job(
                    title=r["title"] or "",
                    company=r["company"] or "",
                    apply_url=r["apply_url"] or "",
                    source=r["source"] or "",
                    date_found=r["date_found"] or "",
                    location=r["location"] or "",
                    description=r["description"] or "",
                )
                job.id = jid
                enrichment = None
                try:
                    enrichment = await load_enrichment(db, jid)
                except Exception:  # noqa: BLE001 — enrichment absence is normal
                    enrichment = None
                vector = encode_job(job, enrichment)
                vix.upsert(str(jid), vector, {"job_id": jid, "source": job.source})
                await db.execute(
                    """
                    INSERT INTO job_embeddings(job_id, model_version, embedding_updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(job_id) DO UPDATE SET
                        model_version = excluded.model_version,
                        embedding_updated_at = excluded.embedding_updated_at
                    """,
                    (jid, MODEL_NAME),
                )
                done += 1
                if done % 250 == 0:
                    await db.commit()
                    print(f"  {done}/{total} embedded...")
            except Exception as exc:  # noqa: BLE001 — one bad job must not stop the sweep
                failed += 1
                if failed <= 5:
                    print(f"  job {jid} failed: {exc}")
        await db.commit()
        print(f"done: embedded={done} failed={failed}")
        return 0 if failed == 0 else 1
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap for a probe run")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.limit)))
