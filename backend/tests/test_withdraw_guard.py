"""Owner 2026-11-01: an assistant may not withdraw an open captcha/login/upload/safety ask or a form question
about an application; only the user clears those. Its own general, sentence-only, doctor and proof asks stay
closable."""
from __future__ import annotations

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


def _row(key: str, question: str) -> dict[str, str]:
    return {"key": key, "question": question, "source": "guess", "answer": "My best guess"}


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _setup(authenticated_async_context) -> tuple[str, int]:
    async with authenticated_async_context() as web:
        token = (await web.post("/api/tokens", json={"name": "agent"})).json()["token"]
        bring = await web.post("/api/jobs/bring", json=_AD)
        assert bring.status_code == 200, bring.text
        return token, int(bring.json()["application_id"])


async def _agent_ask(agent: AsyncClient, **body: Any) -> dict[str, Any]:
    resp = await agent.post("/api/asks", json={"question": "Please help with this.", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _withdraw(agent: AsyncClient, ask_id: int):
    return await agent.post(f"/api/asks/{ask_id}/withdraw", json={"reason": "it is out of date"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["captcha", "login", "upload", "safety"])
async def test_assistant_cannot_withdraw_a_blocker_ask(authenticated_async_context, kind):
    token, app_id = await _setup(authenticated_async_context)
    async with _bearer(token) as agent:
        ask = await _agent_ask(agent, application_id=app_id, kind=kind)
        resp = await _withdraw(agent, ask["id"])
        assert resp.status_code == 409, resp.text
        assert "Only the user can clear" in resp.json()["detail"]
        still = [a for a in (await agent.get("/api/asks")).json()["asks"] if a["id"] == ask["id"]]
        assert still and still[0]["status"] == "open"


@pytest.mark.asyncio
async def test_assistant_cannot_withdraw_a_form_question_ask(authenticated_async_context):
    token, app_id = await _setup(authenticated_async_context)
    async with _bearer(token) as agent:
        ask = await _agent_ask(
            agent, application_id=app_id, kind="question", questions=[_row("notice_period", "Notice period?")]
        )
        resp = await _withdraw(agent, ask["id"])
        assert resp.status_code == 409, resp.text
        still = [a for a in (await agent.get("/api/asks")).json()["asks"] if a["id"] == ask["id"]]
        assert still and still[0]["status"] == "open"


@pytest.mark.asyncio
async def test_assistant_may_withdraw_its_lesson_sentence_general_and_proof_asks(authenticated_async_context):
    token, app_id = await _setup(authenticated_async_context)
    async with _bearer(token) as agent:
        general = await _agent_ask(agent)
        assert (await _withdraw(agent, general["id"])).status_code == 200
        sentence = await _agent_ask(agent, application_id=app_id, kind="question",
                                    questions=[_row("said_0badf00d", "May I submit?")])
        assert (await _withdraw(agent, sentence["id"])).status_code == 200
        doctor = await _agent_ask(agent, application_id=app_id, kind="question",
                                  questions=[_row("doctor_repeat_cv", "Keep this lesson?")])
        assert (await _withdraw(agent, doctor["id"])).status_code == 200
        proof = await _agent_ask(agent, application_id=app_id, context=f"proof_missing:{app_id}")
        assert (await _withdraw(agent, proof["id"])).status_code == 200


@pytest.mark.asyncio
async def test_the_user_may_withdraw_a_captcha_ask_on_the_web(authenticated_async_context):
    token, app_id = await _setup(authenticated_async_context)
    async with _bearer(token) as agent:
        ask = await _agent_ask(agent, application_id=app_id, kind="captcha")
    async with authenticated_async_context() as web:
        resp = await web.post(f"/api/asks/{ask['id']}/withdraw")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "withdrawn"
