"""Outreach pack #7 - where a contact's EMAIL came from, and the `bounced` ledger entry.

Real Postgres, real doors (route + MCP). Helpers are copied, never imported from
another test module (schema isolation). Non-default values throughout.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from migrations import runner
from src.core import settings
from src.repositories import pg
from src.repositories.database import JobDatabase
from src.services.profile.models import UserPreferences

STEM = "0071_contact_email_source"
INBOX = "preferences.daily_check"
RITA = "rita@northwind.example"
_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_AD2 = {**_AD, "apply_url": "https://northwind.example/careers/8", "title": "Platform Engineer"}


def _bounce_source(mid: str = "<bounce-7@mailer.example>") -> dict[str, str]:
    return {"kind": "email", "message_id": mid, "sender": "mailer-daemon@googlemail.com"}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _patch(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _contact(
    client: AsyncClient, app_id: int | None, *, email: str = "rita@northwind.example",
    found_via: str | None = "company_site", name: str = "Rita Recruiter",
) -> int:
    body: dict[str, Any] = {"name": name, "role": "Recruiter", "email": email}
    if app_id is not None:
        body["application_id"] = app_id
    if found_via:
        body["found_via"] = found_via
    resp = await client.post("/api/contacts", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact"]["id"])


async def _draft(client: AsyncClient, app_id: int, contact_id: int, channel: str = "email") -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": contact_id, "channel": channel, "text": "Hi Rita, a short note."},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _sent(client: AsyncClient, contact_id: int) -> None:
    resp = await client.post(f"/api/contacts/{contact_id}/outreach", json={"entry": "sent", "channel": "email"})
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, contact_id: int, message_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/contacts/{contact_id}/outreach-check", params={"message_id": message_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _auto_ready(client: AsyncClient, user_id: str) -> tuple[int, int, int]:
    """A user in auto mode with one job, one linked contact and one email draft."""
    _seed_profile(user_id)
    await _patch(client, INBOX, "auto")
    app_id = await _bring(client)
    cid = await _contact(client, app_id)
    mid = await _draft(client, app_id, cid)
    return app_id, cid, mid


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
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
    return str(lr.cookies.get("job360_session"))


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


def _row_counts() -> dict[str, int]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return {
            t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])  # noqa: S608 — fixed names
            for t in ("contact_outreach", "application_events", "application_contacts", "contact_edits",
                      "profile_edits", "application_asks")
        }
    finally:
        conn.close()


async def _mint_token(client: AsyncClient, name: str = "agent") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _mcp_client(token: str):
    """Official MCP client wired straight into the FastAPI app (in-process) —
    copied from test_mcp_server.py, never imported (fixture isolation)."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _mcp_payload(result) -> dict:
    import json

    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


async def _add(client: AsyncClient, **kw: Any):
    body = {"name": "Rita Recruiter", "role": "Recruiter", **kw}
    return await client.post("/api/contacts", json=body)


async def _bounce(client: AsyncClient, cid: int, mid: str = "<bounce-7@mailer.example>", **extra: Any):
    body: dict[str, Any] = {"entry": "bounced", "channel": "email", "source": _bounce_source(mid)}
    body.update(extra)
    return await client.post(f"/api/contacts/{cid}/outreach", json=body)


async def _columns_and_index(path: str) -> tuple[bool, bool]:
    async with pg.connect(path) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'application_contacts' AND column_name = 'email_source'"
        )
        col = await cur.fetchone() is not None
        cur = await conn.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = 'idx_contact_outreach_bounced'"
        )
        idx = await cur.fetchone() is not None
    return col, idx


@pytest.mark.asyncio
async def test_migration_0071_up_down(tmp_path):
    path = str(tmp_path / f"m71_{uuid.uuid4().hex[:8]}.db")
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path, target=STEM)
    assert await _columns_and_index(path) == (True, True)
    assert await runner.down(path) == STEM
    assert await _columns_and_index(path) == (False, False)
    await runner.up(path, target=STEM)
    assert await _columns_and_index(path) == (True, True)


@pytest.mark.asyncio
async def test_add_contact_stores_email_source_normalised(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _add(client, email=RITA, email_source="Provider Verified")
        assert resp.status_code == 201, resp.text
        contact = resp.json()["contact"]
        assert contact["email_source"] == "provider_verified"
        people = (await client.get("/api/people")).json()["people"]
        assert [p["email_source"] for p in people if p["email"] == RITA] == ["provider_verified"]
        one = (await client.get(f"/api/people?contact_id={contact['id']}")).json()["person"]
        assert one["email_source"] == "provider_verified"


@pytest.mark.asyncio
async def test_email_source_refusals(authenticated_async_context):
    async with authenticated_async_context() as client:
        before = _row_counts()
        bad = await _add(client, email=RITA, email_source="guessed")
        assert bad.status_code == 422
        assert "CONTACT_EMAIL_SOURCES" in bad.text
        orphan = await _add(client, linkedin_url="https://www.linkedin.com/in/rita-r", email_source="pattern_guess")
        assert orphan.status_code == 422
        assert "email_source needs an email" in orphan.text
        assert _row_counts() == before


@pytest.mark.asyncio
async def test_update_email_source_keeps_history(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid = (await _add(client, email=RITA, email_source="on_company_page")).json()["contact"]["id"]
        resp = await client.patch(f"/api/contacts/{cid}", json={"email_source": "pattern_guess"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["email_source"] == "pattern_guess"
        assert [e["value"] for e in body["edit_history"]["email_source"]] == ["on_company_page", "pattern_guess"]
        cleared = await client.patch(f"/api/contacts/{cid}", json={"email_source": ""})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["email_source"] is None


@pytest.mark.asyncio
async def test_new_email_without_source_clears_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid = (await _add(client, email=RITA, email_source="provider_verified")).json()["contact"]["id"]
        changed = await client.patch(f"/api/contacts/{cid}", json={"email": "new@northwind.example"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["email_source"] is None
        assert changed.json()["edit_history"]["email_source"][-1]["value"] == ""
        n_edits = len(changed.json()["edit_history"]["email_source"])
        same = await client.patch(f"/api/contacts/{cid}", json={"email": "NEW@northwind.example"})
        assert same.status_code == 200, same.text
        assert len(same.json()["edit_history"]["email_source"]) == n_edits
        third = await client.patch(
            f"/api/contacts/{cid}", json={"email": "third@northwind.example", "email_source": "user_gave"}
        )
        assert third.json()["email_source"] == "user_gave"
        assert (await client.patch(f"/api/contacts/{cid}", json={"email": ""})).status_code == 200
        alone = await client.patch(f"/api/contacts/{cid}", json={"email_source": "they_emailed"})
        assert alone.status_code == 422


@pytest.mark.asyncio
async def test_check_outreach_asks_on_pattern_guess(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, INBOX, "auto")
        app_id = await _bring(client)
        resp = await _add(
            client, application_id=app_id, email=RITA, found_via="company_site", email_source="pattern_guess"
        )
        cid = resp.json()["contact"]["id"]
        mid = await _draft(client, app_id, cid)
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "email_guessed")
        assert (await client.patch(f"/api/contacts/{cid}", json={"email_source": "user_gave"})).status_code == 200
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")


@pytest.mark.asyncio
async def test_bounce_recorded_once_per_message_id(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        events_before = _row_counts()["application_events"]
        outreach_before = _row_counts()["contact_outreach"]
        first = await _bounce(client, cid)
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["outreach"]["text"] == RITA
        assert body["outreach"]["entry"] == "bounced"
        assert body["event_id"] is None
        assert _row_counts()["application_events"] == events_before
        again = await _bounce(client, cid)
        assert again.status_code == 200, again.text
        assert again.json()["already_existed"] is True
        assert _row_counts()["contact_outreach"] == outreach_before + 1
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        bounced = detail["contacts"][0]["outreach"]["bounced"]
        assert len(bounced) == 1
        assert bounced[0]["source_message_id"] == "<bounce-7@mailer.example>"


@pytest.mark.asyncio
async def test_repeat_bounce_is_noop_after_email_cleared(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        assert (await _bounce(client, cid)).status_code == 201
        cleared = await client.patch(f"/api/contacts/{cid}", json={"email": ""})
        assert cleared.status_code == 200, cleared.text
        again = await _bounce(client, cid)
        assert again.status_code == 200, again.text
        assert again.json()["already_existed"] is True


@pytest.mark.asyncio
async def test_bounce_never_dedupes_onto_another_entry_kind(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        mid = "<shared-9@mailer.example>"
        sent = await client.post(
            f"/api/contacts/{cid}/outreach",
            json={"entry": "sent", "channel": "email", "source": _bounce_source(mid)},
        )
        assert sent.status_code == 201, sent.text
        before = _row_counts()["contact_outreach"]
        clash = await _bounce(client, cid, mid)
        assert clash.status_code == 409, clash.text
        assert "already recorded as 'sent'" in clash.text
        assert _row_counts()["contact_outreach"] == before


@pytest.mark.asyncio
async def test_bounce_is_exempt_from_entry_cap(authenticated_async_context, fixture_user_id, monkeypatch):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        await _sent(client, cid)
        monkeypatch.setattr(settings, "OUTREACH_ENTRIES_PER_CONTACT_MAX", 1)
        second_sent = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "email"})
        assert second_sent.status_code == 429
        assert (await _bounce(client, cid)).status_code == 201


@pytest.mark.asyncio
async def test_bounce_refusals(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        no_mail = await _add(
            client, application_id=app_id, name="No Mail", linkedin_url="https://www.linkedin.com/in/nm"
        )
        nid = no_mail.json()["contact"]["id"]
        before = _row_counts()
        wrong_channel = await client.post(
            f"/api/contacts/{cid}/outreach",
            json={"entry": "bounced", "channel": "linkedin", "source": _bounce_source()},
        )
        assert wrong_channel.status_code == 422
        no_source = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "bounced", "channel": "email"})
        assert no_source.status_code == 422
        assert "message id" in no_source.text
        with_text = await _bounce(client, cid, text="Mailbox full")
        assert with_text.status_code == 422
        no_address = await _bounce(client, nid)
        assert no_address.status_code == 422
        assert "no email address" in no_address.text
        assert _row_counts() == before
        cookie = await _second_user_session_cookie()
    async with authenticated_async_context() as other:
        other.cookies.set("job360_session", cookie)
        foreign = await _bounce(other, cid)
        assert foreign.status_code == 404
    assert _row_counts() == before


@pytest.mark.asyncio
async def test_check_outreach_stops_on_bounced_address_and_new_address_clears_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        assert (await _bounce(client, cid)).status_code == 201
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("stop", "email_bounced")
        moved = await client.patch(f"/api/contacts/{cid}", json={"email": "rita.new@northwind.example"})
        assert moved.status_code == 200
        mid2 = await _draft(client, app_id, cid)
        verdict = await _check(client, cid, mid2)
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
        app2 = await _bring(client, _AD2)
        cid_b = await _contact(client, app2, email=RITA, name="Rita Again")
        mid_b = await _draft(client, app2, cid_b)
        verdict = await _check(client, cid_b, mid_b)
        assert (verdict["decision"], verdict["reason"]) == ("stop", "email_bounced")


@pytest.mark.asyncio
async def test_record_event_outreach_bounced_route_and_mcp(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime
    from src.api.routes.applications import OUTREACH_NEEDS_CONTACT

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        events_before = _row_counts()["application_events"]
        ok = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_bounced", "contact_id": cid, "channel": "email",
                  "source": _bounce_source("<bounce-route@mailer.example>")},
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["event_id"] is None
        assert _row_counts()["application_events"] == events_before
        cold = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_bounced", "channel": "email"},
        )
        assert cold.status_code == 422
        assert cold.json()["detail"] == OUTREACH_NEEDS_CONTACT
        cold_cid = (await _add(client, email="cold@northwind.example")).json()["contact"]["id"]
        token = await _mint_token(client)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            res = await mcp.call_tool(
                "record_event",
                {"event_type": "outreach_bounced", "contact_id": cold_cid, "channel": "email",
                 "source": {"kind": "email", "message_id": "<b-mcp@x>"}},
            )
            assert _mcp_payload(res)["already_existed"] is False
            listed = _mcp_payload(await mcp.call_tool("list_people", {"contact_id": cold_cid}))
            assert listed["person"]["outreach"]["bounced"][0]["source_message_id"] == "<b-mcp@x>"
            bogus = await mcp.call_tool(
                "record_event", {"event_type": "outreach_bogus", "contact_id": cold_cid, "channel": "email"}
            )
            assert bogus.is_error
            assert "outreach_bounced" in bogus.content[0].text


@pytest.mark.asyncio
async def test_mcp_add_and_update_contact_take_email_source(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            added = _mcp_payload(await mcp.call_tool(
                "add_contact", {"name": "Mia Mcp", "email": "mcp@x.example", "email_source": "pattern_guess"}
            ))
            assert added["contact"]["email_source"] == "pattern_guess"
            cid = added["contact"]["id"]
            upd = _mcp_payload(await mcp.call_tool(
                "update_contact", {"contact_id": cid, "email_source": "they_emailed"}
            ))
            assert upd["email_source"] == "they_emailed"


@pytest.mark.asyncio
async def test_audit_lines_never_carry_the_address(authenticated_async_context, fixture_user_id, audit_capture):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, INBOX, "auto")
        app_id = await _bring(client)
        resp = await _add(
            client, application_id=app_id, email=RITA, found_via="company_site", email_source="provider_verified"
        )
        cid = resp.json()["contact"]["id"]
        mid = await _draft(client, app_id, cid)
        assert (await _bounce(client, cid)).status_code == 201
        verdict = await _check(client, cid, mid)
        assert verdict["reason"] == "email_bounced"
    records = audit_capture.records
    assert any(r.get("event") == "outreach_recorded" and r.get("entry") == "bounced" for r in records)
    assert any(r.get("event") == "outreach_check" and r.get("reason") == "email_bounced" for r in records)
    assert any(r.get("event") == "contact_added" and r.get("has_email_source") is True for r in records)
    dump = json.dumps(records, default=str)
    assert RITA not in dump
    assert "provider_verified" not in dump


def test_recipes_carry_email_source_and_bounce():
    from src.api.routes.recipes import load_recipe

    reach = load_recipe("reach").text
    assert "email_source" in reach
    assert len(reach.splitlines()) <= 70
    rules = load_recipe("rules").text
    assert "outreach_bounced" in rules
    assert "email_bounced" in rules
