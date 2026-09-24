"""`pytest_sessionfinish`'s Postgres cleanup must be scoped to THIS session.

The bug (found 2026-09-24)
---------------------------
``conftest.py``'s ``pytest_sessionfinish`` used to ``SELECT schema_name FROM
information_schema.schemata WHERE schema_name LIKE 't\\_%' OR schema_name LIKE
'mem\\_%'`` and DROP every match. Several Claude sessions run the test suite
in parallel against the SAME shared dev Postgres, so that query also matched
schemas a concurrent session's tests were still using mid-run. Whichever
session finished first deleted the other session's tables out from under it —
surfacing there as `relation "users" does not exist`, `no schema has been
selected to create in`, or `_schema_migrations does not exist`.

The fix
-------
``src/repositories/pg.py`` now keeps a module-level ``CREATED_SCHEMAS`` set,
populated by the only two call sites that issue ``CREATE SCHEMA`` (``pg.
_open_raw`` and ``pgsync.connect``). ``conftest._drop_own_schemas()`` drops
only schemas in that set, never a blanket LIKE sweep.

This test proves the scoping two ways:
  1. a schema created OUTSIDE this process's registry (standing in for a
     concurrent session's schema) survives ``_drop_own_schemas()``.
  2. a schema this process actually created (registered via the normal
     ``pg.connect`` path) IS dropped by it.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from src.repositories import pg as _pg
from tests.conftest import _drop_own_schemas


def _schema_exists(cur, schema: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        (schema,),
    )
    return cur.fetchone() is not None


@pytest.mark.asyncio
async def test_sessionfinish_cleanup_never_drops_a_foreign_schema():
    """A schema NOT in this process's registry (another session's) survives."""
    foreign_schema = "t_other_session_probe_" + uuid.uuid4().hex[:8]
    conn = psycopg.connect(_pg.DEFAULT_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{foreign_schema}"')

        # Deliberately NOT registered via pg.register_created_schema /
        # CREATED_SCHEMAS — this stands in for a schema a CONCURRENT Claude
        # session created, that this process never touched.
        assert foreign_schema not in _pg.CREATED_SCHEMAS

        _drop_own_schemas()

        with conn.cursor() as cur:
            assert _schema_exists(cur, foreign_schema), (
                "_drop_own_schemas() dropped a schema it never created — "
                "this is the exact bug that deletes a concurrent session's "
                "tables mid-run."
            )
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{foreign_schema}" CASCADE')
        conn.close()


@pytest.mark.asyncio
async def test_sessionfinish_cleanup_drops_schemas_it_created():
    """A schema THIS process created (via the normal pg.connect path) IS dropped."""
    db_path = "/tmp/schema_cleanup_probe_" + uuid.uuid4().hex[:8] + ".db"
    schema = _pg.schema_for_path(db_path)

    async with _pg.connect(db_path) as conn:
        await conn.execute("SELECT 1")

    assert schema in _pg.CREATED_SCHEMAS

    conn = psycopg.connect(_pg.DEFAULT_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            assert _schema_exists(cur, schema), "setup: schema should exist before cleanup"

        _drop_own_schemas()

        with conn.cursor() as cur:
            assert not _schema_exists(cur, schema), (
                "_drop_own_schemas() left behind a schema this process created"
            )
    finally:
        # Idempotent no-op if the assertion above already succeeded.
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.close()
        _pg.CREATED_SCHEMAS.discard(schema)
