"""A LinkedIn connection note sends on its own under ``outreach_send`` auto (owner 2026-11-13, F14).

Before: ``check_outreach`` always asked for the user's click on a note (needs_owner_click) unless the draft was an
after_connect request or the request was accepted. Now, with outreach_send auto, a note to someone NOT connected
that passes every other check returns send / auto_note, still through the gate (never a bypass): the note limit,
the day and week caps, the per-company cap, the claim, the fact and sensitive-topic checks, a strong
outreach_reviewed, no test application. linkedin_state unknown stays a stop in every mode (safety: a connected
person must never get a connection request). Real Postgres, real HTTP doors; helpers are copied, never imported."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.profile import assistant_settings as rules
from src.services.profile.models import VALID_OUTREACH_SEND_VALUES, AssistantSettings
from tests._outreach_review import seed_strong_review

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
SEND = "assistant_settings.outreach_send"
INBOX = "preferences.daily_check"
NOTE = "Hi Priya, I am a Python data engineer and the Northwind pipelines role fits me."
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


@pytest.fixture(autouse=True)
def _profile(request):
    """A saved profile, so the fact check has something to read (HTTP tests only)."""
    if "authenticated_async_context" not in request.fixturenames:
        return
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer at Acme Data.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        request.getfixturevalue("fixture_user_id"),
        source_action="cv_upload",
    )


def _gate(send: str = "auto", counts: dict[str, int] | None = None, **kw: Any) -> rules.OutreachDecision:
    base: dict[str, Any] = {
        "message_found": True, "latest_draft": True, "channel": "linkedin", "has_linkedin_url": True,
        "linkedin_state": "not_connected", "text_chars": 120, "claimed_by_caller": True,
    }
    cfg = rules.effective(AssistantSettings(outreach_send=send))
    facts = rules.OutreachFacts(**{**base, **kw})
    return rules.may_send_outreach(cfg, facts, rules.OutreachCounts(sent_today=0, **(counts or {})), NOW)


# ── the pure gate ────────────────────────────────────────────────────────────


def test_a_note_to_someone_not_connected_sends_under_auto():
    got = _gate()
    assert (got.decision, got.reason) == ("send", "auto_note")
    assert "record outreach_sent" in got.detail and "Add a note" in got.detail
    assert "auto_note" in rules.LINKEDIN_AUTO_SEND_REASONS


def test_every_other_outreach_send_value_still_waits_for_the_click():
    others = (VALID_OUTREACH_SEND_VALUES - {"auto"}) | {"", "off", "ask", "AUTO"}
    assert "draft_only" in others
    for value in sorted(others):
        got = _gate(value)
        assert (got.decision, got.reason) == ("ask", "needs_owner_click"), value
    assert _gate("draft_only", owner_approved=True).reason == "owner_clicked"
    assert _gate("auto", owner_approved=True).reason == "owner_clicked"  # the user's own yes still wins first


@pytest.mark.parametrize(
    ("kw", "counts", "reason"),
    [
        ({"text_chars": 201}, None, "note_too_long"),
        ({"linkedin_sent_today": settings.LINKEDIN_SENDS_PER_DAY_DEFAULT}, None, "linkedin_daily_cap_reached"),
        ({"invites_this_week": settings.LINKEDIN_INVITES_PER_WEEK_MAX}, None, "weekly_invite_cap_reached"),
        ({"invite_limit_until": NOW + timedelta(days=1)}, None, "invite_limit_reached"),
        ({"invited": True}, None, "already_invited"),
        ({"linkedin_state": "pending"}, None, "linkedin_pending"),
        ({"linkedin_state": "unknown"}, None, "linkedin_state_unknown"),
        ({"linkedin_state": None}, None, "linkedin_state_unknown"),
        ({"claimed_by_caller": False}, None, "needs_claim"),
        ({"claimed_by_other": True}, None, "claimed_by_other_session"),
        ({"last_linkedin_sent_at": NOW - timedelta(seconds=30)}, None, "too_soon"),
        ({"replied": True}, None, "replied"),
        ({"application_closed": True}, None, "application_closed"),
        ({"linked": True, "company": "northwind"}, {"company_sent_in_window": 5}, "company_cap_reached"),
        ({}, {"network_sent_last_hour": 5}, "network_hourly_cap_reached"),
    ],
)
def test_every_stop_still_stops_an_auto_note(kw, counts, reason):
    got = _gate(counts=counts, **kw)
    assert (got.decision, got.reason) == ("stop", reason)


def test_the_fact_and_sensitive_checks_run_before_a_note_goes_alone():
    claims, sensitive = _gate(unsupported_claims=True), _gate(sensitive=True)
    assert (claims.decision, claims.reason) == ("ask", "unsupported_claims")
    assert (sensitive.decision, sensitive.reason) == ("ask", "sensitive_topic")


def test_a_note_to_a_connected_person_is_not_a_request_and_still_waits():
    got = _gate(linkedin_state="connected")
    assert (got.decision, got.reason) == ("ask", "needs_owner_click")


def test_a_second_note_never_goes_to_someone_who_already_got_one():
    """already_sent + a follow-up that is due, state still not_connected: that is a second request."""
    got = _gate(already_sent=True, follow_up=True, linkedin_sent_to_person=1, days_since_sent=9)
    assert got.reason != "auto_note" and got.decision != "send"


# ── the route (HTTP) ─────────────────────────────────────────────────────────


async def _profile_edit(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _auto(client: AsyncClient) -> None:
    await _profile_edit(client, INBOX, "auto")
    await _profile_edit(client, SEND, "auto")


async def _person(client: AsyncClient, n: int = 0, state: str = "not_connected", test: bool = False) -> tuple[int, int]:
    resp = await client.post(
        "/api/jobs/bring",
        json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}", "test": test},
    )
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    cid = int(added.json()["contact"]["id"])
    if state != "unknown":
        patched = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": state})
        assert patched.status_code == 200, patched.text
    return app_id, cid


async def _note(client: AsyncClient, app_id: int, cid: int, text: str = NOTE) -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": text},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _take(client: AsyncClient, cid: int, session: str = "desk") -> dict[str, Any]:
    resp = await client.post("/api/own-browser/take", json={"session": session, "contact_id": cid})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _check(client: AsyncClient, cid: int, mid: int, session: str = "desk") -> tuple[str, str]:
    resp = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid, "session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()["decision"], resp.json()["reason"]


async def _tasks(client: AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get("/api/own-browser/queue")
    assert resp.status_code == 200, resp.text
    return resp.json()["tasks"]


def _sql(sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_an_auto_note_is_queued_claimed_reviewed_and_sent_through_the_gate(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        mid = await _note(client, app_id, cid)
        assert await _tasks(client) == []  # draft_only: it waits for the Send
        assert await _check(client, cid, mid) == ("ask", "needs_owner_click")
        await _auto(client)
        [task] = await _tasks(client)
        assert (task["contact_id"], task["owner_yes"], task["no_note"]) == (cid, False, False)
        assert "auto" in task["why_own_browser"].lower() and "pressed Send" not in task["why_own_browser"]
        took = await _take(client, cid)
        assert took["status"] == "task", took
        assert "Add a note" in took["next"] and "record_event outreach_sent" in took["next"]
        # No strong review of this exact draft: the text would go with nobody pressing Send.
        assert await _check(client, cid, mid) == ("ask", "outreach_not_reviewed")
        seed_strong_review(cid)
        assert await _check(client, cid, mid) == ("send", "auto_note")


@pytest.mark.asyncio
async def test_an_unknown_state_and_a_connected_person_stop_or_wait_under_auto(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _auto(client)
        app_id, cid = await _person(client, 0, "unknown")
        mid = await _note(client, app_id, cid)
        assert await _check(client, cid, mid) == ("stop", "linkedin_state_unknown")
        assert (await _take(client, cid))["status"] == "empty"
        app2, other = await _person(client, 1, "connected")
        m2 = await _note(client, app2, other, "Hi Priya, thanks for connecting. " * 3)
        assert await _check(client, other, m2) == ("ask", "needs_owner_click")


@pytest.mark.asyncio
async def test_a_declined_person_and_a_capped_day_get_no_note_under_auto(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        await _auto(client)
        app_id, cid = await _person(client)
        mid = await _note(client, app_id, cid)
        resp = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "decline"})
        assert resp.status_code == 200, resp.text
        assert await _tasks(client) == []
        assert (await _take(client, cid))["status"] == "empty"
        seed_strong_review(cid)
        assert (await _check(client, cid, mid))[0] != "send"
        # A capped day: the queue may hand the person out, the gate still stops the send.
        app2, second = await _person(client, 1)
        m2 = await _note(client, app2, second)
        seed_strong_review(second)
        assert (await _take(client, second, "laptop"))["status"] == "task"
        monkeypatch.setattr(rules, "linkedin_daily_max", lambda cfg: 0)
        assert await _check(client, second, m2, "laptop") == ("stop", "linkedin_daily_cap_reached")


@pytest.mark.asyncio
async def test_a_note_on_a_test_application_never_goes_alone(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _auto(client)
        app_id, cid = await _person(client, 0, "not_connected", test=True)
        mid = await _note(client, app_id, cid)
        seed_strong_review(cid)
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, mid) == ("stop", "test_application")


@pytest.mark.asyncio
async def test_an_auto_note_counts_as_a_request_in_the_weekly_cap(authenticated_async_context, monkeypatch):
    from src.api import dependencies as api_deps
    from src.services.applications import own_browser

    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        await _auto(client)
        a1, first = await _person(client, 0)
        mid = await _note(client, a1, first)
        seed_strong_review(first)
        assert (await _take(client, first))["status"] == "task"
        assert await _check(client, first, mid) == ("send", "auto_note")
        sent = await client.post(
            f"/api/contacts/{first}/outreach", json={"entry": "sent", "channel": "linkedin", "text": ""}
        )
        assert sent.status_code == 201, sent.text
        db = await api_deps.get_db()
        since = datetime.now(timezone.utc) - timedelta(days=7)
        assert await own_browser.invites_since(db, uid, since) == 1
        # A message to a connected person is not a request.
        a2, second = await _person(client, 1, "connected")
        await _note(client, a2, second)
        sent2 = await client.post(
            f"/api/contacts/{second}/outreach", json={"entry": "sent", "channel": "linkedin", "text": ""}
        )
        assert sent2.status_code == 201, sent2.text
        assert await own_browser.invites_since(db, uid, since) == 1
        # And the weekly cap now stops the next auto note.
        monkeypatch.setattr(settings, "LINKEDIN_INVITES_PER_WEEK_MAX", 1)
        a3, third = await _person(client, 2)
        m3 = await _note(client, a3, third)
        seed_strong_review(third)
        _sql(
            "UPDATE contact_outreach SET recorded_at = ? WHERE entry IN ('sent', 'send_released', 'send_claimed')",
            ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),),
        )
        assert (await _take(client, third, "laptop"))["status"] == "empty"
        assert await _check(client, third, m3, "laptop") == ("stop", "weekly_invite_cap_reached")


# ── Needs you ────────────────────────────────────────────────────────────────


async def _note_blockers(uid: str, app_id: int) -> list[dict[str, Any]]:
    from src.api import dependencies as api_deps
    from src.services.applications import blockers

    db = await api_deps.get_db()
    got = await blockers.blockers_for(db, uid, [app_id])
    return [b for b in got[app_id] if b["kind"] == "outreach_send"]


@pytest.fixture
def _line(monkeypatch):
    from src.services.applications import spine

    async def fifty(_user_id: str) -> int:
        return 50

    monkeypatch.setattr(spine, "user_score_line", fifty)


@pytest.mark.asyncio
async def test_needs_you_shows_an_auto_note_as_the_pcs_and_keeps_the_rest_honest(authenticated_async_context, _line):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id, cid = await _person(client, 0)
        await _note(client, app_id, cid)
        _sql("UPDATE applications SET status = 'applied', fit_score = 90, fit_recorded_at = ? WHERE id = ?",
             (datetime.now(timezone.utc).isoformat(), app_id))
        assert [b["actor"] for b in await _note_blockers(uid, app_id)] == ["you"]  # draft_only: the user sends
        await _auto(client)
        assert [b["actor"] for b in await _note_blockers(uid, app_id)] == ["your_pc"]
        # Connected: a cold message under auto still needs the user's click (nothing goes alone).
        await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "connected"})
        assert [b["actor"] for b in await _note_blockers(uid, app_id)] == ["you"]
        # Unknown state: the assistant reads it first, not the user.
        await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "unknown"})
        got = await _note_blockers(uid, app_id)
        assert [(b["actor"], b["ref"].get("reason")) for b in got] == [("assistant", "linkedin_state_unknown")]
