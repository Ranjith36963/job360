"""Measure date_reliability_ratio. Run before + after Batch 1.

  $ cd backend
  $ python scripts/measure_date_reliability.py

Uses the production DB path by default (DB_PATH from src.core.settings).
Pass --db to point at a different SQLite file.
"""
import argparse
import asyncio

from src.repositories.database import JobDatabase
from src.core.settings import DB_PATH
from ops.exporter import compute_kpis


async def run(db_path: str) -> None:
    db = JobDatabase(db_path)
    await db.init_db()
    try:
        kpis = await compute_kpis(db)
    finally:
        await db.close()

    cursor = None
    # Per-confidence breakdown
    db2 = JobDatabase(db_path)
    await db2.init_db()
    try:
        cursor = await db2._conn.execute(
            "SELECT date_confidence, COUNT(*) FROM jobs GROUP BY date_confidence"
        )
        rows = dict(await cursor.fetchall())
    finally:
        await db2.close()

    total = sum(rows.values())
    print(f"Total jobs: {total}")
    if total:
        for conf, count in sorted(rows.items()):
            print(f"  {conf:20s} {count:>6d}  ({count/total:.1%})")
    print(f"\ndate_reliability_ratio = {kpis['date_reliability_ratio']:.1%}")
    print(f"stale_listing_rate     = {kpis['stale_listing_rate']:.1%}")
    print(f"bucket_accuracy_24h    = {kpis['bucket_accuracy_24h']:.1%}")
    print(f"bucket_accuracy_7d     = {kpis['bucket_accuracy_7d']:.1%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    asyncio.run(run(args.db))
