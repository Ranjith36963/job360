"""Synchronous ``sqlite3``-shaped shim backed by psycopg3.

Used only by the test-suite: ``conftest`` registers this module as
``sys.modules["sqlite3"]`` so test files that do ``import sqlite3`` and
``sqlite3.connect(path)`` transparently talk to the shared Postgres, with the
same schema-per-path isolation the async :mod:`pg` helper provides.

Only the small slice of the sqlite3 surface the tests use is implemented:
``connect``, ``Connection.{execute,executescript,executemany,commit,close,
cursor}`` + context manager, ``Cursor.{fetchone,fetchall,rowcount,lastrowid}``,
``Row``, ``OperationalError`` / ``IntegrityError``. Anything else falls through
to the real stdlib ``sqlite3`` via module ``__getattr__``.
"""

from __future__ import annotations

import sqlite3 as _real_sqlite3
from typing import Any, Iterable, Optional

import psycopg

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

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _Cursor:
        translated = pg.translate(sql)
        cur = self._raw.cursor()
        try:
            cur.execute(translated, tuple(params) if params else None)
        except pg._MISSING_OBJECT_ERRORS as exc:
            raise pg.OperationalError(str(exc)) from exc
        lastrowid = None
        if translated.upper().lstrip().startswith("INSERT") and "returning" not in translated.lower():
            # Probe lastval() ONLY in autocommit (transaction IDLE) — mirrors the
            # async shim (pg.Connection.execute). Inside a transaction a failing
            # probe aborts the whole transaction; skip it there. See pg.py for the
            # full rationale.
            if self._raw.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                try:
                    c2 = self._raw.cursor()
                    c2.execute("SELECT lastval()")
                    row = c2.fetchone()
                    lastrowid = row[0] if row else None
                    c2.close()
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


def __getattr__(name: str):  # PEP 562 — fall through to the real sqlite3
    return getattr(_real_sqlite3, name)
