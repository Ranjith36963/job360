"""Weak spot #4 (2026-10-15) — a repeat ``applied`` is the confirmation, not a second status event.

Live: application 101 had ``applied`` from record_application; the assistant then recorded a SECOND
``record_event applied`` with the Lever confirmation email as ``source`` to raise proof to "email", and the
timeline showed two "applied" entries. Now the repeat is stored as a ``note`` whose detail starts
"Submission confirmed" (source, occurred_at and follow_up_on kept; proof.py counts it as email proof).
Real doors (HTTP + MCP), VALUES asserted (rule #21). Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings

_AD = {
    "title": "AI Engineer",
    "company": "Leverworks",
    "location": "London",
    "apply_url": "https://jobs.lever.co/leverworks/101",
    "description": "RAG, Python, LLM APIs. Ship agents to production.",
}
FOLLOW_UP = (date.today() + timedelta(days=30)).isoformat()
_AD_2 = {**_AD, "title": "Data Engineer", "company": "Northwind", "apply_url": "https://northwind.example/c/7"}
EMAIL = {
    "kind": "email",
    "message_id": "<confirm-101@hire.lever.co>",
    "sender": "no-reply@hire.lever.co",
    "subject": "Thank you for applying to Leverworks",
    "received_at": "2026-10-14T09:30:00+00:00",
}
WARNING = (
    "this application is already applied; recorded as a submission confirmation (proof), not a second applied event"
)


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _receipt(client: AsyncClient, app_id: int) -> None:
    """record_application's own door — writes the FIRST applied."""
    resp = await client.post(f"/api/applications/{app_id}/receipt", json={})
    assert resp.status_code == 201, resp.text


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rows(app_id: int) -> list[dict[str, Any]]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(
            "SELECT id, event_type, detail, occurred_at, source_kind, source_message_id, source_sender, "
            "corrects_event_id, payload FROM application_events WHERE application_id = ? ORDER BY id",
            (app_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


async def _proof_level(client: AsyncClient, app_id: int) -> str:
    resp = await client.get(f"/api/applications/{app_id}/proof")
    assert resp.status_code == 200, resp.text
    return resp.json()["proof"]["level"]


async def _status(client: AsyncClient, app_id: int) -> str:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


@pytest.mark.asyncio
async def test_repeat_applied_with_email_becomes_the_submission_confirmation(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt(client, app_id)
        assert await _proof_level(client, app_id) == "none"
        before = _rows(app_id)
        assert [r["event_type"] for r in before].count("applied") == 1

        body = await _event(
            client, app_id, "applied", detail="Lever says the application was received",
            source=EMAIL, occurred_at="2026-10-14T09:30:00+00:00", follow_up_on=FOLLOW_UP,
        )
        assert body["event_type"] == "note"
        assert body["already_existed"] is False
        assert body["status"] == "applied"
        assert body["warnings"] == [WARNING]
        assert body["follow_up_on"] == FOLLOW_UP

        after = _rows(app_id)
        assert len(after) == len(before) + 1
        new = after[-1]
        assert new["id"] == body["event_id"]
        assert new["event_type"] == "note"
        assert new["detail"] == "Submission confirmed: Lever says the application was received"
        assert new["source_kind"] == "email"
        assert new["source_message_id"] == "<confirm-101@hire.lever.co>"
        assert new["source_sender"] == "no-reply@hire.lever.co"
        assert str(new["occurred_at"]).startswith("2026-10-14T09:30:00")
        assert json.loads(new["payload"])["follow_up_on"] == FOLLOW_UP
        assert [r["event_type"] for r in after].count("applied") == 1, "never a second applied"

        assert await _status(client, app_id) == "applied"
        assert await _proof_level(client, app_id) == "email"


@pytest.mark.asyncio
async def test_repeat_applied_without_detail_is_just_the_prefix(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        body = await _event(client, app_id, "applied", source=EMAIL)
        assert (body["event_type"], body["warnings"]) == ("note", [WARNING])
        assert _rows(app_id)[-1]["detail"] == "Submission confirmed"
        assert await _proof_level(client, app_id) == "email"


@pytest.mark.asyncio
async def test_same_message_id_again_already_exists_and_writes_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt(client, app_id)
        first = await _event(client, app_id, "applied", source=EMAIL)
        assert first["event_type"] == "note"
        count = len(_rows(app_id))

        again = await _event(client, app_id, "applied", source=EMAIL)
        assert again["already_existed"] is True
        assert again["event_id"] == first["event_id"]
        assert again["event_type"] == "note"
        assert again["warnings"] == [], "the same fact again is not a type mismatch"
        assert len(_rows(app_id)) == count

        # A message id first stored as a REAL applied (no earlier applied) dedupes the same way.
        other = await _bring(client, _AD_2)
        email_2 = {**EMAIL, "message_id": "<confirm-7@northwind.example>"}
        real = await _event(client, other, "applied", source=email_2)
        assert real["event_type"] == "applied"
        n = len(_rows(other))
        repeat = await _event(client, other, "applied", source=email_2)
        assert (repeat["already_existed"], repeat["event_id"], repeat["event_type"]) == (
            True, real["event_id"], "applied",
        )
        assert repeat["warnings"] == []
        assert len(_rows(other)) == n


@pytest.mark.asyncio
async def test_first_applied_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        body = await _event(client, app_id, "applied", detail="applied on Lever", source=EMAIL)
        assert body["event_type"] == "applied"
        assert body["status"] == "applied"
        assert body["warnings"] == []
        row = _rows(app_id)[-1]
        assert (row["event_type"], row["detail"], row["source_kind"]) == ("applied", "applied on Lever", "email")
        assert await _proof_level(client, app_id) == "email"


@pytest.mark.asyncio
async def test_applied_with_corrects_event_id_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _event(client, app_id, "applied", occurred_at="2026-10-01T10:00:00+00:00")
        fixed = await _event(
            client, app_id, "applied", detail="actually applied a day later",
            occurred_at="2026-10-02T10:00:00+00:00", corrects_event_id=first["event_id"],
        )
        assert fixed["event_type"] == "applied"
        assert fixed["warnings"] == []
        row = _rows(app_id)[-1]
        assert (row["event_type"], row["corrects_event_id"], row["detail"]) == (
            "applied", first["event_id"], "actually applied a day later",
        )
        assert await _status(client, app_id) == "applied"


@pytest.mark.asyncio
async def test_record_application_after_applied_is_unaffected(authenticated_async_context):
    """The receipt path keeps writing its own applied (only record_event converts)."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _receipt(client, app_id)
        assert [r["event_type"] for r in _rows(app_id)].count("applied") == 2


@pytest.mark.asyncio
async def test_stats_count_the_application_once(authenticated_async_context):
    from src.api import dependencies
    from src.services.applications.stats import compute_stats

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt(client, app_id)
        await _event(client, app_id, "applied", source=EMAIL)
        await _event(client, app_id, "applied", detail="second confirmation, no source")
        assert [r["event_type"] for r in _rows(app_id)].count("applied") == 1
        db = await dependencies.get_db()
        overall = (await compute_stats(db, authenticated_async_context.fixture_user_id))["overall"]
        assert overall["brought"] == 1
        assert overall["applied"] == 1


@pytest.mark.asyncio
async def test_mcp_tool_behaves_like_the_route(authenticated_async_context):
    pytest.importorskip("mcp")
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt(client, app_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:

        async def call(**args: Any) -> dict[str, Any]:
            result = await mcp.call_tool("record_event", args)
            assert not result.is_error, result.content[0].text
            return json.loads(result.content[0].text)

        body = await call(event_type="applied", application_id=app_id, detail="Lever receipt", source=EMAIL)
        assert (body["event_type"], body["status"], body["already_existed"]) == ("note", "applied", False)
        assert body["warnings"] == [WARNING]
        again = await call(event_type="applied", application_id=app_id, source=EMAIL)
        assert (again["already_existed"], again["event_id"], again["warnings"]) == (True, body["event_id"], [])

    rows = _rows(app_id)
    assert [r["event_type"] for r in rows].count("applied") == 1
    assert rows[-1]["detail"] == "Submission confirmed: Lever receipt"
    assert rows[-1]["source_message_id"] == "<confirm-101@hire.lever.co>"
