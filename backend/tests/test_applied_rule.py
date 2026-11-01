"""Owner rule 2026-10-31: no application is recorded `applied` while an item is open (open block, open
CAPTCHA/question ask, unapproved CV, guessed answer). An assistant is REFUSED (409 ``submit_rule_open``,
naming the codes); the user's own web click is not, and stamps ``open_at_submit`` on the applied event.
The doctor lists both. Real Postgres through the routes; helpers are copied, never imported."""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import asks
from src.services.profile.models import CVData, UserPreferences, UserProfile

FORM = "https://careers.northwind.example/apply/7"


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada", skills=["Python"]),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {resp.json()['token']}"},
    )


async def _event(c: AsyncClient, app_id: int, etype: str, payload: dict[str, Any]) -> Any:
    return await c.post(f"/api/applications/{app_id}/events", json={"event_type": etype, "payload": payload})


async def _app(client: AsyncClient, n: int, *, answers: list[dict[str, Any]] | None = None) -> int:
    ad = {"title": f"Data Engineer {n}", "company": "Northwind", "location": "Remote",
          "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. Python."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fill: dict[str, Any] = {"form_url": FORM, "fields_count": 1}
    if answers:
        fill["answers"] = answers
    for etype, payload in (("form_checked", {"questions_count": 1, "answered": 1, "missing": 0}),
                           ("form_filled", fill), ("form_swept", {"fields_checked": 1, "fixed": 0})):
        r = await _event(client, app_id, etype, payload)
        assert r.status_code == 201, r.text
    return app_id


async def _record(c: AsyncClient, app_id: int) -> Any:
    return await c.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})


async def _applied_payload(c: AsyncClient, app_id: int) -> dict[str, Any]:
    detail = (await c.get(f"/api/applications/{app_id}")).json()
    return next(e for e in detail["events"] if e["event_type"] == "applied")["payload"]


@pytest.mark.asyncio
async def _approved_cv(web: AsyncClient, app_id: int, text: str = "Ada, Python.") -> int:
    """Save a CV and have the USER approve it through the web route (owner rule: an assistant needs a CV)."""
    r = await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert r.status_code == 201, r.text
    cv_id = int(r.json()["artifact_id"])
    ok = await _event(web, app_id, "submit_approved", {
        "artifact_id": cv_id, "sha256": hashlib.sha256(text.encode()).hexdigest(), "where": "web"})
    assert ok.status_code == 201, ok.text
    return cv_id


@pytest.mark.parametrize("kind,code", [("captcha", "blocker_open"), ("question", "question_open")])
async def test_agent_refused_with_an_open_ask(authenticated_async_context, fixture_user_id, kind, code):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 1)
        qs = [{"key": "k", "question": "Referred?", "answer": "No", "source": "guess"}] if kind == "question" else None
        await asks.create_ask(await _db(), fixture_user_id, app_id, "Needs you", "", "claude-code", kind=kind, questions=qs)
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
        assert r.status_code == 409 and "submit_rule_open" in r.text and code in r.text, r.text
        assert (await web.get(f"/api/applications/{app_id}")).json()["status"] == "considering"


@pytest.mark.asyncio
async def test_agent_refused_with_an_open_block_and_via_record_event(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 2)
        r = await _event(web, app_id, "blocked", {"reason": "captcha", "step": "upload CV", "page_host": "x.example"})
        assert r.status_code == 201, r.text
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
            assert r.status_code == 409 and "blocked" in r.text, r.text
            r = await _event(agent, app_id, "applied", {})
            assert r.status_code == 409 and "submit_rule_open" in r.text, r.text


@pytest.mark.asyncio
async def test_agent_refused_for_an_unapproved_cv_then_allowed_once_approved(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 3)
        text = "Ada, Python."
        cv = await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
        assert cv.status_code == 201, cv.text
        cv_id = int(cv.json()["artifact_id"] if "artifact_id" in cv.json() else cv.json()["id"])
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
            assert r.status_code == 409 and "cv_not_approved" in r.text, r.text
            sha = hashlib.sha256(text.encode()).hexdigest()
            ok = await _event(web, app_id, "submit_approved", {"artifact_id": cv_id, "sha256": sha, "where": "web"})
            assert ok.status_code == 201, ok.text
            r = await _record(agent, app_id)
            assert r.status_code == 201, r.text
        assert "open_at_submit" not in await _applied_payload(web, app_id)


@pytest.mark.asyncio
async def test_agent_refused_for_a_guessed_answer(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 4, answers=[{"key": "referred", "question": "Were you referred?", "answer": "No",
                                              "source": "guessed"}])
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
        assert r.status_code == 409 and "answer_guessed" in r.text, r.text


@pytest.mark.asyncio
async def test_agent_allowed_when_nothing_open_and_a_pending_outreach_note_does_not_refuse(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 5)
        await _approved_cv(web, app_id)
        person = await web.post(f"/api/applications/{app_id}/contacts", json={"name": "Grace Hopper", "role": "Recruiter"})
        assert person.status_code == 201, person.text
        note = await web.post(
            f"/api/contacts/{person.json()['contact_id']}/outreach",
            json={"entry": "message", "channel": "email", "text": "Hi Grace, I applied to the role."},
        )
        assert note.status_code in (200, 201), note.text
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
        assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_web_mark_applied_with_open_items_saves_and_stamps_then_doctor_lists_it(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 6)
        await _approved_cv(web, app_id)
        await asks.create_ask(await _db(), fixture_user_id, app_id, "Needs you", "", "claude-code", kind="captcha")
        r = await _record(web, app_id)
        assert r.status_code == 201, r.text
        assert "blocker_open" in (await _applied_payload(web, app_id))["open_at_submit"]
        rule = (await web.get("/api/doctor")).json()["submit_rule"]
        assert rule["applied_with_open"] == [{"application_id": app_id, "codes": ["blocker_open"]}]
        assert rule["next_step"] == "Review applications sent with open items"


@pytest.mark.asyncio
async def test_web_applied_event_is_stamped_and_doctor_lists_a_yes_with_an_open_item(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        held = await _app(web, 7)
        await _approved_cv(web, held)
        await asks.create_ask(await _db(), fixture_user_id, held, "Needs you", "", "claude-code", kind="captcha")
        rule = (await web.get("/api/doctor")).json()["submit_rule"]
        assert {"application_id": held, "codes": ["blocker_open"]} in rule["yes_with_open"]
        assert rule["next_step"]
        r = await _event(web, held, "applied", {})
        assert r.status_code == 201, r.text
        assert (await _applied_payload(web, held))["open_at_submit"] == ["blocker_open"]


@pytest.mark.asyncio
async def test_legacy_receipt_route_refuses_a_token_and_stamps_the_session_user(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 8)
        await _approved_cv(web, app_id)
        job_id = int((await web.get(f"/api/applications/{app_id}")).json()["job_id"])
        await asks.create_ask(await _db(), fixture_user_id, app_id, "Needs you", "", "claude-code", kind="captcha")
        async with await _agent(web) as agent:
            r = await agent.post(f"/api/receipts/{job_id}", json={"channel": "company_site"})
        assert r.status_code == 409 and "blocker_open" in r.text, r.text
        r = await web.post(f"/api/receipts/{job_id}", json={"channel": "company_site"})
        assert r.status_code == 201, r.text
        assert (await _applied_payload(web, app_id))["open_at_submit"] == ["blocker_open"]


async def _cv(web: AsyncClient, app_id: int, text: str) -> int:
    r = await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert r.status_code == 201, r.text
    return int(r.json()["artifact_id"])


@pytest.mark.asyncio
async def test_a_seen_cv_counts_even_when_an_approval_is_stale_and_an_older_cv_is_not_the_one_checked(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 9)
        old = await _cv(web, app_id, "old cv")
        sha = hashlib.sha256(b"old cv").hexdigest()
        # Only the OLD cv is seen (no submit_approved, no TTL involved), then a newer one is saved.
        seen = await _event(web, app_id, "cv_seen", {"artifact_id": old, "sha256": sha, "where": "web"})
        assert seen.status_code == 201, seen.text
        new = await _cv(web, app_id, "new cv")
        async with await _agent(web) as agent:
            r = await agent.post(f"/api/applications/{app_id}/receipt", json={"cv_artifact_id": new})
            assert r.status_code == 409 and "cv_not_approved" in r.text, r.text
            r = await _record(agent, app_id)  # none named = the newest, unseen
            assert r.status_code == 409 and "cv_not_approved" in r.text, r.text
            r = await agent.post(f"/api/applications/{app_id}/receipt", json={"cv_artifact_id": old})
            assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_moving_the_status_first_does_not_bypass_the_rule(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 10)
        await asks.create_ask(await _db(), fixture_user_id, app_id, "Needs you", "", "claude-code", kind="captcha")
        async with await _agent(web) as agent:
            await _event(agent, app_id, "replied", {})
            r = await _record(agent, app_id)
        assert r.status_code == 409 and "blocker_open" in r.text, r.text


@pytest.mark.asyncio
async def test_user_submit_after_a_block_is_stamped_and_a_corrected_applied_leaves_the_doctor(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 11)
        r = await _event(web, app_id, "blocked", {"reason": "captcha", "step": "x", "page_host": "x.example"})
        assert r.status_code == 201, r.text
        done = await web.post(f"/api/applications/{app_id}/blocked/submitted", json={})
        assert done.status_code in (200, 201), done.text
        assert "blocked" in (await _applied_payload(web, app_id))["open_at_submit"]
        rule = (await web.get("/api/doctor")).json()["submit_rule"]
        assert [i["application_id"] for i in rule["applied_with_open"]] == [app_id]
        detail = (await web.get(f"/api/applications/{app_id}")).json()
        applied = next(e for e in detail["events"] if e["event_type"] == "applied")
        fix = await web.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "note", "detail": "recorded by mistake", "corrects_event_id": applied["id"]},
        )
        assert fix.status_code == 201, fix.text
        assert (await web.get("/api/doctor")).json()["submit_rule"]["applied_with_open"] == []


@pytest.mark.asyncio
async def test_the_cv_checked_is_the_uploaded_one_not_the_newest(authenticated_async_context, fixture_user_id):
    from src.services.applications import uploads

    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 12)
        old = await _cv(web, app_id, "uploaded cv")
        seen = await _event(web, app_id, "cv_seen", {
            "artifact_id": old, "sha256": hashlib.sha256(b"uploaded cv").hexdigest(), "where": "web"})
        assert seen.status_code == 201, seen.text
        await _cv(web, app_id, "newer cv, never seen")
        assert await uploads.record(
            await _db(), user_id=fixture_user_id, application_id=app_id, kind="cv", artifact={"id": old},
            data=b"uploaded cv", fmt="pdf", renderer_version="t", host="careers.northwind.example",
            recorded_by="claude-code",
        )
        async with await _agent(web) as agent:
            # The receipt freezes the CV the browser uploaded (seen), not the newest (unseen): allowed.
            r = await _record(agent, app_id)
            assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_no_cv_at_all_assistant_refused_but_the_web_users_mark_applied_saves_and_stamps(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _app(web, 13)  # no CV artifact at all
        async with await _agent(web) as agent:
            r = await _record(agent, app_id)
        assert r.status_code == 409 and "cv_not_approved" in r.text, r.text
        r = await _record(web, app_id)
        assert r.status_code == 201, r.text
        assert "cv_not_approved" in (await _applied_payload(web, app_id))["open_at_submit"]
