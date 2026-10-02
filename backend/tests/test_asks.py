"""The "Needs you" queue (owner plan 2026-10-01): the assistant raises an ask
when it would have to guess; the user answers ONCE. Helpers are defined here,
never imported from another test module (a cross-module fixture import breaks
per-test schema isolation).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Platform Engineer",
    "company": "Contoso",
    "location": "London",
    "apply_url": "https://contoso.example/careers/1",
    "description": "Run the platform. Python, Kubernetes.",
}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ask(client: AsyncClient, **body: Any):
    return await client.post("/api/asks", json={"question": "What is your notice period?", **body})


async def _second_user_session_cookie(email: str) -> str:
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

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


async def _mint_token(authenticated_async_context) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


@pytest.mark.asyncio
async def test_create_list_answer_then_change_the_answer(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        created = await _ask(client, application_id=app_id, context="Form field 7")
        assert created.status_code == 201, created.text
        ask = created.json()
        assert ask["question"] == "What is your notice period?"
        assert ask["context"] == "Form field 7"
        assert ask["status"] == "open"
        assert ask["asked_by"] == "web"
        assert ask["application_id"] == app_id
        assert ask["job_title"] == "Platform Engineer" and ask["job_company"] == "Contoso"
        assert ask["answer"] is None and ask["answered_at"] is None

        listed = (await client.get("/api/asks")).json()
        assert listed["open_count"] == 1 and [a["id"] for a in listed["asks"]] == [ask["id"]]

        answered = await client.post(f"/api/asks/{ask['id']}/answer", json={"answer": "Two months"})
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["status"] == "answered" and body["answer"] == "Two months"
        assert body["answered_by"] == "web" and body["answered_at"]
        assert body["answered_by_user"] is True and body["withdrawn_at"] is None
        assert ask["answered_by_user"] is False  # still open: nobody answered

        again = await client.post(f"/api/asks/{ask['id']}/answer", json={"answer": "Three months"})
        assert again.status_code == 200, again.text  # answers can be changed
        assert again.json()["answer"] == "Three months"

        assert (await client.get("/api/asks")).json() == {"asks": [], "open_count": 0}
        done = (await client.get("/api/asks?status=answered")).json()
        assert [a["answer"] for a in done["asks"]] == ["Three months"]  # the row holds the LATEST

        general = await _ask(client, question="Which of my two CVs do you prefer?")
        assert general.status_code == 201 and general.json()["application_id"] is None
        assert general.json()["job_title"] is None
        everything = (await client.get("/api/asks?status=all")).json()["asks"]
        assert [a["status"] for a in everything] == ["open", "answered"]  # open first

        assert (await client.get("/api/asks?status=nope")).status_code == 422
        assert (await client.post("/api/asks/999999/answer", json={"answer": "x"})).status_code == 404
        assert (await client.post("/api/asks/999999/withdraw")).status_code == 404


@pytest.mark.asyncio
async def test_another_users_ask_and_application_are_404(authenticated_async_context):
    async with authenticated_async_context() as owner:
        app_id = await _bring(owner)
        ask_id = (await _ask(owner, application_id=app_id)).json()["id"]

    cookie = await _second_user_session_cookie("intruder-asks@example.com")
    async with _session_client(cookie) as intruder:
        assert (await _ask(intruder, application_id=app_id)).status_code == 404
        assert (await intruder.post(f"/api/asks/{ask_id}/answer", json={"answer": "hijack"})).status_code == 404
        assert (await intruder.post(f"/api/asks/{ask_id}/withdraw")).status_code == 404
        assert (await intruder.get("/api/asks?status=all")).json() == {"asks": [], "open_count": 0}
        assert (await intruder.get("/api/whats-new")).json()["open_asks"] == []
    async with authenticated_async_context() as owner:
        still = (await owner.get("/api/asks")).json()["asks"]
        assert [a["answer"] for a in still] == [None]  # untouched by the intruder


@pytest.mark.asyncio
async def test_caps_are_422_and_names_the_setting(authenticated_async_context):
    from src.core import settings

    async with authenticated_async_context() as client:
        too_long_q = await _ask(client, question="q" * (settings.ASKS_QUESTION_MAX_CHARS + 1))
        assert too_long_q.status_code == 422 and "ASKS_QUESTION_MAX_CHARS" in too_long_q.text
        too_long_c = await _ask(client, context="c" * (settings.ASKS_CONTEXT_MAX_CHARS + 1))
        assert too_long_c.status_code == 422 and "ASKS_CONTEXT_MAX_CHARS" in too_long_c.text
        assert (await _ask(client, question="   ")).status_code == 422
        # a user-supplied owner field is refused, never silently dropped
        assert (await _ask(client, asked_by="agent:evil")).status_code == 422

        ask_id0 = (await _ask(client)).json()["id"]
        forged = await client.post(
            f"/api/asks/{ask_id0}/answer", json={"answer": "x", "answered_by": "agent:evil"}
        )
        assert forged.status_code == 422
        still = (await client.get("/api/asks")).json()["asks"]
        assert [a["answer"] for a in still] == [None]  # the forged call changed nothing

        ask_id = ask_id0
        long_a = await client.post(
            f"/api/asks/{ask_id}/answer", json={"answer": "a" * (settings.ASKS_ANSWER_MAX_CHARS + 1)}
        )
        assert long_a.status_code == 422 and "ASKS_ANSWER_MAX_CHARS" in long_a.text
        assert (await client.post(f"/api/asks/{ask_id}/answer", json={"answer": " "})).status_code == 422
        assert (await client.post(f"/api/asks/{ask_id}/answer", json={"answer": "ok"})).status_code == 200


@pytest.mark.asyncio
async def test_control_characters_are_stripped(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _ask(client, question="Notice\x00 period‮?\nin weeks")
        assert resp.status_code == 201, resp.text
        assert resp.json()["question"] == "Notice period?\nin weeks"


@pytest.mark.asyncio
async def test_open_cap_is_429(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ASKS_MAX_OPEN_PER_USER", 2)
    async with authenticated_async_context() as client:
        first = (await _ask(client, question="one")).json()["id"]
        assert (await _ask(client, question="two")).status_code == 201
        blocked = await _ask(client, question="three")
        assert blocked.status_code == 429 and "ASKS_MAX_OPEN_PER_USER" in blocked.text
        # answering one frees a slot
        assert (await client.post(f"/api/asks/{first}/answer", json={"answer": "x"})).status_code == 200
        assert (await _ask(client, question="three")).status_code == 201


@pytest.mark.asyncio
async def test_events_appear_on_the_timeline_and_never_change_status(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        before = (await client.get(f"/api/applications/{app_id}")).json()["status"]
        ask = (await _ask(client, application_id=app_id)).json()
        await client.post(f"/api/asks/{ask['id']}/answer", json={"answer": "Two months"})

        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["status"] == before == "considering"
        by_type = {e["event_type"]: e for e in detail["events"]}
        assert by_type["asked"]["detail"] == "What is your notice period?"
        assert by_type["asked"]["payload"] == {"ask_id": ask["id"]}
        assert by_type["answered"]["detail"] == "Two months"
        assert by_type["answered"]["payload"] == {"ask_id": ask["id"], "answer": "Two months"}
        assert [a["id"] for a in detail["asks"]] == [ask["id"]]
        assert detail["asks"][0]["answer"] == "Two months"

        # a general ask leaves no timeline event anywhere
        await _ask(client, question="general one")
        detail2 = (await client.get(f"/api/applications/{app_id}")).json()
        assert len(detail2["events"]) == len(detail["events"])


@pytest.mark.asyncio
async def test_get_application_lists_open_asks_first(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = (await _ask(client, application_id=app_id, question="first")).json()["id"]
        second = (await _ask(client, application_id=app_id, question="second")).json()["id"]
        await client.post(f"/api/asks/{second}/answer", json={"answer": "yes"})
        other_app = (await _ask(client, question="not about this job")).json()["id"]
        asks = (await client.get(f"/api/applications/{app_id}")).json()["asks"]
        assert [a["id"] for a in asks] == [first, second]
        assert other_app not in [a["id"] for a in asks]


@pytest.mark.asyncio
async def test_whats_new_always_carries_open_asks_even_with_a_future_since(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ask = (await _ask(client, application_id=app_id)).json()
        done = (await _ask(client, question="already answered")).json()
        await client.post(f"/api/asks/{done['id']}/answer", json={"answer": "yes"})

        resp = await client.get("/api/whats-new", params={"since": "2099-01-01T00:00:00+00:00"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["events"] == []  # the cursor moved past every event ...
        assert [a["id"] for a in body["open_asks"]] == [ask["id"]]  # ... but not past the open ask
        assert body["open_asks"][0]["question"] == "What is your notice period?"


@pytest.mark.asyncio
async def test_re_answering_keeps_both_answers_in_the_history(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ask_id = (await _ask(client, application_id=app_id)).json()["id"]
        await client.post(f"/api/asks/{ask_id}/answer", json={"answer": "first answer"})
        await client.post(f"/api/asks/{ask_id}/answer", json={"answer": "second answer"})
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        answered = [e for e in detail["events"] if e["event_type"] == "answered"]
        assert [e["payload"]["answer"] for e in answered] == ["first answer", "second answer"]
        assert [a["answer"] for a in detail["asks"]] == ["second answer"]  # the row shows the latest
        assert detail["status"] == "considering"


@pytest.mark.asyncio
async def test_withdraw_is_not_open_not_counted_and_listed_under_all(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ASKS_MAX_OPEN_PER_USER", 1)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ask = (await _ask(client, application_id=app_id)).json()
        assert (await _ask(client, question="over the cap")).status_code == 429

        gone = await client.post(f"/api/asks/{ask['id']}/withdraw")
        assert gone.status_code == 200, gone.text
        assert gone.json()["status"] == "withdrawn" and gone.json()["withdrawn_at"]

        assert (await client.get("/api/asks")).json() == {"asks": [], "open_count": 0}
        assert (await client.get("/api/whats-new")).json()["open_asks"] == []
        listed = (await client.get("/api/asks?status=all")).json()["asks"]
        assert [(a["id"], a["status"]) for a in listed] == [(ask["id"], "withdrawn")]
        assert (await _ask(client, question="room again")).status_code == 201  # no longer counts to the cap

        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert [e["event_type"] for e in detail["events"] if e["event_type"].startswith("ask")] == [
            "asked", "ask_withdrawn",
        ]
        assert detail["status"] == "considering"
        # a withdrawn ask cannot be answered or withdrawn again
        assert (await client.post(f"/api/asks/{ask['id']}/answer", json={"answer": "x"})).status_code == 409
        assert (await client.post(f"/api/asks/{ask['id']}/withdraw")).status_code == 409


@pytest.mark.asyncio
async def test_an_answered_ask_cannot_be_withdrawn(authenticated_async_context):
    async with authenticated_async_context() as client:
        ask_id = (await _ask(client)).json()["id"]
        await client.post(f"/api/asks/{ask_id}/answer", json={"answer": "yes"})
        assert (await client.post(f"/api/asks/{ask_id}/withdraw")).status_code == 409


@pytest.mark.asyncio
async def test_stats_are_unchanged_by_asks(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        before = await client.get("/api/applications/stats")
        assert before.status_code == 200, before.text
        a1 = (await _ask(client, application_id=app_id, question="one")).json()["id"]
        a2 = (await _ask(client, application_id=app_id, question="two")).json()["id"]
        await client.post(f"/api/asks/{a1}/answer", json={"answer": "yes"})
        await client.post(f"/api/asks/{a1}/answer", json={"answer": "changed"})
        await client.post(f"/api/asks/{a2}/withdraw")
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert {"asked", "answered", "ask_withdrawn"} <= {e["event_type"] for e in detail["events"]}
        after = await client.get("/api/applications/stats")
        assert {k: v for k, v in after.json().items() if k != "computed_at"} == {
            k: v for k, v in before.json().items() if k != "computed_at"
        }


@pytest.mark.asyncio
async def test_whats_new_open_asks_are_capped_at_the_newest(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ASKS_WHATS_NEW_MAX", 2)
    async with authenticated_async_context() as client:
        for n in range(3):
            assert (await _ask(client, question=f"question {n}")).status_code == 201
        body = (await client.get("/api/whats-new")).json()
        assert [a["question"] for a in body["open_asks"]] == ["question 2", "question 1"]


@pytest.mark.asyncio
async def test_asks_routes_need_a_login():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/asks")).status_code == 401
        assert (await anon.post("/api/asks", json={"question": "x"})).status_code == 401
        assert (await anon.post("/api/asks/1/answer", json={"answer": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_mcp_round_trip_ask_list_answer(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import INSTRUCTIONS, mcp_runtime

    assert "NEEDS YOU" in INSTRUCTIONS and "ask_user" in INSTRUCTIONS

    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as web:
        app_id = await _bring(web)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            made = await mcp.call_tool(
                "ask_user",
                {"question": "Salary expectation?", "context": "form field 3", "application_id": app_id},
            )
            assert not made.is_error, made
            ask = json.loads(made.content[0].text)
            assert ask["status"] == "open" and ask["application_id"] == app_id
            assert ask["asked_by"].startswith("token:")

            wn = json.loads((await mcp.call_tool("whats_new", {"since": "2099-01-01T00:00:00+00:00"})).content[0].text)
            assert [a["id"] for a in wn["open_asks"]] == [ask["id"]]
            assert wn["open_asks"][0]["question"] == "Salary expectation?"
            assert "get_recipe" in wn["assistant_hint"]
            listing = json.loads((await mcp.call_tool("list_applications", {})).content[0].text)
            assert "get_recipe" in listing["assistant_hint"]
            assert "list_asks" not in {t.name for t in (await mcp.list_tools()).tools}

            answered = await mcp.call_tool("answer_ask", {"ask_id": ask["id"], "answer": "55k"})
            assert not answered.is_error, answered
            first = json.loads(answered.content[0].text)
            assert first["answer"] == "55k"
            assert first["answered_by_user"] is False  # the assistant recorded the user's chat answer
            assert first["answered_by"].startswith("token:")

            second = await mcp.call_tool("answer_ask", {"ask_id": ask["id"], "answer": "60k"})
            assert not second.is_error, second
            assert json.loads(second.content[0].text)["answer"] == "60k"

            gone = json.loads((await mcp.call_tool("whats_new", {})).content[0].text)
            assert gone["open_asks"] == []

            detail = json.loads((await mcp.call_tool("get_application", {"application_id": app_id})).content[0].text)
            assert detail["asks"][0]["answer"] == "60k"
            answers = [e["payload"]["answer"] for e in detail["events"] if e["event_type"] == "answered"]
            assert answers == ["55k", "60k"]  # both answers stay in the history

            bad_answer = await mcp.call_tool("answer_ask", {"ask_id": 987654321, "answer": "x"})
            assert bad_answer.is_error and "404" in bad_answer.content[0].text

            bad = await mcp.call_tool("ask_user", {"question": "x", "application_id": 987654321})
            assert bad.is_error and "404" in bad.content[0].text

            brought = json.loads((await mcp.call_tool("bring_job", _AD)).content[0].text)
            assert "get_recipe" in brought["assistant_hint"]


def test_asks_table_is_in_the_per_user_registries():
    """Review finding (2026-10-02): the pg shim strips FK clauses, so ON DELETE
    CASCADE never fires — account erasure and the GDPR export only reach a
    table through these lists."""
    from pathlib import Path

    from src.repositories.database import JobDatabase

    assert "application_asks" in JobDatabase._PER_USER_TABLES
    assert "application_asks" in JobDatabase._EXPORT_TABLES
    observe = Path(__file__).resolve().parent.parent / "scripts" / "observe.py"
    assert "application_asks" in observe.read_text(encoding="utf-8")
