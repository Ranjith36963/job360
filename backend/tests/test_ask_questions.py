"""Owner 2026-10-24 - one ask per job carries every open form question (migration 0087).

Each question has the answer the assistant filled in and a closed `source` saying where it came
from; the user approves `answers` (one per key). An ask also has a `kind`: a question, or a blocker
only the user can clear (captcha, login, upload, safety). Helpers are copied, never imported from
another test module.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Forward Deployed Engineer",
    "company": "n8n",
    "location": "Berlin",
    "apply_url": "https://n8n.example/careers/1",
    "description": "Ship workflows for customers.",
}

QUESTIONS = [
    {"key": "contract_country", "question": "Where should your contract be based?", "answer": "Germany",
     "source": "memory", "remembered_on": "2026-10-12"},
    {"key": "visa_de", "question": "Do you need visa sponsorship for Germany?", "source": "none",
     "choices": ["Yes, I need it", "No"]},
    {"key": "story", "question": "A 300-word story", "answer": "Draft from the Calnex RAG work.", "source": "draft"},
]


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ask(client: AsyncClient, **body: Any):
    return await client.post("/api/asks", json={"question": "Form answers for n8n", **body})


@pytest.mark.asyncio
async def test_an_ask_carries_its_questions_and_takes_one_answer_per_key(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        made = await _ask(client, application_id=app_id, kind="question", questions=QUESTIONS)
        assert made.status_code == 201, made.text
        ask = made.json()
        assert ask["kind"] == "question" and [q["key"] for q in ask["questions"]] == ["contract_country", "visa_de", "story"]
        assert ask["questions"][0]["remembered_on"] == "2026-10-12" and ask["answers"] == {}

        partial = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": {"contract_country": "Germany"}})
        assert partial.status_code == 422 and "exactly these keys" in partial.text
        off_choice = await client.post(
            f"/api/asks/{ask['id']}/answer",
            json={"answers": {"contract_country": "Germany", "visa_de": "Maybe", "story": "ok"}},
        )
        assert off_choice.status_code == 422 and "one of its choices" in off_choice.text

        answers = {"contract_country": "Germany", "visa_de": "No", "story": "My edited story."}
        done = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": answers})
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["status"] == "answered" and body["answers"] == answers
        assert body["answer"].splitlines() == [
            "Where should your contract be based?: Germany",
            "Do you need visa sponsorship for Germany?: No",
            "A 300-word story: My edited story.",
        ]
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        answered = [e for e in events if e["event_type"] == "answered"]
        assert answered[-1]["payload"]["answers"] == answers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("questions", "kind", "needle"),
    [
        ([{**QUESTIONS[0], "source": "hunch"}], None, "source"),
        ([{**QUESTIONS[0], "source": "none"}], None, "source none goes with an empty answer"),
        ([{**QUESTIONS[2], "answer": ""}], None, "source none goes with an empty answer"),
        ([QUESTIONS[0], QUESTIONS[0]], None, "repeats an earlier key"),
        ([{**QUESTIONS[0], "key": "Bad Key!"}], None, "key must be"),
        ([{**QUESTIONS[2], "remembered_on": "2026-10-12"}], None, "only with source memory"),
        ([{**QUESTIONS[1], "choices": [str(i) for i in range(7)]}], None, "at most 6"),
        ([{**QUESTIONS[0], "key": f"q{i}"} for i in range(21)], None, "at most 20 questions"),
        ([QUESTIONS[0]], "captcha", "a blocker ask carries no questions"),
        ([{**QUESTIONS[0], "evil": "x"}], None, "extra"),
    ],
)
async def test_bad_questions_are_422(authenticated_async_context, questions, kind, needle):
    async with authenticated_async_context() as client:
        resp = await _ask(client, questions=questions, **({"kind": kind} if kind else {}))
        assert resp.status_code == 422, resp.text
        assert needle in resp.text


@pytest.mark.asyncio
async def test_a_plain_ask_keeps_working_and_refuses_answers(authenticated_async_context):
    async with authenticated_async_context() as client:
        ask = (await _ask(client)).json()
        assert ask["kind"] == "" and ask["questions"] == [] and ask["answers"] == {}
        bad = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": {"x": "y"}})
        assert bad.status_code == 422 and "has no questions" in bad.text
        assert (await client.post(f"/api/asks/{ask['id']}/answer", json={})).status_code == 422
        ok = await client.post(f"/api/asks/{ask['id']}/answer", json={"answer": "Two weeks"})
        assert ok.status_code == 200 and ok.json()["answer"] == "Two weeks"
        assert (await _ask(client, kind="otp")).status_code == 422


@pytest.mark.asyncio
async def test_a_blocked_captcha_step_raises_a_captcha_ask(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "blocked", "payload": {"reason": "captcha", "step": "the last page", "page_host": "jobs.ashbyhq.com"}},
        )
        assert resp.status_code in (200, 201), resp.text
        asks = (await client.get("/api/asks")).json()["asks"]
        assert [a["kind"] for a in asks if a["application_id"] == app_id] == ["captcha"]


@pytest.mark.asyncio
async def test_a_client_key_stored_before_0087_still_matches_its_retry(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _ask(client, client_key="k-1")
        again = await _ask(client, client_key="k-1")
        assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
        assert again.json()["already_existed"] is True


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
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


@pytest.mark.asyncio
async def test_mcp_ask_user_and_answer_ask_carry_questions_and_answers(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as web:
        app_id = await _bring(web)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            made = await mcp.call_tool(
                "ask_user",
                {"question": "Form answers for n8n", "application_id": app_id, "kind": "question", "questions": QUESTIONS},
            )
            assert not made.is_error, made
            ask = json.loads(made.content[0].text)
            assert len(ask["questions"]) == 3

            bad = await mcp.call_tool("ask_user", {"question": "x", "questions": [{**QUESTIONS[0], "source": "hunch"}]})
            assert bad.is_error

            answers = {"contract_country": "Germany", "visa_de": "No", "story": "ok"}
            done = await mcp.call_tool("answer_ask", {"ask_id": ask["id"], "answers": answers, "user_words": "no visa"})
            assert not done.is_error, done
            assert json.loads(done.content[0].text)["answers"] == answers
            mixed = await mcp.call_tool("answer_ask", {"ask_id": ask["id"], "answers": answers, "withdraw": "old"})
            assert mixed.is_error
