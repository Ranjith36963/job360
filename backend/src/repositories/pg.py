"""psycopg3 async connection helper — the backend's single door to Postgres.

Every module talks to Postgres through here (``from src.repositories import
pg``). Historical note: the API shape intentionally matches the slice of
``aiosqlite`` the codebase used pre-migration, which is why ``commit()`` is a
no-op and query strings are still written in SQLite dialect — ``execute()``
runs them through ``translate()``. Removing that legacy dialect (and this
translator) is the dialect-rewrite batch; until then this module is the
compatibility boundary.

What it provides:
  * ``connect(path_or_dsn=None)`` — awaitable AND async context manager,
    returning a :class:`Connection`.
  * ``Connection.execute(sql, params=())`` — awaitable; translates SQLite SQL
    (``?`` placeholders, ``strftime``/``datetime('now')``, ``AUTOINCREMENT``,
    ``INSERT OR IGNORE``, ``PRAGMA``, ``sqlite_master`` …) to Postgres and
    returns a :class:`Cursor`.
  * ``Connection.executescript(sql)`` — multi-statement DDL (replaces
    ``executescript``).
  * ``Connection.executemany`` / ``commit`` / ``rollback`` / ``close``.
  * ``Cursor.fetchone`` / ``fetchall`` (async), ``rowcount``, ``description``,
    ``lastrowid`` (emulated via ``SELECT lastval()``).
  * ``Row`` — a dict subclass that ALSO supports positional access
    (``row[0]``), so both ``row["col"]`` and ``row[0]`` and ``dict(row)`` work
    exactly like ``aiosqlite.Row``.
  * ``OperationalError`` / ``IntegrityError`` / ``Error`` exception aliases.

Design choices (see the migration report):
  * **One Postgres database, autocommit connections.** SQLite's per-statement
    commit + graceful "missing table -> return []" degrade map cleanly onto
    autocommit (an error never poisons a later statement). Explicit ``commit()``
    becomes a harmless no-op.
  * **Schema-per-path isolation (TEST_MODE only).** In production every
    connection uses the ``public`` schema. Under the test-suite, each distinct
    ``db_path`` string (including ``:memory:`` and tmp files) maps to its own
    Postgres schema, giving the same "fresh DB per test" isolation the old
    tmp-``.db`` files gave — without touching the hundreds of call-sites that
    still pass a path around.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import uuid
from typing import (
    Any,
    AsyncContextManager,
    Callable,
    Generator,
    Iterable,
    Iterator,
    KeysView,
    Optional,
    Sequence,
)

import psycopg
from psycopg import pq
from psycopg.rows import RowMaker

_log = logging.getLogger("job360.db")

# psycopg async cannot run on Windows' default ProactorEventLoop (libpq socket
# readiness is never signalled -> connect hangs). Set the selector policy at
# import so every DB entry point (CLI ``run``, ARQ worker, app lifespan) is safe
# even if it forgot to set it itself. Setting a *policy* (not a loop) is
# idempotent and a no-op on non-Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- Exception aliases (aiosqlite.* -> psycopg.*) -------------------------
Error = psycopg.Error
IntegrityError = psycopg.IntegrityError


class OperationalError(psycopg.OperationalError):
    """aiosqlite.OperationalError stand-in.

    Subclasses ``psycopg.OperationalError`` so callers that catch either name
    behave the same. The shim re-raises Postgres ``UndefinedTable`` (and a couple
    of other "schema object missing" errors) as this type so the codebase's
    SQLite-era graceful degrade (``except OperationalError: return []`` when a
    table is missing on a legacy DB) keeps working — those are
    ``ProgrammingError`` subclasses in psycopg, not ``OperationalError``.

    ``UndefinedColumn`` is deliberately NOT remapped (finding H3): a missing or
    typo'd COLUMN is a bug, not a "feature not migrated yet" degrade. If it were
    an ``OperationalError`` the many ``except OperationalError: return []``
    readers would silently blank the dashboard. It propagates as a
    ``ProgrammingError`` instead.
    """


# Postgres errors that mean "schema OBJECT missing" (SQLite: "no such table").
# NOTE: ``UndefinedColumn`` is intentionally excluded (finding H3). A mis-named
# column must surface loudly, not degrade to an empty result — it is handled
# separately in ``Connection.execute`` (logged + re-raised as ProgrammingError).
_MISSING_OBJECT_ERRORS = (
    psycopg.errors.UndefinedTable,
    psycopg.errors.UndefinedObject,
    psycopg.errors.InvalidSchemaName,
)

# --- Module configuration -------------------------------------------------
def _normalize_dsn(dsn: str) -> str:
    """Windows gotcha: async psycopg hangs connecting to ``localhost`` (the
    IPv6/resolver path never signals readiness under the selector loop). It
    works instantly against ``127.0.0.1``. Normalize so any DSN — including one
    the operator sets to ``@localhost:`` — connects on every platform.
    """
    return dsn.replace("@localhost:", "@127.0.0.1:").replace("@localhost/", "@127.0.0.1/")


# Base DSN for the shared Postgres. settings.py overwrites this at import.
DEFAULT_DSN = _normalize_dsn(
    os.getenv("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")
)

# When True the schema-per-path isolation is active (conftest sets this).
# When False (production) every connection uses the ``public`` schema.
TEST_MODE = False


def configure(dsn: str) -> None:
    """Point the helper at ``dsn`` (called by settings on import)."""
    global DEFAULT_DSN
    DEFAULT_DSN = _normalize_dsn(dsn)


# ==========================================================================
# Row: dict subclass with positional access (aiosqlite.Row parity)
# ==========================================================================
class Row(dict[str, Any]):
    """A mapping row that also supports ``row[0]`` positional indexing."""

    def __init__(self, seq: Sequence[Any], columns: Sequence[str]):
        super().__init__(zip(columns, seq))
        self._seq = tuple(seq)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._seq[key]
        return dict.__getitem__(self, key)

    def __iter__(self) -> Iterator[Any]:
        # aiosqlite.Row iterates VALUES (so ``zip(cols, row)`` and ``list(row)``
        # work). dict(row) still yields {col: value} because that path uses
        # keys(), not __iter__.
        return iter(self._seq)

    # dict.keys() actually returns dict_keys[str, Any] (not nameable here without
    # importing a private typeshed symbol); KeysView[str] accurately describes
    # this shim's contract, hence the narrow, deliberate override ignore.
    def keys(self) -> KeysView[str]:  # type: ignore[override]
        return dict.keys(self)


def _row_factory(cursor: Any) -> RowMaker[Row]:
    desc = cursor.description
    cols = [c.name for c in desc] if desc else []

    def make(values: Sequence[Any]) -> Row:
        return Row(values, cols)

    return make


# ==========================================================================
# SQLite -> Postgres statement translation
# ==========================================================================
_UTC_TS_T = "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
_UTC_TS_SPACE = "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
# docs/fable/02 — canonical ISO offset form that byte-matches the app's own
# `datetime.now(timezone.utc).isoformat()` (…+00:00, NOT …Z). DB-side default
# generators (CURRENT_TIMESTAMP, datetime('now')) must use THIS so text-compared
# timestamp columns sort/filter correctly when default- and app-inserted rows
# mix. `Z` was rejected because Python 3.9's `datetime.fromisoformat` can't parse
# a Z suffix, and several call sites parse these columns unguarded.
_UTC_TS_ISO = "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"HH24:MI:SS\"+00:00\"')"

_RE_STRFTIME_ISOZ = re.compile(
    r"strftime\(\s*'%Y-%m-%dT%H:%M:%SZ'\s*,\s*'now'\s*\)", re.IGNORECASE
)
_RE_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
# SQLite DEFAULT CURRENT_TIMESTAMP yields 'YYYY-MM-DD HH:MM:SS' (UTC). Match it.
_RE_CURRENT_TIMESTAMP = re.compile(r"\bCURRENT_TIMESTAMP\b", re.IGNORECASE)
_RE_AUTOINC_PK = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE
)
_RE_AUTOINC = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
_RE_BLOB = re.compile(r"\bBLOB\b", re.IGNORECASE)
_RE_ADD_COLUMN = re.compile(
    r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE
)
_RE_PRAGMA_TABLE_INFO = re.compile(
    r"PRAGMA\s+table_info\(\s*['\"]?(\w+)['\"]?\s*\)", re.IGNORECASE
)
_RE_PRAGMA_OTHER = re.compile(r"PRAGMA\s+[^;]+", re.IGNORECASE)
_RE_SQLITE_MASTER = re.compile(r"\bsqlite_master\b", re.IGNORECASE)
# SQLite's implicit rowid (insertion order) -> Postgres physical tuple id.
# ``lastrowid`` is unaffected (no word boundary before "rowid" in it).
_RE_ROWID = re.compile(r"\browid\b", re.IGNORECASE)
_RE_INSERT_OR_IGNORE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_RE_BEGIN_IMMEDIATE = re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE)

# SQLite enforces foreign keys OFF by default, and the codebase never turns
# them on (except one test that opts in via PRAGMA). The whole suite therefore
# inserts child rows without a matching parent, and cross-table CREATE ordering
# is loose (jobs is created AFTER tables that reference it; 0014's composite FK
# reverses the applications unique-key column order). To reproduce SQLite's
# behaviour faithfully — and avoid Postgres rejecting orphan inserts / late FK
# targets — we strip ALL foreign-key clauses (inline column-level REFERENCES and
# table-level FOREIGN KEY). ``ON DELETE CASCADE`` never fired under SQLite's
# default-off, so nothing relies on DB-level cascade.
_RE_INLINE_FK = re.compile(
    r"\s+REFERENCES\s+\w+\s*\([^)]*\)"
    r"(\s+ON\s+DELETE\s+CASCADE)?(\s+ON\s+UPDATE\s+CASCADE)?",
    re.IGNORECASE,
)
_RE_TABLE_FK = re.compile(
    r",\s*FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+\w+\s*\([^)]*\)"
    r"(\s+ON\s+DELETE\s+CASCADE)?",
    re.IGNORECASE,
)

# Subquery that emulates SQLite's sqlite_master for the ``type='table'`` case.
_SQLITE_MASTER_VIEW = (
    "(SELECT 'table' AS type, tablename AS name, tablename AS tbl_name "
    "FROM pg_tables WHERE schemaname = current_schema()) AS sqlite_master"
)


def _pragma_table_info_sql(table: str) -> str:
    """Emulate ``PRAGMA table_info`` with the same positional columns.

    aiosqlite returns rows (cid, name, type, notnull, dflt_value, pk); callers
    index ``row[1]`` for the column name. This SELECT preserves that order.
    """
    # ``table`` comes from a ``\w+`` regex match, not user SQL — S608 false positive.
    return (
        "SELECT (ordinal_position - 1) AS cid, column_name AS name, "  # noqa: S608
        "data_type AS type, "
        "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
        "column_default AS dflt_value, 0 AS pk "
        "FROM information_schema.columns "
        f"WHERE table_schema = current_schema() AND table_name = '{table}' "
        "ORDER BY ordinal_position"
    )


def _has_insert(sql_upper: str) -> bool:
    return sql_upper.lstrip().startswith("INSERT")


def _split_string_literals(sql: str) -> list[tuple[str, bool]]:
    """Split ``sql`` into ``(text, is_string_literal)`` chunks on single-quote
    boundaries, honouring SQL's ``''`` escaped-quote-inside-a-literal rule.

    Used so token rewrites (``?`` -> ``%s``, ``rowid`` -> ``ctid``) never touch
    data that lives inside a quoted string literal (findings V3 / L4). Naive
    about dollar-quoting, but no repo SQL uses it.
    """
    chunks: list[tuple[str, bool]] = []
    buf: list[str] = []
    in_str = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":  # '' escape -> stay inside
                    buf.append("'")
                    i += 2
                    continue
                chunks.append(("".join(buf), True))
                buf = []
                in_str = False
        else:
            if ch == "'":
                if buf:
                    chunks.append(("".join(buf), False))
                    buf = []
                buf.append(ch)
                in_str = True
            else:
                buf.append(ch)
        i += 1
    if buf:
        chunks.append(("".join(buf), in_str))
    return chunks


def _apply_outside_string_literals(sql: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to every non-string-literal region of ``sql``.

    Fast-paths the common no-quote statement so the tokenizer only runs when a
    literal is actually present.
    """
    if "'" not in sql:
        return fn(sql)
    return "".join(chunk if is_lit else fn(chunk) for chunk, is_lit in _split_string_literals(sql))


def translate(sql: str) -> str:
    """Translate a single SQLite statement to its Postgres equivalent."""
    # PRAGMA table_info(x) -> information_schema query
    m = _RE_PRAGMA_TABLE_INFO.search(sql)
    if m:
        return _pragma_table_info_sql(m.group(1))
    # Any other PRAGMA -> harmless no-op
    if _RE_PRAGMA_OTHER.search(sql) and "table_info" not in sql.lower():
        return "SELECT 1"

    out = sql
    out = _RE_STRFTIME_ISOZ.sub(_UTC_TS_T, out)
    # docs/fable/02 — emit canonical ISO offset form (…+00:00) so default- and
    # app-inserted timestamps share ONE text-sortable format. Was _UTC_TS_SPACE,
    # which sorted every 'YYYY-MM-DD …' row before every ISO 'YYYY-MM-DDT…' row.
    out = _RE_DATETIME_NOW.sub(_UTC_TS_ISO, out)
    out = _RE_CURRENT_TIMESTAMP.sub(_UTC_TS_ISO, out)
    out = _RE_AUTOINC_PK.sub("BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", out)
    out = _RE_AUTOINC.sub("", out)
    out = _RE_BLOB.sub("BYTEA", out)
    out = _RE_BEGIN_IMMEDIATE.sub("BEGIN", out)
    out = _RE_SQLITE_MASTER.sub(_SQLITE_MASTER_VIEW, out)
    # rowid -> ctid only OUTSIDE string literals (finding V3/L4): a ``rowid``
    # substring inside a quoted value is data, not the implicit-rowid keyword.
    out = _apply_outside_string_literals(out, lambda s: _RE_ROWID.sub("ctid", s))
    low = out.lower()
    # Table-level FOREIGN KEY first (it contains a REFERENCES the inline rule
    # would otherwise chop, leaving a dangling ``FOREIGN KEY (...)``).
    if "foreign key" in low:
        out = _RE_TABLE_FK.sub("", out)
    if "references" in out.lower():
        out = _RE_INLINE_FK.sub("", out)
    # ALTER ... ADD COLUMN -> ADD COLUMN IF NOT EXISTS (idempotent, matches
    # the migration runner's "duplicate column" tolerance).
    if "alter table" in out.lower():
        out = _RE_ADD_COLUMN.sub("ADD COLUMN IF NOT EXISTS ", out)
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if _RE_INSERT_OR_IGNORE.search(out):
        out = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", out)
        if "on conflict" not in out.lower():
            out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # Placeholder translation (last, so nothing we inserted contains '?'). Only
    # OUTSIDE string literals (finding V3/L4): a ``?`` inside a quoted value is
    # data (e.g. ``WHERE note = 'ok?'``), not a bind placeholder.
    out = _apply_outside_string_literals(out, lambda s: s.replace("?", "%s"))
    return out


def _match_dollar_tag(s: str, i: int) -> Optional[str]:
    """If ``s[i:]`` opens a Postgres dollar-quote tag (``$$`` or ``$tag$``),
    return the tag text, else None. Tag body is ``[A-Za-z0-9_]*``."""
    if i >= len(s) or s[i] != "$":
        return None
    j = i + 1
    while j < len(s) and (s[j].isalnum() or s[j] == "_"):
        j += 1
    if j < len(s) and s[j] == "$":
        return s[i : j + 1]
    return None


def _split_on_unquoted_semicolons(script: str) -> list[str]:
    """Split ``script`` on ``;`` that sits OUTSIDE a single-quoted string, a
    dollar-quoted body, or a ``--`` line comment — using ONE quote/dollar-aware
    scanner for all three.

    SPLIT-P3: Postgres function bodies / DO blocks use ``$$ ... ; ... $$`` and a
    naive ``.split(';')`` would sever them mid-body. Its follow-up gap: comment
    stripping must be quote-aware too — a literal ``--`` inside a string value
    (``VALUES ('see --note')``) or a ``$$`` body is NOT a comment, and treating
    it as one truncated the line and silently dropped every later statement at
    migration boot. Both hazards are handled here in the same pass.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)
    in_single = False
    dollar_tag: Optional[str] = None
    while i < n:
        ch = script[i]
        if dollar_tag is not None:
            if script.startswith(dollar_tag, i):  # closing tag
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and script[i + 1] == "'":  # escaped '' inside a string
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        # Outside any string/dollar body: a ``--`` starts a line comment. Skip to
        # (but keep) the newline so a ``;`` inside the comment can't sever a
        # statement. Reached only when NOT in_single and NOT in a dollar body, so
        # a ``--`` inside a literal is left untouched by the branches above.
        if script.startswith("--", i):
            nl = script.find("\n", i)
            if nl == -1:
                break  # comment runs to end of script — nothing more to keep
            i = nl
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        tag = _match_dollar_tag(script, i)
        if tag is not None:
            dollar_tag = tag
            buf.append(tag)
            i += len(tag)
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def split_statements(script: str) -> list[str]:
    """Split a multi-statement script on ``;`` boundaries.

    Splits on ``;`` that is NOT inside a single-quoted string, a ``$$``/``$tag$``
    dollar-quoted body, or a ``--`` line comment. Comment stripping and statement
    splitting share ONE quote/dollar-aware scanner (SPLIT-P3), so a literal
    ``--`` or ``;`` inside a string or function body can neither sever a
    statement nor silently drop a later one.
    """
    return _split_on_unquoted_semicolons(script)


# ==========================================================================
# Schema-per-path isolation (test mode)
# ==========================================================================
def schema_for_path(path: Optional[str]) -> str:
    """Map a ``db_path`` string to a Postgres schema name.

    Production (``TEST_MODE`` False) always returns ``public``. In test mode a
    file path hashes to a stable schema (so repeated connects to the same path
    share state, like a shared ``.db`` file), while ``:memory:`` gets a fresh
    unique schema per connect (like SQLite's per-connection in-memory DB).
    """
    if not TEST_MODE:
        return "public"
    if path is None:
        return "public"
    p = str(path)
    if p.startswith("postgresql://") or p.startswith("postgres://"):
        return "public"
    if p == ":memory:":
        return "mem_" + uuid.uuid4().hex[:16]
    digest = hashlib.md5(os.path.abspath(p).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return "t_" + digest


def _is_dsn(path: Optional[str]) -> bool:
    if path is None:
        return True
    p = str(path)
    return p.startswith("postgresql://") or p.startswith("postgres://")


async def _open_raw(schema: str) -> psycopg.AsyncConnection[Row]:
    try:
        conn = await psycopg.AsyncConnection.connect(
            DEFAULT_DSN, autocommit=True, row_factory=_row_factory
        )
    except psycopg.Error as exc:
        _log.error(
            "db_connect_failed",
            extra={"event": "db_connect_failed", "schema": schema, "error": str(exc)},
            exc_info=True,
        )
        raise
    _log.debug(
        "db_connect_ok",
        extra={"event": "db_connect_ok", "schema": schema},
    )
    if schema != "public":
        async with conn.cursor() as cur:
            try:
                await cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            except (psycopg.errors.DuplicateSchema, psycopg.errors.UniqueViolation):
                # CREATE SCHEMA IF NOT EXISTS is not atomic against a concurrent
                # creator (Postgres races on the pg_namespace unique index).
                # The schema now exists — that's all we needed.
                pass
            await cur.execute(f'SET search_path TO "{schema}", public')
    return conn


# ==========================================================================
# Cursor wrapper
# ==========================================================================
class Cursor:
    """Thin async wrapper over a psycopg cursor with aiosqlite semantics."""

    def __init__(self, cur: psycopg.AsyncCursor[Row], lastrowid: Optional[int]):
        self._cur = cur
        self.lastrowid = lastrowid

    async def fetchone(self) -> Any:
        return await self._cur.fetchone()

    async def fetchall(self) -> list[Any]:
        return await self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self) -> Optional[list[psycopg.Column]]:
        return self._cur.description

    async def close(self) -> None:
        await self._cur.close()

    async def __aenter__(self) -> Cursor:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


# ==========================================================================
# Connection wrapper
# ==========================================================================
class Connection:
    """aiosqlite-shaped async connection backed by psycopg3."""

    def __init__(self, raw: psycopg.AsyncConnection[Row]):
        self._raw = raw
        self._row_factory = Row  # settable no-op (rows are always Row)

    # ``db.row_factory = aiosqlite.Row`` is a no-op — we always yield Row.
    @property
    def row_factory(self) -> Any:
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._row_factory = value

    async def _read_lastval(self) -> Optional[int]:
        """``SELECT lastval()`` on a short-lived, always-closed cursor."""
        async with self._raw.cursor() as c2:
            await c2.execute("SELECT lastval()")
            row = await c2.fetchone()
            return row[0] if row else None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        translated = translate(sql)
        cur = self._raw.cursor()
        prm = tuple(params) if params else None
        try:
            await cur.execute(translated, prm)
        except psycopg.errors.UndefinedColumn:
            # A missing/typo'd COLUMN is a bug, not the graceful "table not
            # migrated yet" degrade. Do NOT remap to OperationalError (readers do
            # ``except OperationalError: return []`` and would blank the
            # dashboard). Log loudly and let it propagate (finding H3).
            _log.error(
                "db_undefined_column",
                extra={"event": "db_undefined_column", "sql": translated},
                exc_info=True,
            )
            raise
        except _MISSING_OBJECT_ERRORS as exc:
            raise OperationalError(str(exc)) from exc
        # ``lastrowid`` (aiosqlite parity) is emulated via ``SELECT lastval()``.
        #
        # Two independent traps, BOTH handled here:
        #  1. STALE ID (finding V2) — only probe when the INSERT actually
        #     inserted a row (``cur.rowcount``). An ``ON CONFLICT DO NOTHING``
        #     that conflicts inserts nothing, and lastval() would then hand back
        #     an id from an EARLIER insert in the same session — silently wrong.
        #  2. POISONED TRANSACTION (finding H2) — a TEXT/UUID-PK insert never
        #     touches a sequence, so lastval() raises "lastval is not yet
        #     defined". In autocommit that error is harmless; inside a
        #     transaction (the migration runner wraps each body in one) it ABORTS
        #     the whole transaction and the next statement dies with
        #     InFailedSqlTransaction. So: probe directly when IDLE, else isolate
        #     it in a SAVEPOINT (``self._raw.transaction()``) — which keeps the
        #     probe's failure local AND still returns a real lastrowid inside a
        #     transaction.
        lastrowid: Optional[int] = None
        if (
            _has_insert(translated.upper())
            and "returning" not in translated.lower()
            and cur.rowcount
        ):
            try:
                if self._raw.info.transaction_status == pq.TransactionStatus.IDLE:
                    lastrowid = await self._read_lastval()
                else:
                    async with self._raw.transaction():
                        lastrowid = await self._read_lastval()
            except psycopg.Error:
                lastrowid = None
        return Cursor(cur, lastrowid)

    async def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Cursor:
        translated = translate(sql)
        cur = self._raw.cursor()
        await cur.executemany(translated, [tuple(p) for p in seq_of_params])
        return Cursor(cur, None)

    async def executescript(self, script: str) -> None:
        for stmt in split_statements(script):
            cur = self._raw.cursor()
            await cur.execute(translate(stmt))
            await cur.close()

    def transaction(self) -> AsyncContextManager[psycopg.AsyncTransaction]:
        """Explicit transaction block (psycopg passthrough).

        aiosqlite has no direct analog — our connection is autocommit. The
        migration runner uses this to make each migration body + its
        ``_schema_migrations`` bookkeeping INSERT atomic: on any error the whole
        body rolls back and the stem is NOT recorded (finding H2). On an
        autocommit connection psycopg emits an explicit BEGIN/COMMIT around the
        block (ROLLBACK on error); nested blocks use SAVEPOINTs.
        """
        return self._raw.transaction()

    async def commit(self) -> None:  # autocommit -> no-op
        return None

    async def rollback(self) -> None:
        try:
            await self._raw.rollback()
        except psycopg.Error:
            pass

    async def close(self) -> None:
        await self._raw.close()

    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


# ==========================================================================
# connect(): awaitable + async context manager (aiosqlite.connect parity)
# ==========================================================================
class _Connector:
    def __init__(self, path: Optional[str]):
        self._path = path
        self._conn: Optional[Connection] = None

    async def _open(self) -> Connection:
        schema = "public" if _is_dsn(self._path) else schema_for_path(self._path)
        raw = await _open_raw(schema)
        self._conn = Connection(raw)
        return self._conn

    def __await__(self) -> Generator[Any, None, Connection]:
        return self._open().__await__()

    async def __aenter__(self) -> Connection:
        return await self._open()

    async def __aexit__(self, *exc: Any) -> None:
        if self._conn is not None:
            await self._conn.close()


def connect(path: Optional[str] = None) -> _Connector:
    """Open a Postgres connection. ``path`` selects the schema (test mode) or
    is ignored (production -> ``public``). Pass ``None`` or a ``postgresql://``
    DSN for the default database.
    """
    return _Connector(str(path) if path is not None else None)


async def drop_schema(path: str) -> None:
    """Drop the schema a ``db_path`` maps to (test teardown helper)."""
    schema = schema_for_path(path)
    if schema == "public":
        return
    conn = await psycopg.AsyncConnection.connect(DEFAULT_DSN, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await conn.close()


# Backwards-compat name: some type hints say ``aiosqlite.Connection``.
Connection.__name__ = "Connection"
