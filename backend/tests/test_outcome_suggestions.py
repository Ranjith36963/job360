"""Feature 9 (G073): ``source.quote`` on email events + the ``outcome_suggested`` note.

Real Postgres via ``authenticated_async_context``. Nothing is fetched, so no HTTP mocking.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_NORTHWIND = {
    "title": "Data Engineer",
    "company": "Northwind Ltd",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/1",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any] = _NORTHWIND) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()["application_id"]


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
    assert cookie
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


async def _post(client: AsyncClient, app_id: int, body: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json=body)


async def _applied(client: AsyncClient, app_id: int) -> None:
    resp = await _post(client, app_id, {"event_type": "applied"})
    assert resp.status_code == 201, resp.text


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _src(mid: str, **extra: Any) -> dict[str, Any]:
    return {"message_id": mid, "sender": "hr@northwind.example", "subject": "Update",
            "received_at": "2026-10-02T08:00:00+00:00", **extra}


def _suggest_body(mid: str = "<s-1@northwind.example>", **payload: Any) -> dict[str, Any]:
    pl = {"event_type": "rejected", "quote": "We have decided\nnot to proceed.",
          "why": "Might be about the other Northwind role", **payload}
    return {"event_type": "outcome_suggested", "payload": pl, "source": _src(mid)}


async def _open(client: AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get("/api/applications/outcome-suggestions")
    assert resp.status_code == 200, resp.text
    return resp.json()["suggestions"]


@pytest.mark.asyncio
async def test_source_quote_is_stored_collapsed_and_shown(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _applied(client, app_id)
        source = {
            "message_id": "<r-1@northwind.example>", "sender": "Dana <dana@northwind.example>",
            "subject": "Your application", "received_at": "2026-10-01T09:00:00+00:00",
            "quote": "Unfortunately we will not be moving forward\nwith your application.\tThanks",
        }
        resp = await _post(client, app_id, {"event_type": "rejected", "source": source})
        assert resp.status_code == 201, resp.text
        detail = await _detail(client, app_id)
        wn = await client.get("/api/whats-new")
    want = "Unfortunately we will not be moving forward with your application. Thanks"
    ev = next(e for e in detail["events"] if e["event_type"] == "rejected")
    assert ev["source"]["quote"] == want
    assert next(e for e in wn.json()["events"] if e["event_type"] == "rejected")["source"]["quote"] == want
    assert detail["status"] == "rejected"


@pytest.mark.asyncio
async def test_source_quote_refusals_and_live_cap(authenticated_async_context, monkeypatch):
    from src.core import settings

    async with authenticated_async_context() as client:
        app_id = await _bring(client)

        async def rec(quote: str, mid: str):
            return await _post(client, app_id, {"event_type": "note", "source": _src(mid, quote=quote)})

        r = await rec("bad\x00char", "<q-1>")
        assert r.status_code == 422 and "quote must not contain control characters" in r.text
        r = await rec("x" * 501, "<q-2>")
        assert r.status_code == 422 and "APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS (500 chars)" in r.text
        monkeypatch.setattr(settings, "APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS", 20)
        assert (await rec("y" * 21, "<q-3>")).status_code == 422
        assert (await rec("y" * 20, "<q-4>")).status_code == 201
        r = await rec("  \n ", "<q-5>")
        assert r.status_code == 201, r.text
        detail = await _detail(client, app_id)
    blank = next(e for e in detail["events"] if e["source"] and e["source"]["message_id"] == "<q-5>")
    assert blank["source"]["quote"] is None


@pytest.mark.asyncio
async def test_quote_refused_on_outreach_doors(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        r = await _post(client, app_id, {
            "event_type": "outreach_sent", "contact_id": 987654321, "channel": "email",
            "source": _src("<o-1>", quote="Hi"),
        })
        assert r.status_code == 422 and "source.quote is kept on application events only" in r.text
        r = await client.post("/api/contacts/987654321/outreach", json={
            "entry": "sent", "channel": "email", "source": _src("<o-1>", quote="Hi"),
        })
        assert r.status_code == 422 and "source.quote is kept on application events only" in r.text


@pytest.mark.asyncio
async def test_outcome_suggested_never_moves_status_and_logs_no_text(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _applied(client, app_id)
        resp = await _post(client, app_id, _suggest_body())
        assert resp.status_code == 201, resp.text
        assert resp.json()["already_existed"] is False
        detail = await _detail(client, app_id)
    assert detail["status"] == "applied"
    ev = next(e for e in detail["events"] if e["event_type"] == "outcome_suggested")
    assert ev["source"] is None
    assert ev["payload"]["quote"] == "We have decided not to proceed."
    assert ev["payload"]["email"]["message_id"] == "<s-1@northwind.example>"
    assert ev["payload"]["email"]["received_at"] == "2026-10-02T08:00:00+00:00"
    recs = [r for r in caplog.records if getattr(r, "event", "") == "outcome_suggestion"]
    assert recs and recs[0].result == "ok"
    for secret in ("not to proceed", "Northwind role", "<s-1@"):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_outcome_suggested_refusals(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        r = await _post(client, app_id, {**_suggest_body(), "source": None})
        assert r.status_code == 422 and "needs source" in r.text
        body = _suggest_body()
        body["source"]["quote"] = "in the wrong place"
        r = await _post(client, app_id, body)
        assert r.status_code == 422 and "payload.quote, not source.quote" in r.text
        bad = {"event_type": "outcome_suggested", "source": _src("<b-1>"),
               "payload": {"event_type": "note", "quote": "q", "extra": 1}}
        r = await _post(client, app_id, bad)
        assert r.status_code == 422
        for needle in ("nothing else", "event_type must be one of", "why must", "Valid example payload:"):
            assert needle in r.text, needle
        r = await _post(client, app_id, {**_suggest_body("<b-2>"), "corrects_event_id": 1})
        assert r.status_code == 422 and "takes no corrects_event_id" in r.text
        r = await _post(client, app_id, {**_suggest_body("<b-3>"), "follow_up_on": (date.today() + timedelta(days=2)).isoformat()})
        assert r.status_code == 422 and "takes no corrects_event_id or follow_up_on" in r.text


@pytest.mark.asyncio
async def test_repeat_suggestion_and_recorded_message_return_the_first(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _applied(client, app_id)
        first = (await _post(client, app_id, _suggest_body())).json()
        again = await _post(client, app_id, _suggest_body())
        assert again.status_code == 201
        assert again.json()["already_existed"] is True
        assert again.json()["event_id"] == first["event_id"]
        real = await _post(client, app_id, {"event_type": "replied", "source": _src("<x-1@northwind.example>")})
        assert real.status_code == 201
        sug = await _post(client, app_id, _suggest_body("<x-1@northwind.example>"))
        body = sug.json()
        assert body["already_existed"] is True
        assert body["event_type"] == "replied"
        assert any("replied" in w for w in body["warnings"])


@pytest.mark.asyncio
async def test_accept_records_real_event_as_web_and_closes_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _applied(client, app_id)
        await _post(client, app_id, _suggest_body())
        items = await _open(client)
        assert len(items) == 1
        s = items[0]
        assert s["job_company"] == "Northwind Ltd"
        assert s["event_type"] == "rejected"
        assert s["quote"] == "We have decided not to proceed."
        assert s["why"] == "Might be about the other Northwind role"
        assert s["email"]["message_id"] == "<s-1@northwind.example>"
        accept = {"event_type": s["event_type"], "occurred_at": s["occurred_at"], "source": {
            "kind": s["email"]["kind"], "message_id": s["email"]["message_id"],
            "sender": s["email"]["sender"], "subject": s["email"]["subject"],
            "received_at": s["email"]["received_at"], "quote": s["quote"],
        }}
        resp = await _post(client, app_id, accept)
        assert resp.status_code == 201, resp.text
        assert resp.json()["already_existed"] is False
        detail = await _detail(client, app_id)
        assert await _open(client) == []
    real = next(e for e in detail["events"] if e["event_type"] == "rejected")
    assert real["recorded_by"] == "web"
    assert real["source"]["quote"] == s["quote"]
    assert detail["status"] == "rejected"


@pytest.mark.asyncio
async def test_ignore_records_dismissal_and_closes_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _applied(client, app_id)
        sid = (await _post(client, app_id, _suggest_body())).json()["event_id"]
        note = (await _post(client, app_id, {"event_type": "note", "detail": "hi"})).json()["event_id"]
        other = await _bring(client, {**_NORTHWIND, "title": "SRE", "apply_url": "https://northwind.example/careers/2"})
        cookie2 = await _second_user_session_cookie()
        async with _session_client(cookie2) as c2:
            app2 = await _bring(c2, {**_NORTHWIND, "apply_url": "https://northwind.example/careers/3"})
            r = await _post(c2, app2, {"event_type": "outcome_dismissed", "payload": {"suggestion_event_id": sid}})
            assert r.status_code == 422
            assert await _open(c2) == []
        r = await _post(client, app_id, {"event_type": "outcome_dismissed", "payload": {"suggestion_event_id": note}})
        assert r.status_code == 422 and "must be an outcome_suggested event on this application" in r.text
        r = await _post(client, other, {"event_type": "outcome_dismissed", "payload": {"suggestion_event_id": sid}})
        assert r.status_code == 422
        r = await _post(client, app_id, {"event_type": "outcome_dismissed", "payload": {"suggestion_event_id": sid}})
        assert r.status_code == 201, r.text
        assert (await _detail(client, app_id))["status"] == "applied"
        assert await _open(client) == []
        r = await _post(client, app_id, {"event_type": "outcome_dismissed", "payload": {"suggestion_event_id": sid}})
        assert r.status_code == 409 and "already ignored" in r.text


def test_outcome_suggestions_route_order_and_auth():
    from fastapi.testclient import TestClient

    from src.api.main import app
    from tests._routes import route_paths

    paths = route_paths(app)
    assert paths.index("/api/applications/outcome-suggestions") < paths.index("/api/applications/{application_id}")
    assert TestClient(app).get("/api/applications/outcome-suggestions").status_code == 401


@pytest.mark.asyncio
async def test_mcp_record_event_stores_quote(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    token = m._current_user.set(m.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    ))
    try:
        async with authenticated_async_context() as client:
            app_id = await _bring(client)
            quote = "We would like to invite you to interview."
            await m.build_server()._tool_manager.get_tool("record_event").fn(
                event_type="interview_requested", application_id=app_id,
                source=_src("<m-1@northwind.example>", quote=quote),
            )
            detail = await _detail(client, app_id)
    finally:
        m._current_user.reset(token)
    ev = next(e for e in detail["events"] if e["event_type"] == "interview_requested")
    assert ev["source"]["quote"] == quote
