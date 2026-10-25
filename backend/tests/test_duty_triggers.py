"""Ignition (owner 2026-10-24): one start link per duty, Run now / Test, cool-down, hourly cap, 429.

services/browser_agent/duty_trigger.py and routes/assistant_trigger.py. The routine endpoint is mocked
with aioresponses (rule #4); the guard's resolver is a fake giving every test host a public address
(``inside.example`` a private one), so nothing leaves the machine. Helpers are defined here, never
imported (per-test schema isolation).
"""
from __future__ import annotations

import contextlib
import socket

import pytest
from aioresponses import aioresponses
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

HUNT = "https://api.anthropic.com/v1/claude_code/routines/trig_HUNT/fire"
SEND = "https://api.anthropic.com/v1/claude_code/routines/trig_SEND/fire"
TOKEN = "sk-ant-oat01-hunt-secret-XYZ"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("BROWSER_SIGN_IN_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    from src.services.fetch import fetcher

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        ip = "10.1.2.3" if host == "inside.example" else "93.184.216.34"
        return [{"hostname": host, "host": ip, "port": port, "family": socket.AF_INET, "proto": 0, "flags": 0}]

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", resolve)


def _calls(mocked) -> list:
    return [c for _key, cs in mocked.requests.items() for c in cs]


async def _row(user_id: str, duty: str) -> dict:
    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        cur = await db._db.execute(
            "SELECT * FROM assistant_duty_triggers WHERE user_id = ? AND duty = ?", (user_id, duty)
        )
        return dict(await cur.fetchone())


@pytest.mark.asyncio
async def test_each_duty_has_its_own_link_and_apply_is_the_send_link(authenticated_async_context):
    async with authenticated_async_context() as client:
        duties = (await client.get("/api/assistant-duty-triggers")).json()["duties"]
        assert [(d["duty"], d["label"], d["set"]) for d in duties] == [
            ("hunt", "Hunt", False), ("batch", "Apply", False), ("inbox", "Gmail check", False),
            ("reach", "People hunt", False),
        ]
        saved = await client.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT, "token": TOKEN})
        assert saved.status_code == 200, saved.text
        assert saved.json()["set"] and saved.json()["has_token"] and saved.json()["host"] == "api.anthropic.com"
        assert TOKEN not in saved.text and "trig_HUNT" not in saved.text
        await client.put("/api/assistant-duty-triggers/batch", json={"url": SEND})
        assert (await client.get("/api/assistant-trigger")).json()["set"] is True  # the Send link
        listed = (await client.get("/api/assistant-duty-triggers")).json()["duties"]
        assert [d["set"] for d in listed] == [True, True, False, False]
        assert TOKEN not in str(listed)
        for bad in ("http://x.example/fire", "https://user@x.example/", "nope"):
            assert (await client.put("/api/assistant-duty-triggers/hunt", json={"url": bad})).status_code == 422
        assert (await client.put("/api/assistant-duty-triggers/apply", json={"url": HUNT})).status_code == 404
        assert (await client.delete("/api/assistant-duty-triggers/hunt")).json() == {"deleted": True}


@pytest.mark.asyncio
async def test_the_token_is_encrypted_at_rest(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/reach", json={"url": HUNT, "token": TOKEN})
    row = await _row(fixture_user_id, "reach")
    assert row["token_enc"] and TOKEN not in row["token_enc"]


@pytest.mark.asyncio
async def test_run_now_starts_the_routine_once_with_fixed_words(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.post("/api/assistant-duty-triggers/hunt/run")).json() == {
            "fired": False, "reason": "not_set"
        }
        await client.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT, "token": TOKEN})
        with aioresponses() as mocked:
            mocked.post(HUNT, status=200, payload={"claude_code_session_url": "https://claude.ai/code/session_X"})
            first = (await client.post("/api/assistant-duty-triggers/hunt/run")).json()
            again = (await client.post("/api/assistant-duty-triggers/hunt/run", json={"test": True})).json()
            calls = _calls(mocked)
        assert first == {"fired": True, "duty": "hunt", "session_url": "https://claude.ai/code/session_X"}
        assert again["fired"] is False and again["reason"] == "cooldown"  # two presses = one start
        assert len(calls) == 1
        assert calls[0].kwargs["allow_redirects"] is False
        assert calls[0].kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
        text = calls[0].kwargs["json"]["text"]
        assert "Run now" in text and "duty `hunt`" in text and "whats_due" in text
        assert "never the user's yes to submit" in text and "check_submit" in text
        view = [d for d in (await client.get("/api/assistant-duty-triggers")).json()["duties"] if d["duty"] == "hunt"]
        assert view[0]["last_result"] == "started"


@pytest.mark.asyncio
async def test_test_button_says_it_is_a_test(authenticated_async_context):
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/inbox", json={"url": HUNT})
        with aioresponses() as mocked:
            mocked.post(HUNT, status=200, payload={})
            out = (await client.post("/api/assistant-duty-triggers/inbox/run", json={"test": True})).json()
            text = _calls(mocked)[0].kwargs["json"]["text"]
        assert out["fired"] is True and "pressed Test" in text and "Gmail check" in text


@pytest.mark.asyncio
async def test_the_hourly_cap_holds_starts(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.services.browser_agent import duty_trigger

    monkeypatch.setattr(duty_trigger, "COOLDOWN_S", 0)
    monkeypatch.setattr(duty_trigger, "FIRES_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/reach", json={"url": HUNT})
        with aioresponses() as mocked:
            mocked.post(HUNT, status=200, payload={}, repeat=True)
            outs = [(await client.post("/api/assistant-duty-triggers/reach/run")).json() for _ in range(3)]
            calls = _calls(mocked)
        assert [o["fired"] for o in outs] == [True, True, False]
        assert outs[2]["reason"] == "hourly_cap" and len(calls) == 2
    assert (await _row(fixture_user_id, "reach"))["hour_count"] == 2


@pytest.mark.asyncio
async def test_a_429_is_obeyed_and_never_retried(authenticated_async_context, monkeypatch):
    from src.services.browser_agent import duty_trigger

    monkeypatch.setattr(duty_trigger, "COOLDOWN_S", 0)
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT})
        with aioresponses() as mocked:
            mocked.post(HUNT, status=429, headers={"Retry-After": "600"}, repeat=True)
            busy = (await client.post("/api/assistant-duty-triggers/hunt/run")).json()
            held = (await client.post("/api/assistant-duty-triggers/hunt/run")).json()
            calls = _calls(mocked)
        assert busy["fired"] is False and busy["reason"] == "rate_limited" and busy["retry_until"]
        assert held["reason"] == "rate_limited" and len(calls) == 1  # waited as asked, no retry


@pytest.mark.asyncio
async def test_a_private_address_is_refused_by_the_guard(authenticated_async_context):
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/hunt", json={"url": "https://inside.example/hook"})
        with aioresponses() as mocked:
            out = (await client.post("/api/assistant-duty-triggers/hunt/run")).json()
            assert _calls(mocked) == []
        assert out["fired"] is False and out["reason"] == "refused"


@pytest.mark.asyncio
async def test_no_assistant_can_read_change_or_fire_a_duty_link(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        assert (await agent.get("/api/assistant-duty-triggers")).status_code == 403
        assert (await agent.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT})).status_code == 403
        assert (await agent.delete("/api/assistant-duty-triggers/hunt")).status_code == 403
        assert (await agent.post("/api/assistant-duty-triggers/hunt/run")).status_code == 403


def test_no_mcp_tool_reaches_a_duty_link():
    from pathlib import Path

    from src.api import mcp_server

    text = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "duty_trigger" not in text and "assistant-duty-triggers" not in text


def test_the_start_text_is_fixed_words_and_ids_only():
    from src.services.browser_agent import duty_trigger

    text = duty_trigger.message("reach", "job_queued", [12, 7])
    assert "Applications: 12, 7." in text and "a new job was queued" in text
    for cause in duty_trigger.CAUSES:
        for duty in duty_trigger.DUTIES:
            assert "whats_due" in duty_trigger.message(duty, cause)
    with pytest.raises(duty_trigger.DutyError):
        duty_trigger.check_duty("apply")


@pytest.mark.asyncio
async def test_send_obeys_a_429_and_shares_the_hourly_cap(authenticated_async_context, monkeypatch):
    """The Send link is the batch duty's link: a Send obeys its Retry-After and counts toward its cap."""
    from src.services.browser_agent import duty_trigger

    monkeypatch.setattr(duty_trigger, "COOLDOWN_S", 0)
    async with authenticated_async_context() as client:
        bring = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Northwind", "location": "London",
            "description": "Build the pipelines. " * 8, "apply_url": "https://northwind.example/careers/7",
        })
        app_id = int(bring.json()["application_id"])
        await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        await client.put("/api/assistant-trigger", json={"url": SEND})
        with aioresponses() as mocked:
            mocked.post(SEND, status=429, repeat=True)
            busy = (await client.post("/api/assistant-trigger/fire")).json()
            held = (await client.post("/api/assistant-trigger/fire")).json()
            run = (await client.post("/api/assistant-duty-triggers/batch/run")).json()
            calls = _calls(mocked)
        assert busy["reason"] == "rate_limited"
        assert held["reason"] == "rate_limited" and run["reason"] == "rate_limited"
        assert len(calls) == 1  # waited as asked: neither the next Send nor Run now called again
        monkeypatch.setattr(duty_trigger, "FIRES_PER_HOUR", 1)
        from src.api.dependencies import get_request_db

        async with contextlib.asynccontextmanager(get_request_db)() as db:
            await db._db.execute("UPDATE assistant_triggers SET retry_until = NULL")
            await db._db.commit()
        capped = (await client.post("/api/assistant-trigger/fire")).json()
        assert capped["reason"] == "hourly_cap"
