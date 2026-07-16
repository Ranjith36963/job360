"""Standalone verifier for Step-1 B11 — migration runner race safety.

Reproduces the concurrent-boot scenario that used to crash the second
process with ``pgsync.IntegrityError: UNIQUE constraint failed:
_schema_migrations.id``. Two ``runner.up()`` calls race against the same
SQLite file; this script passes only if (a) neither raises, and (b) each
migration is recorded exactly once.

Used by ``make verify-step-1``. Exits 0 on success, 1 on failure — CI
friendly.

Run manually::

    cd backend && python scripts/verify_migration_race.py

Creates and removes its own temp DB + toy migrations dir; leaves nothing
behind on success.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


def _write_toy_migrations(root: Path) -> Path:
    mdir = root / "migrations"
    mdir.mkdir()
    # Note: raw CREATE TABLE (no IF NOT EXISTS) so an unguarded runner
    # would trip when the second process re-runs the same body.
    (mdir / "0001_create_alpha.up.sql").write_text("CREATE TABLE alpha (id INTEGER PRIMARY KEY, name TEXT);")
    (mdir / "0001_create_alpha.down.sql").write_text("DROP TABLE alpha;")
    (mdir / "0002_create_beta.up.sql").write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY, val INTEGER);")
    (mdir / "0002_create_beta.down.sql").write_text("DROP TABLE beta;")
    return mdir


async def _run() -> int:
    # Make `migrations` package importable when run from backend/ directly.
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from migrations import runner  # noqa: E402
    from src.repositories import pg

    tmp_root = Path(tempfile.mkdtemp(prefix="verify_migration_race_"))
    db_fd, db_path = tempfile.mkstemp(suffix=".db", dir=tmp_root)
    os.close(db_fd)
    mdir = _write_toy_migrations(tmp_root)

    try:
        results = await asyncio.gather(
            runner.up(db_path, migrations_dir=mdir),
            runner.up(db_path, migrations_dir=mdir),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            print(f"FAIL: concurrent up() raised: {failures!r}", file=sys.stderr)
            return 1

        async with pg.connect(db_path) as db:
            cur = await db.execute("SELECT id, COUNT(*) FROM _schema_migrations GROUP BY id")
            rows = await cur.fetchall()
        dup = [r for r in rows if r[1] != 1]
        if dup:
            print(f"FAIL: duplicate migration rows: {dup!r}", file=sys.stderr)
            return 1
        ids = {r[0] for r in rows}
        expected = {"0001_create_alpha", "0002_create_beta"}
        if ids != expected:
            print(
                f"FAIL: applied set mismatch: got {ids!r}, expected {expected!r}",
                file=sys.stderr,
            )
            return 1

        print("OK: concurrent up() is race-safe (2 migrations, 1 row each)")
        return 0
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
        for p in mdir.iterdir():
            try:
                p.unlink()
            except OSError:
                pass
        try:
            mdir.rmdir()
        except OSError:
            pass
        try:
            tmp_root.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
