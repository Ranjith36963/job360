"""Forward/reverse SQL migration runner (Batch 2).

Motivation: the legacy ``_migrate()`` in ``src/repositories/database.py`` diffs
``PRAGMA table_info`` and can only ADD columns. Batch 2 needs new tables,
foreign keys, unique-constraint rewrites, and data moves. This runner covers
that class of change; the legacy path stays for Batch 1's additive columns.

Layout convention
-----------------
Each migration is a pair of SQL files::

    NNNN_name.up.sql
    NNNN_name.down.sql

where ``NNNN`` is a zero-padded monotonically increasing integer. The runner
applies pending migrations in ascending order and reverses the most recently
applied migration on ``down()``.

Versions that have been applied are recorded in a ``_schema_migrations`` table
with ``(id, applied_at)`` columns. ``id`` is the ``NNNN_name`` stem.

Usage
-----
Library::

    import asyncio
    from migrations import runner
    asyncio.run(runner.up("data/jobs.db"))

CLI::

    python -m migrations.runner up
    python -m migrations.runner down
    python -m migrations.runner status
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parent


async def _ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await db.commit()


async def _applied_ids(db: aiosqlite.Connection) -> list[str]:
    cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
    return [row[0] for row in await cur.fetchall()]


def _discover_pairs(migrations_dir: Path) -> list[str]:
    """Return migration stems (``NNNN_name``) sorted lexically.

    A migration is only included if BOTH its .up.sql and .down.sql exist.
    """
    ups = sorted(migrations_dir.glob("*.up.sql"))
    stems = []
    for u in ups:
        stem = u.name[: -len(".up.sql")]
        down = migrations_dir / f"{stem}.down.sql"
        if down.exists():
            stems.append(stem)
    return stems


async def up(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
    target: Optional[str] = None,
) -> list[str]:
    """Apply all pending migrations up to (and including) ``target``.

    Returns the list of stems that were applied this call.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    applied_now: list[str] = []
    async with aiosqlite.connect(db_path) as db:
        await _ensure_table(db)
        done = set(await _applied_ids(db))
        for stem in _discover_pairs(mdir):
            if stem in done:
                continue
            sql = (mdir / f"{stem}.up.sql").read_text()
            # executescript does not support parameters and commits on entry.
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO _schema_migrations(id, applied_at) VALUES (?, ?)",
                (stem, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            applied_now.append(stem)
            if target is not None and stem == target:
                break
    return applied_now


async def down(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
) -> Optional[str]:
    """Reverse the most recently applied migration.

    Returns the stem that was reverted, or ``None`` if none was applied.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    async with aiosqlite.connect(db_path) as db:
        await _ensure_table(db)
        applied = await _applied_ids(db)
        if not applied:
            return None
        last = applied[-1]
        sql = (mdir / f"{last}.down.sql").read_text()
        await db.executescript(sql)
        await db.execute("DELETE FROM _schema_migrations WHERE id = ?", (last,))
        await db.commit()
        return last


async def status(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
) -> dict[str, list[str]]:
    mdir = migrations_dir or MIGRATIONS_DIR
    async with aiosqlite.connect(db_path) as db:
        await _ensure_table(db)
        applied = await _applied_ids(db)
    all_pairs = _discover_pairs(mdir)
    pending = [s for s in all_pairs if s not in set(applied)]
    return {"applied": applied, "pending": pending}


def _cli() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m migrations.runner [up|down|status] [db_path]", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    # Default DB path mirrors src.core.settings.DB_PATH
    db_path = sys.argv[2] if len(sys.argv) >= 3 else "data/jobs.db"
    if cmd == "up":
        result = asyncio.run(up(db_path))
        print("applied:", result or "<none>")
    elif cmd == "down":
        result = asyncio.run(down(db_path))
        print("reverted:", result or "<none>")
    elif cmd == "status":
        result = asyncio.run(status(db_path))
        print("applied:", result["applied"] or "<none>")
        print("pending:", result["pending"] or "<none>")
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
