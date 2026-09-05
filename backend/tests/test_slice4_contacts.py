"""Slice 4 — contacts (docs/plans/2026-09-05-contacts-stats/spec.md R1-R4, S1-S7, S12).

Frozen per spec.md §Frozen tests — written before the build, not edited to make
the implementation pass; a test believed wrong is called out in the build
report instead.

A contact is an add-only row on an application (`application_contacts`);
adding one appends a `contact_added` event whose payload names the row;
the same non-empty email on the same application is the same contact.
Helpers are COPIED from test_application_spine.py, never imported (a
cross-module fixture import breaks per-test schema isolation).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}

_CONTACT = {
    "name": "Priya Shah",
    "role": "Talent Partner",
    "email": "Priya.Shah@Northwind.example",
    "linkedin_url": "https://www.linkedin.com/in/priyashah",
    "notes": "Replied on LinkedIn within a day.",
}


# ── Shared helpers (copied, not imported) ────────────────────────────────────


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_contact(client: AsyncClient, application_id: int, **body: Any):
    payload = {**_CONTACT, **body}
    return await client.post(f"/api/applications/{application_id}/contacts", json=payload)


async def _mint_token(client: AsyncClient, name: str = "cli") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
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
    assert cookie, "failed to capture second user's session cookie"
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


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


# ═══════════════════════════════════════════════════════════════════════════
# R1 — a contact is a row + a contact_added event naming it
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_contact_creates_row_and_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["already_existed"] is False
        assert isinstance(body["event_id"], int)
        contact = body["contact"]
        assert contact["application_id"] == app_id
        assert contact["name"] == "Priya Shah"
        assert contact["role"] == "Talent Partner"
        # R2 — stored lower-cased + trimmed
        assert contact["email"] == "priya.shah@northwind.example"
        assert contact["linkedin_url"] == _CONTACT["linkedin_url"]
        assert contact["added_by"] == "web"
        assert contact["created_at"]

        detail = await client.get(f"/api/applications/{app_id}")
        assert detail.status_code == 200, detail.text
        events = [e for e in detail.json()["events"] if e["event_type"] == "contact_added"]
        assert len(events) == 1
        assert events[0]["payload"]["contact_id"] == contact["id"]
        assert "Priya Shah" in events[0]["detail"]
        assert events[0]["recorded_by"] == "web"


@pytest.mark.asyncio
async def test_added_by_comes_from_the_credential(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = await _mint_token(client, name="claude-code")
    async with _bearer_client(token) as agent:
        resp = await _add_contact(agent, app_id, email="")
        assert resp.status_code == 201, resp.text
        assert resp.json()["contact"]["added_by"] == "token:claude-code"


# ═══════════════════════════════════════════════════════════════════════════
# R2 — idempotent on email; no email = no identity
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_same_email_twice_is_the_same_contact(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _add_contact(client, app_id)
        assert first.status_code == 201, first.text
        again = await _add_contact(client, app_id, email="  PRIYA.SHAH@northwind.example ", name="P. Shah")
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["already_existed"] is True
        assert body["event_id"] is None
        assert body["contact"]["id"] == first.json()["contact"]["id"]
        # the FIRST row wins — no update on the second add
        assert body["contact"]["name"] == "Priya Shah"

        detail = await client.get(f"/api/applications/{app_id}")
        assert len(detail.json()["contacts"]) == 1
        assert len([e for e in detail.json()["events"] if e["event_type"] == "contact_added"]) == 1


@pytest.mark.asyncio
async def test_without_an_email_every_add_is_a_new_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        a = await _add_contact(client, app_id, email="", name="Alex")
        b = await _add_contact(client, app_id, email="", name="Alex")
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["contact"]["id"] != b.json()["contact"]["id"]
        detail = await client.get(f"/api/applications/{app_id}")
        assert len(detail.json()["contacts"]) == 2


@pytest.mark.asyncio
async def test_same_email_on_a_different_application_is_a_different_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_a = await _bring(client)
        app_b = await _bring(client, {**_AD, "title": "Platform Engineer", "company": "Southwind"})
        a = await _add_contact(client, app_a)
        b = await _add_contact(client, app_b)
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["contact"]["id"] != b.json()["contact"]["id"]


# ═══════════════════════════════════════════════════════════════════════════
# R3 — contacts ride the application: detail, export, ownership, deletion
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_application_lists_contacts_oldest_first(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _add_contact(client, app_id, name="First", email="first@x.example")
        await _add_contact(client, app_id, name="Second", email="second@x.example")
        detail = await client.get(f"/api/applications/{app_id}")
        contacts = detail.json()["contacts"]
        assert [c["name"] for c in contacts] == ["First", "Second"]
        assert set(contacts[0]) >= {"id", "name", "role", "email", "linkedin_url", "notes", "added_by", "created_at"}


@pytest.mark.asyncio
async def test_export_history_includes_contacts(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _add_contact(client, app_id)
        resp = await client.get("/api/applications/export")
        assert resp.status_code == 200, resp.text
        # an export entry names ITS OWN id `id`; `application_id` is a child's FK (spine contract)
        apps = {a["id"]: a for a in resp.json()["applications"]}
        assert app_id in apps
        assert [c["email"] for c in apps[app_id]["contacts"]] == ["priya.shah@northwind.example"]


@pytest.mark.asyncio
async def test_foreign_application_is_404_and_stays_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
    cookie = await _second_user_session_cookie("contacts-intruder@example.com")
    async with _session_client(cookie) as intruder:
        resp = await _add_contact(intruder, app_id)
        assert resp.status_code == 404, resp.text
        missing = await _add_contact(intruder, 987654321)
        assert missing.status_code == 404
        assert resp.json()["detail"] == missing.json()["detail"], "no existence oracle (S1)"
    async with authenticated_async_context() as client:
        detail = await client.get(f"/api/applications/{app_id}")
        assert detail.json()["contacts"] == []


@pytest.mark.asyncio
async def test_contacts_tables_are_in_the_per_user_registries():
    from src.repositories.database import JobDatabase

    assert "application_contacts" in JobDatabase._PER_USER_TABLES
    assert "profile_edits" in JobDatabase._PER_USER_TABLES
    assert "application_contacts" in JobDatabase._EXPORT_TABLES
    assert "profile_edits" in JobDatabase._EXPORT_TABLES

    from pathlib import Path

    observe = Path(__file__).resolve().parent.parent / "scripts" / "observe.py"
    text = observe.read_text(encoding="utf-8")
    assert "application_contacts" in text and "profile_edits" in text


# ═══════════════════════════════════════════════════════════════════════════
# S4/S5 — caps and shapes are 422s naming the field
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_name_over_the_cap_is_422_naming_the_limit(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACT_NAME_MAX_CHARS", 10)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, name="x" * 11, email="")
        assert resp.status_code == 422, resp.text
        assert "name" in resp.text and "10" in resp.text


@pytest.mark.asyncio
async def test_blank_name_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, name="   ", email="")
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "linkedin_url",
    ["javascript:alert(1)", "ftp://linkedin.com/in/x", "linkedin.com/in/x", "data:text/html,hi"],
)
async def test_linkedin_url_must_be_http_or_https(authenticated_async_context, linkedin_url):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, linkedin_url=linkedin_url, email="")
        assert resp.status_code == 422, resp.text
        assert "linkedin_url" in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "email",
    ["not-an-email", "a@b", "a b@c.example", "@c.example", "a@.com", "a@b.", "a@@b.c", "a@b@c.d", "a@b\tc.d"],
)
async def test_email_shape_is_checked(authenticated_async_context, email):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, email=email)
        assert resp.status_code == 422, resp.text
        assert "email" in resp.text


@pytest.mark.parametrize(
    "email",
    ["name@example.com", "first.last+tag@sub.example.co.uk", "x@y.z", "a!#$%@b.c"],
)
def test_email_shape_accepts_what_the_old_regex_accepted(email):
    # The check was `^[^@\s]+@[^@\s]+\.[^@\s]+$`; CodeQL flagged it as polynomial-time
    # (py/polynomial-redos). The structural replacement must accept the same set.
    from src.services.applications.contacts import _validate_email

    assert _validate_email(email) == email.lower()


@pytest.mark.real_sleep
def test_email_check_is_linear_time():
    # Pathological input for the old regex: many `!.` pairs after `@` and no final
    # match. Bounded by CONTACT_EMAIL_MAX_CHARS (254) in the route, but the check
    # itself must not backtrack regardless of the cap.
    import time

    from src.services.applications.contacts import _looks_like_email

    bad = "a@" + "!." * 5000 + "!"
    t0 = time.perf_counter()
    assert _looks_like_email(bad) is True  # dots exist inside the domain → shape ok
    assert _looks_like_email("a@" + "!" * 10000) is False
    assert _looks_like_email("a@" + "!." * 5000 + " ") is False  # whitespace anywhere → reject
    assert time.perf_counter() - t0 < 0.05


@pytest.mark.asyncio
async def test_unknown_field_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, added_by="agent:evil")
        assert resp.status_code == 422, "S2 — the actor is derived, never accepted"


@pytest.mark.asyncio
async def test_per_application_cap_is_409(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_PER_APPLICATION_MAX", 2)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _add_contact(client, app_id, email="a@x.example")).status_code == 201
        assert (await _add_contact(client, app_id, email="b@x.example")).status_code == 201
        third = await _add_contact(client, app_id, email="c@x.example")
        assert third.status_code == 409, third.text
        # an existing email still answers 200 — idempotency is not a new row
        again = await _add_contact(client, app_id, email="a@x.example")
        assert again.status_code == 200 and again.json()["already_existed"] is True


@pytest.mark.asyncio
async def test_occurred_at_too_far_in_the_future_is_422(authenticated_async_context):
    from datetime import datetime, timedelta, timezone

    far = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, occurred_at=far, email="")
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_add_contact_is_rate_limited_per_user(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _add_contact(client, app_id, email="a@x.example")).status_code == 201
        assert (await _add_contact(client, app_id, email="b@x.example")).status_code == 201
        limited = await _add_contact(client, app_id, email="c@x.example")
        assert limited.status_code == 429, limited.text
    cookie = await _second_user_session_cookie("contacts-other@example.com")
    async with _session_client(cookie) as other:
        other_app = await _bring(other)
        unaffected = await _add_contact(other, other_app)
        assert unaffected.status_code == 201, "the limit is per USER, not per IP/process"


# ═══════════════════════════════════════════════════════════════════════════
# S3 — PII stays out of the audit log; S12 — append-only
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_log_never_carries_contact_pii(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, notes="Met at PyCon; knows the hiring manager.")
        assert resp.status_code == 201, resp.text
    blob = repr(audit_capture.records)
    assert "priya.shah@northwind.example" not in blob.lower()
    assert "Priya Shah" not in blob
    assert "linkedin.com/in/priyashah" not in blob
    assert "PyCon" not in blob
    assert any("contact" in str(r.get("msg", "")) or "contact" in str(r.get("tool", "")) or "contact" in str(r.get("action", "")) for r in audit_capture.records), (
        "the add IS audited — with ids and lengths, not the person"
    )


def test_contacts_are_append_only():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+application_contacts", text, re.IGNORECASE):
            offenders.append(str(py))
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+profile_edits", text, re.IGNORECASE):
            offenders.append(str(py))
    assert offenders == [], f"application_contacts/profile_edits must be append-only: {offenders}"

    from src.api.main import app
    from tests._routes import route_table

    # route_table, not app.routes: FastAPI 0.141 nests included routers and the
    # raw list has no /contacts rows, so this loop passed by seeing nothing.
    seen = 0
    for row in route_table(app):
        if row.path.startswith("/api/applications") and "/contacts" in row.path:
            seen += 1
            assert not (row.methods & {"PATCH", "PUT", "DELETE"}), f"{row.path} exposes {set(row.methods)}"
    assert seen, "no /api/applications/*/contacts routes found -- the guard is vacuous"


# ═══════════════════════════════════════════════════════════════════════════
# R12/S11 — the same function on MCP
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mcp_add_contact_calls_the_route(authenticated_async_context):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = await _mint_token(client, name="agent")

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            result = await mcp.call_tool("add_contact", {"application_id": app_id, **_CONTACT})
            assert not result.is_error, result.content[0].text
            body = json.loads(result.content[0].text)
            assert body["contact"]["added_by"] == "token:agent"
            assert body["already_existed"] is False
            stolen = await mcp.call_tool("add_contact", {"application_id": 987654321, **_CONTACT})
            assert stolen.is_error and "404" in stolen.content[0].text
