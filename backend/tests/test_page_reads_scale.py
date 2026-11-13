"""The pages every visit loads must cost a FIXED number of database round trips, whatever the account size.

2026-11-12, production: on a large account ``GET /api/applications?limit=200`` made about 434 statements
(two COUNTs per row), ``GET /api/asks?status=open`` one read per open ask, and the menu badge
(``/api/needs-you-count``, on every page) about 40. The backend is one worker, so each round trip adds up
and blocks everyone. Timing is not asserted (it flakes): the statement COUNT is, and it must not grow with
the number of rows (the same count for 2 applications as for 6).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.api.routes import browser as browser_routes
from src.services.applications import asks as asks_service
from src.services.applications import blockers as blockers_service
from src.services.applications import needs_you_count as nyc
from src.services.applications import spine
from src.services.profile import storage
from tests._cv_review import owners_turn

_AD = {"company": "Northwind", "description": "Build things."}


async def _bring(client: Any, start: int, n: int) -> list[int]:
    ids = []
    for i in range(start, start + n):
        resp = await client.post(
            "/api/jobs/bring",
            json={**_AD, "title": f"Engineer {i}", "apply_url": f"https://northwind.example/c/{i}"},
        )
        assert resp.status_code in (200, 201), resp.text
        app_id = int(resp.json()["application_id"])
        ids.append(app_id)
        for event_type, payload in (("note", {}), ("form_filled", {"form_url": "https://northwind.example/f",
                                                                  "fields_count": 1, "answers": []})):
            sent = await client.post(
                f"/api/applications/{app_id}/events", json={"event_type": event_type, "detail": "n", "payload": payload}
            )
            assert sent.status_code == 201, sent.text
        cv = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": f"CV {i}"})
        assert cv.status_code == 201, cv.text
        ask = await client.post("/api/asks", json={"question": f"Notice period {i}?", "application_id": app_id})
        assert ask.status_code in (200, 201), ask.text
    return ids


async def _statements(db: Any, call: Any) -> tuple[int, Any]:
    """How many statements ``call`` sends through the async door, and its answer."""
    real = db._db.execute  # noqa: SLF001
    seen: list[str] = []

    async def counting(sql: str, params: Any = ()) -> Any:
        seen.append(sql)
        return await real(sql, params)

    db._db.execute = counting  # noqa: SLF001
    try:
        out = await call()
    finally:
        db._db.execute = real  # noqa: SLF001
    return len(seen), out


async def _grow(authenticated_async_context: Any, call: Any) -> tuple[tuple[int, Any], tuple[int, Any]]:
    """Run ``call(db, user_id)`` on 2 applications, then on 6: (statements, answer) for each."""
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        await _bring(client, 0, 2)
    async with browser_routes._db() as db:  # noqa: SLF001
        small = await _statements(db, lambda: call(db, user_id))
    async with authenticated_async_context() as client:
        await _bring(client, 2, 4)
    async with browser_routes._db() as db:  # noqa: SLF001
        large = await _statements(db, lambda: call(db, user_id))
    return small, large


@pytest.mark.asyncio
async def test_list_applications_reads_the_counts_once_per_page(authenticated_async_context):
    (small_n, small), (large_n, large) = await _grow(
        authenticated_async_context, lambda db, uid: spine.list_applications(db, uid, limit=50)
    )
    assert len(small["applications"]) == 2 and len(large["applications"]) == 6
    for row in large["applications"]:
        # bring + note: the counts are real values, not a default (rule #21)
        assert row["events"] >= 2
        assert row["artifacts"] == {"cv": 1}
    assert large_n == small_n


@pytest.mark.asyncio
async def test_list_applications_counts_match_one_row_reads(authenticated_async_context):
    (_, _), (_, large) = await _grow(
        authenticated_async_context, lambda db, uid: spine.list_applications(db, uid, limit=50)
    )
    async with browser_routes._db() as db:  # noqa: SLF001
        for row in large["applications"]:
            cur = await db._db.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM application_events WHERE application_id = ?", (row["id"],)
            )
            assert row["events"] == int((await cur.fetchone())[0])
            cur = await db._db.execute(  # noqa: SLF001
                "SELECT kind, COUNT(*) FROM application_artifacts WHERE application_id = ? GROUP BY kind", (row["id"],)
            )
            assert row["artifacts"] == {r[0]: int(r[1]) for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_tidy_open_asks_reads_the_asks_in_one_query(authenticated_async_context):
    (small_n, small), (large_n, large) = await _grow(authenticated_async_context, asks_service.tidy_open_asks)
    assert small == 0 and large == 0  # one ask per job: nothing to tidy, the answer is unchanged
    assert large_n == small_n


@pytest.mark.asyncio
async def test_needs_you_count_round_trips_do_not_grow(authenticated_async_context):
    (small_n, small), (large_n, large) = await _grow(authenticated_async_context, nyc.needs_you_count)
    assert sum(small["by_actor"].values()) == 2 and sum(large["by_actor"].values()) == 6  # every job counted once
    assert large_n == small_n


@pytest.mark.asyncio
async def test_blockers_share_one_profile_read(authenticated_async_context, monkeypatch):
    """The answer flags and the CV-review rule share ONE profile read; the score line keeps its own (its
    ``spine.user_score_line`` seam is what test_blockers.py pins). Each read is a fresh sync connection, and
    it was three of them on every page's menu badge."""
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        ids = await _bring(client, 0, 3)
        for app_id in ids:
            await owners_turn(user_id, app_id)  # reviews spent: approve_cv is the user's (owner 2026-11-13)
    real = storage.load_profile_with_overlay
    reads: list[str] = []

    def counting(uid: str, **kw: Any) -> Any:
        reads.append(uid)
        return real(uid, **kw)

    monkeypatch.setattr(storage, "load_profile_with_overlay", counting)
    async with browser_routes._db() as db:  # noqa: SLF001
        found = await blockers_service.blockers_for(db, user_id, ids)
    kinds = {b["kind"] for lst in found.values() for b in lst}
    assert kinds == {"approve_cv", "answers", "send_yes", "assistant_step"}  # every profile reader ran
    assert reads == [user_id, user_id]


@pytest.mark.asyncio
async def test_queued_page_reads_the_receipts_once(authenticated_async_context):
    """The duplicate check's receipts read is the same for every row: the queued page shares ONE."""
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        ids = await _bring(client, 0, 4)
        for app_id in ids:
            queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
            assert queued.status_code == 201, queued.text
    async with browser_routes._db() as db:  # noqa: SLF001
        real = db._db.execute  # noqa: SLF001
        seen: list[str] = []

        async def counting(sql: str, params: Any = ()) -> Any:
            seen.append(sql)
            return await real(sql, params)

        db._db.execute = counting  # noqa: SLF001
        try:
            page = await spine.list_applications(db, user_id, queued=True, limit=50)
        finally:
            db._db.execute = real  # noqa: SLF001
    assert sorted(r["id"] for r in page["applications"]) == sorted(ids)
    assert [r["preflight"]["duplicate_flag"] for r in page["applications"]] == ["", "", "", ""]
    assert sum(1 for s in seen if "FROM application_receipts r LEFT JOIN applications a" in s) == 1
