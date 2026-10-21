"""Outreach pack #4 - a contact's company, list_people(company=), known_people.

A contact carries its own optional ``company`` (required when it has no
application). ``known_people`` is a join over the user's OWN stored contacts,
keyed by the one company key (rule #1); it never looks anyone up (product
rules 4/5). Every assertion uses real, non-default values (hard rule 21).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _cold(client: AsyncClient, name: str, company: str, **extra: Any) -> int:
    resp = await client.post("/api/contacts", json={"name": name, "company": company, **extra})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact"]["id"])


async def _linked(client: AsyncClient, app_id: int, name: str, **extra: Any) -> int:
    resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": name, **extra})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact"]["id"])


async def _people(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    resp = await client.get("/api/people", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["people"]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


@pytest.mark.asyncio
async def test_cold_contact_requires_company(authenticated_async_context):
    async with authenticated_async_context() as client:
        bare = await client.post("/api/contacts", json={"name": "Cleo Park", "email": "cleo@contoso.example"})
        assert bare.status_code == 422
        assert "company is required" in bare.json()["detail"]
        unknown = await client.post(
            "/api/contacts", json={"name": "Cleo Park", "email": "cleo@contoso.example", "company": "unknown"}
        )
        assert unknown.status_code == 422
        ok = await client.post(
            "/api/contacts",
            json={"name": "Cleo Park", "email": "cleo@contoso.example", "company": "  Contoso   Ltd "},
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["contact"]["company"] == "Contoso Ltd"


@pytest.mark.asyncio
async def test_company_too_long_is_422(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_COMPANY_MAX_CHARS", 10)
    async with authenticated_async_context() as client:
        resp = await client.post("/api/contacts", json={"name": "Cleo Park", "company": "x" * 11})
        assert resp.status_code == 422
        assert "CONTACT_COMPANY_MAX_CHARS" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_linked_contact_company_is_not_copied_from_job(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD))["application_id"]
        resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Rita Recruiter"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["contact"]["company"] is None


@pytest.mark.asyncio
async def test_update_contact_company_is_an_edit_and_cold_must_keep_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        cold_id = await _cold(client, "Cleo Park", "Contoso")
        upd = await client.patch(f"/api/contacts/{cold_id}", json={"company": "Fabrikam Inc"})
        assert upd.status_code == 200, upd.text
        body = upd.json()
        assert body["company"] == "Fabrikam Inc"
        assert [e["value"] for e in body["edit_history"]["company"]] == ["Contoso", "Fabrikam Inc"]

        clear = await client.patch(f"/api/contacts/{cold_id}", json={"company": ""})
        assert clear.status_code == 422
        assert "must keep a company" in clear.json()["detail"]

        app_id = (await _bring(client, _AD))["application_id"]
        linked_id = await _linked(client, app_id, "Omar Hays", company="Hays")
        cleared = await client.patch(f"/api/contacts/{linked_id}", json={"company": ""})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["company"] is None


@pytest.mark.asyncio
async def test_list_people_company_filter_uses_the_job_company_key(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _cold(client, "Asha Rao", "Northwind Ltd")
        ben = await _cold(client, "Ben Ode", "Contoso")
        job = await _bring(client, {**_AD, "company": "NORTHWIND"})
        await _linked(client, job["application_id"], "Rita")
        await _linked(client, job["application_id"], "Omar", company="Hays")

        assert {p["name"] for p in await _people(client, company="northwind")} == {"Asha Rao", "Rita"}
        assert {p["name"] for p in await _people(client, company="Hays")} == {"Omar"}
        assert len(await _people(client)) == 4

        upd = await client.patch(f"/api/contacts/{ben}", json={"company": "Northwind"})
        assert upd.status_code == 200, upd.text
        assert {p["name"] for p in await _people(client, company="Northwind Ltd")} == {
            "Asha Rao", "Rita", "Ben Ode",
        }


async def _seed_known(client: AsyncClient) -> tuple[int, int]:
    """Cold Asha (sent by email) + Rita on app1. Returns (app1, asha_contact_id)."""
    asha = await _cold(
        client, "Asha Rao", "Northwind Ltd", email="asha@northwind.example", found_via="linkedin"
    )
    sent = await client.post(f"/api/contacts/{asha}/outreach", json={"entry": "sent", "channel": "email"})
    assert sent.status_code == 201, sent.text
    app1 = (await _bring(client, _AD))["application_id"]
    await _linked(client, app1, "Rita")
    return app1, asha


@pytest.mark.asyncio
async def test_bring_job_returns_known_people_with_outreach_state(authenticated_async_context):
    async with authenticated_async_context() as client:
        app1, _asha = await _seed_known(client)
        second = await _bring(
            client, {**_AD, "title": "Senior Analyst", "apply_url": "https://northwind.example/careers/8"}
        )
        known = second["known_people"]
        assert known["count"] == 2 and known["truncated"] is False
        by_name = {p["name"]: p for p in known["people"]}
        assert set(by_name) == {"Asha Rao", "Rita"}
        assert by_name["Asha Rao"]["last_sent_channel"] == "email"
        assert by_name["Asha Rao"]["last_sent_at"] is not None
        assert by_name["Asha Rao"]["company"] == "Northwind Ltd"
        assert by_name["Rita"]["application_ids"] == [app1]
        assert by_name["Rita"]["company"] == "Northwind"

        other = await _bring(client, {**_AD, "company": "Contoso", "apply_url": "https://contoso.example/jobs/1"})
        assert other["known_people"] == {"count": 0, "people": [], "truncated": False}


@pytest.mark.asyncio
async def test_known_people_leaves_out_people_on_this_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        app1, _asha = await _seed_known(client)
        detail = await client.get(f"/api/applications/{app1}")
        assert detail.status_code == 200, detail.text
        known = detail.json()["known_people"]
        assert known["count"] == 1
        assert known["people"][0]["name"] == "Asha Rao"


@pytest.mark.asyncio
async def test_known_people_excludes_person_whose_own_row_is_filtered_by_company(authenticated_async_context):
    async with authenticated_async_context() as client:
        app1 = (await _bring(client, _AD))["application_id"]
        app2 = (await _bring(client, {**_AD, "title": "Senior Analyst", "apply_url": "https://northwind.example/careers/9"}))["application_id"]
        await _linked(client, app1, "Omar", email="omar@hays.example", company="Hays")
        await _linked(client, app2, "Omar", email="omar@hays.example")
        detail = await client.get(f"/api/applications/{app1}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["known_people"]["count"] == 0


@pytest.mark.asyncio
async def test_kit_carries_known_people_and_a_line(authenticated_async_context):
    async with authenticated_async_context() as client:
        app1, _asha = await _seed_known(client)
        kit = await client.get(f"/api/applications/{app1}/kit")
        assert kit.status_code == 200, kit.text
        body = kit.json()
        assert body["known_people"]["count"] == 1
        assert body["known_people"]["people"][0]["name"] == "Asha Rao"
        assert any(line.startswith("known_people: 1 ") for line in body["instructions"])


@pytest.mark.asyncio
async def test_known_people_payload_is_capped(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "KNOWN_PEOPLE_MAX", 1)
    async with authenticated_async_context() as client:
        await _cold(client, "Asha Rao", "Northwind")
        await _cold(client, "Ben Ode", "Northwind Ltd")
        brought = await _bring(client, _AD)
        known = brought["known_people"]
        assert known["count"] == 2
        assert len(known["people"]) == 1
        assert known["truncated"] is True


@pytest.mark.asyncio
async def test_gate_company_cap_uses_the_key_and_counts_cold_contacts(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from tests.test_outreach_gate import _auto_ready, _check, _contact, _sent
    from tests.test_outreach_gate import _bring as gate_bring

    monkeypatch.setattr(settings, "OUTREACH_COMPANY_MAX", 2)
    async with authenticated_async_context() as client:
        _app1, cid, mid = await _auto_ready(client, fixture_user_id)
        app2 = await gate_bring(
            client,
            {**_AD, "company": "Northwind Ltd", "title": "Analyst", "apply_url": "https://n.example/10"},
        )
        other = await _contact(client, app2, email="sam@northwind.example", name="Sam Second")
        await _sent(client, other)
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")

        cy = await client.post(
            "/api/contacts",
            json={"name": "Cy Cold", "email": "cy@northwind.example", "company": "NORTHWIND",
                  "found_via": "linkedin"},
        )
        assert cy.status_code == 201, cy.text
        await _sent(client, int(cy.json()["contact"]["id"]))
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("stop", "company_cap_reached")


@pytest.mark.asyncio
async def test_mcp_company_round_trip(authenticated_async_context):
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
            refused = await mcp.call_tool("add_contact", {"name": "Mona Lisa", "email": "mona@louvre.example"})
            assert refused.is_error
            assert "company is required" in refused.content[0].text

            added = await mcp.call_tool(
                "add_contact", {"name": "Mona Lisa", "email": "mona@louvre.example", "company": "Louvre"}
            )
            assert not added.is_error, added.content[0].text
            assert json.loads(added.content[0].text)["contact"]["company"] == "Louvre"

            listed = await mcp.call_tool("list_people", {"company": "louvre"})
            assert not listed.is_error, listed.content[0].text
            people = json.loads(listed.content[0].text)["people"]
            assert [p["name"] for p in people] == ["Mona Lisa"]

            brought = await mcp.call_tool(
                "bring_job",
                {"title": "Curator", "company": "Louvre", "description": "Curate the collection. Paris, on site."},
            )
            assert not brought.is_error, brought.content[0].text
            assert json.loads(brought.content[0].text)["known_people"]["count"] == 1


@pytest.mark.asyncio
async def test_company_is_logged_as_a_flag_never_a_name(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        await _cold(client, "Cleo Park", "Contoso")
    added = [r for r in audit_capture.records if r.get("event") == "contact_added"]
    assert len(added) == 1 and added[0]["has_company"] is True
    assert "Contoso" not in json.dumps(audit_capture.records, default=str)
