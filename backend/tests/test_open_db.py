"""open_db connection helper — sets busy_timeout so auth/channel writes wait for
a lock instead of 500-ing on 'database is locked' (#11 extension; bug found by
the Loop-2 log-fill run: logout + per-request last_seen write 500'd under search).
"""
from __future__ import annotations

import pytest

from src.repositories.db_retry import open_db


@pytest.mark.asyncio
async def test_open_db_sets_busy_timeout(tmp_path):
    p = str(tmp_path / "t.db")
    async with open_db(p, busy_timeout_ms=12345) as db:
        cur = await db.execute("PRAGMA busy_timeout")
        row = await cur.fetchone()
    assert row[0] == 12345


@pytest.mark.asyncio
async def test_open_db_default_timeout_is_30s(tmp_path):
    p = str(tmp_path / "t.db")
    async with open_db(p) as db:
        cur = await db.execute("PRAGMA busy_timeout")
        row = await cur.fetchone()
    assert row[0] == 30000


@pytest.mark.asyncio
async def test_open_db_usable_for_read_write(tmp_path):
    p = str(tmp_path / "t.db")
    async with open_db(p) as db:
        await db.execute("CREATE TABLE t (x INTEGER)")
        await db.execute("INSERT INTO t VALUES (1)")
        await db.commit()
    async with open_db(p) as db:
        cur = await db.execute("SELECT x FROM t")
        assert (await cur.fetchone())[0] == 1
