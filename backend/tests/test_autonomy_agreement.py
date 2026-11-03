"""GET /api/autonomy/agreement: the watch-only shadow verdicts against the OWNER's own web decisions.

Real HTTP for the report and for the births; the server-only events (autonomy_shadow, answer_verified) and the
owner's decisions are seeded through the spine door, the same append the routes use. VALUE asserts (rule #21).
Helpers are copied, never imported from another test module (schema isolation)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from httpx import ASGITransport, AsyncClient

RTW_Q = "Will you require visa sponsorship?"
SECRET = "SECRET-ANSWER-TEXT"


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB",
        "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add(user_id: str, app_id: int, event_type: str, payload: dict[str, Any], by: str = "web") -> None:
    from src.services.applications import spine

    await spine.append_event(
        await _db(), user_id=user_id, application_id=app_id, event_type=event_type, payload=payload,
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=by,
    )


async def _shadow(
    user_id: str, app_id: int, *, essay: Optional[bool] = True, cv: Optional[bool] = True,
    rtw: Optional[bool] = None, key: str = "why_role", by: str = "web",
) -> None:
    payload: dict[str, Any] = {"decision": "needs_you"}
    payload["essays"] = [] if essay is None else [{"question_key": key, "would_clear": essay, "reasons": []}]
    if cv is not None:
        payload["cv"] = {"would_clear": cv, "reasons": []}
    if rtw is not None:
        payload["rtw_ok"] = rtw
    await _add(user_id, app_id, "autonomy_shadow", payload, by)


async def _fill_with_rtw(user_id: str, app_id: int) -> None:
    answers = [{"question": RTW_Q, "answer": SECRET, "source": "memory", "key": "sponsorship.GB.yes_no"}]
    await _add(user_id, app_id, "form_filled", {"form_url": "https://x.example/apply", "answers": answers})


async def _report(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/autonomy/agreement")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _kind(body: dict[str, Any], kind: str) -> tuple[Any, ...]:
    k = body["kinds"][kind]
    return k["cases"], k["agree"], k["agreement_pct"], k["wrong_facts"], k["bar_met"]


@pytest.mark.asyncio
async def test_shadow_plus_owner_approve_is_agree(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _fill_with_rtw(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, rtw=True)
        await _add(fixture_user_id, app_id, "cv_seen", {"where": "web"})
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        body = await _report(client)
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)
    assert _kind(body, "fixed") == (1, 1, 100.0, 0, False)
    assert _kind(body, "cv") == (1, 1, 100.0, 0, False)
    assert body["pending"] == 0 and body["examples"] == []
    assert body["bar"] == {"min_cases": 20, "min_agree_pct": 95}


@pytest.mark.asyncio
async def test_would_clear_then_owner_edit_is_disagree_and_a_wrong_fact(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _fill_with_rtw(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, rtw=True)
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        await _add(fixture_user_id, app_id, "answer_verified",
                   {"question": "Why this role?", "answer": SECRET, "edited": True, "key": "why_role"})
        await _add(fixture_user_id, app_id, "answer_corrected", {"question": RTW_Q, "was_source": "guessed"})
        resp = await client.get("/api/autonomy/agreement")
        body = resp.json()
    assert _kind(body, "essay") == (1, 0, 0.0, 1, False)
    assert _kind(body, "fixed") == (1, 0, 0.0, 1, False)
    assert {(e["kind"], e["question_key"], e["would_clear"], e["owner"]) for e in body["examples"]} == {
        ("essay", "why_role", True, "disagreed"), ("fixed", "right_to_work", True, "disagreed")}
    assert all(e["application_id"] == app_id for e in body["examples"])
    assert SECRET not in resp.text


@pytest.mark.asyncio
async def test_a_verify_without_edit_agrees_and_an_edit_of_another_question_does_not_count(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _shadow(fixture_user_id, app_id, cv=None)
        await _add(fixture_user_id, app_id, "answer_verified",
                   {"question": "Why this role?", "answer": SECRET, "edited": False, "key": "why_role"})
        await _add(fixture_user_id, app_id, "answer_verified",
                   {"question": "Tell us about yourself", "answer": SECRET, "edited": True})
        body = await _report(client)
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)
    assert body["kinds"]["cv"]["cases"] == 0 and body["kinds"]["fixed"]["cases"] == 0


@pytest.mark.asyncio
async def test_no_decision_is_pending_and_declined_cv_disagrees(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        waiting = await _bring(client, 1)
        await _shadow(fixture_user_id, waiting)  # essay + cv, nothing decided
        declined = await _bring(client, 2)
        await _shadow(fixture_user_id, declined, essay=None, cv=True)
        await _add(fixture_user_id, declined, "submit_declined", {"where": "web"})
        body = await _report(client)
    assert body["pending"] == 2  # the waiting application's essay and cv
    assert _kind(body, "essay") == (0, 0, None, 0, False)
    assert _kind(body, "cv") == (1, 0, 0.0, 0, False)
    assert body["examples"] == [{"application_id": declined, "kind": "cv", "question_key": "cv",
                                 "would_clear": True, "owner": "disagreed"}]


@pytest.mark.asyncio
async def test_a_fixed_case_needs_a_right_to_work_question_in_the_fill(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _shadow(fixture_user_id, app_id, rtw=True)  # rtw_ok True also means "no right-to-work question"
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        body = await _report(client)
    assert body["kinds"]["fixed"]["cases"] == 0
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)


@pytest.mark.asyncio
async def test_the_newest_shadow_and_the_newest_send_decision_win(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _shadow(fixture_user_id, app_id, essay=False, cv=None)
        await _shadow(fixture_user_id, app_id, essay=True, cv=None)  # a second check_submit: one case, not two
        await _add(fixture_user_id, app_id, "submit_declined", {"where": "web"})
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        body = await _report(client)
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)


@pytest.mark.asyncio
async def test_assistant_events_never_count_as_the_owner(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _shadow(fixture_user_id, app_id, rtw=True, by="token:claude")
        await _fill_with_rtw(fixture_user_id, app_id)
        for by in ("token:claude", "agent:Claude Code"):
            await _add(fixture_user_id, app_id, "submit_approved", {"where": "chat"}, by)
            await _add(fixture_user_id, app_id, "cv_seen", {"where": "chat"}, by)
            await _add(fixture_user_id, app_id, "answer_corrected", {"key": "why_role", "was_source": "written"}, by)
        body = await _report(client)
    assert [body["kinds"][k]["cases"] for k in ("essay", "fixed", "cv")] == [0, 0, 0]
    assert body["pending"] == 3 and body["examples"] == []


@pytest.mark.asyncio
async def test_another_users_data_never_appears(authenticated_async_context, fixture_user_id):
    from src.api.main import app
    from tests._signin import async_sign_in

    async with authenticated_async_context() as mine:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
            _cookie, other_id = await async_sign_in(other, "agreement-other@example.com")
            theirs = await _bring(other, 9)
            await _shadow(other_id, theirs)
            await _add(other_id, theirs, "submit_approved", {"where": "web"})
            await _add(other_id, theirs, "cv_seen", {"where": "web"})
            ours = await _bring(mine, 1)
            await _shadow(fixture_user_id, ours, cv=False)
            await _add(fixture_user_id, ours, "submit_approved", {"where": "web"})
            mine_body = await _report(mine)
            their_body = await _report(other)
    assert _kind(mine_body, "essay") == (1, 1, 100.0, 0, False)
    assert _kind(mine_body, "cv") == (1, 0, 0.0, 0, False)  # my cv=False vs my approve; their agreeing cv_seen is not mine
    assert mine_body["pending"] == 0
    assert _kind(their_body, "essay") == (1, 1, 100.0, 0, False)
    assert _kind(their_body, "cv") == (1, 1, 100.0, 0, False)


@pytest.mark.asyncio
async def test_the_bar_flips_at_20_cases_and_95_percent(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        for n in range(1, 20):  # 19 cases, all agree: too few
            app_id = await _bring(client, n)
            await _shadow(fixture_user_id, app_id, cv=None)
            await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        assert _kind(await _report(client), "essay") == (19, 19, 100.0, 0, False)
        # case 20 disagrees (shadow said no, owner approved): 19/20 = 95.0 meets the bar exactly
        app_id = await _bring(client, 20)
        await _shadow(fixture_user_id, app_id, essay=False, cv=None)
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        assert _kind(await _report(client), "essay") == (20, 19, 95.0, 0, True)
        # case 21 disagrees too: 19/21 = 90.5 falls below it
        app_id = await _bring(client, 21)
        await _shadow(fixture_user_id, app_id, essay=False, cv=None)
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        assert _kind(await _report(client), "essay") == (21, 19, 90.5, 0, False)


@pytest.mark.asyncio
async def test_one_wrong_fact_blocks_the_bar_despite_a_high_percentage(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        for n in range(1, 41):  # 40 agreeing cases and one wrong fact: 40/41 = 97.6%, bar still not met
            app_id = await _bring(client, n)
            await _shadow(fixture_user_id, app_id, cv=None)
            await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        app_id = await _bring(client, 41)
        await _shadow(fixture_user_id, app_id, cv=None)
        await _add(fixture_user_id, app_id, "submit_approved", {"where": "web"})
        await _add(fixture_user_id, app_id, "answer_corrected", {"key": "why_role", "was_source": "written"})
        assert _kind(await _report(client), "essay") == (41, 40, 97.6, 1, False)


@pytest.mark.asyncio
async def test_session_only_and_signed_in(authenticated_async_context):
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/autonomy/agreement")).status_code == 401
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            resp = await agent.get("/api/autonomy/agreement")
            assert resp.status_code == 403 and resp.json()["detail"] == "session_required"
