"""Production Postgres backup script (Phase 2 — ops hardening).

Dumps the database named by ``DATABASE_URL`` to a timestamped, gzipped file and
prunes old backups beyond a retention count. Designed to run as a scheduled job
(Railway cron, GitHub Action, or host crontab).

Plain words: it makes a compressed snapshot of the whole database so you can
restore it if something goes wrong, and it deletes the oldest snapshots so the
disk does not fill up.

Usage::

    DATABASE_URL=postgresql://user:pass@host:5432/db \\
    python scripts/backup_db.py --out-dir /var/backups/job360 --keep 14

Requires ``pg_dump`` on PATH (ships with the postgres client package). Restore
with::

    gunzip -c job360-2026-07-01T0200Z.sql.gz | psql "$DATABASE_URL"
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PREFIX = "job360-"
_SUFFIX = ".sql.gz"


def _timestamp() -> str:
    """UTC stamp safe for filenames, e.g. ``2026-07-01T0200Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def make_backup(database_url: str, out_dir: Path) -> Path:
    """Run ``pg_dump`` and gzip the result into ``out_dir``.

    Returns the path of the created ``.sql.gz`` file. Raises ``RuntimeError`` if
    ``pg_dump`` is missing or exits non-zero (so a scheduler marks the run failed).
    """
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump not found on PATH — install the postgres client package")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{_PREFIX}{_timestamp()}{_SUFFIX}"

    # Stream pg_dump stdout straight into the gzip file — never buffer the whole
    # dump in memory (databases outgrow RAM).
    proc = subprocess.Popen(
        ["pg_dump", "--no-owner", "--no-privileges", database_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with gzip.open(target, "wb") as gz:
        shutil.copyfileobj(proc.stdout, gz)
    _, err = proc.communicate()
    if proc.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed ({proc.returncode}): {err.decode(errors='replace').strip()}")
    return target


def prune(out_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` backups. Returns the deleted paths."""
    backups = sorted(
        (p for p in out_dir.glob(f"{_PREFIX}*{_SUFFIX}")),
        key=lambda p: p.name,
        reverse=True,
    )
    stale = backups[keep:]
    for p in stale:
        p.unlink(missing_ok=True)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup the Job360 Postgres database.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(os.environ.get("BACKUP_DIR", "backups")),
        help="Directory to write backups into (default: $BACKUP_DIR or ./backups).",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.environ.get("BACKUP_KEEP", "14")),
        help="How many recent backups to retain (default: 14).",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        created = make_backup(database_url, args.out_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    deleted = prune(args.out_dir, args.keep)
    size_mb = created.stat().st_size / (1024 * 1024)
    print(f"OK: wrote {created.name} ({size_mb:.2f} MB); pruned {len(deleted)} old backup(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
