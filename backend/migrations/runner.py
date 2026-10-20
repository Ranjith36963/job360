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

Before pending migrations run on a real database, the tables they change are
copied into the ``migration_snapshots`` schema; snapshots are kept 14 days
(``MIGRATION_SNAPSHOT_RETENTION_DAYS``), the same idea as the 30-day backups
(docs/product/RUNBOOK-backups.md), and pruned by every ``up()``.

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
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg

from src.core import settings
from src.repositories import pg
from src.utils.logger import get_logger

MIGRATIONS_DIR = Path(__file__).resolve().parent
logger = get_logger("migrations.runner")

# Pre-migration snapshots (gap roadmap slice 20) land here, never on a search_path.
SNAPSHOT_SCHEMA = "migration_snapshots"

# Statements that change or remove an existing table's rows or shape. Matched
# anywhere (not only at a statement start) so CTEs and DO blocks count too; a
# keyword that follows instead (ON UPDATE CASCADE, DO UPDATE SET, FOR UPDATE OF)
# is dropped by _NOT_TABLE, and a name that is no table is dropped by the
# existence check. Over-capturing costs one extra copy; missing a table costs data.
_IDENT = r'(?:"[^"]+"|\w+)'
_QUALIFIED = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"
_RE_TOUCHED = re.compile(
    r"\b(?:ALTER\s+TABLE|DROP\s+TABLE|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
    r"(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    rf"({_QUALIFIED}(?:\s*,\s*{_QUALIFIED})*)",
    re.IGNORECASE,
)
_NOT_TABLE = {"set", "of", "on", "cascade", "restrict", "no", "skip", "nowait", "only", "if", "table"}
# The only snapshot names the prune step will interpolate into DROP TABLE.
_RE_SNAPSHOT_NAME = re.compile(r"^[a-z0-9_]{1,63}$")


def touched_tables(sql: str) -> list[str]:
    """Tables an up.sql alters, drops, updates, deletes from or truncates (lower-cased, in order).

    Comments are stripped first. A schema prefix is dropped (the runner snapshots
    from the connection's own schema); quotes are removed.
    """
    body = "\n".join(_split_sql_statements(sql))
    found: list[str] = []
    for m in _RE_TOUCHED.finditer(body):
        for ref in m.group(1).split(","):
            name = ref.strip().split(".")[-1].strip().strip('"').lower()
            if name and name not in _NOT_TABLE and name not in found:
                found.append(name)
    return found


def _snapshot_name(table: str, stem: str) -> str:
    """``<table>__<migration_id>``, shortened to the migration number past Postgres' 63-byte limit."""
    name = f"{table}__{stem}"
    if len(name) <= 63:
        return name
    return f"{table[:50]}__{stem.split('_', 1)[0]}"


def _snapshots_enabled() -> bool:
    """On for a real database unless MIGRATION_SNAPSHOTS_ENABLED=false; never in test mode."""
    return settings.MIGRATION_SNAPSHOTS_ENABLED and not pg.TEST_MODE


async def _set_local_timeouts(db: pg.Connection) -> None:
    """Bound one migration transaction: a lock wait or a statement past its limit aborts it.

    SET LOCAL lasts until the enclosing transaction ends, so nothing leaks onto the
    connection after COMMIT/ROLLBACK. Values are ints from settings (0 = no limit).
    """
    await db.execute(f"SET LOCAL lock_timeout = '{int(settings.MIGRATION_LOCK_TIMEOUT_S)}s'")
    await db.execute(f"SET LOCAL statement_timeout = '{int(settings.MIGRATION_STATEMENT_TIMEOUT_S)}s'")


async def _snapshot_pending_tables(db: pg.Connection, pending: list[tuple[str, str]]) -> list[str]:
    """Copy every existing table each pending migration touches into ``SNAPSHOT_SCHEMA``.

    ``pending`` is ``[(stem, up_sql)]``. Returns the snapshot names made. A table
    over ``MIGRATION_SNAPSHOT_MAX_ROWS`` is skipped (logged); an existing snapshot is
    kept (it is the older pre-migration copy). Any error propagates, so no migration
    runs without the snapshots it was promised.
    """
    made: list[str] = []
    cap = int(settings.MIGRATION_SNAPSHOT_MAX_ROWS)
    index_ready = False
    for stem, sql in pending:
        for table in touched_tables(sql):
            cur = await db.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (table,),
            )
            if await cur.fetchone() is None:
                continue
            snap = _snapshot_name(table, stem)
            async with db.transaction():
                await _set_local_timeouts(db)
                # `table` is a name information_schema just confirmed, not user input.
                count_sql = f'SELECT count(*) FROM (SELECT 1 FROM "{table}" LIMIT ?) s'  # noqa: S608
                cur = await db.execute(count_sql, (cap + 1,))
                row = await cur.fetchone()
                rows = int(row[0]) if row else 0
                if rows > cap:
                    logger.warning(
                        "migration_snapshot_skipped",
                        extra={"event": "migration_snapshot_skipped", "migration": stem, "table": table,
                               "reason": f"more than {cap} rows (MIGRATION_SNAPSHOT_MAX_ROWS)"},
                    )
                    continue
                if not index_ready:
                    await _ensure_snapshot_index(db)
                    index_ready = True
                await db.execute(
                    f'CREATE TABLE IF NOT EXISTS "{SNAPSHOT_SCHEMA}"."{snap}" AS TABLE "{table}"'
                )
                # Same transaction as the copy: a snapshot never exists without its
                # index row, so the prune step below can always find it.
                await db.execute(
                    f'INSERT INTO "{SNAPSHOT_SCHEMA}"._taken (snapshot, migration, source_table, rows) '  # noqa: S608
                    "VALUES (?, ?, ?, ?) ON CONFLICT (snapshot) DO NOTHING",
                    (snap, stem, table, rows),
                )
            made.append(snap)
            logger.info(
                "migration_snapshot",
                extra={"event": "migration_snapshot", "migration": stem, "table": table,
                       "snapshot": f"{SNAPSHOT_SCHEMA}.{snap}", "rows": rows},
            )
    return made


async def _ensure_snapshot_index(db: pg.Connection) -> None:
    """The snapshot schema and its ``_taken`` index (one row per snapshot table)."""
    await db.execute(f'CREATE SCHEMA IF NOT EXISTS "{SNAPSHOT_SCHEMA}"')
    await db.execute(
        f'CREATE TABLE IF NOT EXISTS "{SNAPSHOT_SCHEMA}"._taken ('
        "snapshot TEXT PRIMARY KEY, migration TEXT, source_table TEXT, rows INTEGER, "
        "taken_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


async def _prune_snapshots(db: pg.Connection) -> list[str]:
    """Drop every snapshot older than ``MIGRATION_SNAPSHOT_RETENTION_DAYS`` and its index row.

    Snapshots copy whole tables (users, applications, receipts) outside the
    erasure path, so a deleted account must not live on in them. Names come only
    from ``_taken`` and are interpolated only after matching ``_RE_SNAPSHOT_NAME``;
    a row that fails it is logged and left alone. Returns the names dropped.
    """
    cur = await db.execute("SELECT to_regclass(?)", (f'"{SNAPSHOT_SCHEMA}"._taken',))
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return []
    cur = await db.execute(
        f'SELECT snapshot, migration FROM "{SNAPSHOT_SCHEMA}"._taken '  # noqa: S608 — module constant
        "WHERE taken_at < now() - make_interval(days => ?) ORDER BY snapshot",
        (int(settings.MIGRATION_SNAPSHOT_RETENTION_DAYS),),
    )
    dropped: list[str] = []
    for snap, migration in await cur.fetchall():
        if not _RE_SNAPSHOT_NAME.match(snap or ""):
            logger.warning(
                "migration_snapshot_prune_refused",
                extra={"event": "migration_snapshot_prune_refused", "migration": migration,
                       "reason": "snapshot name fails the strict name check"},
            )
            continue
        async with db.transaction():
            await _set_local_timeouts(db)
            await db.execute(f'DROP TABLE IF EXISTS "{SNAPSHOT_SCHEMA}"."{snap}"')
            await db.execute(f'DELETE FROM "{SNAPSHOT_SCHEMA}"._taken WHERE snapshot = ?', (snap,))  # noqa: S608
        dropped.append(snap)
        logger.info(
            "migration_snapshot_pruned",
            extra={"event": "migration_snapshot_pruned", "migration": migration,
                   "snapshot": f"{SNAPSHOT_SCHEMA}.{snap}",
                   "retention_days": int(settings.MIGRATION_SNAPSHOT_RETENTION_DAYS)},
        )
    return dropped


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


# Advisory-lock key for ``up()``: a fixed constant so every concurrent booter of
# the production schema (``public``) serializes on the same lock.
MIGRATION_LOCK_KEY = 0x30B360C0DE  # arbitrary stable 64-bit-ish int


async def _migration_lock_key(db: pg.Connection) -> int:
    """The advisory-lock key that serializes booters of THIS connection's schema.

    Production always runs in ``public`` and always gets ``MIGRATION_LOCK_KEY``,
    exactly as before. Test mode gives every test its own schema (``t_*`` /
    ``mem_*``, ``pg.schema_for_path``); an advisory lock is database-wide, so
    one shared key made every pytest-xdist worker queue behind every other
    worker's full migration run although they never touch the same schema
    (measured 2026-10-19: three of four workers parked on ``pg_advisory_lock``).
    Two booters of the SAME schema still get the same key, so the race the lock
    exists to close is still closed.
    """
    cur = await db.execute("SELECT current_schema()")
    row = await cur.fetchone()
    schema = row[0] if row else "public"
    if schema == "public":
        return MIGRATION_LOCK_KEY
    digest = hashlib.md5(f"migrations:{schema}".encode(), usedforsecurity=False).digest()
    return int.from_bytes(digest[:7], "big")  # 56 bits: positive, fits a bigint


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


def _discover_pairs(migrations_dir: Optional[Path] = None) -> list[tuple[str, Path]]:
    """Return ``(stem, up_file)`` for every migration, sorted lexically.

    A migration is only included if BOTH its .up.sql and .down.sql exist — a
    forward file with no reverse is not a migration this runner will apply.

    ``migrations_dir`` defaults to this package's own directory so a caller
    that just wants "what migrations exist?" (tests, the doc-sync guard) does
    not have to know where they live.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    pairs: list[tuple[str, Path]] = []
    for up_file in sorted(mdir.glob("*.up.sql")):
        stem = up_file.name[: -len(".up.sql")]
        if (mdir / f"{stem}.down.sql").exists():
            pairs.append((stem, up_file))
    return pairs


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
    FastAPI (``src/api/dependencies.py`` lifespan) calls this on startup, and
    so does any operator running the CLI. If two processes race against the
    same database, the naive path had them both read an
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
    # Advisory-lock key (``_migration_lock_key``): one per schema, so all
    # concurrent booters of a schema serialize on the same lock. Postgres has no
    # "database is locked" — this just prevents two processes running the same
    # migration body at once. Each migration now runs in its OWN BEGIN/COMMIT
    # (transactional migrations, docs/fable/02) so it applies atomically; the
    # session-level advisory lock is held across those transactions. Idempotency
    # comes from ON CONFLICT DO NOTHING on the _schema_migrations INSERT plus the
    # re-read under the lock.
    async with pg.connect(db_path) as db:
        lock_key = await _migration_lock_key(db)
        # Acquire the lock BEFORE creating the migrations table so concurrent
        # booters don't race on CREATE TABLE (also non-atomic in Postgres).
        await db.execute("SELECT pg_advisory_lock(?)", (lock_key,))
        try:
            await _ensure_table(db)
            applied_set = set(await _applied_ids(db))
            # Retention runs on every boot, pending or not (GDPR: see _prune_snapshots).
            if _snapshots_enabled():
                await _prune_snapshots(db)
            pending: list[tuple[str, str]] = []
            for stem, _up_file in _discover_pairs(mdir):
                if stem in applied_set:
                    if target is not None and stem == target:
                        break
                    continue
                pending.append((stem, (mdir / f"{stem}.up.sql").read_text()))
                if target is not None and stem == target:
                    break
            # Slice 20: copy what the pending migrations will change BEFORE any runs.
            if pending and _snapshots_enabled():
                await _snapshot_pending_tables(db, pending)
            for stem, sql in pending:
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
                #
                # Slice 20: SET LOCAL lock/statement timeouts bound the transaction, so
                # a migration queued behind a live lock, or one that runs too long, is
                # cancelled and rolled back instead of stalling the booting deploy.
                try:
                    async with db.transaction():
                        await _set_local_timeouts(db)
                        await _apply_up_sql(db, sql)
                        await db.execute(
                            "INSERT INTO _schema_migrations(id, applied_at) "
                            "VALUES (?, ?) ON CONFLICT DO NOTHING",
                            (stem, datetime.now(timezone.utc).isoformat()),
                        )
                except Exception as exc:
                    timed_out = isinstance(exc, (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled))
                    logger.error(
                        "migration_failed",
                        extra={"event": "migration_failed", "migration": stem, "timed_out": timed_out,
                               "error_type": type(exc).__name__, "error": str(exc)[:500]},
                    )
                    raise
                applied_set.add(stem)
                applied_now.append(stem)
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
            await _set_local_timeouts(db)  # same bounds as up() (slice 20)
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
    all_pairs = [stem for stem, _ in _discover_pairs(mdir)]
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
    all_pairs = [stem for stem, _ in _discover_pairs(mdir)]
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
