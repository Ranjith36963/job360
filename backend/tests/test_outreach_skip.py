"""Skip is saved (owner 2026-10-27): a `skipped` ledger entry on the person's newest drafted note.
Helpers are copied, never imported (per-test schema isolation)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


async def _person_with_note(client: AsyncClient, email: str = "priya@northwind.example") -> tuple[int, int]:
    added = await client.post("/api/contacts", json={"name": "Priya Shah", "role": "Recruiter", "email": email, "company": "Northwind"})
    assert added.status_code == 201, added.text
    cid = int(added.json()["contact"]["id"])
    return cid, await _draft(client, cid)


async def _draft(client: AsyncClient, cid: int, text: str = "Hi Priya, I admire your team.") -> int:
    r = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "message", "channel": "linkedin", "text": text})
    assert r.status_code == 201, r.text
    return int(r.json()["outreach"]["id"])


async def _skip(client: AsyncClient, cid: int):
    return await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "skipped", "channel": "linkedin"})


def _to_reach(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What Needs you lists: a drafted note, not sent, not skipped."""
    return [p for p in people if p["message_count"] > 0 and not p["last_sent"] and not p["skipped"]]


def _sent_counts(node: Any) -> list[int]:
    """Every `outreach_sent` number anywhere in the stats reply."""
    if isinstance(node, dict):
        return [int(v) for k, v in node.items() if k == "outreach_sent" and isinstance(v, int)] + [
            n for v in node.values() for n in _sent_counts(v)
        ]
    if isinstance(node, list):
        return [n for v in node for n in _sent_counts(v)]
    return []


async def _second_user_cookie(email: str = "skipper-two@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    sync_client.close()
    assert cookie
    return cookie


@pytest.mark.asyncio
async def test_skip_hides_the_person_but_keeps_the_note(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid, _ = await _person_with_note(client)
        assert len(_to_reach((await client.get("/api/people")).json()["people"])) == 1
        r = await _skip(client, cid)
        assert r.status_code == 201, r.text
        assert r.json()["outreach"]["entry"] == "skipped"
        people = (await client.get("/api/people")).json()["people"]
        assert _to_reach(people) == []
        assert people[0]["skipped"] is True  # the assistant can see it in list_people
        assert people[0]["message_count"] == 1 and people[0]["last_sent"] is None
        full = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert full["outreach"]["skipped"] is True
        assert full["outreach"]["messages"][0]["text"] == "Hi Priya, I admire your team."


@pytest.mark.asyncio
async def test_skip_is_idempotent(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid, _ = await _person_with_note(client)
        assert (await _skip(client, cid)).status_code == 201
        again = await _skip(client, cid)
        assert again.status_code == 200, again.text
        assert again.json()["already_existed"] is True
        full = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert sum(1 for m in full["outreach"]["messages"]) == 1


@pytest.mark.asyncio
async def test_skip_is_not_a_send(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid, mid = await _person_with_note(client)
        await _skip(client, cid)
        full = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert full["outreach"]["sent"] == [] and full["outreach"]["last_sent"] is None
        stats = await client.get("/api/applications/stats")
        assert stats.status_code == 200, stats.text
        assert sum(_sent_counts(stats.json())) == 0
        check = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})
        assert check.status_code == 200, check.text
        assert "already_sent" not in str(check.json())


@pytest.mark.asyncio
async def test_a_newer_draft_brings_the_person_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid, _ = await _person_with_note(client)
        await _skip(client, cid)
        await _draft(client, cid, "Hi Priya, a fresh note.")
        people = (await client.get("/api/people")).json()["people"]
        assert people[0]["skipped"] is False
        assert len(_to_reach(people)) == 1


@pytest.mark.asyncio
async def test_skip_needs_a_drafted_note_and_takes_no_text(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await client.post("/api/contacts", json={"name": "No Note", "email": "nn@example.com", "company": "Northwind"})
        cid = int(added.json()["contact"]["id"])
        assert (await _skip(client, cid)).status_code == 422
        await _draft(client, cid)
        bad = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "skipped", "channel": "linkedin", "text": "x"}
        )
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_skip_of_another_users_contact_is_404_and_leaves_it_listed(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid, _ = await _person_with_note(client)
        cookie = await _second_user_cookie()
        from src.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
        ) as other:
            assert (await _skip(other, cid)).status_code == 404
            assert (await other.get("/api/people")).json()["people"] == []
        assert len(_to_reach((await client.get("/api/people")).json()["people"])) == 1
