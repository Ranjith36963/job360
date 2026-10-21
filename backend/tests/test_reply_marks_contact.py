"""A `replied` event whose email sender is a contact we sent outreach to on the SAME
application also marks that person replied (so the card stops saying "No reply yet").
Append-only: a new outreach_replied entry; old events are never edited."""
from __future__ import annotations

import pytest

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _setup(client, *, sent: bool):
    app_id = (await client.post("/api/jobs/bring", json=_AD)).json()["application_id"]
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": "Priya Shah", "role": "Recruiter", "email": "priya@northwind.example"},
    )
    contact_id = added.json()["contact"]["id"]
    if sent:
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_sent", "contact_id": contact_id, "channel": "email"},
        )
        assert r.status_code == 201, r.text
    return app_id, contact_id


async def _reply(client, app_id, sender, message_id="m1"):
    return await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "replied", "source": {
            "kind": "email", "message_id": message_id, "sender": sender, "subject": "Re: role",
        }},
    )


async def _replies(client, contact_id):
    person = await client.get(f"/api/people?contact_id={contact_id}")
    assert person.status_code == 200, person.text
    return person.json()["person"]["outreach"]["replies"]


@pytest.mark.asyncio
async def test_reply_from_contacted_person_marks_contact_replied(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, contact_id = await _setup(client, sent=True)
        resp = await _reply(client, app_id, "Priya Shah <Priya@Northwind.example>")
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "replied"
        assert len(await _replies(client, contact_id)) == 1
        # re-reading the same email adds nothing
        again = await _reply(client, app_id, "priya@northwind.example")
        assert again.status_code in (200, 201)
        assert len(await _replies(client, contact_id)) == 1


@pytest.mark.asyncio
async def test_reply_from_unknown_or_unsent_contact_marks_nobody(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, contact_id = await _setup(client, sent=False)
        assert (await _reply(client, app_id, "priya@northwind.example")).status_code == 201
        assert await _replies(client, contact_id) == []
        assert (await _reply(client, app_id, "someone@else.example", "m2")).status_code == 201
        assert await _replies(client, contact_id) == []
