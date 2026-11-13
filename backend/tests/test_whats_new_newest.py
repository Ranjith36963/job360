"""``GET /api/whats-new?newest=true`` returns the NEWEST events, so a box that shows the last 12 reads one page.

2026-11-13, production: Home's "What your assistant did" followed up to 25 oldest-first pages (~10 s) to
reach the newest rows. ``newest=true`` picks them newest first in SQL (``ORDER BY id DESC LIMIT``), hands the
page back oldest first (the order every caller reads) and names ``next_before_id`` to continue backwards.
The default call must stay byte-identical (the MCP ``whats_new`` tool passes nothing new).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import browser as browser_routes

SINCE = "2000-01-01T00:00:00+00:00"
N_NOTES = 9


async def _bring(client: AsyncClient, n: int, test: bool = False) -> int:
    ad = {"title": f"Engineer {n}", "company": f"Co{n}", "description": "Build things.",
          "apply_url": f"https://co{n}.example/careers/{n}", "test": test}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


async def _notes(client: AsyncClient, app_id: int, n: int) -> None:
    for i in range(n):
        r = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "note", "detail": f"n{i}"})
        assert r.status_code == 201, r.text


async def _default_walk(client: AsyncClient) -> list[dict[str, Any]]:
    """The oracle: every event for SINCE, the default oldest-first way, page by page (limit 3)."""
    out: list[dict[str, Any]] = []
    params: dict[str, Any] = {"since": SINCE, "limit": 3}
    for _ in range(100):
        body = (await client.get("/api/whats-new", params=params)).json()
        out.extend(body["events"])
        if not body["truncated"]:
            return out
        params = {"since": body["next_since"], "after_id": body["next_after_id"], "limit": 3}
    raise AssertionError("the default walk never ended")


@pytest.mark.asyncio
async def test_newest_page_is_the_k_highest_ids_in_ascending_order(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, N_NOTES)
        oracle = [e["id"] for e in await _default_walk(client)]
        assert len(oracle) > 5
        resp = await client.get("/api/whats-new", params={"since": SINCE, "limit": 5, "newest": "true"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["id"] for e in body["events"]] == sorted(oracle)[-5:]
        assert body["truncated"] is True
        assert body["next_before_id"] == min(e["id"] for e in body["events"])
        assert [a["id"] for a in body["applications"]] == [app_id]


@pytest.mark.asyncio
async def test_before_id_walks_the_whole_set_backwards_without_gap_or_repeat(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, N_NOTES)
        oracle = sorted(e["id"] for e in await _default_walk(client))
        seen: list[int] = []
        params: dict[str, Any] = {"since": SINCE, "limit": 4, "newest": "true"}
        last: dict[str, Any] = {}
        for _ in range(20):
            last = (await client.get("/api/whats-new", params=params)).json()
            page = [e["id"] for e in last["events"]]
            assert page == sorted(page)
            seen = page + seen  # older pages go in front
            if not last["truncated"]:
                break
            params = {**params, "before_id": last["next_before_id"]}
        assert seen == oracle
        assert last["truncated"] is False


@pytest.mark.asyncio
async def test_default_call_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, 4)
        oracle = [e["id"] for e in await _default_walk(client)]
        body = (await client.get("/api/whats-new", params={"since": SINCE, "limit": 200})).json()
        assert [e["id"] for e in body["events"]] == oracle  # oldest first, all of them
        assert body["next_before_id"] is None
        assert body["truncated"] is False
        assert {"now", "since", "events", "applications", "next_since", "next_after_id", "truncated",
                "open_asks", "proof_missing", "interviews", "inbox_items", "inbox_page"} <= set(body)
        short = (await client.get("/api/whats-new", params={"since": SINCE, "limit": 2})).json()
        assert [e["id"] for e in short["events"]] == oracle[:2]  # still the OLDEST two
        assert short["truncated"] is True and short["next_before_id"] is None


@pytest.mark.asyncio
async def test_another_users_events_never_appear(authenticated_async_context):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from tests._signin import sign_in

    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, 3)
        sync_client = TestClient(app)
        cookie, _ = sign_in(sync_client, "other-newest@example.com")
        sync_client.close()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
        ) as other:
            other_app = await _bring(other, 7)
            await _notes(other, other_app, 3)
            theirs = (await other.get("/api/whats-new", params={"since": SINCE, "limit": 200, "newest": "true"})).json()
            assert {e["application_id"] for e in theirs["events"]} == {other_app}
        body = (await client.get("/api/whats-new", params={"since": SINCE, "limit": 200, "newest": "true"})).json()
        assert body["events"] and {e["application_id"] for e in body["events"]} == {app_id}


@pytest.mark.asyncio
async def test_a_test_application_is_left_out_exactly_as_by_default(authenticated_async_context):
    async with authenticated_async_context() as client:
        real = await _bring(client, 1)
        fake = await _bring(client, 2, test=True)
        await _notes(client, real, 3)
        await _notes(client, fake, 3)
        default = {e["id"] for e in await _default_walk(client)}
        newest = (await client.get("/api/whats-new", params={"since": SINCE, "limit": 200, "newest": "true"})).json()
        assert {e["id"] for e in newest["events"]} == default
        assert fake not in {e["application_id"] for e in newest["events"]}


@pytest.mark.asyncio
async def test_before_id_without_newest_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/whats-new", params={"since": SINCE, "before_id": 5})
        assert resp.status_code == 422
        assert "newest" in str(resp.json()["detail"])


async def _statements(db: Any, call: Any) -> int:
    real = db._db.execute  # noqa: SLF001
    seen: list[str] = []

    async def counting(sql: str, params: Any = ()) -> Any:
        seen.append(sql)
        return await real(sql, params)

    db._db.execute = counting  # noqa: SLF001
    try:
        await call()
    finally:
        db._db.execute = real  # noqa: SLF001
    return len(seen)


@pytest.mark.asyncio
async def test_statement_count_does_not_grow_with_the_events(authenticated_async_context):
    from src.services.applications import spine

    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, 3)
    async with browser_routes._db() as db:  # noqa: SLF001
        small = await _statements(db, lambda: spine.whats_new(db, user_id, since=SINCE, limit=5, newest=True))
    async with authenticated_async_context() as client:
        await _notes(client, app_id, 30)
        for n in range(2, 6):
            await _notes(client, await _bring(client, n), 5)
    async with browser_routes._db() as db:  # noqa: SLF001
        large = await _statements(db, lambda: spine.whats_new(db, user_id, since=SINCE, limit=5, newest=True))
    assert small == large, f"{small} statements for 4 events, {large} for many more"


async def _walk(client: AsyncClient, since: str, after_id: int | None = None) -> list[dict[str, Any]]:
    """The oracle for any (since, after_id): the default oldest-first pages, followed to the end."""
    out: list[dict[str, Any]] = []
    params: dict[str, Any] = {"since": since, "limit": 3}
    if after_id is not None:
        params["after_id"] = after_id
    for _ in range(100):
        body = (await client.get("/api/whats-new", params=params)).json()
        out.extend(body["events"])
        if not body["truncated"]:
            return out
        params = {"since": body["next_since"], "after_id": body["next_after_id"], "limit": 3}
    raise AssertionError("the default walk never ended")


async def _newest(client: AsyncClient, **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/whats-new", params={"newest": "true", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_exactly_limit_events_is_not_truncated_and_one_more_is(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, 5)
        oracle = sorted(e["id"] for e in await _walk(client, SINCE))
        total = len(oracle)
        exact = await _newest(client, since=SINCE, limit=total)
        assert [e["id"] for e in exact["events"]] == oracle
        assert exact["truncated"] is False
        assert exact["next_before_id"] == oracle[0]
        over = await _newest(client, since=SINCE, limit=total - 1)
        assert over["truncated"] is True
        assert [e["id"] for e in over["events"]] == oracle[1:]  # exactly `limit`, the newest ones
        assert over["next_before_id"] == oracle[1]


@pytest.mark.asyncio
async def test_since_in_the_middle_keeps_only_events_from_then_on(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, N_NOTES)
        everything = await _walk(client, SINCE)
        mid = everything[len(everything) // 2]["recorded_at"]  # a seeded event's own stamp
        oracle = await _walk(client, mid)
        assert 0 < len(oracle) < len(everything)
        assert all(e["recorded_at"] >= mid for e in oracle)
        k = 3
        page = await _newest(client, since=mid, limit=k)
        assert [e["id"] for e in page["events"]] == [e["id"] for e in oracle][-k:]
        seen: list[dict[str, Any]] = []
        params: dict[str, Any] = {"since": mid, "limit": 4}
        for _ in range(20):
            body = await _newest(client, **params)
            seen = body["events"] + seen
            if not body["truncated"]:
                break
            params = {**params, "before_id": body["next_before_id"]}
        assert [e["id"] for e in seen] == [e["id"] for e in oracle]
        assert all(e["recorded_at"] >= mid for e in seen), "an event older than `since` came back"


@pytest.mark.asyncio
async def test_newest_with_after_id_is_the_newest_k_of_the_default_answer(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, N_NOTES)
        everything = await _walk(client, SINCE)
        boundary = everything[2]
        oracle = await _walk(client, boundary["recorded_at"], boundary["id"])
        assert len(oracle) > 3
        k = 3
        page = await _newest(client, since=boundary["recorded_at"], after_id=boundary["id"], limit=k)
        assert [e["id"] for e in page["events"]] == [e["id"] for e in oracle][-k:]
        assert page["truncated"] is True
        whole = await _newest(client, since=boundary["recorded_at"], after_id=boundary["id"], limit=200)
        assert [e["id"] for e in whole["events"]] == [e["id"] for e in oracle]
        assert whole["truncated"] is False


@pytest.mark.asyncio
async def test_no_matching_events_gives_an_empty_untruncated_page(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _notes(client, app_id, 3)
        body = await _newest(client, since="2999-01-01T00:00:00+00:00", limit=5)
        assert body["events"] == []
        assert body["truncated"] is False
        assert body["next_before_id"] is None
