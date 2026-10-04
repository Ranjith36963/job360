"""FC-008 — a read must stay a read.

Production 2026-10-04: every signed-in request ran ``UPDATE sessions SET
last_seen`` (``services/auth/sessions.py``). Postgres here is autocommit, so
that UPDATE was a commit, and prod's commits wait on a slow disk (0.2-1 s,
spikes past 10 s). ``GET /api/auth/me`` took 17-61 s when ~7 requests arrived
together. On top of that, routes that declared ``Depends(get_request_db)``
before ``Depends(require_user)`` borrowed a pooled connection FIRST and held it
while auth waited on that write, which pinned the 10-connection pool.

This file pins three things:

1. The session touch is throttled (``SESSION_TOUCH_INTERVAL_SECONDS``): one
   UPDATE per window, counted, with an injected clock (no sleep).
2. Expiry is unchanged: ``expires_at`` alone decides; ``last_seen`` neither
   extends nor shortens a session.
3. The CLASS guard — "hidden write on a read path": authenticated GETs with a
   fresh session issue NO INSERT/UPDATE/DELETE at all, plus the guard for the
   pool half — no unauthenticated request on any route ever borrows a DB
   connection before it is refused. Each has a negative control proving the
   spy can see the thing it guards against.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from migrations import runner
from src.core import settings
from src.repositories import pg
from src.services.auth import sessions as auth_sessions

SESSION_SECRET = "test-secret-" + "t" * 32

# ── Statement spy ──────────────────────────────────────────────────────────────

_WRITE_RE = re.compile(r"^\s*(?:WITH\b.*?\b)?(INSERT|UPDATE|DELETE|MERGE|UPSERT|REPLACE)\b", re.I | re.S)


def _is_write(sql: str) -> bool:
    """True for a statement that changes rows (what costs a commit/fsync)."""
    return bool(_WRITE_RE.match(sql))


class StatementSpy:
    """Records every SQL statement sent through ``pg.Connection``.

    ``pg.Connection`` is the single DB door (CLAUDE.md): sessions, auth_deps and
    ``JobDatabase`` (the ``get_request_db`` handle) all go through it, so
    patching the class sees every statement a request issues.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.statements: list[str] = []
        orig_execute = pg.Connection.execute
        orig_many = pg.Connection.executemany
        orig_script = pg.Connection.executescript
        spy = self

        async def execute(self_: Any, sql: str, params: Any = ()) -> Any:
            spy.statements.append(sql)
            return await orig_execute(self_, sql, params)

        async def executemany(self_: Any, sql: str, seq: Any) -> Any:
            spy.statements.append(sql)
            return await orig_many(self_, sql, seq)

        async def executescript(self_: Any, script: str) -> None:
            spy.statements.append(script)
            await orig_script(self_, script)

        monkeypatch.setattr(pg.Connection, "execute", execute)
        monkeypatch.setattr(pg.Connection, "executemany", executemany)
        monkeypatch.setattr(pg.Connection, "executescript", executescript)

    def clear(self) -> None:
        self.statements.clear()

    @property
    def writes(self) -> list[str]:
        return [s for s in self.statements if _is_write(s)]

    def session_updates(self) -> list[str]:
        return [s for s in self.writes if re.search(r"\bUPDATE\s+sessions\b", s, re.I)]


def test_write_classifier_sees_writes_and_ignores_reads() -> None:
    """The classifier itself: a blind classifier would make every guard green."""
    assert _is_write("UPDATE sessions SET last_seen = ? WHERE id = ?")
    assert _is_write("  insert into audit_log(x) values (?)")
    assert _is_write("DELETE FROM sessions WHERE id = ?")
    assert _is_write("WITH x AS (SELECT 1) UPDATE t SET a = 1")
    assert not _is_write("SELECT user_id, expires_at, last_seen FROM sessions WHERE id = ?")
    assert not _is_write("SELECT id FROM t WHERE note = 'UPDATE me'")


# ── 1 + 2. Unit: the throttle and expiry ───────────────────────────────────────


@pytest.fixture
async def session_db() -> AsyncIterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await runner.up(path, target="0001_auth")
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("user-1", "u@example.test", "!"),
        )
        await db.commit()
    yield path
    try:
        await pg.drop_schema(path)
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


async def _set_session(path: str, *, expires_at: str, last_seen: str | None = None) -> None:
    async with pg.connect(path) as db:
        await db.execute("UPDATE sessions SET expires_at = ?", (expires_at,))
        if last_seen is not None:
            await db.execute("UPDATE sessions SET last_seen = ?", (last_seen,))
        await db.commit()


async def _read_session(path: str) -> dict[str, Any]:
    async with pg.connect(path) as db:
        cur = await db.execute("SELECT expires_at, last_seen FROM sessions")
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_two_resolves_inside_the_window_write_once_and_after_it_write_again(
    session_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    await _set_session(
        session_db,
        last_seen=(T0 - timedelta(hours=1)).isoformat(),
        expires_at=(T0 + timedelta(days=30)).isoformat(),
    )
    spy = StatementSpy(monkeypatch)

    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=T0) == "user-1"
    assert len(spy.session_updates()) == 1, spy.statements
    assert (await _read_session(session_db))["last_seen"] == T0.isoformat()

    t1 = T0 + timedelta(seconds=299)
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=t1) == "user-1"
    assert len(spy.session_updates()) == 1, (
        "a resolve inside the touch window must issue NO UPDATE — it is a pure read. "
        f"statements: {spy.statements}"
    )
    assert (await _read_session(session_db))["last_seen"] == T0.isoformat()

    t2 = T0 + timedelta(seconds=300)
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=t2) == "user-1"
    assert len(spy.session_updates()) == 2, spy.statements
    assert (await _read_session(session_db))["last_seen"] == t2.isoformat()


@pytest.mark.asyncio
async def test_interval_zero_restores_write_every_resolve(
    session_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting is a real parameter, read at call time — not a hardcode."""
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 0)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    await _set_session(session_db, expires_at=(T0 + timedelta(days=30)).isoformat())
    spy = StatementSpy(monkeypatch)
    for i in range(3):
        await auth_sessions.resolve_session(
            session_db, cookie, secret=SESSION_SECRET, now=T0 + timedelta(seconds=i)
        )
    assert len(spy.session_updates()) == 3


@pytest.mark.asyncio
async def test_unparseable_last_seen_counts_as_stale(
    session_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strange stored value costs one write; it can never suppress writes forever."""
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    await _set_session(session_db, last_seen="not a timestamp", expires_at=(T0 + timedelta(days=1)).isoformat())
    spy = StatementSpy(monkeypatch)
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=T0) == "user-1"
    assert len(spy.session_updates()) == 1
    assert (await _read_session(session_db))["last_seen"] == T0.isoformat()


@pytest.mark.asyncio
async def test_expired_session_is_rejected_even_with_a_fresh_last_seen(
    session_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    await _set_session(
        session_db,
        last_seen=T0.isoformat(),
        expires_at=(T0 - timedelta(seconds=1)).isoformat(),
    )
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=T0) is None


@pytest.mark.asyncio
async def test_valid_session_is_accepted_even_with_a_very_old_last_seen(
    session_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No idle timeout exists: an old last_seen must not shorten a session."""
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    await _set_session(
        session_db,
        last_seen=(T0 - timedelta(days=29)).isoformat(),
        expires_at=(T0 + timedelta(hours=1)).isoformat(),
    )
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=T0) == "user-1"


@pytest.mark.asyncio
async def test_touching_never_extends_expiry(session_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``expires_at`` is absolute: touches leave it byte-identical, and the
    session dies at that instant however recently it was seen."""
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    cookie = await auth_sessions.create_session(session_db, user_id="user-1", secret=SESSION_SECRET)
    expires = T0 + timedelta(minutes=10)
    await _set_session(
        session_db,
        last_seen=(T0 - timedelta(hours=1)).isoformat(),
        expires_at=expires.isoformat(),
    )
    for minutes in (0, 5, 9):
        assert await auth_sessions.resolve_session(
            session_db, cookie, secret=SESSION_SECRET, now=T0 + timedelta(minutes=minutes)
        ) == "user-1"
    row = await _read_session(session_db)
    assert row["expires_at"] == expires.isoformat()
    assert row["last_seen"] == (T0 + timedelta(minutes=5)).isoformat()
    assert await auth_sessions.resolve_session(session_db, cookie, secret=SESSION_SECRET, now=expires) is None


# ── 3. The class guard: hidden write on a read path ────────────────────────────

READ_PATHS = (
    "/api/auth/me",
    "/api/applications",
    "/api/applications/stats",
    "/api/asks",
    "/api/whats-new",
)


def _set_all_last_seen(db_path: Any, when: datetime) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(db_path))
    conn.execute("UPDATE sessions SET last_seen = ?", (when.isoformat(),))
    conn.commit()
    conn.close()


def _db_path_of(_ctx: Any) -> Any:
    return settings.DB_PATH  # authenticated_async_context patched it to the test DB


@pytest.mark.asyncio
async def test_authenticated_reads_issue_no_writes(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GUARD (FC-008). A signed-in GET with a fresh session must not write.

    If this fails, some code on a read path now issues INSERT/UPDATE/DELETE.
    On prod's disk every such write is a commit that can take seconds, and it
    runs on EVERY page load. Move the write off the read path (throttle it,
    make it conditional, or do it on an explicit POST) — do not delete this.
    """
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    _set_all_last_seen(_db_path_of(authenticated_async_context), datetime.now(timezone.utc))
    spy = StatementSpy(monkeypatch)
    offenders: dict[str, list[str]] = {}
    async with authenticated_async_context() as client:
        for path in READ_PATHS:
            spy.clear()
            resp = await client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:300]}"
            assert spy.statements, f"{path}: the spy saw NO statements at all — it is blind, not green"
            if spy.writes:
                offenders[path] = spy.writes
    assert not offenders, (
        "HIDDEN WRITE ON A READ PATH (FC-008, docs/harness/FAILURE_CATALOG.md): these "
        "authenticated GETs issued writes, each one a commit on every page load:\n"
        + "\n".join(f"  {p}: {w}" for p, w in offenders.items())
    )


@pytest.mark.asyncio
async def test_negative_control_spy_sees_the_touch_on_a_stale_session(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL for the guard above: with a stale session the same
    request DOES write, and the spy must see exactly that one UPDATE."""
    monkeypatch.setattr(settings, "SESSION_TOUCH_INTERVAL_SECONDS", 300)
    _set_all_last_seen(
        _db_path_of(authenticated_async_context), datetime.now(timezone.utc) - timedelta(hours=1)
    )
    spy = StatementSpy(monkeypatch)
    async with authenticated_async_context() as client:
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        assert len(spy.session_updates()) == 1, spy.statements
        assert spy.writes == spy.session_updates()
        spy.clear()
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        assert spy.writes == [], "the touch just happened; the next read must be pure"


# ── 3b. The pool half: auth resolves before a DB handle is borrowed ────────────


def _db_spy() -> tuple[list[str], Any]:
    entered: list[str] = []

    async def fake_get_request_db() -> AsyncIterator[object]:
        entered.append("db")
        yield object()

    return entered, fake_get_request_db


@pytest.mark.asyncio
async def test_no_unauthenticated_request_borrows_a_db_handle(authenticated_async_context: Any) -> None:
    """GUARD (FC-008, pool half). Every route, no credentials: if the answer is
    401/403 the route must not have entered ``get_request_db`` first.

    Before the fix 37 routes declared ``db`` before ``user`` and borrowed a
    pooled connection while auth ran. Fix: ``dependencies=AUTH_FIRST`` (or
    ``VERIFIED_FIRST``) on the route — see ``src/api/auth_deps.py``.
    """
    from src.api.dependencies import get_request_db
    from src.api.main import app
    from tests._routes import route_table

    entered, fake = _db_spy()
    app.dependency_overrides[get_request_db] = fake
    offenders: list[str] = []
    checked = 0
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            for row in route_table(app):
                if not row.path.startswith("/api/") or row.path.startswith("/api/mcp"):
                    continue
                path = re.sub(r"\{[^}]+\}", "1", row.path)
                for method in sorted(row.methods - {"HEAD", "OPTIONS"}):
                    entered.clear()
                    resp = await client.request(method, path)
                    if resp.status_code in (401, 403):
                        checked += 1
                        if entered:
                            offenders.append(f"{method} {row.path}")
    finally:
        app.dependency_overrides.pop(get_request_db, None)
    assert checked >= 30, f"only {checked} routes refused an anonymous call — the sweep is blind"
    assert not offenders, (
        "These routes borrow a DB connection BEFORE refusing an anonymous caller "
        "(FC-008). Add dependencies=AUTH_FIRST / VERIFIED_FIRST to the route:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.asyncio
async def test_negative_control_old_param_order_borrows_first() -> None:
    """NEGATIVE CONTROL: the old shape (``db`` before ``user``, no route
    dependencies) DOES enter the DB dependency on a 401, and the fixed shape
    does not. Proves the spy above can see the bug it guards against."""
    from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user

    entered, fake = _db_spy()
    mini = FastAPI()

    @mini.get("/old")
    async def old(db: object = Depends(fake), user: CurrentUser = Depends(require_user)) -> dict[str, str]:  # noqa: B008
        return {"ok": "yes"}

    @mini.get("/new", dependencies=AUTH_FIRST)
    async def new(db: object = Depends(fake), user: CurrentUser = Depends(require_user)) -> dict[str, str]:  # noqa: B008
        return {"ok": "yes"}

    async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://test") as client:
        assert (await client.get("/old")).status_code == 401
        assert entered == ["db"], "old order must borrow first — otherwise the guard is blind"
        entered.clear()
        assert (await client.get("/new")).status_code == 401
        assert entered == []


@pytest.mark.asyncio
async def test_auth_runs_once_per_request_with_auth_first(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dependencies=AUTH_FIRST`` plus the endpoint's own ``user`` parameter
    must resolve the user ONCE (FastAPI's per-request dependency cache), not twice."""
    from src.api import auth_deps

    calls: list[int] = []
    orig = auth_deps.resolve_current_user

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return await orig(*args, **kwargs)

    monkeypatch.setattr(auth_deps, "resolve_current_user", counting)
    async with authenticated_async_context() as client:
        for path in ("/api/asks", "/api/applications/stats"):
            calls.clear()
            resp = await client.get(path)
            assert resp.status_code == 200, resp.text
            assert len(calls) == 1, f"{path}: auth resolved {len(calls)} times"
