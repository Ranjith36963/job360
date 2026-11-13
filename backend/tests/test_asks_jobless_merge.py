"""Job-less asks merge, and Memory closes them (production 2026-11-13).

The owner had 4 open asks tied to no job (583, 584, 587, 588) asking the SAME country work-rights questions:
``create_ask`` never merged a job-less ask (family [] when application_id is None), ``tidy_open_asks``
inner-joined applications (job-less asks were never tidied) and ``memory_closes.covered`` skipped job-less
asks. Upstream, the Answer-once page only had slots for saved countries, so each run filed a new ask for
US / NL / SE. Now: the user's read collapses job-less duplicates into one; saving the country facts closes
it; a NEW job-less ask that only asks Memory facts is refused (422, Answer-once page); a job ask is untouched.
Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

US_AUTH = {"key": "right_to_work.us.work_authorization", "question": "Are you legally allowed to work in the US?",
           "answer": "", "source": "not_known"}
US_SPONSOR = {"key": "right_to_work.us.needs_sponsorship",
              "question": "Will you now or in the future need visa sponsorship in the US?",
              "answer": "", "source": "not_known"}
NL_SPONSOR = {"key": "right_to_work.nl.needs_sponsorship",
              "question": "Will you now or in the future need visa sponsorship in the Netherlands?",
              "answer": "", "source": "not_known"}
US_PAGE = {"country": "US", "work_authorization": "citizen", "needs_sponsorship": False}
NL_PAGE = {"country": "NL", "work_authorization": "citizen", "needs_sponsorship": False}
MEMORY_CLOSED = ("withdrawn", "system", "memory_answers")


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _token(web: AsyncClient) -> str:
    resp = await web.post("/api/tokens", json={"name": "apply-routine"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _stored_jobless_ask(user_id: str, rows: list[dict[str, str]]) -> int:
    """A job-less assistant ask as production stored it before this fix (never merged)."""
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    cur = conn.execute(
        "INSERT INTO application_asks (user_id, application_id, question, context, asked_by, asked_at, kind, "
        "questions, answers) VALUES (?, NULL, ?, '', 'assistant:apply-routine', ?, 'question', ?, '') RETURNING id",
        (user_id, "Work rights for the jobs in your queue", datetime.now(timezone.utc).isoformat(), json.dumps(rows)),
    )
    ask_id = int(cur.fetchone()[0])
    conn.commit()
    conn.close()
    return ask_id


async def _bring(web: AsyncClient, company: str, country: str, queue: bool = False) -> int:
    resp = await web.post("/api/jobs/bring", json={
        "title": "AI Engineer", "company": company, "location": "Remote", "country": country, "queue": queue,
        "apply_url": f"https://{company.lower()}.example/jobs/1", "description": "Build agents for customers. " * 8})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _all_asks(web: AsyncClient) -> dict[int, dict[str, Any]]:
    resp = await web.get("/api/asks", params={"status": "all"})
    assert resp.status_code == 200, resp.text
    return {int(a["id"]): a for a in resp.json()["asks"]}


async def _open_ids(web: AsyncClient) -> list[int]:
    """The user's own Needs-you read (it tidies and runs Memory closes first)."""
    resp = await web.get("/api/asks", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    return sorted(int(a["id"]) for a in resp.json()["asks"])


@pytest.mark.asyncio
async def test_four_jobless_work_rights_asks_become_one_open_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        job_id = await _bring(web, "Acme", "GB")
        async with _agent(await _token(web)) as agent:
            job_ask = await agent.post("/api/asks", json={"question": "Form answers", "application_id": job_id,
                                                          "kind": "question", "questions": [US_SPONSOR]})
            assert job_ask.status_code == 201, job_ask.text
        olds = [_stored_jobless_ask(fixture_user_id, rows)
                for rows in ([US_AUTH, US_SPONSOR], [US_SPONSOR], [US_AUTH, NL_SPONSOR], [US_SPONSOR, NL_SPONSOR])]
        keep = olds[-1]
        assert await _open_ids(web) == sorted([int(job_ask.json()["id"]), keep])
        asks_ = await _all_asks(web)
        assert sorted(q["key"] for q in asks_[keep]["questions"]) == sorted(
            [US_AUTH["key"], US_SPONSOR["key"], NL_SPONSOR["key"]])
        for old in olds[:-1]:
            assert (asks_[old]["status"], asks_[old]["withdrawn_by"]) == ("withdrawn", "superseded")
        assert asks_[int(job_ask.json()["id"])]["status"] == "open"  # a job ask is untouched
        assert await _open_ids(web) == sorted([int(job_ask.json()["id"]), keep])  # a second read changes nothing


@pytest.mark.asyncio
async def test_saving_the_country_facts_closes_the_jobless_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        both = _stored_jobless_ask(fixture_user_id, [US_AUTH, US_SPONSOR, NL_SPONSOR])
        us_only = _stored_jobless_ask(fixture_user_id, [US_SPONSOR])
        await _open_ids(web)  # tidy: one open ask keeps every row
        resp = await web.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [US_PAGE]}}]})
        assert resp.status_code == 200, resp.text
        assert await _open_ids(web) == [us_only]  # NL not saved yet: the merged ask stays open
        resp = await web.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [US_PAGE, NL_PAGE]}}]})
        assert resp.status_code == 200, resp.text
        assert await _open_ids(web) == []
        ask = (await _all_asks(web))[us_only]
        assert (ask["status"], ask["withdrawn_by"], ask["withdrawn_reason"]) == MEMORY_CLOSED
        assert (await _all_asks(web))[both]["withdrawn_by"] == "superseded"


@pytest.mark.asyncio
async def test_a_new_pure_memory_jobless_ask_is_refused_and_a_job_ask_is_not(authenticated_async_context):
    async with authenticated_async_context() as web:
        job_id = await _bring(web, "Acme", "US")
        async with _agent(await _token(web)) as agent:
            refused = await agent.post("/api/asks", json={"question": "Work rights", "kind": "question",
                                                          "questions": [US_AUTH, US_SPONSOR]})
            assert refused.status_code == 422, refused.text
            assert "Answer once" in refused.text
            on_job = await agent.post("/api/asks", json={"question": "Work rights", "kind": "question",
                                                         "application_id": job_id, "questions": [US_SPONSOR]})
            assert on_job.status_code == 201, on_job.text


@pytest.mark.asyncio
async def test_a_new_jobless_question_merges_into_the_open_jobless_ask(authenticated_async_context):
    other = {"key": "portfolio_pick", "question": "Which portfolio project should lead the outreach?",
             "answer": "The agent platform", "source": "guess"}
    async with authenticated_async_context() as web:
        async with _agent(await _token(web)) as agent:
            first = await agent.post("/api/asks", json={"question": "Outreach", "kind": "question",
                                                        "questions": [other, US_SPONSOR]})
            assert first.status_code == 201, first.text
            second = await agent.post("/api/asks", json={"question": "Outreach", "kind": "question",
                                                         "questions": [{**other, "answer": "The CV tool"}]})
            assert second.status_code == 201, second.text
        assert second.json()["merged_from"] == [int(first.json()["id"])]
        assert await _open_ids(web) == [int(second.json()["id"])]


@pytest.mark.asyncio
async def test_answer_once_has_a_slot_for_each_queued_jobs_country(authenticated_async_context):
    async with authenticated_async_context() as web:
        resp = await web.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]}}]})
        assert resp.status_code == 200, resp.text
        await _bring(web, "Acme", "US", queue=True)
        await _bring(web, "Bolt", "SE", queue=True)
        await _bring(web, "Corp", "NL")  # not queued: no slot
        resp = await web.get("/api/profile/questionnaire")
        assert resp.status_code == 200, resp.text
        countries = [c["country"] for c in resp.json()["countries"]]
        assert countries == ["GB", "US", "SE"]
