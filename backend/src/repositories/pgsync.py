"""Synchronous Postgres helper (psycopg3) — the blocking counterpart of :mod:`pg`.

Used by the CLI, profile storage, and tests that need a plain blocking
connection. ``connect(path)`` maps the legacy db-path argument to the shared
Postgres DSN, with the same schema-per-path isolation the async :mod:`pg`
helper provides in TEST_MODE.

Surface: ``connect``, ``Connection.{execute,executescript,executemany,commit,
close,cursor}`` + context manager, ``Cursor.{fetchone,fetchall,rowcount,
lastrowid}``, ``Row``, ``OperationalError`` / ``IntegrityError`` / ``Error``.
SQL passes through the same SQLite→Postgres ``translate()`` as :mod:`pg`
(legacy dialect in query strings — removing that is the dialect-rewrite batch).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

import psycopg
from psycopg import pq

from src.repositories import pg

Error = psycopg.Error
OperationalError = pg.OperationalError
IntegrityError = psycopg.IntegrityError
Row = pg.Row


class _Cursor:
    def __init__(self, cur, lastrowid: Optional[int]):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        self._cur.close()


class Connection:
    def __init__(self, raw: psycopg.Connection):
        self._raw = raw
        self.row_factory = pg.Row

    def _read_lastval(self):
        c2 = self._raw.cursor()
        try:
            c2.execute("SELECT lastval()")
            row = c2.fetchone()
            return row[0] if row else None
        finally:
            c2.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _Cursor:
        translated = pg.translate(sql)
        cur = self._raw.cursor()
        try:
            cur.execute(translated, tuple(params) if params else None)
        except psycopg.errors.UndefinedColumn:
            # A mis-named COLUMN is a bug, not a graceful degrade — propagate it
            # (finding H3). ``UndefinedColumn`` is excluded from
            # ``pg._MISSING_OBJECT_ERRORS`` so it is no longer remapped.
            raise
        except pg._MISSING_OBJECT_ERRORS as exc:
            raise pg.OperationalError(str(exc)) from exc
        # Emulate lastrowid via lastval(), but only when a row was actually
        # inserted, and SAVEPOINT-guarded inside a transaction (findings V2/H2).
        lastrowid = None
        # Mirrors the async shim (pg.Connection.execute) — see pg.py for the full
        # rationale on both traps: the ``cur.rowcount`` guard avoids a STALE id
        # after ON CONFLICT DO NOTHING, and the SAVEPOINT keeps a failing probe
        # from poisoning an enclosing transaction.
        if (
            translated.upper().lstrip().startswith("INSERT")
            and "returning" not in translated.lower()
            and cur.rowcount
        ):
            try:
                if self._raw.info.transaction_status == pq.TransactionStatus.IDLE:
                    lastrowid = self._read_lastval()
                else:
                    with self._raw.transaction():
                        lastrowid = self._read_lastval()
            except psycopg.Error:
                lastrowid = None
        return _Cursor(cur, lastrowid)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> _Cursor:
        translated = pg.translate(sql)
        cur = self._raw.cursor()
        cur.executemany(translated, [tuple(p) for p in seq_of_params])
        return _Cursor(cur, None)

    def executescript(self, script: str) -> None:
        for stmt in pg.split_statements(script):
            cur = self._raw.cursor()
            cur.execute(pg.translate(stmt))
            cur.close()

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor(), None)

    def commit(self) -> None:  # autocommit -> no-op
        return None

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except psycopg.Error:
            pass

    def close(self) -> None:
        try:
            self._raw.close()
        except psycopg.Error:
            pass

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *exc) -> None:
        # sqlite3 leaves the connection open on __exit__, but we must close to
        # avoid exhausting Postgres connections across the suite.
        self.close()


def connect(path: Optional[str] = None, *args, **kwargs) -> Connection:
    schema = pg.schema_for_path(str(path) if path is not None else None)
    raw = psycopg.connect(pg.DEFAULT_DSN, autocommit=True, row_factory=pg._row_factory)
    if schema != "public":
        with raw.cursor() as cur:
            try:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            except (psycopg.errors.DuplicateSchema, psycopg.errors.UniqueViolation):
                pass
            cur.execute(f'SET search_path TO "{schema}", public')
    return Connection(raw)

