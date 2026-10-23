"""Feature 10 (G289): ``record_events`` (batch) + the append-only ``inbox_items`` store.

Real Postgres via ``authenticated_async_context``. Nothing is fetched, so no HTTP mocking.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from tests.test_application_spine import _second_user_session_cookie, _session_client

_NORTHWIND = {
    "title": "Data Engineer", "company": "Northwind Ltd", "location": "Remote",
    "apply_url": "https://northwind.example/careers/1", "description": "Build the pipelines. Python, dbt.",
}
_CONTOSO = {
    "title": "Platform Engineer", "company": "Contoso", "location": "London",
    "apply_url": "https://contoso.example/jobs/9", "description": "Own the platform. Kubernetes, Go.",
}
_MAIL = {
    "message_id": "u-1", "thread_id": "thr-globex-9", "sender": "talent@globex.example",
    "subject": "Your application at Globex", "received_at": "2026-10-18T09:30:00+00:00",
}


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()["application_id"]


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
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


async def _post(client: AsyncClient, **body: Any):
    return await client.post("/api/applications/record-events", json=body)


async def _ok(client: AsyncClient, **body: Any) -> dict[str, Any]:
    resp = await _post(client, **body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mail(message_id: str = "u-1", **over: Any) -> dict[str, Any]:
    return {**_MAIL, "message_id": message_id, **over}


async def _rows(table: str, where: str = "1=1") -> int:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {where}"  # noqa: S608
        return int(dict(conn.execute(sql).fetchone())["n"])
    finally:
        conn.close()


def _ev(app_id: int, event_type: str = "replied", **source: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"application_id": app_id, "event_type": event_type}
    if source:
        item["source"] = source
    return item


@pytest.mark.asyncio
async def test_batch_records_each_event_with_its_own_result(authenticated_async_context):
    async with authenticated_async_context() as client:
        a, b = await _bring(client, _NORTHWIND), await _bring(client, _CONTOSO)
        data = await _ok(client, events=[
            _ev(a, "replied", message_id="m-1", thread_id="thr-1"),
            _ev(b, "rejected", message_id="m-2"),
            _ev(987654321, "note"),
        ])
        r0, r1, r2 = data["events"]
        assert r0["ok"] and r0["event"]["event_type"] == "replied" and r0["event"]["status"] == "replied"
        assert r1["ok"] and r1["event"]["event_type"] == "rejected" and r1["event"]["status"] == "rejected"
        assert r2["ok"] is False and r2["error"].startswith("404")
        detail = (await client.get(f"/api/applications/{a}")).json()
        srcs = [e["source"] for e in detail["events"] if e.get("source")]
        assert any(s["message_id"] == "m-1" and s["thread_id"] == "thr-1" for s in srcs)


@pytest.mark.asyncio
async def test_repeat_message_id_writes_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        a, b = await _bring(client, _NORTHWIND), await _bring(client, _CONTOSO)
        evs = [_ev(a, "replied", message_id="m-1"), _ev(b, "rejected", message_id="m-2")]
        await _ok(client, events=evs)
        again = await _ok(client, events=evs)
        assert [r["event"]["already_existed"] for r in again["events"]] == [True, True]
        assert await _rows("application_events", "source_message_id = 'm-1'") == 1


@pytest.mark.asyncio
async def test_items_use_record_event_validators(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        single = await client.post(f"/api/applications/{a}/events", json={"event_type": "note", "scheduled_at": "2026-11-01T10:00:00+00:00"})
        assert single.status_code == 422
        data = await _ok(client, events=[
            {"application_id": a, "event_type": "note", "scheduled_at": "2026-11-01T10:00:00+00:00"},
            _ev(a, "replied", message_id="m-9", subject="a\nb"),
            {"application_id": a, "event_type": "outreach_sent"},
            {"event_type": "outreach_sent", "contact_id": 5, "channel": "email"},
            {"event_type": "note"},
        ])
        assert data["events"][0]["error"] == f"422: {single.json()['detail']}"
        assert data["events"][1]["error"] == "422: subject must not contain control characters"
        assert "contact_id" in data["events"][2]["error"]
        assert data["events"][3]["error"].startswith("422: a cold contact's outreach")
        assert data["events"][4]["error"].startswith("422: application_id is required")


@pytest.mark.asyncio
async def test_cap_empty_and_shape_errors_refuse_the_whole_call(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "RECORD_EVENTS_MAX_ITEMS", 2)
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        base_events = await _rows("application_events")
        r = await _post(client, events=[_ev(a, "note")] * 2, unmatched=[_mail()])
        assert r.status_code == 422 and "RECORD_EVENTS_MAX_ITEMS (2)" in r.json()["detail"]
        r = await _post(client)
        assert r.status_code == 422 and "give at least one" in r.json()["detail"]
        r = await _post(client, events=[{**_ev(a, "note"), "foo": 1}])
        assert r.status_code == 422
        assert await _rows("application_events") == base_events
        assert await _rows("inbox_items") == 0


@pytest.mark.asyncio
async def test_unmatched_is_stored_once_and_listed(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = (await _ok(client, unmatched=[_mail()]))["unmatched"][0]
        assert isinstance(first["inbox_item_id"], int) and first["already_existed"] is False
        assert first["resolution"] == ""
        second = (await _ok(client, unmatched=[_mail()]))["unmatched"][0]
        assert second["inbox_item_id"] == first["inbox_item_id"] and second["already_existed"] is True
        listed = (await client.get("/api/inbox-items")).json()
        assert listed["open_count"] == 1 and len(listed["items"]) == 1
        item = listed["items"][0]
        assert item["sender"] == "talent@globex.example" and item["thread_id"] == "thr-globex-9"
        assert item["received_at"] == "2026-10-18T09:30:00+00:00"
        news = (await client.get("/api/whats-new")).json()
        assert news["inbox_items"][0]["message_id"] == "u-1"


@pytest.mark.asyncio
async def test_unmatched_for_a_recorded_message_is_not_stored(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _ok(client, events=[_ev(a, "replied", message_id="m-5")])
        r = (await _ok(client, unmatched=[_mail("m-5")]))["unmatched"][0]
        assert r["already_recorded"] is True and r["application_id"] == a and r["inbox_item_id"] is None
        assert await _rows("inbox_items") == 0


@pytest.mark.asyncio
async def test_recording_the_event_resolves_the_open_item(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _ok(client, unmatched=[_mail("u-2")])
        ev = await client.post(
            f"/api/applications/{a}/events", json={"event_type": "replied", "source": _mail("u-2")}
        )
        assert ev.status_code == 201, ev.text
        listed = (await client.get("/api/inbox-items?status=all")).json()
        item = listed["items"][0]
        assert item["resolution"] == "matched" and item["application_id"] == a
        assert item["event_id"] == ev.json()["event_id"] and item["resolved_by"] == "web"
        assert listed["open_count"] == 0


@pytest.mark.asyncio
async def test_web_resolve_match_dismiss_and_refusals(authenticated_async_context):
    async with authenticated_async_context() as client:
        b = await _bring(client, _NORTHWIND)
        c = await _bring(client, _CONTOSO)
        out = (await _ok(client, unmatched=[_mail("u-1"), _mail("u-2")]))["unmatched"]
        id1, id2 = out[0]["inbox_item_id"], out[1]["inbox_item_id"]
        before = await _rows("application_events")
        m = await client.post(f"/api/inbox-items/{id1}/resolve", json={"application_id": c})
        assert m.status_code == 200, m.text
        body = m.json()
        assert body["resolution"] == "matched" and body["job_company"] == "Contoso" and body["event_id"] is None
        assert await _rows("application_events") == before
        news = (await client.get("/api/whats-new")).json()
        assert any(i["id"] == id1 and i["resolution"] == "matched" for i in news["inbox_items"])
        d = await client.post(f"/api/inbox-items/{id2}/resolve", json={"dismiss": True})
        assert d.json()["resolution"] == "dismissed"
        assert (await client.post(f"/api/inbox-items/{id1}/resolve", json={"dismiss": True})).status_code == 409
        both = await client.post(f"/api/inbox-items/{id1}/resolve", json={"application_id": b, "dismiss": True})
        assert both.status_code == 422
        assert (await client.post(f"/api/inbox-items/{id1}/resolve", json={})).status_code == 422
        third = (await _ok(client, unmatched=[_mail("u-3")]))["unmatched"][0]["inbox_item_id"]
        cookie = await _second_user_session_cookie()
        async with _session_client(cookie) as client2:
            a2 = await _bring(client2, _NORTHWIND)
            assert (await client2.post(f"/api/inbox-items/{third}/resolve", json={"application_id": a2})).status_code == 404
        assert (await client.post(f"/api/inbox-items/{third}/resolve", json={"application_id": 987654321})).status_code == 404


@pytest.mark.asyncio
async def test_dismissed_caps_and_audit_via(authenticated_async_context, monkeypatch, audit_capture):
    from src.core import settings

    async with authenticated_async_context() as client:
        r = (await _ok(client, unmatched=[{**_mail("u-d"), "dismissed": True}]))["unmatched"][0]
        assert r["resolution"] == "dismissed"
        assert (await client.get("/api/inbox-items")).json()["items"] == []
        assert [i["resolution"] for i in (await client.get("/api/whats-new")).json()["inbox_items"]] == ["dismissed"]
        await _ok(client, unmatched=[_mail("u-open")])
        again = await _ok(client, unmatched=[{**_mail("u-open"), "dismissed": True}])  # the assistant dismisses it
        assert again["unmatched"][0]["resolution"] == "dismissed"
        vias = [x.get("via") for x in audit_capture.records if x.get("event") == "inbox_item_resolved"]
        assert vias == ["record_events"]
        monkeypatch.setattr(settings, "INBOX_ITEMS_MAX_OPEN_PER_USER", 1)
        r = (await _ok(client, unmatched=[_mail("u-1"), _mail("u-2")]))["unmatched"]
        assert r[0]["ok"] and r[1]["error"].startswith("429") and "INBOX_ITEMS_MAX_OPEN_PER_USER" in r[1]["error"]
        monkeypatch.setattr(settings, "INBOX_ITEMS_MAX_PER_USER", 3)  # 3 rows stored: even a dismissed one is refused
        r = (await _ok(client, unmatched=[{**_mail("u-3"), "dismissed": True}]))["unmatched"][0]
        assert r["error"].startswith("429") and "INBOX_ITEMS_MAX_PER_USER" in r["error"]


@pytest.mark.asyncio
async def test_outreach_event_closes_the_open_item(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        c = await client.post(f"/api/applications/{a}/contacts", json={"name": "Pat Recruiter", "role": "Recruiter"})
        await _ok(client, unmatched=[_mail("u-7")])
        rec = await client.post(f"/api/applications/{a}/events", json={
            "event_type": "outreach_replied", "contact_id": c.json()["contact"]["id"], "channel": "email",
            "source": _mail("u-7"),
        })
        assert rec.status_code == 201, rec.text
        item = (await client.get("/api/inbox-items?status=all")).json()["items"][0]
        assert item["resolution"] == "matched" and item["application_id"] == a


@pytest.mark.asyncio
async def test_logs_ids_never_the_mail(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        data = await _ok(client, events=[_ev(a, "replied", message_id="m-log-1", sender="talent@globex.example"),
                                         _ev(a, "note")], unmatched=[_mail("u-log")])
        await client.post(f"/api/inbox-items/{data['unmatched'][0]['inbox_item_id']}/resolve", json={"dismiss": True})
        for rec in audit_capture.records:
            text = str(rec)
            for secret in ("talent@globex.example", "Your application at Globex", "thr-globex-9", "m-log-1", "u-log"):
                assert secret not in text
        mine = [r for r in audit_capture.records if r.get("event") == "record_events"]
        assert mine and mine[-1]["events_count"] == 2


@pytest.mark.asyncio
async def test_mcp_tool_returns_the_route_result(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    user = m.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"
    )
    tools = m.build_server()._tool_manager
    token = m._current_user.set(user)
    try:
        async with authenticated_async_context() as client:
            a = await _bring(client, _NORTHWIND)
            out = await tools.get_tool("record_events").fn(
                events=[_ev(a, "replied", message_id="m-t")], unmatched=[_mail("u-t")]
            )
            assert out["events"][0]["ok"] and out["events"][0]["event"]["event_type"] == "replied"
            assert out["unmatched"][0]["ok"] and isinstance(out["unmatched"][0]["inbox_item_id"], int)
            with pytest.raises(Exception):
                await tools.get_tool("record_events").fn(events=[{"bogus": 1}])
            with pytest.raises(Exception) as exc:
                await tools.get_tool("record_events").fn()
            assert "give at least one" in str(exc.value)
    finally:
        m._current_user.reset(token)


def test_inbox_items_rows_are_write_once():
    src = Path(__file__).resolve().parent.parent / "src"
    service = src / "services" / "applications" / "inbox_items.py"
    updates = 0
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert not re.search(r"DELETE\s+FROM\s+inbox_items", text, re.IGNORECASE), py
        n = len(re.findall(r"UPDATE\s+inbox_items", text, re.IGNORECASE))
        if n:
            assert py == service, py
            updates += n
    text = service.read_text(encoding="utf-8")
    assert updates == 1 and len(re.findall(r"WHERE id = \? AND user_id = \? AND resolution = ''", text)) == 1


def test_inbox_items_is_erased_and_exported():
    from src.repositories.database import JobDatabase

    assert "inbox_items" in JobDatabase._PER_USER_TABLES
    assert "inbox_items" in JobDatabase._EXPORT_TABLES


def test_recipes_teach_unmatched_mail():
    from src.api.routes.recipes import load_recipe

    daily = load_recipe("daily").text
    assert "record_events" in daily and "inbox_items" in daily and "match_email" in daily
    assert len(daily.splitlines()) == 70
    rules = load_recipe("rules").text
    assert "UNMATCHED MAIL" in rules and "dismissed" in rules
