"""Outreach pack #2 — no-email identity, ``reachable`` and first-class ``source_url``.

Without an email, ``add_contact`` looks for the same person in the same scope
(the application when linked, the user's cold contacts when not): first by the
normalised CURRENT linkedin_url, then by CURRENT name (linked: unless both
sides carry different LinkedIn URLs; cold: only when neither side has one).
``reachable`` is derived (CURRENT email or linkedin_url saved), never stored.
``source_url`` is stored only, never fetched (M2).

Helpers are copied from ``test_outreach_people.py``, never imported (importing
across test modules breaks per-test schema isolation).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_person(client: AsyncClient, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", **body}
    return await client.post("/api/contacts", json=payload)


async def _add_contact(client: AsyncClient, application_id: int, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", **body}
    return await client.post(f"/api/applications/{application_id}/contacts", json=payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://www.LinkedIn.com/in/Priya-Shah/?trk=abc#x", "https://linkedin.com/in/priya-shah"),
        ("https://uk.linkedin.com/in/priya-shah", "https://linkedin.com/in/priya-shah"),
        ("https://linkedin.com/in/priya-shah///", "https://linkedin.com/in/priya-shah"),
        ("  ", ""),
        ("https://Example.org/Team/", "https://example.org/team"),
    ],
)
def test_linkedin_identity_key_normalises(raw, expected):
    from src.services.applications.contacts import linkedin_identity_key

    assert linkedin_identity_key(raw) == expected


@pytest.mark.asyncio
async def test_same_linkedin_without_email_is_the_same_contact(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _add_contact(
            client, app_id, name="Priya Shah", linkedin_url="https://www.linkedin.com/in/Priya-Shah/"
        )
        assert first.status_code == 201, first.text
        again = await _add_contact(
            client, app_id, name="P. Shah", linkedin_url="http://linkedin.com/in/priya-shah?trk=public"
        )
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["already_existed"] is True
        assert body["matched_on"] == "linkedin_url"
        assert body["contact_id"] == first.json()["contact_id"]
        assert body["event_id"] is None
        assert body["contact"]["name"] == "Priya Shah"
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert len(detail["contacts"]) == 1
        assert len([e for e in detail["events"] if e["event_type"] == "contact_added"]) == 1


@pytest.mark.asyncio
async def test_same_name_without_email_or_url_is_the_same_contact(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _add_contact(client, app_id, name="Dana Whitfield")
        assert first.status_code == 201
        again = await _add_contact(client, app_id, name="  dana   WHITFIELD ")
        assert again.status_code == 200, again.text
        assert again.json()["matched_on"] == "name"
        assert again.json()["contact_id"] == first.json()["contact_id"]


@pytest.mark.asyncio
async def test_same_name_with_a_different_linkedin_is_a_new_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        a = await _add_contact(client, app_id, name="Sam Lee", linkedin_url="https://linkedin.com/in/sam-lee-1")
        b = await _add_contact(client, app_id, name="Sam Lee", linkedin_url="https://linkedin.com/in/sam-lee-2")
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["contact_id"] != b.json()["contact_id"]
        c = await _add_contact(client, app_id, name="Sam Lee")
        assert c.status_code == 200, c.text
        assert c.json()["matched_on"] == "name"
        assert c.json()["contact_id"] == a.json()["contact_id"]


@pytest.mark.asyncio
async def test_name_match_fills_nothing_in(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _add_contact(client, app_id, name="Ola Nordmann")
        assert first.status_code == 201
        again = await _add_contact(client, app_id, name="Ola Nordmann", linkedin_url="https://linkedin.com/in/ola-n")
        assert again.status_code == 200, again.text
        assert again.json()["matched_on"] == "name"
        assert again.json()["contact"]["linkedin_url"] == ""


@pytest.mark.asyncio
async def test_dedupe_reads_current_values_through_contact_edits(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        r = await _add_contact(client, app_id, name="Ravi Kumar", linkedin_url="https://linkedin.com/in/ravi-old")
        assert r.status_code == 201
        rid = r.json()["contact_id"]
        patched = await client.patch(f"/api/contacts/{rid}", json={"linkedin_url": "https://linkedin.com/in/ravi-new"})
        assert patched.status_code == 200, patched.text

        other = await _add_contact(
            client, app_id, name="Someone Else", linkedin_url="https://linkedin.com/in/ravi-old"
        )
        assert other.status_code == 201, other.text
        assert other.json()["contact_id"] != rid

        found = await _add_contact(client, app_id, name="R. Kumar", linkedin_url="https://linkedin.com/in/ravi-new")
        assert found.status_code == 200, found.text
        assert found.json()["contact_id"] == rid
        assert found.json()["matched_on"] == "linkedin_url"

        renamed = await client.patch(f"/api/contacts/{rid}", json={"name": "Ravindra Kumar"})
        assert renamed.status_code == 200, renamed.text
        by_name = await _add_contact(client, app_id, name="ravindra kumar")
        assert by_name.status_code == 200, by_name.text
        assert by_name.json()["contact_id"] == rid
        assert by_name.json()["matched_on"] == "name"


@pytest.mark.asyncio
async def test_deferred_email_row_matches_by_name_and_is_reachable(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _add_person(client, email="stale-ci@x.example")
        first_id = first.json()["contact_id"]
        moved = await client.patch(f"/api/contacts/{first_id}", json={"email": "moved-ci@x.example"})
        assert moved.status_code == 200, moved.text
        deferred = await _add_person(client, email="stale-ci@x.example", name="New Owner")
        assert deferred.status_code == 201, deferred.text
        deferred_id = deferred.json()["contact_id"]

        again = await _add_person(client, name="New Owner")
        assert again.status_code == 200, again.text
        assert again.json()["contact_id"] == deferred_id
        assert again.json()["matched_on"] == "name"

        people = (await client.get("/api/people?email=stale-ci@x.example")).json()["people"]
        assert len(people) == 1 and people[0]["reachable"] is True

        nobody = await _add_person(client, name="Nobody Known")
        assert nobody.status_code == 201
        groups = (await client.get("/api/applications/stats")).json()["by_contact_found_via"]
        unset = next(g for g in groups if g["key"] is None)
        assert unset["contacts"] == 3
        assert unset["reachable"] == 2


@pytest.mark.asyncio
async def test_cold_and_linked_scopes_dedupe_separately(authenticated_async_context):
    async with authenticated_async_context() as client:
        app1 = await _bring(client)
        app2 = await _bring(client, {**_AD, "title": "Platform Engineer", "company": "Southwind"})
        cold = await _add_person(client, name="Priya Shah")
        assert cold.status_code == 201
        linked = await _add_contact(client, app1, name="Priya Shah")
        assert linked.status_code == 201
        assert linked.json()["contact_id"] != cold.json()["contact_id"]
        cold_again = await _add_person(client, name="Priya Shah")
        assert cold_again.status_code == 200
        assert cold_again.json()["contact_id"] == cold.json()["contact_id"]
        other_app = await _add_contact(client, app2, name="Priya Shah")
        assert other_app.status_code == 201
        assert other_app.json()["contact_id"] not in (cold.json()["contact_id"], linked.json()["contact_id"])


@pytest.mark.asyncio
async def test_cold_name_match_needs_no_linkedin_on_either_side(authenticated_async_context):
    async with authenticated_async_context() as client:
        s = await _add_person(client, name="Sam Lee")
        assert s.status_code == 201
        with_url = await _add_person(client, name="Sam Lee", linkedin_url="https://linkedin.com/in/sam-lee-9")
        assert with_url.status_code == 201, with_url.text
        assert with_url.json()["contact_id"] != s.json()["contact_id"]
        same_url = await _add_person(client, name="Sam Lee", linkedin_url="https://linkedin.com/in/sam-lee-9")
        assert same_url.status_code == 200
        assert same_url.json()["matched_on"] == "linkedin_url"
        assert same_url.json()["contact_id"] == with_url.json()["contact_id"]
        bare = await _add_person(client, name="sam lee")
        assert bare.status_code == 200
        assert bare.json()["matched_on"] == "name"
        assert bare.json()["contact_id"] == s.json()["contact_id"]


@pytest.mark.asyncio
async def test_replay_at_the_cap_is_200_not_409(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_PER_APPLICATION_MAX", 1)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _add_contact(client, app_id, name="Cap Person")).status_code == 201
        replay = await _add_contact(client, app_id, name="Cap Person")
        assert replay.status_code == 200 and replay.json()["already_existed"] is True
        over = await _add_contact(client, app_id, name="Other Person")
        assert over.status_code == 409
        assert "CONTACTS_PER_APPLICATION_MAX" in over.json()["detail"]


@pytest.mark.asyncio
async def test_replay_never_spends_the_rate_limit(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        assert (await _add_person(client, name="Rate Person")).status_code == 201
        for _ in range(3):
            assert (await _add_person(client, name="Rate Person")).status_code == 200
        assert (await _add_person(client, name="New Person")).status_code == 429


@pytest.mark.asyncio
async def test_concurrent_no_email_adds_same_linkedin_never_two_rows(authenticated_async_context):
    from src.services.applications.contacts import linkedin_identity_key

    url = "https://linkedin.com/in/race-li"
    async with authenticated_async_context() as client_a, authenticated_async_context() as client_b:
        results = await asyncio.gather(
            _add_person(client_a, name="A", linkedin_url=url),
            _add_person(client_b, name="B", linkedin_url=url),
        )
        assert sorted(r.status_code for r in results) == [200, 201], [r.text for r in results]
        winner = next(r for r in results if r.status_code == 201)
        loser = next(r for r in results if r.status_code == 200)
        assert loser.json()["contact_id"] == winner.json()["contact_id"]
        assert loser.json()["matched_on"] == "linkedin_url"
        people = (await client_a.get("/api/people")).json()["people"]
        assert len([p for p in people if linkedin_identity_key(p["linkedin_url"]) == url]) == 1


@pytest.mark.asyncio
async def test_list_people_reachable_and_source_url(authenticated_async_context):
    async with authenticated_async_context() as client:
        has_url = await _add_person(
            client, name="Has Url", linkedin_url="https://linkedin.com/in/has-url",
            source_url="https://northwind.example/team",
        )
        assert has_url.status_code == 201, has_url.text
        assert (await _add_person(client, name="No Way")).status_code == 201
        assert (await _add_person(client, name="Mail Only", email="mail@x.example")).status_code == 201

        people = {p["name"]: p for p in (await client.get("/api/people")).json()["people"]}
        assert people["Has Url"]["reachable"] is True
        assert people["Has Url"]["source_url"] == "https://northwind.example/team"
        assert people["No Way"]["reachable"] is False and people["No Way"]["source_url"] is None
        assert people["Mail Only"]["reachable"] is True

        one = (await client.get(f"/api/people?contact_id={has_url.json()['contact_id']}")).json()["person"]
        assert one["reachable"] is True and one["source_url"] == "https://northwind.example/team"


@pytest.mark.asyncio
async def test_source_url_add_edit_clear_and_validate(authenticated_async_context, monkeypatch):
    from src.core import settings

    async with authenticated_async_context() as client:
        added = await _add_person(client, name="Src One", source_url="https://northwind.example/team")
        assert added.status_code == 201, added.text
        cid = added.json()["contact_id"]
        assert added.json()["contact"]["source_url"] == "https://northwind.example/team"

        edited = await client.patch(f"/api/contacts/{cid}", json={"source_url": "https://northwind.example/press/2022"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["source_url"] == "https://northwind.example/press/2022"
        assert [h["value"] for h in edited.json()["edit_history"]["source_url"]] == [
            "https://northwind.example/team", "https://northwind.example/press/2022",
        ]
        cleared = await client.patch(f"/api/contacts/{cid}", json={"source_url": ""})
        assert cleared.status_code == 200 and cleared.json()["source_url"] is None

        bad = await _add_person(client, name="Src Bad", source_url="javascript:alert(1)")
        assert bad.status_code == 422 and "source_url" in bad.text

        monkeypatch.setattr(settings, "CONTACT_SOURCE_URL_MAX_CHARS", 40)
        long = await _add_person(client, name="Src Long", source_url="https://northwind.example/" + "a" * 40)
        assert long.status_code == 422 and "CONTACT_SOURCE_URL_MAX_CHARS" in long.text

        none = await _add_person(client, name="Src None")
        assert none.status_code == 201 and none.json()["contact"]["source_url"] is None

        monkeypatch.setattr(settings, "CONTACT_SOURCE_URL_MAX_CHARS", 1000)
        url = "https://northwind.example/" + "a" * 700
        kept = await _add_person(client, name="Src Kept", source_url=url)
        assert kept.status_code == 201, kept.text
        assert kept.json()["contact"]["source_url"] == url

        ctrl = await _add_person(client, name="Src Ctrl", source_url="https://northwind.example/a\nb")
        assert ctrl.status_code == 422 and "source_url" in ctrl.text


@pytest.mark.asyncio
async def test_stats_by_contact_found_via_counts_reachable(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ann = await _add_contact(
            client, app_id, name="Ann", found_via="linkedin", linkedin_url="https://linkedin.com/in/ann"
        )
        ben = await _add_contact(client, app_id, name="Ben", found_via="linkedin")
        cy = await _add_contact(client, app_id, name="Cy", found_via="linkedin")
        assert all(r.status_code == 201 for r in (ann, ben, cy))
        patched = await client.patch(
            f"/api/contacts/{cy.json()['contact_id']}", json={"linkedin_url": "https://linkedin.com/in/cy"}
        )
        assert patched.status_code == 200, patched.text
        groups = (await client.get("/api/applications/stats")).json()["by_contact_found_via"]
        group = next(g for g in groups if g["key"] == "linkedin")
        assert group["contacts"] == 3 and group["reachable"] == 2


@pytest.mark.asyncio
async def test_mcp_add_contact_dedupes_and_carries_source_url(authenticated_async_context):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        minted = await client.post("/api/tokens", json={"name": "agent"})
        assert minted.status_code == 201, minted.text
        token = minted.json()["token"]
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            def payload(res: Any) -> dict[str, Any]:
                assert not res.is_error, res.content[0].text
                return json.loads(res.content[0].text)

            first = payload(await mcp.call_tool("add_contact", {
                "name": "Mona Lisa", "linkedin_url": "https://www.linkedin.com/in/mona/",
                "source_url": "https://louvre.example/team",
            }))
            assert first["already_existed"] is False
            assert first["contact"]["source_url"] == "https://louvre.example/team"
            again = payload(await mcp.call_tool("add_contact", {
                "name": "Mona L.", "linkedin_url": "https://linkedin.com/in/mona",
            }))
            assert again["already_existed"] is True and again["matched_on"] == "linkedin_url"
            assert again["contact_id"] == first["contact_id"]
            upd = payload(await mcp.call_tool("update_contact", {
                "contact_id": first["contact_id"], "source_url": "https://louvre.example/press",
            }))
            assert upd["source_url"] == "https://louvre.example/press"
            people = payload(await mcp.call_tool("list_people", {}))["people"]
            assert len(people) == 1
            assert people[0]["reachable"] is True
            assert people[0]["source_url"] == "https://louvre.example/press"


@pytest.mark.asyncio
async def test_add_contact_description_no_longer_promises_duplicates():
    from src.api.mcp_server import build_server

    tools = {t.name: t for t in await build_server().list_tools()}
    desc = tools["add_contact"].description
    assert "Without an email every call makes a new row" not in desc
    assert "matched_on" in desc and "source_url" in desc
