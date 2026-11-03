"""A scheduled run gets its own credential, and it never approves for the user (owner 2026-11-02, option b).

The owner's hourly scheduled tasks and his live chats used one connector, so the server could not tell a
run's "the user said yes" from his. A personal token minted with ``for_scheduled_runs: true`` (migration
0093) is the run's own credential: ``CurrentUser.scheduled_run`` is True only for it, and every approval it
sends for the user is 403 ``chat_approval_from_run`` - a chat cv_seen / submit_approved / submit_declined,
a ``remember: true`` edit, a newly approved ``user_info.answers`` record, an answered ask. Run work (a
form_filled, a guess, submit_attempted, a setting REQUEST) stays allowed; a setting confirm was already
the user's web session only. Same over REST and MCP (the tools call the same route functions).

Real doors (HTTP + MCP), VALUES asserted (rule #21). Helpers copied, never imported across test modules.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.auth import api_tokens

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. Python, dbt.",
}
ASKED = "Here is the CV for Data Engineer at Northwind. OK to use it?"
ANSWERS = "user_info.answers"
SUBMIT = "assistant_settings.submit_mode"
REFUSED = {"code": "chat_approval_from_run", "message": "A scheduled run cannot approve for the user; put it on Needs you"}


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _mcp_client(token: str):
    import httpx as httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _mint(client: AsyncClient, name: str, for_runs: bool | None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if for_runs is not None:
        body["for_scheduled_runs"] = for_runs
    resp = await client.post("/api/tokens", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _setup(client: AsyncClient) -> tuple[int, dict[str, Any], str, str]:
    """An application with a saved CV, a normal token and a scheduled-run token."""
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert saved.status_code == 201, saved.text
    chat = (await _mint(client, "claude-chat", None))["token"]
    runs = (await _mint(client, "scheduled-runs", True))["token"]
    cv = {"artifact_id": saved.json()["artifact_id"], "sha256": kit_service.sha256_text(CV_TEXT)}
    return app_id, cv, chat, runs


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _types(client: AsyncClient, app_id: int) -> list[str]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return [e["event_type"] for e in resp.json()["events"]]


async def _patch(client: AsyncClient, *edits: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits)})


async def _answers(client: AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get("/api/profile")
    assert resp.status_code == 200, resp.text
    return list(resp.json()["user_info"]["answers"])


def _chat_yes(cv: dict[str, Any], event_type: str) -> dict[str, Any]:
    base = {"where": "chat"} if event_type == "submit_declined" else {**cv, "where": "chat"}
    return {**base, "user_words": "yes, go ahead", "asked": ASKED}


@pytest.mark.asyncio
async def test_token_creation_stores_the_flag_default_false(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        plain = await _mint(client, "laptop", None)
        explicit = await _mint(client, "phone", False)
        runs = await _mint(client, "scheduled-runs", True)
        listed = {t["name"]: t["for_scheduled_runs"] for t in (await client.get("/api/tokens")).json()["tokens"]}
    assert (plain["for_scheduled_runs"], explicit["for_scheduled_runs"], runs["for_scheduled_runs"]) == (
        False, False, True,
    )
    assert listed == {"laptop": False, "phone": False, "scheduled-runs": True}
    owners = [await api_tokens.resolve(str(settings.DB_PATH), t["token"]) for t in (plain, explicit, runs)]
    assert [(o.user_id, o.for_scheduled_runs) for o in owners if o] == [
        (fixture_user_id, False), (fixture_user_id, False), (fixture_user_id, True),
    ]


@pytest.mark.asyncio
async def test_a_scheduled_tokens_chat_approval_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, _, runs = await _setup(client)
    async with _bearer_client(runs) as agent:
        for event_type in sorted(kit_service.CHAT_APPROVAL_EVENTS):
            refused = await _event(agent, app_id, event_type, _chat_yes(cv, event_type))
            assert refused.status_code == 403, refused.text
            assert refused.json()["detail"] == REFUSED
        assert not {"cv_seen", "submit_approved", "submit_declined"} & set(await _types(agent, app_id))


@pytest.mark.asyncio
async def test_a_normal_tokens_chat_approval_is_accepted(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, chat, _ = await _setup(client)
    async with _bearer_client(chat) as agent:
        for event_type in ("cv_seen", "submit_approved"):
            ok = await _event(agent, app_id, event_type, _chat_yes(cv, event_type))
            assert ok.status_code == 201, ok.text
        assert {"cv_seen", "submit_approved"} <= set(await _types(agent, app_id))


@pytest.mark.asyncio
async def test_the_web_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _, _, _ = await _setup(client)
        assert (await client.post(f"/api/applications/{app_id}/cv-seen", json={})).status_code == 201
        assert "cv_seen" in await _types(client, app_id)
        remembered = await _patch(
            client, {"path": ANSWERS, "value": [{"question": "Notice period?", "answer": "1 month", "approved": True}]},
        )
        assert remembered.status_code == 200, remembered.text
        assert [(a["answer"], a["approved"]) for a in await _answers(client)] == [("1 month", True)]


@pytest.mark.asyncio
async def test_a_scheduled_token_still_does_run_work(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _, _, runs = await _setup(client)
    async with _bearer_client(runs) as agent:
        filled = await _event(agent, app_id, "form_filled", {
            "form_url": "https://northwind.example/careers/7/apply", "fields_count": 1,
            "answers": [{"question": "Why this role?", "answer": "The pipelines.", "source": "written"}],
        })
        assert filled.status_code == 201, filled.text
        assert (await _event(agent, app_id, "submit_attempted", {})).status_code == 201
        guess = await _patch(
            agent, {"path": ANSWERS, "value": [{"question": "Notice period?", "answer": "1 month", "approved": False}]},
        )
        assert guess.status_code == 200, guess.text
        assert [(a["answer"], a["approved"]) for a in await _answers(agent)] == [("1 month", False)]
        assert {"form_filled", "submit_attempted"} <= set(await _types(agent, app_id))


@pytest.mark.asyncio
async def test_the_remember_paths_refuse_a_scheduled_token(authenticated_async_context):
    async with authenticated_async_context() as client:
        _, _, chat, runs = await _setup(client)
        approved = {"question": "Notice period?", "answer": "1 month", "approved": True}
        assert (await _patch(client, {"path": ANSWERS, "value": [approved]})).status_code == 200
        ask = await client.post("/api/asks", json={"question": "Which start date suits you?"})
        assert ask.status_code == 201, ask.text
        ask_id = ask.json()["id"]
    where = {"countries": [{"country": "GB", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes"}]}
    async with _bearer_client(runs) as agent:
        # remember: true (a sensitive answer the user agreed to keep)
        kept = await _patch(agent, {"path": "user_info.right_to_work", "value": where, "remember": True})
        assert kept.status_code == 403 and kept.json()["detail"] == REFUSED, kept.text
        # a NEW approved answer, a changed approved answer, an approval moved to another topic: all refused
        for record in (
            {"question": "Willing to relocate?", "answer": "No", "approved": True},
            {**approved, "answer": "3 months"},
            {**approved, "topic": "notice.GB"},
        ):
            sent = await _patch(agent, {"path": ANSWERS, "value": [approved, record]
                                        if record["question"] != approved["question"] else [record]})
            assert sent.status_code == 403 and sent.json()["detail"] == REFUSED, (record, sent.text)
        upsert = await _patch(agent, {"path": ANSWERS, "op": "upsert", "value": {**approved, "answer": "2 weeks"}})
        assert upsert.status_code == 403 and upsert.json()["detail"] == REFUSED, upsert.text
        # re-sending what the user already approved, as-is, plus a guess: allowed
        stored = await _answers(agent)
        guess = {"question": "Willing to relocate?", "answer": "No", "approved": False}
        same = await _patch(agent, {"path": ANSWERS, "value": [*stored, guess]})
        assert same.status_code == 200, same.text
        assert sorted((a["answer"], a["approved"]) for a in await _answers(agent)) == [("1 month", True), ("No", False)]
        # an answered ask is the user's own word; withdrawing the run's own stale ask is not
        answered = await agent.post(f"/api/asks/{ask_id}/answer", json={"answer": "Monday", "user_words": "Monday"})
        assert answered.status_code == 403 and answered.json()["detail"] == REFUSED, answered.text
        mine = await agent.post("/api/asks", json={"question": "Which salary should I enter?"})
        assert mine.status_code == 201, mine.text
        withdrawn = await agent.post(f"/api/asks/{mine.json()['id']}/withdraw", json={"reason": "the form changed"})
        assert withdrawn.status_code == 200, withdrawn.text
    async with _bearer_client(chat) as agent:
        kept = await _patch(agent, {"path": "user_info.right_to_work", "value": where, "remember": True})
        assert kept.status_code == 200 and kept.json()["not_saved"] == [], kept.text


@pytest.mark.asyncio
async def test_a_scheduled_token_may_request_a_setting_never_confirm(authenticated_async_context):
    async with authenticated_async_context() as client:
        _, _, _, runs = await _setup(client)
    async with _bearer_client(runs) as agent:
        held = await _patch(agent, {"path": SUBMIT, "value": "auto_when_sure"})
        assert held.status_code == 200, held.text
        [waiting] = held.json()["waiting"]
        confirm = await agent.post(f"/api/assistant-settings/requests/{waiting['id']}/confirm")
        assert confirm.status_code == 403, confirm.text  # require_session_user: a token of any kind only requests
    async with authenticated_async_context() as client:
        view = (await client.get("/api/assistant-settings")).json()
        assert [w["id"] for w in view["waiting"]] == [waiting["id"]]


@pytest.mark.asyncio
async def test_mcp_matches_rest(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id, cv, chat, runs = await _setup(client)
        ask_id = (await client.post("/api/asks", json={"question": "Which start date suits you?"})).json()["id"]
    refused_text = f"403: {json.dumps(REFUSED)}"
    async with mcp_runtime(), _mcp_client(runs) as mcp:
        seen = await mcp.call_tool(
            "record_event", {"event_type": "cv_seen", "application_id": app_id, "payload": _chat_yes(cv, "cv_seen")}
        )
        assert seen.is_error and seen.content[0].text.endswith(refused_text), seen.content[0].text
        kept = await mcp.call_tool("update_profile", {"edits": [
            {"path": ANSWERS, "value": [{"question": "Notice period?", "answer": "1 month", "approved": True}]},
        ]})
        assert kept.is_error and refused_text in kept.content[0].text, kept.content[0].text
        answered = await mcp.call_tool("answer_ask", {"ask_id": ask_id, "answer": "Monday", "user_words": "Monday"})
        assert answered.is_error and answered.content[0].text.endswith(refused_text), answered.content[0].text
        work = await mcp.call_tool("record_event", {"event_type": "submit_attempted", "application_id": app_id})
        assert not work.is_error, work.content[0].text
    async with mcp_runtime(), _mcp_client(chat) as mcp:
        ok = await mcp.call_tool(
            "record_event", {"event_type": "cv_seen", "application_id": app_id, "payload": _chat_yes(cv, "cv_seen")}
        )
        assert not ok.is_error, ok.content[0].text
    async with _bearer_client(chat) as agent:
        types = await _types(agent, app_id)
        assert types.count("cv_seen") == 1 and "submit_attempted" in types
        assert (await agent.get("/api/profile")).status_code == 404  # the refused approval wrote no profile at all


def test_only_a_scheduled_token_is_a_run():
    from src.api.auth_deps import CurrentUser
    from src.services.auth import run_credential

    assert CurrentUser(id="u", email="e").scheduled_run is False
    run_credential.refuse_if_run(CurrentUser(id="u", email="e", auth_via="token"), "cv_seen")
    with pytest.raises(Exception) as exc:
        run_credential.refuse_if_run(CurrentUser(id="u", email="e", auth_via="token", scheduled_run=True), "cv_seen")
    assert getattr(exc.value, "status_code", None) == 403 and getattr(exc.value, "detail", None) == REFUSED
