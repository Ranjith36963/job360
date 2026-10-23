"""Outreach pack #14 — a contact's phone and links, and the web-only Confirm mark.

Phone is stored normalised (optional leading ``+``, then digits); 7+ digits is an identity for
the no-email dedupe (after the LinkedIn URL, before the name). ``links`` is a list of up to
``CONTACT_LINKS_MAX`` http(s) URLs. Both are stored only: Job360 never dials or opens them (M2).
The Confirm mark is a ``contact_edits`` row (``field='confirmed'``) written by a session-only route.

Helpers are copied from ``test_contact_identity.py``, never imported (importing across test
modules breaks per-test schema isolation).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


@pytest.fixture(autouse=True)
def _isolate_limits():
    from src.services.auth import rate_limit

    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()
    try:
        yield
    finally:
        rate_limit._FAILURES.clear()
        rate_limit._BUCKETS.clear()


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_person(client: AsyncClient, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", "company": "Northwind", **body}
    return await client.post("/api/contacts", json=payload)


async def _add_contact(client: AsyncClient, application_id: int, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", **body}
    return await client.post(f"/api/applications/{application_id}/contacts", json=payload)


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "agent"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))
        self.records[-1]["_message"] = record.getMessage()


@pytest.fixture
def log_capture():
    root = logging.getLogger()
    handler = _Capture()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    audit = logging.getLogger("job360.audit")
    old_audit = audit.level
    audit.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)
    audit.setLevel(old_audit)


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "stored", "key"),
    [
        ("+44 (0)7700 900-123", "+4407700900123", "4407700900123"),
        ("12-34", "1234", ""),
        ("(07700) 900.123", "07700900123", "07700900123"),
    ],
)
def test_phone_normalises_and_identity_key_needs_seven_digits(raw, stored, key):
    from src.services.applications.contacts import _validate_phone, phone_identity_key

    assert _validate_phone(raw) == stored
    assert phone_identity_key(stored) == key


def test_blank_phone_is_unset():
    from src.services.applications.contacts import _validate_phone

    assert _validate_phone("  ") is None
    assert _validate_phone(None) is None


# ── add / read ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_contact_stores_phone_and_links(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _add_person(
            client, phone="+44 7700 900123", links=["https://github.com/priya", "https://priya.example/", " "]
        )
        assert resp.status_code == 201, resp.text
        contact = resp.json()["contact"]
        assert contact["phone"] == "+447700900123"
        assert contact["links"] == ["https://github.com/priya", "https://priya.example/"]
        people = (await client.get("/api/people")).json()["people"]
        assert people[0]["phone"] == "+447700900123"
        assert people[0]["links"] == ["https://github.com/priya", "https://priya.example/"]
    rows = _sql("SELECT links FROM application_contacts WHERE id = ?", (contact["id"],))
    assert rows[0][0] == '["https://github.com/priya", "https://priya.example/"]'


@pytest.mark.asyncio
async def test_phone_dedupes_after_linkedin_before_name(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        a = await _add_contact(client, app_id, name="Priya Shah", phone="07700 900123")
        assert a.status_code == 201, a.text
        b = await _add_contact(client, app_id, name="P. Shah", phone="(07700) 900-123")
        assert b.status_code == 200, b.text
        assert b.json()["matched_on"] == "phone"
        assert b.json()["contact_id"] == a.json()["contact_id"]
        # a short number carries no identity
        s1 = await _add_contact(client, app_id, name="Sam A", phone="123-45")
        s2 = await _add_contact(client, app_id, name="Sam B", phone="123-45")
        assert s1.status_code == 201 and s2.status_code == 201
        assert s1.json()["contact_id"] != s2.json()["contact_id"]
        # LinkedIn outranks phone
        c = await _add_contact(
            client, app_id, name="Cy Li", linkedin_url="https://linkedin.com/in/x", phone="07700900999"
        )
        assert c.status_code == 201
        d = await _add_contact(
            client, app_id, name="Someone", linkedin_url="https://linkedin.com/in/x", phone="07700900111"
        )
        assert d.status_code == 200, d.text
        assert d.json()["matched_on"] == "linkedin_url"
        assert d.json()["contact_id"] == c.json()["contact_id"]


@pytest.mark.asyncio
async def test_name_match_respects_phones(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _add_contact(client, app_id, name="Lee Park", phone="07700900111")
        assert first.status_code == 201
        other = await _add_contact(client, app_id, name="Lee Park", phone="07700900222")
        assert other.status_code == 201, other.text
        assert other.json()["contact_id"] != first.json()["contact_id"]
        plain = await _add_contact(client, app_id, name="Lee Park")
        assert plain.status_code == 200, plain.text
        assert plain.json()["matched_on"] == "name"
        cold = await _add_person(client, name="Kim Ro", phone="07700900333")
        assert cold.status_code == 201
        cold2 = await _add_person(client, name="Kim Ro")
        assert cold2.status_code == 201, cold2.text
        assert cold2.json()["contact_id"] != cold.json()["contact_id"]


@pytest.mark.asyncio
async def test_phone_and_links_are_validated(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        r = await _add_person(client, phone="call me")
        assert r.status_code == 422 and "phone" in r.json()["detail"]
        r = await _add_person(client, links=[f"https://x.example/{i}" for i in range(6)])
        assert r.status_code == 422 and "CONTACT_LINKS_MAX" in r.json()["detail"]
        r = await _add_person(client, links=["javascript:alert(1)"])
        assert r.status_code == 422 and "http" in r.json()["detail"]
        monkeypatch.setattr(settings, "CONTACT_PHONE_MAX_CHARS", 10)
        r = await _add_person(client, phone="+44 7700 900 123 4")
        assert r.status_code == 422 and "CONTACT_PHONE_MAX_CHARS" in r.json()["detail"]


@pytest.mark.asyncio
async def test_update_contact_phone_and_links_append_edits(authenticated_async_context):
    async with authenticated_async_context() as client:
        made = await _add_person(client, phone="07700 900123")
        cid = made.json()["contact_id"]
        resp = await client.patch(
            f"/api/contacts/{cid}", json={"phone": "+1 415 555 0100", "links": ["https://x.example/a"]}
        )
        assert resp.status_code == 200, resp.text
        view = resp.json()
        assert view["phone"] == "+14155550100"
        assert view["links"] == ["https://x.example/a"]
        assert view["edit_history"]["phone"][-1]["value"] == "+14155550100"
        assert view["edit_history"]["phone"][0]["value"] == "07700900123"
        base = _sql("SELECT phone FROM application_contacts WHERE id = ?", (cid,))
        assert base[0][0] == "07700900123"
        edit = _sql("SELECT value FROM contact_edits WHERE contact_id = ? AND field = 'links'", (cid,))
        assert edit[0][0] == '["https://x.example/a"]'
        cleared = await client.patch(f"/api/contacts/{cid}", json={"phone": "", "links": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["phone"] is None
        assert cleared.json()["links"] == []


# ── Confirm ──────────────────────────────────────────────────────────────────


def test_confirm_route_is_session_only_with_auth_first():
    from src.api.auth_deps import require_session_user, require_user
    from src.api.routes.applications import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/contacts/{contact_id}/confirm")
    assert [d.dependency for d in route.dependencies] == [require_user]
    assert any(d.call is require_session_user for d in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_confirm_marks_an_agent_added_contact_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        made = await _add_person(agent, name="Agent Found", phone="07700900444")
        assert made.status_code == 201, made.text
        assert made.json()["contact"]["confirmed"] is False
        cid = made.json()["contact_id"]
        assert (await agent.post(f"/api/contacts/{cid}/confirm")).status_code in (401, 403)
    async with authenticated_async_context() as client:
        first = await client.post(f"/api/contacts/{cid}/confirm")
        assert first.status_code == 200, first.text
        assert first.json()["confirmed"] is True
        again = await client.post(f"/api/contacts/{cid}/confirm")
        assert again.status_code == 200 and again.json()["confirmed"] is True
        assert "confirmed" not in again.json()["edit_history"]
        rows = _sql("SELECT field, value, recorded_by FROM contact_edits WHERE contact_id = ?", (cid,))
        assert [tuple(r) for r in rows] == [("confirmed", "true", "web")]
        people = (await client.get("/api/people")).json()["people"]
        assert next(p for p in people if cid in p["contact_ids"])["confirmed"] is True
        # a contact the user added on the web is confirmed from the start; confirming writes no row
        mine = await _add_person(client, name="Web Added")
        assert mine.json()["contact"]["confirmed"] is True
        mid = mine.json()["contact_id"]
        assert (await client.post(f"/api/contacts/{mid}/confirm")).status_code == 200
        assert _sql("SELECT 1 FROM contact_edits WHERE contact_id = ?", (mid,)) == []
        assert (await client.post("/api/contacts/999999/confirm")).status_code == 404


@pytest.mark.asyncio
async def test_no_mcp_door_confirms_a_contact(authenticated_async_context):
    from src.api import mcp_server as m

    served = m.served_tool_parameters()
    assert not [name for name, params in served.items() if "confirmed" in params]
    assert not [name for name in served if "confirm" in name]
    async with authenticated_async_context() as client:
        made = await _add_person(client)
        resp = await client.patch(f"/api/contacts/{made.json()['contact_id']}", json={"confirmed": True})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_mcp_add_contact_phone_links(authenticated_async_context):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            def payload(res: Any) -> dict[str, Any]:
                assert not res.is_error, res.content[0].text
                return json.loads(res.content[0].text)

            one = payload(await mcp.call_tool("add_contact", {
                "name": "Mo", "company": "Northwind", "phone": "07700 900555", "links": ["https://mo.example"],
            }))
            assert one["contact"]["phone"] == "07700900555"
            assert one["contact"]["links"] == ["https://mo.example"]
            upd = payload(await mcp.call_tool("update_contact", {"contact_id": one["contact_id"], "links": []}))
            assert upd["links"] == []
            people = payload(await mcp.call_tool("list_people", {}))["people"]
            assert people[0]["confirmed"] is False
            assert people[0]["phone"] == "07700900555"


# ── feed, export, logs ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logs_never_carry_phone_or_links(authenticated_async_context, log_capture):
    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        made = await _add_person(agent, phone="+447700900999", links=["https://loglinks.example"])
        assert made.status_code == 201, made.text
        cid = made.json()["contact_id"]
    async with authenticated_async_context() as client:
        assert (await client.post(f"/api/contacts/{cid}/confirm")).status_code == 200
    audit = [r for r in log_capture.records if r.get("event") in ("contact_added", "contact_confirmed")]
    assert {r["event"] for r in audit} == {"contact_added", "contact_confirmed"}
    assert next(r for r in audit if r["event"] == "contact_added")["has_phone"] is True
    for record in log_capture.records:
        text = repr(record)
        assert "7700900999" not in text and "loglinks" not in text


@pytest.mark.asyncio
async def test_concurrent_confirms_write_one_row_and_do_not_use_the_edit_cap(
    authenticated_async_context, monkeypatch
):
    import asyncio

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        cid = (await _add_person(agent, name="Race Found")).json()["contact_id"]
    async with authenticated_async_context() as client:
        results = await asyncio.gather(*[client.post(f"/api/contacts/{cid}/confirm") for _ in range(4)])
        assert [r.status_code for r in results] == [200] * 4
        rows = _sql("SELECT 1 FROM contact_edits WHERE contact_id = ? AND field = 'confirmed'", (cid,))
        assert len(rows) == 1
        # the Confirm mark is not a real edit: a cap of 1 still allows one
        monkeypatch.setattr(settings, "CONTACT_EDITS_PER_CONTACT_MAX", 1)
        assert (await client.patch(f"/api/contacts/{cid}", json={"notes": "n"})).status_code == 200
        assert (await client.patch(f"/api/contacts/{cid}", json={"notes": "m"})).status_code == 409
