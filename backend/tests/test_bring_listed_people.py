"""Outreach pack #15 — ``bring_job`` takes ``listed_people``.

People the ad itself names (a "Meet the hiring team" block) are stored as
contacts on the new application with ``found_via='job_ad'``, through
``add_contact`` so the slice-2 identity dedupe applies. Helpers are copied
here, never imported from another test module (breaks per-test schema isolation).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import AsyncClient

AD = {
    "title": "Platform Engineer",
    "company": "Fjord Labs",
    "location": "Oslo",
    "apply_url": "https://fjord.example/jobs/42",
    "description": "Run Kubernetes and Terraform for a data platform. Python.",
}
AD2 = {**AD, "title": "Data Engineer", "company": "Nordlys", "apply_url": "https://nordlys.example/jobs/7"}
PRIYA = {"name": "Priya Shah", "role": "Hiring Manager", "linkedin_url": "https://www.linkedin.com/in/priya-shah"}
TOM = {"name": "Tom Reed", "role": "Recruiter"}


class _CapturingAuditHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _CapturingAuditHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


async def _bring(client: AsyncClient, people: Any = None, ad: dict[str, Any] = AD):
    body = dict(ad)
    if people is not None:
        body["listed_people"] = people
    return await client.post("/api/jobs/bring", json=body)


async def _detail(client: AsyncClient, application_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{application_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _assert_nothing_written(client: AsyncClient, ad: dict[str, Any] = AD) -> None:
    clean = await _bring(client, ad=ad)
    assert clean.status_code == 200, clean.text
    assert clean.json()["existing"] is False, "the failed bring must not have left a job behind"


@pytest.mark.asyncio
async def test_bring_stores_listed_people_as_job_ad_contacts(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _bring(client, [PRIYA, TOM])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        priya_out, tom_out = body["listed_people"]
        assert priya_out["name"] == "Priya Shah" and isinstance(priya_out["contact_id"], int)
        assert priya_out["already_existed"] is False and priya_out["matched_on"] is None
        assert priya_out["error"] is None
        assert tom_out["name"] == "Tom Reed" and isinstance(tom_out["contact_id"], int)
        assert tom_out["already_existed"] is False and tom_out["matched_on"] is None and tom_out["error"] is None

        detail = await _detail(client, body["application_id"])
        assert len(detail["contacts"]) == 2
        by_id = {c["id"]: c for c in detail["contacts"]}
        priya = by_id[priya_out["contact_id"]]
        assert priya["found_via"] == "job_ad" and priya["role"] == "Hiring Manager"
        assert priya["linkedin_url"] == "https://www.linkedin.com/in/priya-shah"
        assert priya["source_url"] == "https://fjord.example/jobs/42"
        tom = by_id[tom_out["contact_id"]]
        assert tom["found_via"] == "job_ad" and tom["linkedin_url"] == ""
        added = [e for e in detail["events"] if e["event_type"] == "contact_added"]
        assert len(added) == 2
        assert {e["payload"]["contact_id"] for e in added} == {priya_out["contact_id"], tom_out["contact_id"]}


@pytest.mark.asyncio
async def test_rebring_dedupes_and_adds_only_the_new_person(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = (await _bring(client, [PRIYA, TOM])).json()
        second_resp = await _bring(client, [PRIYA, TOM, {"name": "Ana Lima", "role": "CTO"}])
        assert second_resp.status_code == 200, second_resp.text
        second = second_resp.json()
        assert second["existing"] is True
        p, t, a = second["listed_people"]
        assert p["already_existed"] is True and p["matched_on"] == "linkedin_url"
        assert p["contact_id"] == first["listed_people"][0]["contact_id"]
        assert t["already_existed"] is True and t["matched_on"] == "name"
        assert a["already_existed"] is False and isinstance(a["contact_id"], int)
        detail = await _detail(client, second["application_id"])
        assert len(detail["contacts"]) == 3
        assert len([e for e in detail["events"] if e["event_type"] == "contact_added"]) == 3


@pytest.mark.asyncio
async def test_listed_person_matches_a_contact_added_earlier_and_changes_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = (await _bring(client)).json()
        app_id = first["application_id"]
        added = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Priya Shah", "role": "Talent Partner", "found_via": "linkedin"},
        )
        assert added.status_code == 201, added.text
        again = (await _bring(client, [PRIYA])).json()
        item = again["listed_people"][0]
        assert item["already_existed"] is True and item["matched_on"] == "name"
        detail = await _detail(client, app_id)
        assert len(detail["contacts"]) == 1
        assert detail["contacts"][0]["found_via"] == "linkedin"
        assert detail["contacts"][0]["role"] == "Talent Partner"


@pytest.mark.asyncio
async def test_more_than_max_is_422_and_nothing_is_written(authenticated_async_context, monkeypatch):
    from src.services.applications import contacts

    monkeypatch.setattr(contacts, "LISTED_PEOPLE_MAX", 2)
    async with authenticated_async_context() as client:
        resp = await _bring(client, [PRIYA, TOM, {"name": "Ana Lima"}])
        assert resp.status_code == 422, resp.text
        assert "LISTED_PEOPLE_MAX" in json.dumps(resp.json())
        await _assert_nothing_written(client)


@pytest.mark.asyncio
async def test_bad_person_is_422_naming_the_item_before_any_write(authenticated_async_context):
    async with authenticated_async_context() as client:
        cases = [
            ([PRIYA, {"name": "X", "linkedin_url": "javascript:alert(1)"}],
             "listed_people[1]: linkedin_url must start with http:// or https://"),
            ([{"name": "   "}], "listed_people[0]: name must be"),
            ([{"name": "Priya Shah", "email": "p@fjord.example"}], ""),
        ]
        for i, (people, expected) in enumerate(cases):
            ad = {**AD, "title": f"Platform Engineer {i}"}
            resp = await _bring(client, people, ad=ad)
            assert resp.status_code == 422, resp.text
            assert expected in json.dumps(resp.json())
            await _assert_nothing_written(client, ad)


@pytest.mark.asyncio
async def test_cap_is_reported_per_person_and_keeps_the_job(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_PER_APPLICATION_MAX", 1)
    async with authenticated_async_context() as client:
        resp = await _bring(client, [PRIYA, TOM])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["application_id"]
        first, second = body["listed_people"]
        assert isinstance(first["contact_id"], int)
        assert second["contact_id"] is None and second["error"] == "contact_cap"


@pytest.mark.asyncio
async def test_rate_limit_is_reported_per_person_and_keeps_the_job(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        resp = await _bring(client, [PRIYA, TOM])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["application_id"]
        first, second = body["listed_people"]
        assert isinstance(first["contact_id"], int)
        assert second["contact_id"] is None and second["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_no_listed_people_is_null(authenticated_async_context):
    async with authenticated_async_context() as client:
        plain = await _bring(client)
        assert plain.status_code == 200 and plain.json()["listed_people"] is None
        empty = await _bring(client, [])
        assert empty.status_code == 200, empty.text
        assert empty.json()["listed_people"] is None
        detail = await _detail(client, empty.json()["application_id"])
        assert detail["contacts"] == []


@pytest.mark.asyncio
async def test_audit_log_has_counts_never_people(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        resp = await _bring(client, [PRIYA, TOM])
        assert resp.status_code == 200, resp.text
    blob = repr(audit_capture.records)
    for secret in ("Priya Shah", "Tom Reed", "linkedin.com/in/priya-shah", "fjord.example"):
        assert secret not in blob
    rec = [r for r in audit_capture.records if r.get("event") == "job_bring_listed_people"]
    assert len(rec) == 1
    assert (rec[0]["sent"], rec[0]["stored"], rec[0]["already_existed"], rec[0]["refused"]) == (2, 2, 0, 0)


@pytest.mark.asyncio
async def test_mcp_bring_job_takes_listed_people(authenticated_async_context):
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

            brought = payload(await mcp.call_tool("bring_job", {**AD, "listed_people": [PRIYA]}))
            item = brought["listed_people"][0]
            assert item["name"] == "Priya Shah" and isinstance(item["contact_id"], int)
            got = payload(await mcp.call_tool("get_application", {"application_id": brought["application_id"]}))
            mine = [c for c in got["contacts"] if c["id"] == item["contact_id"]]
            assert len(mine) == 1 and mine[0]["found_via"] == "job_ad"

            bad = await mcp.call_tool("bring_job", {**AD2, "listed_people": [{"name": "A", "phone": "1"}]})
            assert bad.is_error and "422" in bad.content[0].text
