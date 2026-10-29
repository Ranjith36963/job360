"""Which applications are TESTS (owner 2026-10-29): the one rule the core surfaces read.

An application is a test when it carries a ``simulation`` event that no other event corrects
(the rule stats already uses, ``stats._SIMULATED_SQL``). ``bring_job(test=true)`` writes that
event first, at birth; a correcting ``note`` (``corrects_event_id`` = the mark) makes it real again.
Every user-facing list leaves tests out (S7b: ``tests/test_test_flag_surfaces.py`` enumerates them);
a record page stays reachable by id. The account data export (GDPR) keeps everything.

The submit gates (daily cap, grant, ``assistant_settings._applied_after``) keep their own, stricter
rule (a mark recorded after a submit gives no slot back) and do not read this module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import settings

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase


_TEST_SQL = (
    "EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = {app_id} "
    "AND s.user_id = {user_id} AND s.event_type = ? AND NOT EXISTS "
    "(SELECT 1 FROM application_events c WHERE c.corrects_event_id = s.id))"
)


def TEST_ID_SQL(app_id: str, user_id: str) -> tuple[str, list[Any]]:  # noqa: N802 - pairs with TEST_APP_SQL
    """``(sql, params)``: true when the application whose id is the column ``app_id`` (owned by the
    column ``user_id``) is a test. For rows that only carry an ``application_id`` (a receipt, a contact,
    an event); a NULL id is never a test. Both are constant column names from our own SQL."""
    return _TEST_SQL.format(app_id=app_id, user_id=user_id), [settings.SIMULATION_EVENT_TYPE]


def TEST_APP_SQL(alias: str) -> tuple[str, list[Any]]:  # noqa: N802 - the name the plan uses
    """``(sql, params)``: true when the ``applications`` row ``alias`` is a test. ``alias`` is a
    constant table name or alias from our own SQL, never caller text."""
    return TEST_ID_SQL(f"{alias}.id", f"{alias}.user_id")


async def test_app_ids(db: JobDatabase, user_id: str) -> set[int]:
    """The ids of this user's test applications (usually none)."""
    sql, params = TEST_APP_SQL("a")
    cur = await db._db.execute(f"SELECT a.id FROM applications a WHERE a.user_id = ? AND {sql}", [user_id, *params])  # noqa: S608
    return {int(r[0]) for r in await cur.fetchall()}
