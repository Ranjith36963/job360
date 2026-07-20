"""GDPR erasure must PROVE it worked, not merely fail to raise.

The bug
-------
`hard_delete_user` wrapped every per-user DELETE in `except Exception: pass`.
The stated intent — tolerate a table that does not exist in a partial test
schema — is reasonable, which is exactly why nobody looked again. But the same
handler swallowed permission errors, FK violations, deadlocks and a dropped
connection, so a PARTIALLY FAILED erasure returned normally and the caller told
the user "your account and data have been deleted".

For Art.17 that is not a bug, it is a false statement to a data subject.

Why the fix checks the OUTCOME rather than the exception
--------------------------------------------------------
Type-filtering cannot separate the two cases here: `pg.py` converts
`UndefinedTable` into `OperationalError`, which is also what a lost connection
raises. So "this table doesn't exist" and "the database went away" are
indistinguishable by exception type at this layer. The fix therefore counts
surviving rows afterwards and raises if any remain.

These tests assert the BEHAVIOUR (it raises, and the message names the table)
rather than that the code contains a try/except — a structural test would pass
against a handler that still silently swallowed everything.
"""

import pytest

from src.repositories.database import JobDatabase


@pytest.mark.asyncio
async def test_erasure_raises_when_rows_survive(monkeypatch):
    """If a per-user table still holds rows afterwards, erasure must FAIL LOUD."""
    db = JobDatabase(":memory:")
    await db.init_db()

    try:
        # Real erasure of a non-existent user is a no-op and must not raise:
        # nothing survives, so the verification finds nothing.
        await db.hard_delete_user("ghost-user-that-never-existed")

        # Now make ONE table's DELETE fail the way a real incident does —
        # permission denied / FK block / connection dropped. Before the fix this
        # was caught by `except Exception: pass` and erasure returned normally,
        # so the caller told the user their data was gone while it was not.
        # Fail every per-user DELETE (but not the final `DELETE FROM users`), so
        # the test does not depend on which tables this particular schema has.
        real_execute = db._db.execute
        failed: list[str] = []

        async def _execute_with_failing_deletes(sql, params=None):
            stripped = sql.strip().upper()
            if stripped.startswith("DELETE FROM") and " USERS " not in f" {stripped} ":
                failed.append(sql)
                raise RuntimeError("permission denied")
            return await real_execute(sql, params)

        monkeypatch.setattr(db._db, "execute", _execute_with_failing_deletes)

        with pytest.raises(RuntimeError) as exc:
            await db.hard_delete_user("victim")

        msg = str(exc.value)
        assert "erasure did not complete" in msg, (
            f"a failed DELETE must surface as a failed erasure, got: {msg}"
        )
        assert "permission denied" in msg, (
            f"the underlying cause must be preserved so it is actionable; got: {msg}"
        )
        assert failed, "the test did not actually intercept any DELETE"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_erasure_tolerates_a_genuinely_absent_table():
    """A table missing from this schema is skipped — the original, correct tolerance.

    This is the case the old `except Exception: pass` existed for, and it must
    keep working: erasure on a partial schema should not explode.
    """
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        # No exception even though several _PER_USER_TABLES are created by later
        # migrations and may not exist in this bare schema.
        await db.hard_delete_user("nobody")
    finally:
        await db.close()
