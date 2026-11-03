"""Owner 2026-11-02 (production application 90): the Send press at 16:03 started nothing and the yes lapsed
after 2 hours because the job was never on the queue the apply runs read. Now Send queues the job (the web
page already starts the run itself after Send), the yes lasts
7 days (``SUBMIT_APPROVAL_TTL_MINUTES``; the fingerprint still voids it at once when the CV changes) and a
job the owner pressed Send on is FIRST in ``list_applications(queued=true)``. Helpers are copied."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}


def _form(n: int) -> str:
    return f"https://careers.northwind.example/apply/{n}"


def _ad(n: int) -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt.", "queue": True,
    }


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _sql(statement: str, params: tuple = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


async def _filled_app(client: AsyncClient, n: int) -> tuple[int, int]:
    """A queued job with a fit, a CV and a filled + swept form, no yes yet."""
    resp = await client.post("/api/jobs/bring", json=_ad(n))
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
    assert (await client.put(f"/api/applications/{app_id}/fit", json=fit)).status_code == 200
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert resp.status_code == 201, resp.text
    answers = [NOTICE]
    fill = await _event(client, app_id, "form_filled", {"form_url": _form(n), "fields_count": 1, "answers": answers})
    await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "answers": answers})
    return app_id, fill


async def _send(client: AsyncClient, app_id: int, fill: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, app_id: int, n: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": _form(n)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _backdate(app_id: int, minutes: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    _sql("UPDATE application_events SET recorded_at = ? WHERE application_id = ? AND event_type = 'submit_approved'",
         (old, app_id))


def test_the_yes_default_is_seven_days():
    assert settings.SUBMIT_APPROVAL_TTL_MINUTES == 7 * 24 * 60


@pytest.mark.asyncio
async def test_a_yes_from_three_hours_ago_still_holds_and_one_over_seven_days_is_stale(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, fill = await _filled_app(client, 2)
        await _send(client, app_id, fill)
        _backdate(app_id, 3 * 60)
        assert (await _check(client, app_id, 2))["reason"] == "user_approved", "3 hours old: the old 2 h clock is gone"
        _backdate(app_id, settings.SUBMIT_APPROVAL_TTL_MINUTES + 5)
        verdict = await _check(client, app_id, 2)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "approval_stale"), verdict
        assert "(expired:" in verdict["detail"] and "older than 7 days" in verdict["detail"], verdict


@pytest.mark.asyncio
async def test_a_changed_cv_voids_the_yes_at_once(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, fill = await _filled_app(client, 3)
        await _send(client, app_id, fill)
        assert (await _check(client, app_id, 3))["reason"] == "user_approved"
        resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT + "\nNew."})
        assert resp.status_code == 201, resp.text
        verdict = await _check(client, app_id, 3)
        assert verdict["decision"] != "submit" and verdict["reason"] != "user_approved", verdict


@pytest.mark.asyncio
async def test_a_job_the_owner_pressed_send_on_is_first_in_queue_order(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first, _ = await _filled_app(client, 4)
        second, _ = await _filled_app(client, 5)
        third, fill = await _filled_app(client, 6)
        resp = await client.get("/api/applications", params={"queued": True})
        assert [r["id"] for r in resp.json()["applications"]] == [first, second, third], "oldest first before Send"
        await _send(client, third, fill)
        resp = await client.get("/api/applications", params={"queued": True})
        assert [r["id"] for r in resp.json()["applications"]] == [third, first, second], "the Sent job leads"


@pytest.mark.asyncio
async def test_send_puts_a_job_that_was_never_queued_on_the_queue(authenticated_async_context, fixture_user_id):
    """Production application 90: the job had no `queued` event, so the apply runs (which read only the
    queue) never saw it after the owner's Send. Send now puts it on the queue, first."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        other, _ = await _filled_app(client, 7)
        app_id, fill = await _filled_app(client, 8)
        await _event(client, app_id, "unqueued", {})
        resp = await client.get("/api/applications", params={"queued": True})
        assert [r["id"] for r in resp.json()["applications"]] == [other], "off the queue before Send"
        await _send(client, app_id, fill)
        resp = await client.get("/api/applications", params={"queued": True})
        assert [r["id"] for r in resp.json()["applications"]] == [app_id, other], "Send queues it, first"


def test_the_unbound_chat_yes_default_is_two_hours():
    assert settings.SUBMIT_APPROVAL_TTL_UNBOUND_MINUTES == 120


@pytest.mark.asyncio
async def test_a_chat_yes_three_hours_old_does_not_cover_a_new_fill(authenticated_async_context, fixture_user_id):
    """A chat ``submit_approved`` carries no fingerprint, so it is bound to the CV and the clock only: with the
    7-day web clock it would have covered a changed fill for days (verifier, PR #1466). It lapses after 2 hours."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, fill = await _filled_app(client, 7)
        await _send(client, app_id, fill)
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        web = [e for e in detail["events"] if e["event_type"] == "submit_approved"][-1]["payload"]
        chat_yes = {"artifact_id": web["artifact_id"], "sha256": web["sha256"], "where": "chat"}
        resp = await client.post("/api/tokens", json={"name": "claude-code"})
        assert resp.status_code == 201, resp.text
        from httpx import ASGITransport

        from src.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {resp.json()['token']}"},
        ) as agent:
            await _event(agent, app_id, "submit_approved", chat_yes)
            assert (await _check(agent, app_id, 7))["reason"] == "user_approved", "fresh chat yes holds"
            new = [{**NOTICE, "answer": "3 months"}]
            await _event(agent, app_id, "form_filled", {"form_url": _form(7), "fields_count": 1, "answers": new})
            await _event(agent, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "answers": new})
            _backdate(app_id, 3 * 60)
            verdict = await _check(agent, app_id, 7)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "approval_stale"), verdict
        assert "older than 2 hours" in verdict["detail"], verdict


@pytest.mark.asyncio
async def test_a_web_yes_three_days_old_and_unchanged_still_holds(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, fill = await _filled_app(client, 8)
        await _send(client, app_id, fill)
        _backdate(app_id, 3 * 24 * 60)
        assert (await _check(client, app_id, 8))["reason"] == "user_approved"
