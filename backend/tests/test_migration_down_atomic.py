"""A failed down-migration must not leave the schema half-reverted.

The bug
-------
`up()` got the atomicity fix (finding H2): the migration body and the
`_schema_migrations` bookkeeping row commit in one transaction. `down()` never
did, and it is the more dangerous of the two.

The connection is autocommit and `executescript()` runs each statement on its
own committed cursor. A down-migration like 0011 rebuilds `jobs`:

    DROP INDEX x5 -> CREATE jobs_old -> INSERT SELECT -> DROP TABLE jobs
    -> RENAME -> CREATE INDEX x5

If any statement after the first fails, the already-dropped indexes stay
dropped, the exception propagates, and the `DELETE FROM _schema_migrations` line
never runs. The migration is therefore still recorded as APPLIED while the
schema is silently missing its indexes — and `up()` will never re-run it to
repair, precisely because it is marked applied.

Nothing errors afterwards. Queries just get slower, forever.

These tests assert the OUTCOME (bookkeeping and schema agree after a failure)
rather than that the code contains a `transaction()` call — a structural test
would pass against a transaction that was opened and never used.
"""

import uuid

import pytest

from migrations import runner
from src.repositories import pg
from src.repositories.database import JobDatabase


@pytest.mark.asyncio
async def test_failed_down_does_not_mark_migration_reverted(tmp_path, monkeypatch):
    """If the down SQL blows up, _schema_migrations must NOT lose the row.

    Bookkeeping and schema have to agree: either both moved or neither did.
    """
    db_path = str(tmp_path / f"m_{uuid.uuid4().hex[:8]}.db")

    # Migrations build on top of the legacy tables init_db() creates — running
    # runner.up() against a bare schema fails on the first ALTER of a table that
    # does not exist yet. Same order conftest uses.
    _seed = JobDatabase(db_path)
    await _seed.init_db()
    await _seed.close()

    # Apply real migrations so there is something to revert.
    await runner.up(db_path)

    async with pg.connect(db_path) as db:
        cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
        before = [r[0] for r in await cur.fetchall()]

    assert before, "no migrations applied — cannot exercise down()"
    last = before[-1]

    # Reproduce the ACTUAL failure mode: the script PARTIALLY applies, then
    # fails. A mock that raises before doing anything proves nothing — without a
    # transaction the exception simply propagates and no bookkeeping changes
    # either way, so such a test passes against the unfixed code. (Verified: it
    # did.) The bug only exists because earlier statements have already
    # committed by the time a later one blows up.
    real_executescript = pg.Connection.executescript

    async def _partial_then_boom(self, sql):  # type: ignore[no-untyped-def]
        # a real, committed schema change...
        await real_executescript(self, "CREATE TABLE _down_partial_marker (x int);")
        # ...and then the failure, exactly like a later statement colliding
        raise RuntimeError("simulated failure midway through the down migration")

    monkeypatch.setattr(pg.Connection, "executescript", _partial_then_boom)

    with pytest.raises(RuntimeError):
        await runner.down(db_path)

    monkeypatch.setattr(pg.Connection, "executescript", real_executescript)

    async with pg.connect(db_path) as db:
        cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
        after = [r[0] for r in await cur.fetchall()]

        # THE load-bearing assertion. Bookkeeping alone cannot tell the two
        # versions apart: without a transaction the exception propagates before
        # the DELETE either way, so the row survives in both. What only holds
        # under a transaction is that the schema work done BEFORE the failure is
        # undone. Without one, the marker table is still sitting there committed
        # — exactly like migration 0011's dropped indexes staying dropped.
        cur = await db.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = current_schema() "
            "AND table_name = '_down_partial_marker'"
        )
        leaked = (await cur.fetchone())[0]

    assert leaked == 0, (
        "a statement that ran BEFORE the failure stayed committed — the down "
        "migration is not atomic. Real consequence (migration 0011): the "
        "DROP INDEX statements survive, the migration stays marked applied so "
        "up() never repairs it, and every query against jobs does a seq scan "
        "from then on. Nothing errors; it just gets slower forever."
    )

    assert after == before, (
        "a FAILED down-migration changed the bookkeeping. If the row were "
        "removed the migration would silently re-apply; if the schema had "
        "half-reverted while the row stayed, up() would never repair it "
        f"(last={last}, before={before}, after={after})"
    )


@pytest.mark.asyncio
async def test_successful_down_removes_exactly_one_row(tmp_path):
    """The happy path still works — atomicity must not break normal reverts."""
    db_path = str(tmp_path / f"m_{uuid.uuid4().hex[:8]}.db")
    # Migrations build on top of the legacy tables init_db() creates — running
    # runner.up() against a bare schema fails on the first ALTER of a table that
    # does not exist yet. Same order conftest uses.
    _seed = JobDatabase(db_path)
    await _seed.init_db()
    await _seed.close()

    await runner.up(db_path)

    async with pg.connect(db_path) as db:
        cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
        before = [r[0] for r in await cur.fetchall()]

    reverted = await runner.down(db_path)
    assert reverted == before[-1]

    async with pg.connect(db_path) as db:
        cur = await db.execute("SELECT id FROM _schema_migrations ORDER BY id")
        after = [r[0] for r in await cur.fetchall()]

    assert after == before[:-1], "down() should remove exactly the last applied migration"
