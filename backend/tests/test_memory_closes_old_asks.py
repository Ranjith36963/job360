"""Memory first (owner 2026-11-05): an old ask that Memory now answers closes itself.

Production: application 661 (Bell Integration, GB) kept ask 451 open (``sponsorship.gb.yes_no``, source
``not_known``, asked 2026-11-03) though the owner's GB page says ``needs_sponsorship: false``, and the job
stayed blocked. Now an open ask whose EVERY question Memory answers for THAT job's country is withdrawn by
Job360 (``system``, reason ``memory_answers``) on the kit read, check_submit, the user's Needs-you read and
a Memory save. Answered, mixed, blocker, wrong-country and unset-Memory asks stay open. Every negative case
runs beside a covered ask that does close, so each test fails where nothing closes. Helpers are defined
here, never imported from another test module.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from httpx import ASGITransport, AsyncClient

SPONSOR_Q = "Will you now or in the future require visa sponsorship to work in the UK?"
AUTH_Q = "Do you have the right to work in this country?"
GB_PAGE = {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}


def _sponsor_row(key: str = "sponsorship.gb.yes_no") -> dict[str, str]:
    return {"key": key, "question": SPONSOR_Q, "answer": "", "source": "not_known"}


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _token(web: AsyncClient) -> str:
    resp = await web.post("/api/tokens", json={"name": "apply-routine"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


async def _bring(web: AsyncClient, company: str, country: Optional[str] = "GB") -> int:
    body: dict[str, Any] = {"title": "AI Engineer", "company": company, "location": "London",
                            "apply_url": f"https://{company.lower().replace(' ', '')}.example/jobs/1",
                            "description": "Build agents for customers. " * 8}
    if country:
        body["country"] = country
    resp = await web.post("/api/jobs/bring", json=body)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _memory(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _ask(agent: AsyncClient, app_id: int, rows: list[dict[str, str]], kind: str = "question") -> int:
    body: dict[str, Any] = {"question": "Form answers", "application_id": app_id, "kind": kind}
    if rows:
        body["questions"] = rows
    resp = await agent.post("/api/asks", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def _store_rows(ask_id: int, rows: list[dict[str, str]]) -> None:
    """A blocker ask takes no rows through the API; give it one as stored, so only its kind protects it."""
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE application_asks SET questions = ? WHERE id = ?", (json.dumps(rows), ask_id))
    conn.commit()
    conn.close()


async def _asks(web: AsyncClient) -> dict[int, dict[str, Any]]:
    """Every ask, read with status=all (that read closes nothing)."""
    resp = await web.get("/api/asks", params={"status": "all"})
    assert resp.status_code == 200, resp.text
    return {int(a["id"]): a for a in resp.json()["asks"]}


def _closed_by_memory(ask: dict[str, Any]) -> tuple[str, str, str, Any]:
    return ask["status"], ask["withdrawn_by"], ask["withdrawn_reason"], ask["answered_by"]


BELL_FORM = "https://careers.bellintegration.example/apply/661"


def _seed_cv(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python AI engineer.", name="Ada Lovelace", skills=["Python"]),
                             preferences=UserPreferences(target_job_titles=["AI Engineer"])),
                 user_id, source_action="cv_upload")


MEMORY_CLOSED = ("withdrawn", "system", "memory_answers", None)


@pytest.mark.asyncio
async def test_bell_a_kit_read_closes_the_gb_sponsorship_ask_as_memory_never_as_the_user(
    authenticated_async_context,
):
    async with authenticated_async_context() as web:
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        app_id = await _bring(web, "Bell Integration")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, [_sponsor_row()])
            assert (await _asks(web))[ask_id]["status"] == "open"
            kit = await agent.get(f"/api/applications/{app_id}/kit")
            assert kit.status_code == 200, kit.text
        ask = (await _asks(web))[ask_id]
        assert _closed_by_memory(ask) == MEMORY_CLOSED
        assert ask["answer"] in (None, "") and ask["answers"] == {}
        detail = (await web.get(f"/api/applications/{app_id}")).json()
        [event] = [e for e in detail["events"] if e["event_type"] == "ask_withdrawn"]
        assert (event["recorded_by"], event["payload"]) == ("system", {"ask_id": ask_id, "reason": "memory_answers"})
        assert event["detail"] == (
            f"Closed by Job360: your Memory now answers this for this job's country. Asked: {ask['question']}")
        assert not any(e["event_type"] == "answered" for e in detail["events"])


@pytest.mark.asyncio
async def test_bell_check_submit_over_mcp_closes_the_ask_and_no_longer_counts_it(
    authenticated_async_context, fixture_user_id,
):
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        _seed_cv(fixture_user_id)
        app_id = await _bring(web, "Bell Integration")
        for etype, payload in (
            ("form_checked", {"questions_count": 1, "answered": 1, "missing": 0}),
            ("form_filled", {"form_url": BELL_FORM, "fields_count": 1}),
            ("form_swept", {"fields_checked": 1, "fixed": 0}),
        ):
            done = await web.post(f"/api/applications/{app_id}/events", json={"event_type": etype, "payload": payload})
            assert done.status_code == 201, done.text
        token = await _token(web)
        async with _agent(token) as agent:
            ask_id = await _ask(agent, app_id, [_sponsor_row()])
        import httpx2

        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                                  headers={"Authorization": f"Bearer {token}"})
        async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            got = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": BELL_FORM})
            assert not got.is_error, got
            verdict = json.loads(got.content[0].text)
        # The ask no longer stops the job: the gate moves past question_open to its next ordinary ask.
        assert (verdict["decision"], verdict["reason"]) == ("ask", "score_line_unknown")
        assert _closed_by_memory((await _asks(web))[ask_id]) == MEMORY_CLOSED


@pytest.mark.asyncio
async def test_a_memory_save_closes_the_open_ask_and_unset_memory_left_it_open(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Deloitte")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, [_sponsor_row()])
        # Unset is absent (rule #29): a GB page without needs_sponsorship answers nothing.
        await _memory(web, "user_info.right_to_work", {"countries": [{"country": "GB", "work_authorization": "citizen"}]})
        assert (await _asks(web))[ask_id]["status"] == "open"
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        assert _closed_by_memory((await _asks(web))[ask_id]) == MEMORY_CLOSED


@pytest.mark.asyncio
async def test_needs_you_read_closes_a_covered_ask_and_leaves_a_mixed_one_open(authenticated_async_context):
    async with authenticated_async_context() as web:
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        covered_app = await _bring(web, "Tempest Vane")
        mixed_app = await _bring(web, "Sydon AI")
        async with _agent(await _token(web)) as agent:
            covered = await _ask(agent, covered_app, [_sponsor_row(), {
                "key": "right_to_work.gb", "question": AUTH_Q, "answer": "Yes", "source": "guess"}])
            mixed = await _ask(agent, mixed_app, [_sponsor_row(), {
                "key": "referred", "question": "Were you referred by an employee?", "answer": "No",
                "source": "guess"}])
        listed = await web.get("/api/asks")
        assert listed.status_code == 200, listed.text
        assert [a["id"] for a in listed.json()["asks"]] == [mixed]
        every = await _asks(web)
        assert _closed_by_memory(every[covered]) == MEMORY_CLOSED
        assert every[mixed]["status"] == "open" and len(every[mixed]["questions"]) == 2


@pytest.mark.asyncio
async def test_a_partly_answered_ask_is_untouched(authenticated_async_context):
    async with authenticated_async_context() as web:
        first, second = await _bring(web, "Deloitte"), await _bring(web, "Accenture")
        async with _agent(await _token(web)) as agent:
            touched = await _ask(agent, first, [_sponsor_row()])
            control = await _ask(agent, second, [_sponsor_row()])
        saved = await web.post(f"/api/asks/{touched}/answers/save", json={"answers": {"sponsorship.gb.yes_no": "No"}})
        assert saved.status_code == 200, saved.text
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        every = await _asks(web)
        assert (every[touched]["status"], every[touched]["answers"]) == ("open", {"sponsorship.gb.yes_no": "No"})
        assert _closed_by_memory(every[control]) == MEMORY_CLOSED


@pytest.mark.asyncio
async def test_the_wrong_country_and_an_unknown_country_stay_open(authenticated_async_context):
    async with authenticated_async_context() as web:
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        gb_app, us_app = await _bring(web, "Bell Integration"), await _bring(web, "Bell US", "US")
        nowhere = await _bring(web, "Bell Nowhere", None)
        assert (await web.get(f"/api/applications/{nowhere}")).json().get("job_country") in (None, "")
        async with _agent(await _token(web)) as agent:
            gb = await _ask(agent, gb_app, [_sponsor_row()])
            us = await _ask(agent, us_app, [_sponsor_row("sponsorship.us.yes_no")])
            unknown = await _ask(agent, nowhere, [_sponsor_row()])
        assert (await web.get("/api/asks")).status_code == 200
        every = await _asks(web)
        assert _closed_by_memory(every[gb]) == MEMORY_CLOSED
        assert (every[us]["status"], every[unknown]["status"]) == ("open", "open")


@pytest.mark.asyncio
async def test_captcha_and_login_asks_are_untouched(authenticated_async_context):
    async with authenticated_async_context() as web:
        await _memory(web, "user_info.right_to_work", {"countries": [GB_PAGE]})
        captcha_app, login_app, question_app = (await _bring(web, "Captcha Co"), await _bring(web, "Login Co"),
                                                await _bring(web, "Question Co"))
        async with _agent(await _token(web)) as agent:
            captcha = await _ask(agent, captcha_app, [], kind="captcha")
            login = await _ask(agent, login_app, [], kind="login")
            for blocker in (captcha, login):
                _store_rows(blocker, [_sponsor_row()])
            question = await _ask(agent, question_app, [_sponsor_row()])
            for app_id in (captcha_app, login_app, question_app):
                assert (await agent.get(f"/api/applications/{app_id}/kit")).status_code == 200
        every = await _asks(web)
        assert (every[captcha]["status"], every[login]["status"]) == ("open", "open")
        assert [q["key"] for q in every[captcha]["questions"] + every[login]["questions"]] == [
            "sponsorship.gb.yes_no", "sponsorship.gb.yes_no"]
        assert _closed_by_memory(every[question]) == MEMORY_CLOSED


@pytest.mark.asyncio
async def test_an_equality_value_only_the_assistant_saved_stays_open_until_the_user_sets_it(
    authenticated_async_context,
):
    """#1506's rule: a sensitive value the user has not confirmed waits for the user; "Prefer not to say" is
    set."""
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Deloitte")
        row = {"key": "equality.gender", "question": "What is your gender?", "answer": "", "source": "not_known"}
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, [row])
            await _memory(agent, "user_info.equality", {"gender": "Male"})
            assert (await agent.get(f"/api/applications/{app_id}/kit")).status_code == 200
            assert (await _asks(web))[ask_id]["status"] == "open"
        await _memory(web, "user_info.equality", {"gender": "Prefer not to say"})
        assert _closed_by_memory((await _asks(web))[ask_id]) == MEMORY_CLOSED
