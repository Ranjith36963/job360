"""F8 (needs-you audit 2026-11-13): with ``assistant_settings.use_email_codes`` on, a code box is the assistant's.

Production had five open "enter the code" asks (jobs 946, 985...) for a user whose setting was ON: nothing read the
setting when a block was recorded or an ask opened. With it on, an ``otp_needed`` block opens no owner ask, reads as
an assistant blocker, and routes to the PC queue (Job360's cloud browser refuses code boxes). Off = unchanged. Two
``otp_needed`` blocks on one job with no accepted code between them hand the job back to the owner (the only exit
from the loop). Real doors, real Postgres; fixtures only, never production ids.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.services.applications import asks as asks_service
from src.services.applications import blockers, needs_you_count
from src.services.browser_agent import routing
from src.services.profile import schedule
from src.services.profile.models import CVData, UserProfile

CODES = "assistant_settings.use_email_codes"
FORM = "https://job-boards.greenhouse.io/northwind/jobs/7"
GMAIL_WORDS = "read the code from Gmail and enter it"
WITHDRAWN = "Job360: the assistant reads the code from your email"


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Ada Lovelace", name="Ada Lovelace")), user_id,
                 source_action="cv_upload")


async def _codes(client: AsyncClient, on: bool) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": CODES, "value": on}]})
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/assistant-settings")).json()["use_email_codes"]["effective"] is on


async def _bring(client: AsyncClient, n: int = 1, url: str = FORM) -> int:
    ad = {"title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "apply_url": url,
          "description": "Build the pipelines."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> dict:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type,
                                                                          "payload": payload or {}})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _otp(client: AsyncClient, app_id: int) -> None:
    await _event(client, app_id, "blocked", {"reason": "otp_needed", "step": "code box",
                                             "page_host": "job-boards.greenhouse.io"})


async def _blockers(user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    db = await api_deps.get_db()
    return await blockers.blockers_for(db, user_id, ids)  # type: ignore[return-value]


async def _block_payload(client: AsyncClient, app_id: int) -> dict[str, Any]:
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return [e for e in events if e["event_type"] == "blocked"][-1]["payload"]


@pytest.mark.asyncio
async def test_on_the_block_opens_no_ask_and_is_the_assistants(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _codes(client, True)
        app_id = await _bring(client)
        await _otp(client, app_id)
        assert (await client.get("/api/asks")).json() == {"asks": [], "open_count": 0}
        assert (await _block_payload(client, app_id))["ask_id"] is None
        rows = (await _blockers(uid, [app_id]))[app_id]
        assert (rows[0]["kind"], rows[0]["actor"], rows[0]["label"]) == ("assistant_step", "assistant", GMAIL_WORDS)
        assert all(b["actor"] != "you" for b in rows)
        db = await api_deps.get_db()
        counted = await needs_you_count.needs_you_count(db, uid)
        assert counted["jobs"] == 0 and counted["by_actor"]["you"] == 0
        assert await schedule._waiting_on_you(db, uid, [app_id]) == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_off_the_block_still_asks_the_owner(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        app_id = await _bring(client)
        await _otp(client, app_id)
        [ask] = (await client.get("/api/asks")).json()["asks"]
        assert "one-time code" in ask["question"] and ask["kind"] == "login"
        rows = (await _blockers(uid, [app_id]))[app_id]
        assert (rows[0]["kind"], rows[0]["actor"], rows[0]["label"]) == ("site_step", "you", "enter the code")
        db = await api_deps.get_db()
        assert await schedule._waiting_on_you(db, uid, [app_id]) == 1  # noqa: SLF001


def test_ask_route_sends_a_code_box_to_the_pc_only_when_the_setting_is_on():
    on = routing.ask_route("login", block_reason="otp_needed", email_codes=True)
    assert (on.kind, on.reason) == ("own_browser", "email_code")
    off = routing.ask_route("login", block_reason="otp_needed")
    assert (off.kind, off.reason) == ("human", "mfa")
    assert (routing.ask_route("login", block_reason="otp_needed", email_codes=False).kind) == "human"
    # a login that is not a code box is unaffected by the setting
    assert routing.ask_route("login", block_reason="login_needed", email_codes=True).reason == "password"
    assert "email_code" in routing.ROUTE_REASONS
    assert routing.label(on)["label"] == "Your assistant reads the code from Gmail: runs when your PC is on"


@pytest.mark.asyncio
async def test_the_pc_queue_lists_the_assistants_code_box_only_when_on(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _codes(client, True)
        app_id = await _bring(client)
        await _otp(client, app_id)
        queue = (await client.get("/api/own-browser/queue")).json()
        assert queue["count"] == 1 and queue["tasks"][0]["task_id"] == f"application:{app_id}"
        assert queue["tasks"][0]["browser"]["reason"] == "email_code"
        await _codes(client, False)
        assert (await client.get("/api/own-browser/queue")).json()["count"] == 0


@pytest.mark.asyncio
async def test_turning_it_on_withdraws_the_open_code_ask_with_the_exact_reason(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        from_block = await _bring(client, 1)
        other_reason = await _bring(client, 2, "https://co2.example/apply/2")
        by_hand = await _bring(client, 3, "https://co3.example/apply/3")
        await _otp(client, from_block)  # setting off: the old behaviour opens the ask
        await _event(client, other_reason, "blocked", {"reason": "captcha", "step": "open_form"})
        hand = await client.post("/api/asks", json={"question": "Enter the one-time code", "application_id": by_hand,
                                                    "kind": "login"})
        assert hand.status_code == 201, hand.text
        assert len((await client.get("/api/asks")).json()["asks"]) == 3  # off: all kept
        await _codes(client, True)
        open_now = {a["application_id"] for a in (await client.get("/api/asks")).json()["asks"]}
        assert open_now == {other_reason, by_hand}  # only the block's own code ask went
        everything = (await client.get("/api/asks", params={"status": "all"})).json()["asks"]
        [gone] = [a for a in everything if a["application_id"] == from_block]
        assert gone["withdrawn_reason"] == WITHDRAWN and gone["withdrawn_by"] == "system"
        db = await api_deps.get_db()
        assert await asks_service.tidy_open_asks(db, "someone-else") == 0  # another user's asks are never read


@pytest.mark.asyncio
async def test_turning_it_off_hands_the_code_back_at_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _codes(client, True)
        app_id = await _bring(client)
        await _otp(client, app_id)
        assert (await _blockers(uid, [app_id]))[app_id][0]["actor"] == "assistant"
        await _codes(client, False)
        first = (await _blockers(uid, [app_id]))[app_id][0]
        assert (first["kind"], first["actor"], first["label"]) == ("site_step", "you", "enter the code")


@pytest.mark.asyncio
async def test_two_codes_that_never_work_hand_the_job_back_to_the_owner(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _codes(client, True)
        app_id = await _bring(client)
        await _otp(client, app_id)
        assert (await client.get("/api/asks")).json()["open_count"] == 0
        await _event(client, app_id, "unblocked", {"resolution": "retried"})
        await _otp(client, app_id)  # the second block with no accepted code between
        [ask] = (await client.get("/api/asks")).json()["asks"]
        assert ask["kind"] == "login" and ask["application_id"] == app_id
        assert ask["browser"]["kind"] == "human"  # the owner's ask stays the owner's
        first = (await _blockers(uid, [app_id]))[app_id][0]
        assert (first["kind"], first["actor"]) == ("site_step", "you")
        assert (await client.get("/api/asks")).json()["open_count"] == 1  # tidy keeps an escalated ask
        assert (await client.get("/api/own-browser/queue")).json()["count"] == 0
        lines = " ".join(r.getMessage() + str(getattr(r, "event", "")) + str(getattr(r, "reason", ""))
                         for r in caplog.records)
        assert "otp_block_assistant" in lines and "otp_block_escalated" in lines
        assert "job-boards.greenhouse.io" not in " ".join(
            str(getattr(r, "page_host", "")) for r in caplog.records if "otp_block" in str(getattr(r, "event", "")))


@pytest.mark.asyncio
async def test_an_accepted_code_between_two_blocks_does_not_escalate(authenticated_async_context):
    from src.api.main import app as fastapi_app

    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _codes(client, True)
        app_id = await _bring(client)
        await _otp(client, app_id)
        now = datetime.now(timezone.utc)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            used = await agent.post(f"/api/applications/{app_id}/events", json={
                "event_type": "email_code_used", "payload": {
                    "to": "test@example.com", "sender": "Greenhouse <no-reply@greenhouse.io>",
                    "subject": "Security code for your application", "purpose": "apply",
                    "form_host": "job-boards.greenhouse.io",
                    "asked_at": (now - timedelta(minutes=2)).isoformat(),
                    "received_at": (now - timedelta(minutes=1)).isoformat()}})
            assert used.status_code == 201, used.text
        await _event(client, app_id, "unblocked", {"resolution": "retried"})
        await _otp(client, app_id)
        assert (await client.get("/api/asks")).json()["open_count"] == 0
        assert (await _blockers(uid, [app_id]))[app_id][0]["actor"] == "assistant"


def test_the_recipe_no_longer_sends_a_code_box_to_the_user_while_the_setting_is_on():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "recipes" / "rules.md").read_text(encoding="utf-8")
    assert "While Email codes is on a code box is yours" in text
    assert "never ask_user for an emailed code" in text
    assert "PC queue" in text
