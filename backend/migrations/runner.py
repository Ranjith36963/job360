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

from src.repositories import pg

MIGRATIONS_DIR = Path(__file__).resolve().parent


async def _ensure_table(db: pg.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await db.commit()


async def _resync_identity_sequences(db: pg.Connection) -> int:
    """Re-point every ``id`` identity sequence at MAX(id). Returns tables fixed.

    Why (docs/fable/02 D3): the table-rebuild migrations (0002, 0010, 0011 …) copy
    rows with their ORIGINAL ids — ``INSERT INTO x_new (id, …) SELECT id, …``.
    Postgres does NOT advance an identity/serial sequence when you supply the id
    explicitly, so after a rebuild the sequence still sits at 1 while the table
    holds ids up to N. The next natural INSERT then tries id=1 and dies with a
    UniqueViolation. SQLite's AUTOINCREMENT had no such split, so the migrations
    never needed this — it only appeared once we moved to Postgres.

    Running this after every ``up()`` is cheap and idempotent: setval to MAX(id)
    is a no-op when the sequence is already correct. `is_called` is set false for
    an empty table so the first insert still gets id=1.
    """
    # Only tables that actually own an auto-generated `id`. Note: we must NOT call
    # pg_get_serial_sequence() inside this query's WHERE — it RAISES (not returns
    # NULL) for a table with no `id` column, and the planner happily evaluates it
    # against catalog relations like pg_statistic. Filter first, resolve after.
    # current_schema(), NOT a hardcoded 'public': the test harness gives each test
    # its own schema (t_*/mem_*) via search_path, and prod runs in public. Keying on
    # the active schema makes this correct in both instead of silently no-op'ing.
    cur = await db.execute(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND column_name = 'id'
          AND (is_identity = 'YES' OR column_default LIKE 'nextval%')
        """
    )
    tables = [r[0] for r in await cur.fetchall()]
    fixed = 0
    for table in tables:
        seq_cur = await db.execute("SELECT pg_get_serial_sequence(?, 'id')", (table,))
        seq_row = await seq_cur.fetchone()
        seq = seq_row[0] if seq_row else None
        if not seq:
            continue
        await db.execute(
            f"SELECT setval('{seq}', "  # noqa: S608 — names come from pg_class, not user input
            f"GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 0), 1), "
            f"(SELECT COALESCE(MAX(id), 0) FROM {table}) > 0)"
        )
        fixed += 1
    return fixed


async def _applied_ids(db: pg.Connection) -> list[str]:
    cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
    return [row[0] for row in await cur.fetchall()]


async def _applied_map(db: pg.Connection) -> dict[str, str]:
    """Return ``{stem: applied_at}`` for every recorded migration.

    Used by the enhanced ``status`` CLI printer (Step-0 Tier B) to show the
    timestamp column. Keeps the legacy ``_applied_ids`` helper in place so
    existing callers (``up`` / ``down`` / the library-facing ``status`` dict)
    don't change shape.
    """
    cur = await db.execute("SELECT id, applied_at FROM _schema_migrations ORDER BY id")
    return {row[0]: row[1] for row in await cur.fetchall()}


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


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into statements on ``;`` boundaries.

    Delegates to ``pg.split_statements`` (single source of truth) which strips
    ``--`` comments, then splits on ``;`` that is NOT inside a single-quoted
    string or a ``$$``/``$tag$`` dollar-quoted body. SPLIT-P3: this used to be a
    plain ``cleaned.split(";")`` here and in pg.py — safe for today's migrations
    but a future one with a Postgres function body / DO block (``$$ ... ; ... $$``)
    would have been severed mid-statement at boot.
    """
    return pg.split_statements(sql)


async def _apply_up_sql(db: pg.Connection, sql: str) -> None:
    """Run the up SQL statement-by-statement, tolerating a pre-existing
    forward state for idempotent ``ADD COLUMN`` statements.

    Motivation: the legacy ``_migrate()`` in ``src/repositories/database.py``
    backfills jobs + run_log columns at every ``init_db()`` boot. When a
    test or tool calls ``init_db`` and then ``runner.up`` on the same DB,
    the raw ``ALTER TABLE ... ADD COLUMN`` in a migration SQL file would
    fail with ``duplicate column name``. We swallow that specific error
    so the runner records the stem as applied and moves on — the column
    is already in place, which is the migration's net effect anyway.

    Any OTHER error (syntax, missing table, constraint conflict) is still
    re-raised so real migration bugs surface loudly.
    """
    for stmt in _split_sql_statements(sql):
        try:
            await db.execute(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            is_add_column = "alter table" in stmt.lower() and "add column" in stmt.lower()
            # ADD COLUMN is translated to ADD COLUMN IF NOT EXISTS, so a
            # duplicate no longer errors; the guard stays as belt-and-braces
            # for both the SQLite ("duplicate column name") and Postgres
            # ("already exists") wordings.
            if is_add_column and ("duplicate column name" in msg or "already exists" in msg):
                continue
            raise


async def up(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
    target: Optional[str] = None,
) -> list[str]:
    """Apply all pending migrations up to (and including) ``target``.

    Returns the list of stems that were applied this call.

    Concurrent-boot safety (Step-1 B11)
    -----------------------------------
    FastAPI (``src/api/dependencies.py`` lifespan) and the ARQ worker
    (``src/workers/settings.py``) both call this on startup. If two processes
    race against the same database, the naive path had them both read an
    identical "applied" set, both run the same migration body, and both
    INSERT into ``_schema_migrations`` — the second INSERT tripped the
    ``UNIQUE(id)`` constraint and the process crashed.

    Fix: a session-level ``pg_advisory_lock`` (below) puts only one booter in
    the critical section; the applied set is re-read *inside* the lock so a
    loser sees the winner's stems already applied and skips. The final INSERT
    carries ``ON CONFLICT DO NOTHING`` as belt-and-braces.

    Atomicity (finding H2): each migration body runs inside ONE explicit
    ``db.transaction()`` together with its ``_schema_migrations`` INSERT, so a
    body that fails mid-way rolls back entirely and its stem is NOT recorded —
    a half-applied migration can never be marked "done".
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    applied_now: list[str] = []
    # Advisory-lock key: a fixed constant so all concurrent booters (FastAPI
    # lifespan + ARQ worker) serialize on the same lock. Postgres has no
    # "database is locked" — this just prevents two processes running the same
    # migration body at once. Each migration now runs in its OWN BEGIN/COMMIT
    # (transactional migrations, docs/fable/02) so it applies atomically; the
    # session-level advisory lock is held across those transactions. Idempotency
    # comes from ON CONFLICT DO NOTHING on the _schema_migrations INSERT plus the
    # re-read under the lock.
    lock_key = 0x30B360C0DE  # arbitrary stable 64-bit-ish int
    async with pg.connect(db_path) as db:
        # Acquire the lock BEFORE creating the migrations table so concurrent
        # booters don't race on CREATE TABLE (also non-atomic in Postgres).
        await db.execute("SELECT pg_advisory_lock(?)", (lock_key,))
        try:
            await _ensure_table(db)
            applied_set = set(await _applied_ids(db))
            for stem in _discover_pairs(mdir):
                if stem in applied_set:
                    if target is not None and stem == target:
                        break
                    continue
                sql = (mdir / f"{stem}.up.sql").read_text()
                # One explicit transaction per migration body (finding H2): the
                # body AND its _schema_migrations bookkeeping INSERT commit
                # atomically. On ANY error the whole body rolls back and the stem
                # is NOT recorded — no more half-applied migrations marked "done".
                # Relies on the pg-shim lastval() guard (pg.py) so an ON CONFLICT
                # INSERT can't abort this transaction via a failed lastval probe.
                # A DuplicateTable is no longer swallowed-and-recorded: the
                # advisory lock above already serialises concurrent booters, so a
                # genuine duplicate now surfaces loudly instead of masking a
                # partially-applied state.
                async with db.transaction():
                    await _apply_up_sql(db, sql)
                    await db.execute(
                        "INSERT INTO _schema_migrations(id, applied_at) "
                        "VALUES (?, ?) ON CONFLICT DO NOTHING",
                        (stem, datetime.now(timezone.utc).isoformat()),
                    )
                applied_set.add(stem)
                applied_now.append(stem)
                if target is not None and stem == target:
                    break
            # Rebuild migrations copy explicit ids, which leaves identity sequences
            # behind MAX(id) → the next insert would collide (docs/fable/02 D3).
            #
            # ONLY when we actually applied something. A previous revision ran this
            # unconditionally so an already-broken DB would "self-heal" on any boot —
            # well-meant, but I never measured it: the sweep costs ~2.8s (18 tables ×
            # pg_get_serial_sequence + a setval whose MAX(id) subquery scans the
            # table), and `up()` is called by nearly every test, almost always with
            # nothing pending. That turned a ~14min suite into ~5h.
            #
            # Correctness is unaffected: a sequence can only fall behind BECAUSE a
            # migration copied explicit ids, so resyncing exactly when a migration
            # was applied covers every case that creates the problem. Healing a DB
            # broken by some earlier boot is a one-off repair, not something worth
            # taxing every test run for.
            if applied_now:
                await _resync_identity_sequences(db)
        finally:
            await db.execute("SELECT pg_advisory_unlock(?)", (lock_key,))
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
    async with pg.connect(db_path) as db:
        await _ensure_table(db)
        applied = await _applied_ids(db)
        if not applied:
            return None
        last = applied[-1]
        sql = (mdir / f"{last}.down.sql").read_text()
        # Atomic, for the same reason up() is (finding H2) — the fix was applied
        # there and never mirrored here.
        #
        # The connection is autocommit and executescript() runs each statement on
        # its own committed cursor. A down-migration like 0011 rebuilds `jobs`:
        # DROP INDEX x5 -> CREATE jobs_old -> INSERT SELECT -> DROP TABLE ->
        # RENAME -> CREATE INDEX x5. If any statement after the first fails, the
        # already-dropped indexes stay dropped, the exception propagates, and the
        # `DELETE FROM _schema_migrations` below never runs — so the migration is
        # still recorded as APPLIED while the schema is silently missing its
        # indexes. up() will never re-run it to repair, precisely because it is
        # marked applied. Nothing errors afterwards; queries just get slower
        # forever.
        #
        # One transaction: the schema change and the bookkeeping row commit
        # together or not at all.
        async with db.transaction():
            await db.executescript(sql)
            await db.execute("DELETE FROM _schema_migrations WHERE id = ?", (last,))
        return last


async def status(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
) -> dict[str, list[str]]:
    mdir = migrations_dir or MIGRATIONS_DIR
    async with pg.connect(db_path) as db:
        await _ensure_table(db)
        applied = await _applied_ids(db)
    all_pairs = _discover_pairs(mdir)
    pending = [s for s in all_pairs if s not in set(applied)]
    return {"applied": applied, "pending": pending}


async def _status_rows(
    db_path: str,
    *,
    migrations_dir: Optional[Path] = None,
) -> tuple[list[tuple[str, str, str]], bool]:
    """Return ``(rows, had_table)`` where rows = ``[(stem, state, applied_at)]``.

    ``had_table`` is False when ``_schema_migrations`` did not exist prior to
    this call (the CLI printer surfaces a hint to run ``up`` first). The call
    itself still creates the table (via the shared ``_ensure_table`` helper)
    so re-invoking ``status`` is idempotent and matches the library contract.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    async with pg.connect(db_path) as db:
        # Detect pre-existing schema-migrations table before _ensure_table
        # would create it, so the CLI printer can surface "run up first".
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'")
        had_table = (await cur.fetchone()) is not None
        await _ensure_table(db)
        applied = await _applied_map(db)
    all_pairs = _discover_pairs(mdir)
    rows: list[tuple[str, str, str]] = []
    for stem in all_pairs:
        if stem in applied:
            rows.append((stem, "applied", applied[stem] or ""))
        else:
            rows.append((stem, "pending", ""))
    # Include applied stems whose up.sql / down.sql files are gone (rare —
    # usually means a local branch removed a migration that a prior branch
    # applied). Surface them so operators notice.
    for stem, ts in applied.items():
        if stem not in {r[0] for r in rows}:
            rows.append((stem, "applied (orphan)", ts or ""))
    return rows, had_table


def _format_status_table(rows: list[tuple[str, str, str]]) -> str:
    """Render the ``status`` rows as a plain-text aligned table (stdlib only).

    Backwards-compat: the last line is still ``applied: N / pending: M`` so
    existing CI greps keep working.
    """
    headers = ("Stem", "State", "Applied at")
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    lines: list[str] = []
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines.append(fmt.format(*headers))
    lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append(fmt.format(*r))
    applied_n = sum(1 for r in rows if r[1].startswith("applied"))
    pending_n = sum(1 for r in rows if r[1] == "pending")
    lines.append(f"applied: {applied_n} / pending: {pending_n}")
    return "\n".join(lines)


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
        reverted = asyncio.run(down(db_path))
        print("reverted:", reverted or "<none>")
    elif cmd == "status":
        rows, had_table = asyncio.run(_status_rows(db_path))
        if not had_table:
            print("no migrations table — run `up` first")
        if not rows:
            # No migrations discovered on disk AND none applied — still emit
            # the legacy summary line so CI greps don't break.
            print("applied: 0 / pending: 0")
        else:
            print(_format_status_table(rows))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
