"""The two "newest event per application" reads must stay ONE pass over the events.

2026-11-12, production: ``/api/needs-you-count`` took 6 s on a large account and froze the single
backend worker for every other request. Both ``blocked.open_blocks`` and ``blockers._events_by_app``
found the newest event with a CORRELATED subquery (``e.id = (SELECT MAX(..) WHERE .. = e.application_id)``).
Postgres re-ran it once per matching row, so the cost grew with the SQUARE of the history.

Timing is not asserted (it flakes). The query PLAN is: it must hold no sub-plan at all, so the
newest-per-application step is a single grouped scan joined back by primary key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import blockers as blockers_service

_AD = {
    "title": "Engineer",
    "company": "Northwind",
    "description": "Build things.",
    "apply_url": "https://northwind.example/c/0",
}


def _sub_plans(node: Any) -> list[str]:
    """Every sub-plan in an ``EXPLAIN (FORMAT JSON)`` tree (correlated or hashed: both re-read events)."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("Parent Relationship") in ("SubPlan", "InitPlan"):
            found.append(str(node.get("Subplan Name") or node.get("Node Type")))
        for child in node.values():
            found.extend(_sub_plans(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_sub_plans(child))
    return found


async def _plan_of(db: Any, call: Any) -> Any:
    """Run ``call`` with the connection's ``execute`` recorded, then EXPLAIN the event read it made."""
    seen: list[tuple[str, tuple[Any, ...]]] = []
    real = db._db.execute  # noqa: SLF001

    async def recording(sql: str, params: Any = ()) -> Any:
        seen.append((sql, tuple(params)))
        return await real(sql, params)

    db._db.execute = recording  # noqa: SLF001
    try:
        await call()
    finally:
        db._db.execute = real  # noqa: SLF001
    reads = [(s, p) for s, p in seen if "FROM application_events e" in s]
    assert len(reads) == 1, [s for s, _ in seen]
    sql, params = reads[0]
    cur = await real("EXPLAIN (FORMAT JSON) " + sql, params)
    row = await cur.fetchone()
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _seed(authenticated_async_context: Any) -> list[int]:
    ids: list[int] = []
    async with authenticated_async_context() as client:
        for n in range(3):
            resp = await client.post(
                "/api/jobs/bring",
                json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"},
            )
            assert resp.status_code in (200, 201), resp.text
            app_id = int(resp.json()["application_id"])
            ids.append(app_id)
            for event_type, payload in (
                ("form_filled", {"form_url": "https://northwind.example/f", "fields_count": 1}),
                ("blocked", {"reason": "login_needed", "step": "Sign in"}),
                ("form_filled", {"form_url": "https://northwind.example/f", "fields_count": 2}),
            ):
                sent = await client.post(
                    f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload}
                )
                assert sent.status_code == 201, sent.text
    return ids


@pytest.mark.asyncio
async def test_open_blocks_reads_the_events_in_one_pass(authenticated_async_context):
    ids = await _seed(authenticated_async_context)
    user_id = authenticated_async_context.fixture_user_id
    async with browser_routes._db() as db:  # noqa: SLF001
        blocks = await blocked_service.open_blocks(db, user_id, ids)
        assert set(blocks) == set(ids)  # the answer is unchanged: every seeded application is blocked
        plan = await _plan_of(db, lambda: blocked_service.open_blocks(db, user_id, ids))
    assert _sub_plans(plan) == []


@pytest.mark.asyncio
async def test_events_by_app_reads_the_events_in_one_pass(authenticated_async_context):
    ids = await _seed(authenticated_async_context)
    user_id = authenticated_async_context.fixture_user_id
    async with browser_routes._db() as db:  # noqa: SLF001
        by_app = await blockers_service._events_by_app(db, user_id, ids)  # noqa: SLF001
        for app_id in ids:
            filled = [e for e in by_app[app_id] if e["event_type"] == "form_filled"]
            assert [e["payload"]["fields_count"] for e in filled] == [2]  # only the NEWEST form_filled
            assert [e["event_type"] for e in by_app[app_id] if e["event_type"] == "blocked"] == ["blocked"]
        plan = await _plan_of(db, lambda: blockers_service._events_by_app(db, user_id, ids))  # noqa: SLF001
    assert _sub_plans(plan) == []
