"""Owner 2026-10-26 - the questions almost every form asks, verified once in Memory.

GET /api/profile/common-questions lists each catalog question with the user's saved answer (read the
same way the kit reads it, so Memory and the form filler agree) and whether the user verified it; a
per-country question gets one row per saved country; nothing is guessed. Helpers are defined here.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile import common_questions


def test_the_catalog_is_well_formed():
    rows = common_questions.catalog()
    # Owner plan 2026-10-28 (B1): the four CV-answered rows left the catalog; exactly 29 remain.
    assert len(rows) == 29 and len({q["id"] for q in rows}) == len(rows)
    assert not {"education", "current_company", "current_title", "years_experience"} & {q["id"] for q in rows}
    assert {q["group"] for q in rows} == set(common_questions.GROUPS)


async def _rows(client: AsyncClient) -> dict[str, dict[str, Any]]:
    resp = await client.get("/api/profile/common-questions")
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()["questions"]}


@pytest.mark.asyncio
async def test_saved_answers_show_and_the_users_own_are_verified(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context() as client:
        before = await _rows(client)
        assert before["email"]["answer"] is None and before["email"]["state"] == "missing"  # never guessed
        assert not any(k.startswith("needs_sponsorship.") for k in before)  # no country saved yet
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.contact", "value": {"email": "ada@example.com", "phone": "+447700900123"}},
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "visa", "needs_sponsorship": False}]}},
            {"path": "user_info.answers", "op": "upsert", "value": {
                "question": "How did you hear about this job?", "answer": "LinkedIn", "approved": True,
                "topic": "form.how_heard"}},
        ]})
        assert saved.status_code == 200, saved.text
        rows = await _rows(client)
        assert (rows["email"]["answer"], rows["email"]["verified"]) == ("ada@example.com", True)
        assert (rows["needs_sponsorship.GB"]["answer"], rows["needs_sponsorship.GB"]["verified"]) == ("No", True)
        assert rows["needs_sponsorship.GB"]["question"].endswith("in GB?")
        assert (rows["how_heard"]["answer"], rows["how_heard"]["verified"]) == ("LinkedIn", True)
        summary = (await client.get("/api/profile/common-questions")).json()["summary"]
        assert summary["verified"] >= 4 and summary["total"] == summary["verified"] + summary["to_verify"] + summary["missing"]
        # An assistant's own write is not the user's word: it shows, but is not verified.
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            put = await agent.patch("/api/profile", json={"edits": [
                {"path": "user_info.logistics", "value": {"notice_period": "1 month"}, "source": "agent_inferred"}]})
            assert put.status_code == 200, put.text
        notice = (await _rows(client))["notice_period"]
        assert (notice["answer"], notice["verified"], notice["state"]) == ("1 month", False, "inferred")
        # An approved answer the assistant saved itself shows, but is not the user's verified word.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            put = await agent.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "op": "upsert",
                "value": {"question": "Referred by an employee?", "answer": "No", "approved": True,
                          "topic": "form.referred"}}]})
            assert put.status_code == 200, put.text
        referred = (await _rows(client))["referred"]
        assert (referred["answer"], referred["verified"]) == ("No", False)


@pytest.mark.asyncio
async def test_only_memory_or_guessed_and_the_assistant_is_told_what_to_draft(authenticated_async_context):
    """Owner 2026-10-26: "only two things, from memory and guessed; there is no not known". An answer the
    assistant drafted (approved: false) reads Guessed; the open ones are listed for it in get_profile."""
    from src.services.profile import common_questions as cq
    from src.services.profile.models import UserInfo, UserProfile

    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.contact", "value": {"email": "ada@example.com"}},
            {"path": "user_info.answers", "op": "upsert", "value": {
                "question": "How did you hear about this job?", "answer": "LinkedIn", "approved": False,
                "topic": "form.how_heard"}},
        ]})
        assert saved.status_code == 200, saved.text
        rows = await _rows(client)
        assert (rows["email"]["label"], rows["how_heard"]["label"]) == ("memory", "guessed")
        assert rows["how_heard"]["verified"] is False and rows["phone"]["label"] == "not_known"
        assert {r["label"] for r in rows.values()} <= {"memory", "guessed", "not_known"}
    profile = UserProfile(user_info=UserInfo(contact={"email": "a@b.c"}))
    open_ids = {o["id"]: o for o in cq.open_questions(profile)}
    assert "email" not in open_ids and open_ids["phone"]["save_to"] == "user_info.contact"
    assert "how_heard" not in open_ids  # B1: asked per job on Needs you, never drafted from Memory
    assert open_ids["over_18"] == {"id": "over_18", "question": "Are you 18 or older?",
                                   "save_to": "user_info.answers", "topic": "form.over_18"}


# ── B1: per-job rows, the slimmer catalog, sensitive rows need a web write ────


def test_b1_catalog_shape():
    by_id = {q["id"]: q for q in common_questions.catalog()}
    for qid in ("how_heard", "referred", "worked_before", "consent_processing", "consent_future",
                "consent_sms", "current_salary"):
        assert by_id[qid].get("per_job") is True, qid
    for gone in ("education", "current_company", "current_title", "years_experience"):
        assert gone not in by_id
    assert by_id["office_days"]["kit_key"] == "right_to_work.{cc}.onsite" and "topic" not in by_id["office_days"]
    assert by_id["travel"]["kit_key"] == "logistics.{cc}.travel_ok_pct"


@pytest.mark.asyncio
async def test_b1_per_job_flag_no_education_and_office_days_kit_key(authenticated_async_context):
    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "visa", "onsite": "yes"}]}}]})
        assert saved.status_code == 200, saved.text
        rows = await _rows(client)
        assert rows["how_heard"]["per_job"] is True and rows["email"]["per_job"] is False
        assert not any(k.split(".")[0] == "education" for k in rows)
        assert rows["office_days.GB"]["kit_key"] == "right_to_work.GB.onsite"
        assert (rows["office_days.GB"]["answer"], rows["office_days.GB"]["set_by"]) == ("yes", "web")
        assert rows["office_days.GB"]["set_at"] and rows["office_days.GB"]["save_to"] == "user_info.right_to_work"
        assert rows["expected_salary.GB"]["sensitive"] is True and rows["email"]["sensitive"] is False
        summary = (await client.get("/api/profile/common-questions")).json()["summary"]
        assert summary["total"] == len([r for r in rows.values() if not r["per_job"]])


@pytest.mark.asyncio
async def test_b1_sensitive_row_needs_a_web_write(authenticated_async_context):
    """An assistant's user_said write to a sensitive row reads guessed; the web user's own write reads memory."""
    from src.api.main import app

    salary = {"country": "GB", "min": 50000, "max": 60000, "currency": "GBP", "period": "year"}
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            put = await agent.patch("/api/profile", json={"edits": [{
                "path": "preferences.salary_by_country", "value": [salary], "source": "user_said",
                "remember": True}]})
            assert put.status_code == 200, put.text
        row = (await _rows(client))["expected_salary.GB"]
        assert (row["label"], row["verified"], row["guess_source"]) == ("guessed", False, "user_said")
        assert row["set_by"] != "web"
        web = await client.patch("/api/profile", json={"edits": [
            {"path": "preferences.salary_by_country", "value": [salary]}]})
        assert web.status_code == 200, web.text
        row = (await _rows(client))["expected_salary.GB"]
        assert (row["label"], row["verified"], row["set_by"], row["guess_source"]) == ("memory", True, "web", None)


def test_b1_open_questions_and_summary_skip_per_job():
    from src.services.profile.models import UserProfile

    open_ids = {o["id"] for o in common_questions.open_questions(UserProfile())}
    per_job = {q["id"] for q in common_questions.catalog() if q.get("per_job")}
    assert open_ids and not (open_ids & per_job)
    rows = [{"verified": False, "answer": None, "per_job": True}, {"verified": True, "answer": "x", "per_job": False}]
    assert common_questions.summary(rows) == {"total": 1, "verified": 1, "to_verify": 0, "missing": 0}


@pytest.mark.asyncio
async def test_b1_get_profile_common_questions_open_lists_no_per_job_id(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada", skills=["Python"])),
                 fixture_user_id, source_action="cv_upload")
    async with authenticated_async_context():
        get_profile = mcp_server.build_server()._tool_manager.get_tool("get_profile").fn
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            opened = (await get_profile()).get("common_questions_open") or []
        finally:
            mcp_server._current_user.set(None)
    per_job = {q["id"] for q in common_questions.catalog() if q.get("per_job")}
    assert opened and not ({o["id"] for o in opened} & per_job)
