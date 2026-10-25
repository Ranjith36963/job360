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
    {"key": "visa_de", "question": "Do you need visa sponsorship for Germany?", "answer": "No", "source": "guess",
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
        ([{**QUESTIONS[0], "source": "none"}], None, "never use source none"),
        ([{**QUESTIONS[2], "answer": ""}], None, "draft your best guess"),
        ([{**QUESTIONS[2], "answer": "   "}], None, "1 question with no answer: questions[0] (story)"),
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


# ── Owner 2026-10-25: no "not known yet" ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unanswered_questions_are_all_named_with_what_to_do(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        qs = [QUESTIONS[0], {**QUESTIONS[1], "answer": "", "source": "none"}, {**QUESTIONS[2], "answer": ""}]
        resp = await _ask(client, application_id=app_id, kind="question", questions=qs)
        assert resp.status_code == 422, resp.text
        text = resp.text
        assert "2 questions with no answer: questions[1] (visa_de), questions[2] (story)" in text
        assert "source guess" in text and "Read the form yourself" in text and "Never leave an answer empty" in text
        assert "paste or copy question text" in text and "Valid example question" in text
        assert (await client.get("/api/asks")).json()["asks"] == []  # nothing was saved
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        assert not [e for e in events if e["event_type"] == "asked"]


@pytest.mark.asyncio
async def test_a_guess_and_a_draft_with_answers_are_accepted(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        qs = [
            {"key": "consent", "question": "I agree to data processing", "answer": "Yes", "source": "guess",
             "choices": ["Yes", "No"]},
            {"key": "why", "question": "Why n8n?", "answer": "I build workflow tools.", "source": "draft"},
        ]
        resp = await _ask(client, application_id=app_id, kind="question", questions=qs)
        assert resp.status_code == 201, resp.text
        assert [(q["source"], q["answer"]) for q in resp.json()["questions"]] == [
            ("guess", "Yes"), ("draft", "I build workflow tools."),
        ]


@pytest.mark.asyncio
async def test_an_old_stored_ask_with_source_none_still_reads_and_merges(authenticated_async_context):
    """Rows stored before the ban keep source none: reads never re-validate, and #1215's merge carries them."""
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        made = (await _ask(client, application_id=app_id, kind="question", questions=QUESTIONS[:1])).json()
        old = [{"key": "visa_de", "question": "Visa?", "answer": "", "source": "none"}]
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE application_asks SET questions = ? WHERE id = ?", (json.dumps(old), made["id"]))
        conn.commit()
        conn.close()

        def shown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{k: v for k, v in q.items() if v is not None} for q in rows]

        listed = await client.get("/api/asks")
        assert listed.status_code == 200, listed.text
        assert shown(listed.json()["asks"][0]["questions"]) == old
        got = await client.get(f"/api/applications/{app_id}")
        assert got.status_code == 200 and shown(got.json()["asks"][0]["questions"]) == old

        # A new ask on the job merges with the old row: the old none row is kept, the new row joins.
        merged = await _ask(client, application_id=app_id, kind="question", questions=[QUESTIONS[2]])
        assert merged.status_code == 201, merged.text
        body = merged.json()
        assert body["merged_from"] == [made["id"]]
        assert [(q["key"], q["source"], q["answer"]) for q in body["questions"]] == [
            ("visa_de", "none", ""), ("story", "draft", QUESTIONS[2]["answer"]),
        ]
        # A newer guess for the old row's key replaces its "not known yet".
        again = await _ask(client, application_id=app_id, kind="question", questions=[QUESTIONS[1]])
        assert [(q["key"], q["source"]) for q in again.json()["questions"]] == [("story", "draft"), ("visa_de", "guess")]


@pytest.mark.asyncio
async def test_mcp_ask_user_refuses_an_unanswered_question_with_guidance(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            bad = await mcp.call_tool(
                "ask_user", {"question": "x", "questions": [{**QUESTIONS[1], "answer": "", "source": "none"}]},
            )
            assert bad.is_error
            text = bad.content[0].text
            assert "Nothing was saved: 1 question with no answer: questions[0] (visa_de)" in text
            assert "draft your best guess" in text and "never use source none" in text
