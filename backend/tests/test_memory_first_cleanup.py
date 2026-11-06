"""Memory first (owner 2026-11-05, plan S3): old saved answers that Memory now covers.

The user's web session lists every saved answer in Memory's families beside what Memory says (the country
page, else its region's, else ANY's) and a verdict; Delete removes one through PATCH /profile's remove_at,
only from the web session. New records carry ``saved_by``, set by the server. Real HTTP doors and VALUE
asserts (rule #21). Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

LIST = "/api/profile/memory-covered-answers"
SPONSOR_Q = "Will you now or in the future require visa sponsorship?"


def _saved(question: str, answer: str, topic: str, **extra: Any) -> dict[str, Any]:
    return {"question": question, "answer": answer, "approved": True, "topic": topic, **extra}


OLD = [
    _saved(SPONSOR_Q, "Yes", "sponsorship.GB.yes_no", country="GB"),  # Memory says No: different
    _saved("Do you need sponsorship to work in the US?", "No", "sponsorship.US.yes_no", country="US"),  # same
    _saved("Do you need a visa to work in India?", "No", "sponsorship.IN.yes_no", country="IN"),  # nothing set
    _saved("Why do you want this job?", "I like the product.", "why_this_job"),  # not Memory's: not listed
]


def _seed(user_id: str, answers: list[dict[str, Any]]) -> None:
    """The owner's old records as they sit on production (before S2, a whole-list write)."""
    from src.services.profile import edits
    from src.services.profile.models import UserProfile
    from src.services.profile.storage import load_profile, save_profile

    if load_profile(user_id, with_overlay=False) is None:
        save_profile(UserProfile(), user_id, source_action="agent_edit")
    edits.record_edits(user_id, edits.WEB_ACTOR, [
        ("user_info.right_to_work", {"countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False},
            {"country": "US", "work_authorization": "visa", "needs_sponsorship": False}]}),
        ("user_info.answers", answers),
    ], enforce_rate_limit=False)


def _answers(user_id: str) -> list[dict[str, Any]]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)
    return [] if profile is None else list(profile.user_info.answers or [])


def _edit_id(user_id: str) -> int:
    from src.services.profile import edits

    return edits.current_edit_id(user_id)


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_the_list_shows_each_memory_answer_beside_memory_with_a_verdict(
    authenticated_async_context, fixture_user_id
):
    _seed(fixture_user_id, OLD)
    async with authenticated_async_context() as client:
        resp = await client.get(LIST)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["answers"]
    assert [(r["index"], r["country"], r["answer"], r["memory"], r["verdict"]) for r in rows] == [
        (0, "GB", "Yes", "No", "different"),
        (1, "US", "No", "No", "same"),
        (2, "IN", "No", None, "memory_empty"),
    ]
    assert rows[0]["question"] == SPONSOR_Q and rows[0]["topic"] == "sponsorship.GB.yes_no"
    assert resp.json()["profile_edit_id"] == _edit_id(fixture_user_id)


@pytest.mark.asyncio
async def test_a_web_delete_removes_exactly_that_answer_and_moves_the_edit_id(
    authenticated_async_context, fixture_user_id
):
    _seed(fixture_user_id, OLD)
    async with authenticated_async_context() as client:
        listed = (await client.get(LIST)).json()
        before = listed["profile_edit_id"]
        gb = listed["answers"][0]
        resp = await client.delete(f"{LIST}/{gb['id']}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == gb["id"] and resp.json()["profile_edit_id"] > before
        assert resp.json()["profile_edit_id"] == _edit_id(fixture_user_id)
        assert [r["question"] for r in _answers(fixture_user_id)] == [q["question"] for q in OLD[1:]]
        again = await client.delete(f"{LIST}/{gb['id']}")
        assert again.status_code == 404, again.text
        history = (await client.get("/api/profile/edits/history", params={"path": "user_info.answers"})).json()
    newest = history["rows"][0]  # the delete is one more row of the field's history, newest first
    assert newest["set_by"] == "web" and [r["topic"] for r in newest["value"]] == [q["topic"] for q in OLD[1:]]


@pytest.mark.asyncio
async def test_a_token_can_neither_list_nor_delete_and_nothing_changes(authenticated_async_context, fixture_user_id):
    _seed(fixture_user_id, OLD)
    async with authenticated_async_context() as client:
        gb = (await client.get(LIST)).json()["answers"][0]
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    before, edit_id = _answers(fixture_user_id), _edit_id(fixture_user_id)
    async with _agent(token) as agent:
        refused = await agent.delete(f"{LIST}/{gb['id']}")
        assert refused.status_code == 403, refused.text
        assert (await agent.get(LIST)).status_code == 403
    assert _answers(fixture_user_id) == before and _edit_id(fixture_user_id) == edit_id


@pytest.mark.asyncio
async def test_a_new_saved_answer_records_who_saved_it_and_the_caller_cannot_claim_it(
    authenticated_async_context, fixture_user_id
):
    essay = {"question": "Why do you want this job?", "answer": "The product.", "topic": "why_this_job"}
    heard = {"question": "Are you happy to do a take-home exercise?", "answer": "Yes", "topic": "take_home",
             "saved_by": "web"}  # an assistant claiming the owner's click: the server overrides it
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.answers", "op": "append", "value": essay}]})
        assert resp.status_code == 200, resp.text
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with _agent(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [
            {"path": "user_info.answers", "op": "append", "value": heard}]})
        assert resp.status_code == 200, resp.text
    assert [(r["topic"], r.get("saved_by")) for r in _answers(fixture_user_id)] == [
        ("why_this_job", "web"), ("take_home", "assistant")]


def test_an_old_record_keeps_no_saved_by_and_an_unknown_value_is_refused():
    from src.services.profile import edits, user_info

    old = user_info.validate_user_info("user_info.answers", [OLD[3]])
    assert "saved_by" not in old[0]
    with pytest.raises(edits.ProfileEditError) as exc:
        user_info.validate_user_info("user_info.answers", [{**OLD[3], "saved_by": "owner"}])
    assert exc.value.status_code == 422
