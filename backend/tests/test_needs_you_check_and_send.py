"""F6 + F7: the owner's "Send" comes from the submit gate's own rule, and a saved answer is never "answer this".

F6 (production audit, app 315): ``send_yes`` showed "Send" on the owner for EVERY filled form with no yes, though
check_submit would have said ``submit`` (auto mode, nothing needs the owner) and though the fill held an old CV.
Now: when the gate would ask the owner, the job gets ONE ``check_send`` item ("check the answers and send") that
carries the open or guessed questions of that fill; when it would say submit, the filled form is the assistant's
(``assistant_step`` "submit"); a fill older than the newest CV is the assistant's ("refill the form").
F7: a question whose answer is the user's own approved Memory answer for THAT job's country is never an owner item.

The blocker is DISPLAY ONLY: every case also asks ``kit.submit_verdict`` (the gate, untouched) and pins the safe
direction: the blocker says assistant only where the gate says submit. Real Postgres, real doors to seed.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.services.applications import blockers, kit, spine, which_browser
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://careers.northwind.example/apply/7"
OTHER_FORM = "https://jobs.southwind.example/apply/3"
SPONSOR_Q = "Will you now or in the future require visa sponsorship to work in the UK?"
GB_PAGE = {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}


def _ad(n: int, country: str = "GB") -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": f"Company{n}", "location": "London", "country": country,
        "remote": False, "apply_url": f"https://co{n}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake.",
    }


def _seed(user_id: str, *, signed_in_ask: bool = False) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", location="London, UK"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id,
        AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                          ask_before_signed_in_submit=signed_in_ask),
        "test_seed",
    )


async def _mode(client: AsyncClient, auto: bool) -> None:
    """The mode is the user's own edit (the practice-run rule reads the edit history, not the base)."""
    value = "auto_when_sure" if auto else "confirm"
    resp = await client.patch("/api/profile", json={"edits": [{"path": "assistant_settings.submit_mode", "value": value}]})
    assert resp.status_code == 200, resp.text


async def _post(client: AsyncClient, url: str, body: dict[str, Any], ok: tuple[int, ...] = (200, 201)) -> Any:
    resp = await client.post(url, json=body)
    assert resp.status_code in ok, resp.text
    return resp.json()


async def _bring(client: AsyncClient, n: int, country: str = "GB") -> int:
    body = await _post(client, "/api/jobs/bring", _ad(n, country))
    app_id = int(body["application_id"])
    fit = await client.put(f"/api/applications/{app_id}/fit",
                           json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
    assert fit.status_code == 200, fit.text
    return app_id


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    await _post(client, f"/api/applications/{app_id}/events", {"event_type": event_type, "payload": payload or {}})


async def _cv(client: AsyncClient, app_id: int, text: str = "Ada Lovelace, Python.") -> int:
    return int((await _post(client, f"/api/applications/{app_id}/artifacts", {"kind": "cv", "text": text}))["artifact_id"])


async def _fill(client: AsyncClient, app_id: int, *, url: str = FORM, answers: list[dict[str, str]] | None = None,
                extra: dict[str, Any] | None = None, sweep: bool = True) -> None:
    payload: dict[str, Any] = {"form_url": url, "fields_count": 1, **(extra or {})}
    if answers is not None:
        payload["answers"] = answers
    await _event(client, app_id, "form_filled", payload)
    if sweep:
        await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, **(extra or {})})


async def _prior_applied(client: AsyncClient) -> int:
    """An earlier application on the same host, sent: it clears the practice run and the new-site ask."""
    app_id = await _bring(client, 90)
    await _cv(client, app_id)
    await _post(client, f"/api/applications/{app_id}/cv-seen", {})
    await _fill(client, app_id)
    await _post(client, f"/api/applications/{app_id}/receipt", {"channel": "company_site"})
    return app_id


async def _ready_app(client: AsyncClient, n: int = 1, *, seen: bool = True, url: str = FORM,
                     answers: list[dict[str, str]] | None = None, extra: dict[str, Any] | None = None) -> int:
    """A filled, swept form on a job above the line; the CV is the user's own (seen) unless ``seen`` is False."""
    app_id = await _bring(client, n)
    await _cv(client, app_id)
    if seen:
        await _post(client, f"/api/applications/{app_id}/cv-seen", {})
    await _fill(client, app_id, url=url, answers=answers, extra=extra)
    return app_id


async def _rows(user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    db = await api_deps.get_db()
    return await blockers.blockers_for(db, user_id, ids)  # type: ignore[return-value]


async def _gate(user_id: str, app_id: int, url: str = FORM, *, assistant: bool = False) -> tuple[str, str]:
    from datetime import datetime, timezone

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    verdict = await kit.submit_verdict(db, user_id, row, url, datetime.now(timezone.utc), email_verified=True,
                                       assistant=assistant)
    return verdict.decision, verdict.reason


def _kinds(rows: list[dict[str, Any]]) -> list[str]:
    return [b["kind"] for b in rows]


def _you(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in rows if b["actor"] == "you"]


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


# ── F6 ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_clean_swept_fill_is_the_assistants_and_the_gate_agrees(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, True)
        await _prior_applied(client)
        app_id = await _ready_app(client)
        got = (await _rows(uid, [app_id]))[app_id]
        assert (await _gate(uid, app_id)) == ("submit", "auto_when_sure")
        assert _you(got) == [], got
        assert [(b["kind"], b["label"]) for b in got] == [("assistant_step", "submit")]


@pytest.mark.asyncio
async def test_confirm_mode_is_one_check_send_item_never_send_yes_or_answers(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, False)
        app_id = await _ready_app(client)
        got = (await _rows(uid, [app_id]))[app_id]
        assert _kinds(got) == ["check_send"], got
        item = got[0]
        assert (item["actor"], item["label"]) == ("you", "check the answers and send")
        assert item["ref"]["event_id"] and item["ref"]["questions"] == []
        assert (await _gate(uid, app_id))[0] == "ask"


@pytest.mark.asyncio
async def test_a_guess_and_an_open_ask_on_one_fill_are_one_check_send_with_both_questions(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, True)
        await _prior_applied(client)
        guess = {"question": "Were you referred by an employee?", "answer": "No", "source": "guessed", "key": "referred"}
        app_id = await _ready_app(client, answers=[guess])
        await _post(client, "/api/asks", {
            "application_id": app_id, "question": "Form questions", "kind": "question",
            "questions": [{"key": "notice", "question": "What is your notice period?", "source": "not_known", "answer": ""}]})
        got = (await _rows(uid, [app_id]))[app_id]
        assert "answers" not in _kinds(got) and "send_yes" not in _kinds(got), got
        [item] = [b for b in got if b["kind"] == "check_send"]
        assert set(item["ref"]["questions"]) == {"referred", "notice"}
        assert (await _gate(uid, app_id))[0] == "ask"


@pytest.mark.asyncio
async def test_a_question_asked_before_any_fill_stays_answers(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, True)
        app_id = await _bring(client, 1)
        await _post(client, "/api/asks", {
            "application_id": app_id, "question": "Form questions", "kind": "question",
            "questions": [{"key": "notice", "question": "What is your notice period?", "source": "not_known", "answer": ""}]})
        got = (await _rows(uid, [app_id]))[app_id]
        assert _kinds(_you(got)) == ["answers"] and _you(got)[0]["label"] == "answer 1 question"


@pytest.mark.asyncio
async def test_a_fill_older_than_the_newest_cv_is_the_assistants_refill(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, False)
        guess = {"question": "Were you referred by an employee?", "answer": "No", "source": "guessed", "key": "referred"}
        app_id = await _ready_app(client, answers=[guess])
        await _cv(client, app_id, "Ada Lovelace, a newer version.")
        got = (await _rows(uid, [app_id]))[app_id]
        assert "check_send" not in _kinds(got) and "answers" not in _kinds(got) and "send_yes" not in _kinds(got), got
        # main (2026-11-13): the newer CV is unreviewed, so the assistant also reviews it first; nothing is the owner's.
        assert [b["label"] for b in got if b["actor"] == "assistant"] == ["review the CV", "refill the form"]
        assert not [b for b in got if b["actor"] == "you"], got


_REASONS = ["new_site_first_time", "cv_not_seen", "sensitive_unconfirmed", "question_open", "practice_run",
            "ask_always_site"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _REASONS)
async def test_every_owner_reason_is_check_send_and_the_gate_asks(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid, signed_in_ask=reason == "ask_always_site")
        await _mode(client, True)
        if reason != "practice_run":
            await _prior_applied(client)
        kw: dict[str, Any] = {}
        if reason == "new_site_first_time":
            kw["url"] = OTHER_FORM
        if reason == "cv_not_seen":
            kw["seen"] = False
        if reason == "sensitive_unconfirmed":
            kw["answers"] = [{"question": "Do you have a criminal record?", "answer": "No", "source": "profile",
                              "key": "criminal_record"}]
        if reason == "ask_always_site":
            kw["extra"] = {which_browser.KEY: which_browser.OWN}
        app_id = await _ready_app(client, **kw)
        if reason == "question_open":
            await _post(client, "/api/asks", {"application_id": app_id, "question": "Notice period?"})
        got = (await _rows(uid, [app_id]))[app_id]
        gate = await _gate(uid, app_id, kw.get("url", FORM))
        assert gate[0] == "ask", (reason, gate)
        owner = [b for b in got if b["kind"] in ("check_send", "approve_cv")]
        assert owner, (reason, got)  # the owner sees it (cv_not_seen shows as approve the CV, with the Send folded)
        assert not any(b["label"] == "submit" for b in got), (reason, got)
        if reason != "cv_not_seen":
            assert "check_send" in _kinds(got), (reason, got)
        assert "send_yes" not in _kinds(got)


# ── F7 ───────────────────────────────────────────────────────────────────────


async def _sponsor_ask(client: AsyncClient, app_id: int, source: str = "memory", answer: str = "No") -> None:
    await _post(client, "/api/asks", {
        "application_id": app_id, "question": "Form questions", "kind": "question",
        "questions": [{"key": "sponsorship.gb.yes_no", "question": SPONSOR_Q, "source": source, "answer": answer}]})


@pytest.mark.asyncio
async def test_an_answer_the_user_saved_for_this_country_is_not_an_owner_item(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [GB_PAGE]}}]})
        assert saved.status_code == 200, saved.text
        gb, us = await _bring(client, 1, "GB"), await _bring(client, 2, "US")
        await _sponsor_ask(client, gb)
        await _sponsor_ask(client, us)
        got = await _rows(uid, [gb, us])
        assert "answers" not in _kinds(got[gb]), got[gb]  # the user's own GB answer
        assert [b["label"] for b in got[us] if b["kind"] == "answers"] == ["answer 1 question"]  # never another country's


@pytest.mark.asyncio
async def test_an_answer_an_assistant_saved_and_the_user_never_confirmed_still_asks(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        token = (await _post(client, "/api/tokens", {"name": "claude-code"}))["token"]
        async with _agent(token) as agent:
            saved = await agent.patch("/api/profile", json={"edits": [
                {"path": "user_info.right_to_work", "value": {"countries": [GB_PAGE]}}]})
            assert saved.status_code == 200, saved.text
        app_id = await _bring(client, 1, "GB")
        await _sponsor_ask(client, app_id)
        got = (await _rows(uid, [app_id]))[app_id]
        assert [b["label"] for b in got if b["kind"] == "answers"] == ["answer 1 question"]


# ── list_applications next_step follows the blocker ──────────────────────────


@pytest.mark.asyncio
async def test_next_step_follows_check_send_and_the_assistants_submit(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, True)
        await _prior_applied(client)
        clean = await _ready_app(client, 1)
        guess = {"question": "Were you referred by an employee?", "answer": "No", "source": "guessed", "key": "referred"}
        owner = await _ready_app(client, 2, answers=[guess])
        db = await api_deps.get_db()
        page = await spine.list_applications(db, uid, limit=50)
        steps = {r["id"]: r["next_step"]["code"] for r in page["applications"]}
        assert steps[owner] == "confirm_submit", steps
        assert steps[clean] != "confirm_submit", steps
        detail = await spine.get_application_detail(db, uid, clean)
        assert detail["next_step"]["code"] == steps[clean] and detail["next_step"]["code"] != "confirm_submit"


# ── the Needs-you filters (#1597): check_send is the owner's Send step ───────


def test_check_send_maps_to_the_send_filter_never_other():
    from src.services.applications import needs_you_count

    blocker = {"kind": "check_send", "actor": "you", "label": "check the answers and send", "ref": {"event_id": 1}}
    assert needs_you_count.filter_for(blocker) == "check_send"
    assert needs_you_count.filters_of([blocker]) == {"check_send"}
    assert needs_you_count._is_known(blocker)  # in the table: no "unknown kind" log line, no Other fallback


@pytest.mark.asyncio
async def test_a_check_send_job_is_listed_and_counted_in_the_send_filter_and_not_in_other(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _mode(client, False)
        app_id = await _ready_app(client)
        assert _kinds((await _rows(uid, [app_id]))[app_id]) == ["check_send"]
        resp = await client.get("/api/needs-you-count")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by = {f["key"]: f for f in body["filters"]}
        assert by["check_send"]["job_ids"] == [app_id] and by["check_send"]["count"] == 1, by["check_send"]
        assert app_id not in by["other"]["job_ids"] and by["other"]["count"] == 0, by["other"]
        assert by["all"]["job_ids"] == [app_id] and body["n"] == 1, body
