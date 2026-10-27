"""AUTH-4: single-use auth tokens are claimed atomically.

Magic-link, password-reset and email-verification tokens used to be checked
with a SELECT and then marked used with a separate, unguarded UPDATE, so two
concurrent consumes of one token could both succeed. Each test below forces
the race deterministically: every read of the token row waits (briefly) until
both callers have read it, so both see the token as unused. Exactly one
consume may succeed.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from migrations import runner
from src.repositories import pg
from src.services.auth import email_verification, magic_link, password_reset, tokens

SECRET = "test-secret-" + "x" * 40


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "claim.db")

    async def _bootstrap():
        # Same legacy tables the sibling auth tests create before migrating.
        async with pg.connect(path) as db:
            await db.executescript(
                """
                CREATE TABLE user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'applied',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                """
            )
            await db.commit()
        await runner.up(path)

    asyncio.run(_bootstrap())
    return path


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _race_open_db(monkeypatch, module):
    """Make the first two readers of a token row both read before either writes."""
    real_open_db = module.open_db
    state = {"arrived": 0, "event": asyncio.Event()}

    class _Conn:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            setattr(self._conn, name, value)

        async def execute(self, sql, params=()):
            cur = await self._conn.execute(sql, params)
            if sql.lstrip().upper().startswith("SELECT") and "token_hash" in sql:
                state["arrived"] += 1
                if state["arrived"] >= 2:
                    state["event"].set()
                else:
                    try:
                        await asyncio.wait_for(state["event"].wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
            return cur

    @asynccontextmanager
    async def _open_db(*args, **kwargs):
        async with real_open_db(*args, **kwargs) as conn:
            yield _Conn(conn)

    monkeypatch.setattr(module, "open_db", _open_db)


async def _insert_user(db_path, user_id="u1", email="racer@example.com"):
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (user_id, email, "x"),
        )
        await db.commit()


def test_magic_link_two_concurrent_consumes_only_one_wins(db_path, monkeypatch):
    raw, h = tokens.generate_token()

    async def _go():
        async with pg.connect(db_path) as db:
            await db.execute(
                "INSERT INTO magic_link_tokens(email, token_hash, expires_at) VALUES (?, ?, ?)",
                ("racer@example.com", h, _future()),
            )
            await db.commit()
        _race_open_db(monkeypatch, magic_link)
        return await asyncio.gather(
            magic_link.consume_magic_link(db_path=db_path, raw_token=raw, secret=SECRET),
            magic_link.consume_magic_link(db_path=db_path, raw_token=raw, secret=SECRET),
        )

    results = asyncio.run(_go())
    assert sum(r is not None for r in results) == 1, results


def test_password_reset_two_concurrent_confirms_only_one_wins(db_path, monkeypatch):
    raw, h = tokens.generate_token()

    async def _go():
        await _insert_user(db_path)
        async with pg.connect(db_path) as db:
            await db.execute(
                "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                ("u1", h, _future()),
            )
            await db.commit()
        _race_open_db(monkeypatch, password_reset)
        return await asyncio.gather(
            password_reset.confirm_password_reset(
                db_path=db_path, raw_token=raw, new_password="Fake-Password-1!"
            ),
            password_reset.confirm_password_reset(
                db_path=db_path, raw_token=raw, new_password="Fake-Password-2!"
            ),
        )

    results = asyncio.run(_go())
    assert sum(r is not None for r in results) == 1, results


def test_email_verification_two_concurrent_confirms_only_one_wins(db_path, monkeypatch):
    raw, h = tokens.generate_token()

    async def _go():
        await _insert_user(db_path)
        async with pg.connect(db_path) as db:
            await db.execute(
                "INSERT INTO email_verifications(user_id, token_hash, email, expires_at) "
                "VALUES (?, ?, ?, ?)",
                ("u1", h, "racer@example.com", _future()),
            )
            await db.commit()
        _race_open_db(monkeypatch, email_verification)
        return await asyncio.gather(
            email_verification.confirm_email_verification(db_path=db_path, raw_token=raw),
            email_verification.confirm_email_verification(db_path=db_path, raw_token=raw),
        )

    results = asyncio.run(_go())
    assert sum(r is not None for r in results) == 1, results
