"""S11b (owner 2026-10-29): the user marks one person as a duplicate of another, from the web, with Undo.

The mark is an appended ``contact_edits`` row (field ``duplicate_of``); the base row is never touched
(S12). A marked person leaves list_people (so known_people), the Needs-you count and check_outreach
(stop), and stays in the GDPR export. Only the user's web session marks or unmarks.
Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

_AD = {
    "title": "Platform Engineer", "company": "Larkspur", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://larkspur.example/careers/9", "description": "Kubernetes and Go.",
}


def _edits(contact_id: int) -> list[tuple[str, str, str]]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT field, value, recorded_by FROM contact_edits WHERE contact_id = ? ORDER BY id", (contact_id,),
        ).fetchall()
    return [(r["field"], r["value"], r["recorded_by"]) for r in rows]


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _person(client: AsyncClient, name: str, app_id: int | None, linkedin: str) -> int:
    url = "/api/contacts" if app_id is None else f"/api/applications/{app_id}/contacts"
    resp = await client.post(url, json={"name": name, "company": "Larkspur", "found_via": "event",
                                        "linkedin_url": linkedin})
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return int(body["contact"]["id"] if "contact" in body else body["id"])


async def _draft(client: AsyncClient, cid: int) -> int:
    msg = await client.post(f"/api/contacts/{cid}/outreach",
                            json={"entry": "message", "channel": "email", "text": "Hello there"})
    assert msg.status_code == 201, msg.text
    return int(msg.json()["outreach"]["id"])


async def _names(client: AsyncClient, **params: Any) -> list[str]:
    resp = await client.get("/api/people", params=params)
    assert resp.status_code == 200, resp.text
    return sorted(p["name"] for p in resp.json()["people"])


@pytest.mark.asyncio
async def test_a_marked_duplicate_leaves_people_and_undo_brings_it_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        kept = await _person(client, "Lucas Pecorino", None, "https://www.linkedin.com/in/lucas-p")
        twin = await _person(client, "Lucas P.", None, "https://www.linkedin.com/in/lucas-pecorino")
        assert await _names(client) == ["Lucas P.", "Lucas Pecorino"]

        marked = await client.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": kept})
        assert marked.status_code == 200, marked.text
        assert marked.json()["duplicate_of"] == kept
        assert await _names(client) == ["Lucas Pecorino"]
        dups = (await client.get("/api/people/duplicates")).json()["duplicates"]
        assert dups == [{"contact_id": twin, "name": "Lucas P.", "duplicate_of": kept,
                         "duplicate_of_name": "Lucas Pecorino"}]
        one = (await client.get("/api/people", params={"contact_id": twin})).json()["person"]
        assert one["duplicate_of"] == kept  # the full record still answers, with the mark

        undo = await client.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": None})
        assert undo.status_code == 200, undo.text
        assert undo.json()["duplicate_of"] is None
        assert await _names(client) == ["Lucas P.", "Lucas Pecorino"]
        assert (await client.get("/api/people/duplicates")).json()["duplicates"] == []
        again = await client.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": None})
        assert again.status_code == 409, again.text
        # Append-only: a mark and its Undo are two rows; the base row is untouched.
        assert _edits(twin) == [("duplicate_of", str(kept), "web"), ("duplicate_of", "", "web")]


@pytest.mark.asyncio
async def test_the_kept_person_must_be_the_users_own_and_another_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _person(client, "Alex Stoker", None, "https://www.linkedin.com/in/alex-stoker")
        b = await _person(client, "Alex S.", None, "https://www.linkedin.com/in/alex-s")
        c = await _person(client, "Alex Stoker (2)", None, "https://www.linkedin.com/in/alex-stoker-2")
        self_mark = await client.post(f"/api/contacts/{a}/duplicate", json={"duplicate_of": a})
        assert self_mark.status_code == 422, self_mark.text
        missing = await client.post(f"/api/contacts/{a}/duplicate", json={"duplicate_of": 987654321})
        assert missing.status_code == 404, missing.text
        assert (await client.post(f"/api/contacts/{b}/duplicate", json={"duplicate_of": a})).status_code == 200
        chain = await client.post(f"/api/contacts/{c}/duplicate", json={"duplicate_of": b})
        assert chain.status_code == 422, chain.text
        kept_marked = await client.post(f"/api/contacts/{a}/duplicate", json={"duplicate_of": c})
        assert kept_marked.status_code == 422, kept_marked.text
        assert _edits(a) == [] and _edits(c) == []
        with pgsync.connect(str(settings.DB_PATH)) as conn:  # another user's person (rule #12)
            conn.execute("INSERT INTO users (id, email, password_hash) VALUES ('s11b-x', 's11b@x.example', 'h')")
            stranger = conn.execute(
                "INSERT INTO application_contacts (user_id, application_id, name, added_by, created_at) "
                "VALUES ('s11b-x', NULL, 'Their Person', 'web', '2026-10-29') RETURNING id"
            ).fetchone()[0]
            conn.commit()
        foreign = await client.post(f"/api/contacts/{c}/duplicate", json={"duplicate_of": stranger})
        assert foreign.status_code == 404, foreign.text
        theirs = await client.post(f"/api/contacts/{stranger}/duplicate", json={"duplicate_of": a})
        assert theirs.status_code == 404, theirs.text


@pytest.mark.asyncio
async def test_an_assistant_may_not_mark_or_unmark_and_never_gets_a_duplicate_to_message(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        brought = await client.post("/api/jobs/bring", json=_AD)
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        kept = await _person(client, "Jo Recruiter", app_id, "https://www.linkedin.com/in/jo-r")
        twin = await _person(client, "Jo R.", None, "https://www.linkedin.com/in/jo-recruiter")
        assert (await client.patch(f"/api/contacts/{twin}", json={"email": "jo@larkspur.example"})).status_code == 200
        message_id = await _draft(client, twin)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with _agent(token) as agent:
            refused = await agent.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": kept})
            assert refused.status_code == 403, refused.text
            assert (await agent.get("/api/people/duplicates")).status_code == 403
            extra = await agent.patch(f"/api/contacts/{twin}", json={"duplicate_of": kept})
            assert extra.status_code == 422, extra.text  # update_contact has no such field
        assert (await client.get("/api/needs-you-count")).json()["other"] == 1
        assert (await client.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": kept})).status_code == 200
        assert (await client.get("/api/needs-you-count")).json()["other"] == 0
        async with _agent(token) as agent:
            check = await agent.get(f"/api/contacts/{twin}/outreach-check", params={"message_id": message_id})
            assert check.status_code == 200, check.text
            assert (check.json()["decision"], check.json()["reason"]) == ("stop", "duplicate_contact")
            assert await _names(agent) == ["Jo Recruiter"]
            assert await _names(agent, company="Larkspur") == ["Jo Recruiter"]
            undo = await agent.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": None})
            assert undo.status_code == 403, undo.text
        assert _edits(twin)[-1] == ("duplicate_of", str(kept), "web")


@pytest.mark.asyncio
async def test_a_marked_duplicate_stays_in_the_data_export(authenticated_async_context):
    async with authenticated_async_context() as client:
        kept = await _person(client, "Sam Keep", None, "https://www.linkedin.com/in/sam-keep")
        twin = await _person(client, "Sam Twin", None, "https://www.linkedin.com/in/sam-twin")
        assert (await client.post(f"/api/contacts/{twin}/duplicate", json={"duplicate_of": kept})).status_code == 200
        export = await client.get("/api/auth/users/me/export")
        assert export.status_code == 200, export.text
        body = export.json()
        assert {r["id"] for r in body["application_contacts"]} == {kept, twin}
        assert [(r["contact_id"], r["field"], r["value"]) for r in body["contact_edits"]] == [
            (twin, "duplicate_of", str(kept)),
        ]
