"""An employer's confirmation email marks the job sent by itself (owner 2026-11-02; services/applications/auto_applied.py).

Real Postgres through the real doors: an assistant (bearer token) records over HTTP or MCP, the user reads
``/api/needs-you-count`` and undoes on the web. Pinned, one test per guardrail:
* an email tied to ONE application whose submit was CLEARED (check_submit said submit) and started (or its
  form filled) makes it ``applied`` (recorded by ``system``, the email stays the proof note) and the Confirm
  sent item goes; an assistant's own ``applied`` is still refused (#1469 not weakened);
* no clearance (B1), an email older than the fill, or a tie by the company name alone: the user is asked;
* an email that matches two applications changes nothing: the user is asked, as before;
* a rejection email never marks applied; nor does an email on a job with no submit or fill, or one the user
  said was not sent;
* a started submit with no email asks the user only after CONFIRM_SENT_WAIT_DAYS;
* the user's web Undo reverses it (and says "not sent"); an assistant cannot.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from tests._clearance import clear

ATS_SENDER = "Northwind via Greenhouse <no-reply@greenhouse-mail.io>"  # the ATS's own sender domain


def _email(n: int = 1, subject: str = "Thank you for applying to Northwind", sender: str = ATS_SENDER,
           received_at: str | None = None) -> dict[str, Any]:
    return {"kind": "email", "message_id": f"<confirm-{n}@mailer.example>", "sender": sender, "subject": subject,
            "received_at": received_at or datetime.now(timezone.utc).isoformat()}


async def _agent(web: AsyncClient) -> AsyncClient:
    from src.api.main import app

    resp = await web.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {resp.json()['token']}"})


async def _bring(web: AsyncClient, n: int, company: str = "Northwind", ats: bool = True) -> int:
    url = f"https://boards.greenhouse.io/northwind/jobs/{n}" if ats else f"https://careers.example/jobs/{n}"
    ad = {"title": f"Data Engineer {n}", "company": company, "location": "London", "country": "GB", "remote": False,
          "apply_url": url, "description": "Build the pipelines."}
    resp = await web.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _post(c: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> Any:
    return await c.post(f"/api/applications/{app_id}/events",
                        json={"event_type": event_type, "payload": payload or {}, **extra})


async def _start(c: AsyncClient, app_id: int, how: str = "submit_attempted") -> None:
    payload = {"form_url": "https://boards.greenhouse.io/apply", "fields_count": 3} if how == "form_filled" else {}
    resp = await _post(c, app_id, how, payload)
    assert resp.status_code == 201, resp.text


async def _confirmation(c: AsyncClient, app_id: int, n: int = 1, **kw: Any) -> Any:
    email = _email(n, **kw)
    return await _post(c, app_id, "note", detail=f"Submission confirmed: {email['subject']}", source=email)


async def _detail(web: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await web.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(detail: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [e for e in detail["events"] if e["event_type"] == event_type]


async def _confirm_sent_ids(web: AsyncClient) -> list[int]:
    resp = await web.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()["job_ids_by_kind"].get("confirm_sent", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["submit_attempted", "form_filled"])
async def test_an_email_matched_to_one_cleared_application_marks_it_applied(authenticated_async_context, how):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 1)
        async with await _agent(web) as agent:
            await _start(agent, app_id, how)
            refused = await _post(agent, app_id, "applied", source=_email(9))
            # #1469 and the applied rule still refuse an assistant's own `applied` (no CV yes, no clearance).
            assert refused.status_code == 409 and refused.json()["detail"].split(":")[0] in (
                "submit_rule_open", "submit_not_cleared")
            clearance = await clear(uid, app_id)  # what check_submit writes when it says `submit`
            resp = await _confirmation(agent, app_id)
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "applied" and resp.json()["event_type"] == "note"
        assert any(w.startswith("Job360 recorded this application as applied") for w in resp.json()["warnings"])
        detail = await _detail(web, app_id)
        assert detail["status"] == "applied"
        [applied] = _events(detail, "applied")
        assert applied["recorded_by"] == "system" and applied["payload"]["confirmed_by"] == "email"
        assert applied["payload"]["note_event_id"] == resp.json()["event_id"]
        assert "cv_not_approved" in applied["payload"]["open_at_submit"]  # stamped like the user's own click
        [start] = _events(detail, how)
        assert applied["occurred_at"] >= start["recorded_at"]  # never before the fill
        [note] = _events(detail, "note")
        assert note["detail"] == "Submission confirmed: Thank you for applying to Northwind"
        assert [e["id"] for e in _events(detail, "submit_cleared")] == [clearance]
        assert await _confirm_sent_ids(web) == []


@pytest.mark.asyncio
async def test_b1_no_clearance_means_no_auto_apply(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 10)
        async with await _agent(web) as agent:
            await _post(agent, app_id, "form_filled", {"form_url": "https://boards.greenhouse.io/apply", "fields_count": 3})
            resp = await _confirmation(agent, app_id)  # every email field is the caller's own text
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert _events(await _detail(web, app_id), "applied") == []
        assert await _confirm_sent_ids(web) == [app_id]  # the owner is asked "did it go out?", as before


@pytest.mark.asyncio
async def test_an_email_older_than_the_fill_means_no_auto_apply(authenticated_async_context):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 11)
        async with await _agent(web) as agent:
            await _start(agent, app_id)
            await clear(uid, app_id)
            old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            resp = await _confirmation(agent, app_id, received_at=old)
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert _events(await _detail(web, app_id), "applied") == []
        assert await _confirm_sent_ids(web) == [app_id]


@pytest.mark.asyncio
async def test_the_company_name_in_the_subject_alone_is_no_tie(authenticated_async_context):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 12, ats=False)  # a company site: no known sender domain
        async with await _agent(web) as agent:
            await _start(agent, app_id)
            await clear(uid, app_id)
            resp = await _confirmation(agent, app_id, sender="Talent <talent@mailer.example>")
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert _events(await _detail(web, app_id), "applied") == []
        assert await _confirm_sent_ids(web) == [app_id]


@pytest.mark.asyncio
async def test_an_email_matching_two_applications_changes_nothing(authenticated_async_context):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        first, second = await _bring(web, 2), await _bring(web, 3)  # both on the same ATS: the sender fits both
        async with await _agent(web) as agent:
            await _start(agent, first)
            await clear(uid, first)
            resp = await _confirmation(agent, first)
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert [_events(await _detail(web, i), "applied") for i in (first, second)] == [[], []]
        assert await _confirm_sent_ids(web) == [first]  # the user is asked, as before


@pytest.mark.asyncio
async def test_a_rejection_email_does_not_mark_applied(authenticated_async_context):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 4)
        async with await _agent(web) as agent:
            await _start(agent, app_id)
            await clear(uid, app_id)
            rejected = await _post(agent, app_id, "rejected", detail="Not moving forward", source=_email(1))
            assert rejected.status_code == 201, rejected.text
            same = await _confirmation(agent, app_id, 1)  # the same email again, now called a confirmation
            other = await _confirmation(agent, app_id, 2)  # a later one, on a rejected job
        assert same.json()["already_existed"] is True and same.json()["status"] == "rejected"
        assert other.status_code == 201 and other.json()["status"] == "rejected", other.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "rejected" and _events(detail, "applied") == []


@pytest.mark.asyncio
async def test_no_submit_no_fill_or_a_not_sent_means_the_user_is_asked(authenticated_async_context):
    async with authenticated_async_context() as web:
        bare, said_no = await _bring(web, 5, "Acme", ats=False), await _bring(web, 6, "Globex", ats=False)
        async with await _agent(web) as agent:
            await _start(agent, said_no)
        resolved = await web.post(f"/api/applications/{said_no}/submit/resolve")  # "It was not sent"
        assert resolved.status_code == 201, resolved.text
        async with await _agent(web) as agent:
            a = await _confirmation(agent, bare, 1, subject="Thank you for applying to Acme")
            b = await _confirmation(agent, said_no, 2, subject="Thank you for applying to Globex")
        assert (a.json()["status"], b.json()["status"]) == ("considering", "considering")
        assert [_events(await _detail(web, i), "applied") for i in (bare, said_no)] == [[], []]
        assert sorted(await _confirm_sent_ids(web)) == sorted([bare, said_no])


@pytest.mark.asyncio
async def test_no_email_after_the_wait_asks_the_user(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 7)
        async with await _agent(web) as agent:
            await _start(agent, app_id)
        assert await _confirm_sent_ids(web) == []  # still inside the wait: nobody is asked yet
        old = (datetime.now(timezone.utc) - timedelta(days=settings.CONFIRM_SENT_WAIT_DAYS, hours=1)).isoformat()
        with pgsync.connect(str(settings.DB_PATH)) as conn:
            conn.execute("UPDATE application_events SET recorded_at = %s WHERE application_id = %s "
                         "AND event_type = 'submit_attempted'", (old, app_id))
            conn.commit()
        assert await _confirm_sent_ids(web) == [app_id]
        [item] = [b for b in (await _detail(web, app_id))["blockers"] if b["kind"] == "confirm_sent"]
        assert item["actor"] == "you" and item["label"] == "no confirmation email in 3 days — did it go out?"
        assert item["ref"]["reason"] == "no_confirmation_email"


@pytest.mark.asyncio
async def test_the_users_web_undo_reverses_it_and_an_assistant_cannot(authenticated_async_context):
    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 8)
        async with await _agent(web) as agent:
            await _start(agent, app_id)
            await clear(uid, app_id)
            assert (await _confirmation(agent, app_id)).json()["status"] == "applied"
            assert (await agent.post(f"/api/applications/{app_id}/auto/undo")).status_code == 403
        undo = await web.post(f"/api/applications/{app_id}/auto/undo")
        assert undo.status_code == 201, undo.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "considering"
        [fix] = [e for e in _events(detail, "note") if e["corrects_event_id"] is not None]
        assert fix["corrects_event_id"] == _events(detail, "applied")[0]["id"]
        assert fix["detail"] == "Undone: you said it was not sent" and fix["recorded_by"] == "web"
        assert [e["payload"]["outcome"] for e in _events(detail, "submit_resolved")] == ["not_sent"]
        assert await _confirm_sent_ids(web) == []
        assert (await web.post(f"/api/applications/{app_id}/auto/undo")).status_code == 409


@pytest.mark.asyncio
async def test_the_mcp_record_event_door_marks_it_applied_too(authenticated_async_context):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(web, 9)
        async with await _agent(web) as agent:
            await _start(agent, app_id)
        await clear(uid, app_id)
        token = (await web.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                                  headers={"Authorization": f"Bearer {token}"})
        async with mcp_runtime():
            async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
                result = await mcp.call_tool("record_event", {
                    "application_id": app_id, "event_type": "note",
                    "detail": "Submission confirmed: Thank you for applying to Northwind", "source": _email()})
                assert not result.is_error, result.content[0].text
                assert json.loads(result.content[0].text)["status"] == "applied"
        assert (await _detail(web, app_id))["status"] == "applied"
