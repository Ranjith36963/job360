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
RTW_KEY = "sponsorship.GB.yes_no"
ESSAY_Q = "Why do you want this role?"
SECRET = "SECRET-ANSWER-TEXT"
CV_ID = 7


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


async def _add(user_id: str, app_id: int, event_type: str, payload: dict[str, Any], by: str = "web") -> int:
    from src.services.applications import spine

    ev = await spine.append_event(
        await _db(), user_id=user_id, application_id=app_id, event_type=event_type, payload=payload,
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=by,
    )
    return int(ev["event_id"])


async def _fill(user_id: str, app_id: int, *, rtw: bool = False) -> int:
    answers = [{"question": ESSAY_Q, "answer": SECRET, "source": "written", "key": "why_role"}]
    if rtw:
        answers.append({"question": RTW_Q, "answer": SECRET, "source": "memory", "key": RTW_KEY})
    return await _add(user_id, app_id, "form_filled", {"form_url": "https://x.example/apply", "answers": answers})


async def _shadow(
    user_id: str, app_id: int, fill: Optional[int], *, essay: Optional[bool] = True, cv: Optional[bool] = True,
    rtw: Optional[bool] = None, rtw_present: Optional[bool] = None, by: str = "web",
) -> None:
    payload: dict[str, Any] = {"decision": "needs_you", "form_filled_event_id": fill, "cv_artifact_id": CV_ID}
    payload["essays"] = [] if essay is None else [{"question_key": "why_role", "would_clear": essay, "reasons": []}]
    if cv is not None:
        payload["cv"] = {"would_clear": cv, "reasons": []}
    if rtw is not None:
        payload["rtw_ok"] = rtw
        payload["rtw_present"] = True if rtw_present is None else rtw_present
    await _add(user_id, app_id, "autonomy_shadow", payload, by)


async def _send(user_id: str, app_id: int, event_type: str = "submit_approved", by: str = "web", **extra: Any) -> int:
    return await _add(user_id, app_id, event_type, {"artifact_id": CV_ID, "where": "web", **extra}, by)


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
        fill = await _fill(fixture_user_id, app_id, rtw=True)
        await _shadow(fixture_user_id, app_id, fill, rtw=True)
        await _add(fixture_user_id, app_id, "cv_seen", {"artifact_id": CV_ID, "where": "web"})
        await _send(fixture_user_id, app_id)
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
        fill = await _fill(fixture_user_id, app_id, rtw=True)
        await _shadow(fixture_user_id, app_id, fill, rtw=True)
        await _send(fixture_user_id, app_id)
        await _add(fixture_user_id, app_id, "answer_verified", {
            "question": ESSAY_Q, "answer": SECRET, "edited": True, "key": "why_role", "form_filled_event_id": fill})
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
        fill = await _fill(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, fill, cv=None)
        await _add(fixture_user_id, app_id, "answer_verified", {
            "question": ESSAY_Q, "answer": SECRET, "edited": False, "key": "why_role", "form_filled_event_id": fill})
        await _add(fixture_user_id, app_id, "answer_verified", {
            "question": "Tell us about yourself", "answer": SECRET, "edited": True, "form_filled_event_id": fill})
        body = await _report(client)
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)
    assert body["kinds"]["cv"]["cases"] == 0 and body["kinds"]["fixed"]["cases"] == 0


@pytest.mark.asyncio
async def test_no_decision_is_pending(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, fill)  # essay + cv, nothing decided
        body = await _report(client)
    assert body["pending"] == 2
    assert _kind(body, "essay") == (0, 0, None, 0, False)
    assert _kind(body, "cv") == (0, 0, None, 0, False)
    assert body["examples"] == []


@pytest.mark.asyncio
async def test_a_decline_is_never_agreement(authenticated_async_context, fixture_user_id):
    """B1: 20 declines of things autonomy would NOT clear are no cases; a decline of one it would clear disagrees."""
    async with authenticated_async_context() as client:
        for n in range(1, 21):
            app_id = await _bring(client, n)
            fill = await _fill(fixture_user_id, app_id)
            await _shadow(fixture_user_id, app_id, fill, essay=False, cv=False)
            await _send(fixture_user_id, app_id, "submit_declined")
        body = await _report(client)
        assert _kind(body, "essay") == (0, 0, None, 0, False)
        assert _kind(body, "cv") == (0, 0, None, 0, False)
        assert body["pending"] == 0 and body["examples"] == []
        cleared = await _bring(client, 99)
        fill = await _fill(fixture_user_id, cleared)
        await _shadow(fixture_user_id, cleared, fill)
        await _send(fixture_user_id, cleared, "submit_declined")
        body = await _report(client)
    assert _kind(body, "essay") == (1, 0, 0.0, 0, False)
    assert _kind(body, "cv") == (1, 0, 0.0, 0, False)
    assert {(e["application_id"], e["kind"], e["would_clear"], e["owner"]) for e in body["examples"]} == {
        (cleared, "essay", True, "disagreed"), (cleared, "cv", True, "disagreed")}


@pytest.mark.asyncio
async def test_a_yes_pairs_only_with_the_fill_it_names(authenticated_async_context, fixture_user_id):
    """B2: a yes before the fill, or bound to another fill, is not the answer to this shadow."""
    async with authenticated_async_context() as client:
        early = await _bring(client, 1)
        await _send(fixture_user_id, early)  # a yes with no fill under it
        fill = await _fill(fixture_user_id, early)
        await _shadow(fixture_user_id, early, fill, cv=None)

        bound = await _bring(client, 2)
        old = await _fill(fixture_user_id, bound)
        await _send(fixture_user_id, bound, fingerprint={"form_filled_event_id": old})
        new = await _fill(fixture_user_id, bound)
        await _shadow(fixture_user_id, bound, new, cv=None)

        ok = await _bring(client, 3)
        fill_ok = await _fill(fixture_user_id, ok)
        await _shadow(fixture_user_id, ok, fill_ok, cv=None)
        await _send(fixture_user_id, ok, fingerprint={"form_filled_event_id": fill_ok})

        ranged = await _bring(client, 4)
        first = await _fill(fixture_user_id, ranged)
        await _shadow(fixture_user_id, ranged, first, cv=None)
        await _send(fixture_user_id, ranged)  # no fingerprint: after the fill, before any next one
        await _fill(fixture_user_id, ranged)  # a refill after the yes does not take it back
        body = await _report(client)
    assert _kind(body, "essay") == (2, 2, 100.0, 0, False)
    assert body["pending"] == 2  # the early yes and the yes bound to the older fill


def test_the_shadow_event_records_what_it_judged():
    from src.services.applications import evidence

    shadow = {
        "watch_only": True, "form_filled_event_id": 41, "cv_artifact_id": 9, "rtw_present": True,
        "essays": [], "cv": {"would_clear": True, "reasons": []}, "rtw": {"ok": True, "detail": "x"},
    }
    got = evidence.event_payload(shadow)
    assert (got["form_filled_event_id"], got["cv_artifact_id"], got["rtw_present"]) == (41, 9, True)


@pytest.mark.asyncio
async def test_a_right_to_work_wrong_fact_survives_a_later_fill(authenticated_async_context, fixture_user_id):
    """C1: the edit is on the later fill; the shadow was on the earlier one. Still a wrong fact."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        first = await _fill(fixture_user_id, app_id, rtw=True)
        await _shadow(fixture_user_id, app_id, first, rtw=True, cv=None)
        second = await _fill(fixture_user_id, app_id, rtw=False)  # the newest fill has no right-to-work row
        await _add(fixture_user_id, app_id, "answer_verified", {
            "question": RTW_Q, "answer": SECRET, "edited": True, "key": RTW_KEY, "form_filled_event_id": second})
        body = await _report(client)
    assert _kind(body, "fixed") == (1, 0, 0.0, 1, False)


@pytest.mark.asyncio
async def test_rtw_present_false_never_opens_a_fixed_case(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id, rtw=True)  # the NEWEST fill has one, the shadow says it had none
        await _shadow(fixture_user_id, app_id, fill, rtw=True, rtw_present=False, cv=None)
        await _send(fixture_user_id, app_id)
        body = await _report(client)
    assert body["kinds"]["fixed"]["cases"] == 0
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)


@pytest.mark.asyncio
async def test_a_correction_is_matched_by_the_fill_rows_question_text(authenticated_async_context, fixture_user_id):
    """C2: the owner's correction names the question text only; the shadow keeps the row's key."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, fill, cv=None)
        await _send(fixture_user_id, app_id)
        await _add(fixture_user_id, app_id, "answer_corrected", {"question": ESSAY_Q, "was_source": "written"})
        body = await _report(client)
    assert _kind(body, "essay") == (1, 0, 0.0, 1, False)


@pytest.mark.asyncio
async def test_one_send_is_one_essay_case_however_many_essays(authenticated_async_context, fixture_user_id):
    """B3: three essays, one Send: one case; an edit of any one of them makes it a disagreement."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id)
        three = [{"question_key": k, "would_clear": True, "reasons": []} for k in ("why_role", "q2", "q3")]
        await _add(fixture_user_id, app_id, "autonomy_shadow", {
            "form_filled_event_id": fill, "essays": three, "decision": "needs_you"})
        await _send(fixture_user_id, app_id)
        assert _kind(await _report(client), "essay") == (1, 1, 100.0, 0, False)
        await _add(fixture_user_id, app_id, "answer_verified", {
            "question": "Question three", "answer": SECRET, "edited": True, "key": "q3", "form_filled_event_id": fill})
        assert _kind(await _report(client), "essay") == (1, 0, 0.0, 1, False)


@pytest.mark.asyncio
async def test_the_newest_shadow_of_a_fill_and_the_newest_send_decision_win(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id)
        await _shadow(fixture_user_id, app_id, fill, essay=False, cv=None)
        await _shadow(fixture_user_id, app_id, fill, essay=True, cv=None)  # a second check_submit: one case
        await _send(fixture_user_id, app_id, "submit_declined")
        await _send(fixture_user_id, app_id, "submit_approved")
        body = await _report(client)
    assert _kind(body, "essay") == (1, 1, 100.0, 0, False)


@pytest.mark.asyncio
async def test_assistant_events_never_count_as_the_owner(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        fill = await _fill(fixture_user_id, app_id, rtw=True)
        await _shadow(fixture_user_id, app_id, fill, rtw=True, by="token:claude")
        for by in ("token:claude", "agent:Claude Code"):
            await _send(fixture_user_id, app_id, by=by)
            await _add(fixture_user_id, app_id, "cv_seen", {"artifact_id": CV_ID, "where": "chat"}, by)
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
            their_fill = await _fill(other_id, theirs)
            await _shadow(other_id, theirs, their_fill)
            await _send(other_id, theirs)
            await _add(other_id, theirs, "cv_seen", {"artifact_id": CV_ID, "where": "web"})
            ours = await _bring(mine, 1)
            our_fill = await _fill(fixture_user_id, ours)
            await _shadow(fixture_user_id, ours, our_fill, cv=False)
            mine_body = await _report(mine)
            their_body = await _report(other)
    assert _kind(mine_body, "essay") == (0, 0, None, 0, False)
    assert mine_body["pending"] == 2  # my essay and my cv wait; their yes did not answer them
    assert _kind(their_body, "essay") == (1, 1, 100.0, 0, False)
    assert _kind(their_body, "cv") == (1, 1, 100.0, 0, False)


async def _agreeing_case(client: AsyncClient, user_id: str, n: int, *, agree: bool, edit: bool = False) -> None:
    app_id = await _bring(client, n)
    fill = await _fill(user_id, app_id)
    await _shadow(user_id, app_id, fill, essay=agree, cv=None)  # a "no" the owner then approves = disagreement
    await _send(user_id, app_id)
    if edit:
        await _add(user_id, app_id, "answer_corrected", {"key": "why_role", "was_source": "written"})


@pytest.mark.asyncio
async def test_the_bar_flips_at_20_cases_and_95_percent(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        for n in range(1, 20):  # 19 cases, all agree: too few
            await _agreeing_case(client, fixture_user_id, n, agree=True)
        assert _kind(await _report(client), "essay") == (19, 19, 100.0, 0, False)
        await _agreeing_case(client, fixture_user_id, 20, agree=False)  # 19/20 = 95.0 meets the bar exactly
        assert _kind(await _report(client), "essay") == (20, 19, 95.0, 0, True)
        await _agreeing_case(client, fixture_user_id, 21, agree=False)  # 19/21 = 90.5
        assert _kind(await _report(client), "essay") == (21, 19, 90.5, 0, False)


@pytest.mark.asyncio
async def test_the_bar_compares_integers_not_the_rounded_percentage(authenticated_async_context, fixture_user_id):
    """D: 113/119 = 94.96% rounds to 95.0 for display but is below 95%."""
    async with authenticated_async_context() as client:
        for n in range(1, 114):
            await _agreeing_case(client, fixture_user_id, n, agree=True)
        for n in range(114, 120):
            await _agreeing_case(client, fixture_user_id, n, agree=False)
        assert _kind(await _report(client), "essay") == (119, 113, 95.0, 0, False)


@pytest.mark.asyncio
async def test_one_wrong_fact_blocks_the_bar_despite_a_high_percentage(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        for n in range(1, 41):  # 40 agreeing cases and one wrong fact: 40/41 = 97.6%, bar still not met
            await _agreeing_case(client, fixture_user_id, n, agree=True)
        await _agreeing_case(client, fixture_user_id, 41, agree=True, edit=True)
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
