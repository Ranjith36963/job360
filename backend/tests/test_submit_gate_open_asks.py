"""Owner rule 2026-10-31: no submit while this job has an open ask (CAPTCHA / sign-in / upload / safety
-> ``blocker_open``; a question -> ``question_open``). A yes clears neither; answered / withdrawn asks,
another job's ask and the doctor's "keep this lesson?" ask do not stop it. Real Postgres through the route.
Helpers are copied, never imported across test modules (schema isolation)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import asks
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://careers.northwind.example/apply/7"
BASE = "score_line_unknown"  # the first ordinary ask when nothing else stops


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


async def _ready_app(client: AsyncClient, n: int) -> int:
    ad = {
        "title": f"Data Engineer {n}", "company": "Northwind", "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. Python.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    for etype, payload in (
        ("form_checked", {"questions_count": 1, "answered": 1, "missing": 0}),
        ("form_filled", {"form_url": FORM, "fields_count": 1}),
        ("form_swept", {"fields_checked": 1, "fixed": 0}),
    ):
        r = await client.post(f"/api/applications/{app_id}/events", json={"event_type": etype, "payload": payload})
        assert r.status_code == 201, r.text
    return app_id


async def _check(client: AsyncClient, app_id: int) -> tuple[str, str]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["decision"], body["reason"]


async def _ask(user_id: str, app_id: int, kind: str, questions: list[dict[str, Any]] | None = None,
               context: str = "") -> int:
    made = await asks.create_ask(
        await _db(), user_id, app_id, f"Needs you ({kind})", context, "claude-code", kind=kind, questions=questions
    )
    return int(made["id"])


def _row(key: str = "referred") -> list[dict[str, Any]]:
    return [{"key": key, "question": "Were you referred?", "answer": "No", "source": "guess"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,reason",
    [("captcha", "blocker_open"), ("login", "blocker_open"), ("upload", "blocker_open"),
     ("safety", "blocker_open"), ("question", "question_open")],
)
async def test_each_open_ask_kind_stops_a_submit(authenticated_async_context, fixture_user_id, kind, reason):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        assert await _check(client, app_id) == ("ask", BASE)
        await _ask(fixture_user_id, app_id, kind, _row() if kind == "question" else None)
        assert await _check(client, app_id) == ("ask", reason)


@pytest.mark.asyncio
async def test_a_yes_never_clears_an_open_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        yes = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "submit_approved", "payload": {"artifact_id": None, "sha256": "", "where": "web"}},
        )
        assert yes.status_code == 201, yes.text
        assert await _check(client, app_id) == ("submit", "user_approved")
        await _ask(fixture_user_id, app_id, "captcha")
        assert await _check(client, app_id) == ("ask", "blocker_open")
        await _ask(fixture_user_id, app_id, "question", _row())
        assert await _check(client, app_id) == ("ask", "blocker_open")  # blocker wins, still no submit


@pytest.mark.asyncio
async def test_a_question_ask_whose_field_holds_an_answer_still_stops(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        await _ask(fixture_user_id, app_id, "question", _row())
        assert await _check(client, app_id) == ("ask", "question_open")


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["answered", "withdrawn"])
async def test_answered_or_withdrawn_asks_pass(authenticated_async_context, fixture_user_id, how):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        ask_id = await _ask(fixture_user_id, app_id, "question", _row())
        assert await _check(client, app_id) == ("ask", "question_open")
        db = await _db()
        if how == "answered":
            await asks.answer_ask(db, fixture_user_id, ask_id, "ok", "web", answers={"referred": "No"})
        else:
            await asks.withdraw_ask(db, fixture_user_id, ask_id, "claude-code", "not needed")
        assert await _check(client, app_id) == ("ask", BASE)


@pytest.mark.asyncio
async def test_an_ask_on_another_application_does_not_block_this_one(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        mine, other = await _ready_app(client, 1), await _ready_app(client, 2)
        await _ask(fixture_user_id, other, "captcha")
        assert await _check(client, mine) == ("ask", BASE)
        assert await _check(client, other) == ("ask", "blocker_open")


@pytest.mark.asyncio
async def test_the_doctor_lesson_and_proof_asks_do_not_block(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit, spine

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        lesson = _row("doctor_repeat_41")
        lesson[0].update(question="This mistake came back. Keep this lesson?", answer="Keep")
        await _ask(fixture_user_id, app_id, "", lesson)
        await _ask(fixture_user_id, app_id, "", None, context=f"proof_missing:{app_id}")
        assert await _check(client, app_id) == ("ask", BASE)
        row = await spine.get_owned_application(await _db(), fixture_user_id, app_id)
        facts = await kit.gate_facts(await _db(), fixture_user_id, row, email_verified=True)
        assert facts.open_asks == ()


def test_reasons_are_listed_and_the_pure_gate_orders_them_after_blocked():
    from datetime import datetime, timezone

    from src.services.profile import assistant_settings as rules

    assert {"blocker_open", "question_open"} <= set(rules.REASONS)
    cfg = rules.effective(AssistantSettings(submit_mode="auto_when_sure"))
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=3)
    facts = rules.SubmitFacts(
        status="considering", has_receipt=False, submit_override=None, cv_seen=True, approved=True,
        form_filled=True, form_swept=True, form_host="careers.northwind.example", blocked=True,
        open_asks=("question", "login"),
    )
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    assert rules.may_submit(cfg, facts, FORM, counts, now=now).reason == "blocked"


@pytest.mark.asyncio
async def test_a_merged_ask_with_a_form_row_and_a_doctor_row_still_blocks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        lesson = _row("doctor_repeat_9")
        await _ask(fixture_user_id, app_id, "question", lesson)
        assert await _check(client, app_id) == ("ask", BASE)  # doctor-only passes
        await _ask(fixture_user_id, app_id, "question", _row("salary"))  # merges into the same ask
        assert await _check(client, app_id) == ("ask", "question_open")


@pytest.mark.asyncio
async def test_send_closes_the_agents_sentence_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        await asks.create_ask(await _db(), fixture_user_id, app_id, "May I submit this one?", "", "claude-code")
        assert await _check(client, app_id) == ("ask", "question_open")
        cv = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "CV text"})
        assert cv.status_code in (200, 201), cv.text
        sent = await client.post(f"/api/applications/{app_id}/send/approve")
        assert sent.status_code == 201, sent.text
        assert (await _check(client, app_id))[1] != "question_open"


@pytest.mark.asyncio
async def test_send_keeps_a_form_question_ask_open(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        await _ask(fixture_user_id, app_id, "question", _row("salary"))
        n = await asks.withdraw_open_matching(await _db(), fixture_user_id, app_id, "web", "answered by Send", blockers=False)
        assert n == 0
        assert await _check(client, app_id) == ("ask", "question_open")


@pytest.mark.asyncio
async def test_unblocked_withdraws_a_later_merged_blocker_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        blocked = {"reason": "captcha", "step": "submit", "page_host": "jobs.northwind.example"}
        r = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "blocked", "payload": blocked})
        assert r.status_code == 201, r.text
        await _ask(fixture_user_id, app_id, "login")  # supersedes the blocked ask: id not on the event
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "unblocked", "payload": {"resolution": "user_did_it"}},
        )
        assert r.status_code == 201, r.text
        assert await _check(client, app_id) == ("ask", BASE)


@pytest.mark.asyncio
async def test_an_agents_unblocked_keeps_the_captcha_ask_but_the_web_resolve_clears_it(
    authenticated_async_context, fixture_user_id
):
    from httpx import ASGITransport

    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_app(client, 1)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        await _ask(fixture_user_id, app_id, "captcha")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as agent:
            blocked = {"reason": "site_error", "step": "submit", "page_host": "jobs.northwind.example"}
            r = await agent.post(f"/api/applications/{app_id}/events", json={"event_type": "blocked", "payload": blocked})
            assert r.status_code == 201, r.text
            r = await agent.post(
                f"/api/applications/{app_id}/events",
                json={"event_type": "unblocked", "payload": {"resolution": "retried"}},
            )
            assert r.status_code == 201, r.text
        assert await _check(client, app_id) == ("ask", "blocker_open")
        # a block is open again so the user's Mark resolved has something to clear
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "blocked", "payload": {"reason": "site_error", "step": "x", "page_host": "a.example"}},
        )
        assert r.status_code == 201, r.text
        assert (await client.post(f"/api/applications/{app_id}/blocked/resolve")).status_code == 201
        assert await _check(client, app_id) == ("ask", BASE)
