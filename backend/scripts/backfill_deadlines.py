"""Backfill application deadlines from job descriptions.

Iterates all jobs in the catalog where deadline IS NULL and description IS NOT
NULL, runs the conservative extractor, and writes matches back.  Safe to run
multiple times (idempotent — only touches rows where deadline IS NULL).

Usage (from the repo root or from backend/):
    python backend/scripts/backfill_deadlines.py
    python backend/scripts/backfill_deadlines.py --db-path /path/to/jobs.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Ensure 'backend/' is on sys.path so 'src.*' imports resolve correctly
# regardless of where the script is invoked from.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
os.chdir(_BACKEND_DIR)

from src.services.deadline import extract_deadline  # noqa: E402 — path set above


def _default_db_path() -> str:
    return str(_BACKEND_DIR / "data" / "jobs.db")


def backfill(db_path: str) -> tuple[int, int]:
    """Return (scanned, updated)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, description FROM jobs "
            "WHERE deadline IS NULL AND description IS NOT NULL AND description != ''"
        )
        rows = cur.fetchall()
        scanned = len(rows)
        updated = 0
        for row in rows:
            result = extract_deadline(row["description"])
            if result is not None:
                deadline_iso, source = result
                conn.execute(
                    "UPDATE jobs SET deadline = ?, deadline_source = ? WHERE id = ?",
                    (deadline_iso, source, row["id"]),
                )
                updated += 1
        conn.commit()
    finally:
        conn.close()
    return scanned, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill deadlines into jobs.db")
    parser.add_argument(
        "--db-path",
        default=_default_db_path(),
        help="Path to jobs.db (default: backend/data/jobs.db)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"DB not found: {args.db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {args.db_path} ...")
    scanned, updated = backfill(args.db_path)
    print(f"Done — scanned {scanned}, set {updated} deadlines.")


if __name__ == "__main__":
    main()
