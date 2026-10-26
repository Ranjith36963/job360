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
    assert len(rows) >= 30 and len({q["id"] for q in rows}) == len(rows)
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
        assert rows["how_heard"]["verified"] is False and rows["phone"]["label"] is None
        assert {r["label"] for r in rows.values()} <= {"memory", "guessed", None}
    profile = UserProfile(user_info=UserInfo(contact={"email": "a@b.c"}))
    open_ids = {o["id"]: o for o in cq.open_questions(profile)}
    assert "email" not in open_ids and open_ids["phone"]["save_to"] == "user_info.contact"
    assert open_ids["how_heard"] == {"id": "how_heard", "question": "How did you hear about this job?",
                                     "save_to": "user_info.answers", "topic": "form.how_heard"}
