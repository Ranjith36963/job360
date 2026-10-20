"""Migration safety (gap roadmap slice 20).

Migrations auto-apply on boot and every merge to main deploys, so the runner
bounds each migration with lock/statement timeouts, snapshots the tables a
pending migration changes, and CI refuses a new destructive migration unless it
says why (``-- allow-drop: <reason>``).
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import psycopg
import pytest

from migrations import runner
from src.core import settings
from src.repositories import pg

# Migrations up to this number predate the guard; it applies to every later one.
LAST_UNGUARDED = 66
_RE_DROP = re.compile(r"\bDROP\s+(?:COLUMN|TABLE)\b", re.IGNORECASE)
_RE_ALLOW = re.compile(r"^[ \t]*--[ \t]*allow-drop:[ \t]*\S", re.IGNORECASE | re.MULTILINE)


def unmarked_drops(files: list[Path]) -> list[str]:
    """Names of up.sql files newer than 0066 that DROP a column/table without an allow-drop line."""
    bad = []
    for f in files:
        number = int(f.name.split("_", 1)[0])
        if number <= LAST_UNGUARDED:
            continue
        raw = f.read_text()
        statements = "\n".join(pg.split_statements(raw))  # comments stripped
        if _RE_DROP.search(statements) and not _RE_ALLOW.search(raw):
            bad.append(f.name)
    return bad


def test_no_new_migration_drops_without_a_reason():
    files = sorted(runner.MIGRATIONS_DIR.glob("*.up.sql"))
    assert len(files) >= LAST_UNGUARDED
    bad = unmarked_drops(files)
    assert bad == [], (
        f"{bad}: DROP COLUMN / DROP TABLE in a migration newer than 0066 needs a line "
        "'-- allow-drop: <reason>' in the same file (data is lost on deploy)"
    )


def test_drop_guard_catches_an_unmarked_drop(tmp_path):
    (tmp_path / "0066_old.up.sql").write_text("DROP TABLE legacy;")
    (tmp_path / "0067_bad.up.sql").write_text("ALTER TABLE jobs drop column salary;")
    (tmp_path / "0068_ok.up.sql").write_text("-- allow-drop: column unused since slice 5\nDROP TABLE x;")
    (tmp_path / "0069_comment.up.sql").write_text("-- we do not DROP TABLE here\nCREATE TABLE y (id INT);")
    (tmp_path / "0070_bad2.up.sql").write_text("-- allow-drop:\nDROP TABLE IF EXISTS z;")
    assert unmarked_drops(sorted(tmp_path.glob("*.up.sql"))) == ["0067_bad.up.sql", "0070_bad2.up.sql"]


def test_touched_tables_parses_every_destructive_form():
    sql = """
    -- DELETE FROM in_a_comment;
    ALTER TABLE jobs ADD COLUMN x TEXT;
    alter table if exists only public."Users" drop column y;
    DROP TABLE IF EXISTS old_a, public.old_b CASCADE;
    UPDATE applications SET status = 'x' WHERE id IN (SELECT id FROM t FOR UPDATE);
    delete from sessions;
    TRUNCATE TABLE ONLY audit_log;
    TRUNCATE tokens;
    INSERT INTO kept (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET id = 1;
    CREATE TABLE fk (a INT REFERENCES jobs(id) ON UPDATE CASCADE ON DELETE SET NULL);
    CREATE TABLE created_only (id INT);
    """
    assert runner.touched_tables(sql) == [
        "jobs", "users", "old_a", "old_b", "applications", "sessions", "audit_log", "tokens",
    ]


def test_snapshot_name_fits_postgres_identifier_limit():
    assert runner._snapshot_name("jobs", "0067_x") == "jobs__0067_x"
    long = runner._snapshot_name("t" * 60, "0067_a_long_migration_name")
    assert long == "t" * 50 + "__0067" and len(long) <= 63


def test_snapshots_are_off_in_test_mode(monkeypatch):
    monkeypatch.setattr(settings, "MIGRATION_SNAPSHOTS_ENABLED", True)
    assert pg.TEST_MODE is True
    assert runner._snapshots_enabled() is False
    monkeypatch.setattr(pg, "TEST_MODE", False)
    assert runner._snapshots_enabled() is True
    monkeypatch.setattr(settings, "MIGRATION_SNAPSHOTS_ENABLED", False)
    assert runner._snapshots_enabled() is False


def _db_path(tmp_path: Path) -> str:
    return str(tmp_path / f"safety_{uuid.uuid4().hex[:8]}.db")


async def _scalar(db_path: str, sql: str, params: tuple = ()):
    async with pg.connect(db_path) as db:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_each_migration_runs_under_the_configured_timeouts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MIGRATION_LOCK_TIMEOUT_S", 7)
    monkeypatch.setattr(settings, "MIGRATION_STATEMENT_TIMEOUT_S", 33)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_probe.up.sql").write_text(
        "CREATE TABLE probe AS SELECT current_setting('lock_timeout') AS lt, "
        "current_setting('statement_timeout') AS st;"
    )
    (d / "0001_probe.down.sql").write_text("DROP TABLE probe;")
    db_path = _db_path(tmp_path)
    assert await runner.up(db_path, migrations_dir=d) == ["0001_probe"]
    assert await _scalar(db_path, "SELECT lt || '/' || st FROM probe") == "7s/33s"


@pytest.mark.asyncio
async def test_lock_timeout_fails_the_migration_loudly_and_changes_nothing(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "MIGRATION_LOCK_TIMEOUT_S", 1)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_base.up.sql").write_text("CREATE TABLE busy (id INT);")
    (d / "0001_base.down.sql").write_text("DROP TABLE busy;")
    db_path = _db_path(tmp_path)
    await runner.up(db_path, migrations_dir=d)
    (d / "0002_alter.up.sql").write_text("CREATE TABLE side (id INT);\nALTER TABLE busy ADD COLUMN note TEXT;")
    (d / "0002_alter.down.sql").write_text("DROP TABLE side;")

    caplog.set_level(logging.ERROR, logger="job360.migrations.runner")
    async with pg.connect(db_path) as holder:
        async with holder.transaction():
            await holder.execute("LOCK TABLE busy IN ACCESS EXCLUSIVE MODE")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                await runner.up(db_path, migrations_dir=d)

    failed = [r for r in caplog.records if getattr(r, "event", "") == "migration_failed"]
    assert [(r.migration, r.timed_out) for r in failed] == [("0002_alter", True)]
    assert await _scalar(db_path, "SELECT to_regclass('side') IS NULL") is True
    assert await _scalar(db_path, "SELECT count(*) FROM information_schema.columns "
                                  "WHERE table_schema = current_schema() AND table_name = 'busy'") == 1
    assert (await runner.status(db_path, migrations_dir=d))["pending"] == ["0002_alter"]


@pytest.mark.asyncio
async def test_statement_timeout_cancels_a_slow_migration(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "MIGRATION_STATEMENT_TIMEOUT_S", 1)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_slow.up.sql").write_text("CREATE TABLE slow_side (id INT);\nSELECT pg_sleep(5);")
    (d / "0001_slow.down.sql").write_text("DROP TABLE slow_side;")
    db_path = _db_path(tmp_path)
    caplog.set_level(logging.ERROR, logger="job360.migrations.runner")
    with pytest.raises(psycopg.errors.QueryCanceled):
        await runner.up(db_path, migrations_dir=d)
    failed = [r for r in caplog.records if getattr(r, "event", "") == "migration_failed"]
    assert [(r.migration, r.timed_out) for r in failed] == [("0001_slow", True)]
    assert await _scalar(db_path, "SELECT to_regclass('slow_side') IS NULL") is True


@pytest.mark.asyncio
async def test_pending_migration_snapshots_the_tables_it_touches(tmp_path, monkeypatch, caplog):
    snap_schema = f"msnap_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(runner, "SNAPSHOT_SCHEMA", snap_schema)
    monkeypatch.setattr(runner, "_snapshots_enabled", lambda: True)
    monkeypatch.setattr(settings, "MIGRATION_SNAPSHOT_MAX_ROWS", 2)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_base.up.sql").write_text(
        "CREATE TABLE small (id INT, name TEXT);\nINSERT INTO small VALUES (1, 'a'), (2, 'b');\n"
        "CREATE TABLE big (id INT);\nINSERT INTO big VALUES (1), (2), (3);"
    )
    (d / "0001_base.down.sql").write_text("DROP TABLE small; DROP TABLE big;")
    db_path = _db_path(tmp_path)
    await runner.up(db_path, migrations_dir=d)  # nothing touched yet: no snapshot schema
    assert await _scalar(db_path, f"SELECT to_regnamespace('{snap_schema}') IS NULL") is True

    (d / "0002_change.up.sql").write_text(
        "ALTER TABLE small ADD COLUMN extra TEXT;\nUPDATE small SET name = 'z';\n"
        "DELETE FROM big;\nDROP TABLE IF EXISTS ghost;"
    )
    (d / "0002_change.down.sql").write_text("ALTER TABLE small DROP COLUMN extra;")
    caplog.set_level(logging.INFO, logger="job360.migrations.runner")
    try:
        assert await runner.up(db_path, migrations_dir=d) == ["0002_change"]
        rows = await _scalar(
            db_path,
            f'SELECT string_agg(id || name, \',\' ORDER BY id) FROM "{snap_schema}"."small__0002_change"',  # noqa: S608
        )
        assert rows == "1a,2b"  # the pre-migration rows, not the UPDATEd ones
        cols = await _scalar(db_path, "SELECT count(*) FROM information_schema.columns "
                                      "WHERE table_schema = ? AND table_name = 'small__0002_change'", (snap_schema,))
        assert cols == 2  # the pre-migration shape (no `extra`)
        tables = await _scalar(db_path, "SELECT string_agg(table_name, ',') FROM information_schema.tables "
                                        "WHERE table_schema = ? AND table_name <> '_taken'", (snap_schema,))
        assert tables == "small__0002_change"  # big skipped (over the cap), ghost absent
        index = await _scalar(db_path, "SELECT string_agg(snapshot || '/' || migration || '/' || source_table "  # noqa: S608
                                       f"|| '/' || rows, ',') FROM \"{snap_schema}\"._taken")
        assert index == "small__0002_change/0002_change/small/2"
        events = [(r.event, r.table) for r in caplog.records if getattr(r, "event", "").startswith("migration_snap")]
        assert events == [("migration_snapshot", "small"), ("migration_snapshot_skipped", "big")]
    finally:
        async with pg.connect(db_path) as db:
            await db.execute(f'DROP SCHEMA IF EXISTS "{snap_schema}" CASCADE')


@pytest.mark.asyncio
async def test_no_snapshot_when_nothing_is_pending(tmp_path, monkeypatch):
    calls = []

    async def spy(db, pending):
        calls.append([stem for stem, _ in pending])
        return []

    monkeypatch.setattr(runner, "_snapshots_enabled", lambda: True)
    monkeypatch.setattr(runner, "_snapshot_pending_tables", spy)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_a.up.sql").write_text("CREATE TABLE a (id INT);")
    (d / "0001_a.down.sql").write_text("DROP TABLE a;")
    db_path = _db_path(tmp_path)
    await runner.up(db_path, migrations_dir=d)
    await runner.up(db_path, migrations_dir=d)
    assert calls == [["0001_a"]]


@pytest.mark.asyncio
async def test_old_snapshots_are_pruned_on_every_up(tmp_path, monkeypatch, caplog):
    """GDPR: snapshots hold whole tables outside the erasure path, so they expire."""
    snap_schema = f"msnap_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(runner, "SNAPSHOT_SCHEMA", snap_schema)
    monkeypatch.setattr(runner, "_snapshots_enabled", lambda: True)
    monkeypatch.setattr(settings, "MIGRATION_SNAPSHOT_RETENTION_DAYS", 14)
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_base.up.sql").write_text("CREATE TABLE people (id INT);\nINSERT INTO people VALUES (1);")
    (d / "0001_base.down.sql").write_text("DROP TABLE people;")
    (d / "0002_a.up.sql").write_text("UPDATE people SET id = 2;")
    (d / "0002_a.down.sql").write_text("SELECT 1;")
    (d / "0003_b.up.sql").write_text("UPDATE people SET id = 3;")
    (d / "0003_b.down.sql").write_text("SELECT 1;")
    db_path = _db_path(tmp_path)
    try:
        await runner.up(db_path, migrations_dir=d, target="0001_base")
        await runner.up(db_path, migrations_dir=d, target="0002_a")
        await runner.up(db_path, migrations_dir=d)
        async with pg.connect(db_path) as db:
            await db.execute(
                f'UPDATE "{snap_schema}"._taken SET taken_at = now() - interval \'15 days\' '  # noqa: S608
                "WHERE snapshot = 'people__0002_a'"
            )
            # A tampered index row: old, but not a name the prune may interpolate.
            await db.execute(
                f'INSERT INTO "{snap_schema}"._taken (snapshot, migration, taken_at) '  # noqa: S608
                "VALUES ('people\"; DROP TABLE people; --', '0009_x', now() - interval '30 days')"
            )
        caplog.set_level(logging.INFO, logger="job360.migrations.runner")
        assert await runner.up(db_path, migrations_dir=d) == []  # nothing pending: prune still runs

        tables = await _scalar(db_path, "SELECT string_agg(table_name, ',' ORDER BY table_name) "
                                        "FROM information_schema.tables "
                                        "WHERE table_schema = ? AND table_name <> '_taken'", (snap_schema,))
        assert tables == "people__0003_b"
        index = await _scalar(db_path, f'SELECT string_agg(migration, \',\' ORDER BY migration) '  # noqa: S608
                                       f'FROM "{snap_schema}"._taken')
        assert index == "0003_b,0009_x"  # pruned row gone; the refused row is left for a human
        assert await _scalar(db_path, "SELECT count(*) FROM people") == 1  # the bad name never ran
        events = [(r.event, r.migration) for r in caplog.records
                  if getattr(r, "event", "").startswith("migration_snapshot_prune")]
        assert sorted(events) == [("migration_snapshot_prune_refused", "0009_x"),
                                  ("migration_snapshot_pruned", "0002_a")]
    finally:
        async with pg.connect(db_path) as db:
            await db.execute(f'DROP SCHEMA IF EXISTS "{snap_schema}" CASCADE')
