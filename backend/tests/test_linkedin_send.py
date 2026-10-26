"""check_outreach can say ``send`` for LinkedIn (PR C, owner decisions 2026-10-25): only after the user's per-card
click on THIS draft, and never because ``outreach_send`` is ``auto``. The pure gate first (every reason alone,
the order, first match wins), then the real doors: route, MCP tool, click, claim, pace. Helpers are copied from
sibling test modules, never imported (schema isolation)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.applications import own_browser
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SEND = "assistant_settings.outreach_send"
INBOX = "preferences.daily_check"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


# ── the pure gate ────────────────────────────────────────────────────────────


def _cfg(**kw: Any) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(**kw))


def _facts(**kw: Any) -> rules.OutreachFacts:
    """A LinkedIn draft that passes every rule: connected, short, clicked, nothing else running."""
    base: dict[str, Any] = {
        "message_found": True, "latest_draft": True, "channel": "linkedin", "has_linkedin_url": True,
        "linkedin_state": "connected", "text_chars": 120, "owner_approved": True, "claimed_by_caller": True,
    }
    base.update(kw)
    return rules.OutreachFacts(**base)


def _gate(cfg: rules.EffectiveSettings | None = None, **facts: Any) -> rules.OutreachDecision:
    return rules.may_send_outreach(
        cfg or _cfg(), _facts(**facts), rules.OutreachCounts(sent_today=0), NOW
    )


_RECENT = NOW - timedelta(seconds=30)
_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("stop", "paused", {"cfg": {"paused_until": "until_resumed"}}),
    ("stop", "message_not_found", {"message_found": False}),
    ("stop", "not_latest_draft", {"latest_draft": False}),
    ("stop", "no_linkedin_url", {"has_linkedin_url": False}),
    ("stop", "already_sent", {"already_sent": True}),
    ("stop", "replied", {"replied": True}),
    ("stop", "application_closed", {"application_closed": True}),
    ("stop", "linkedin_state_unknown", {"linkedin_state": None}),
    ("stop", "linkedin_pending", {"linkedin_state": "pending"}),
    ("stop", "note_too_long", {"linkedin_state": "not_connected", "text_chars": 201}),
    ("stop", "message_too_long", {"linkedin_state": "connected", "text_chars": 401}),
    ("stop", "invite_limit_reached", {"linkedin_state": "not_connected", "invite_limit_until": NOW + timedelta(days=1)}),
    ("stop", "linkedin_daily_cap_reached", {"linkedin_sent_today": own_browser.LINKEDIN_DAILY_MAX}),
    ("stop", "too_soon", {"last_linkedin_sent_at": _RECENT}),
    ("stop", "claimed_by_other_session", {"claimed_by_other": True}),
    ("ask", "needs_owner_click", {"owner_approved": False}),
    ("stop", "needs_claim", {"claimed_by_caller": False}),
    ("send", "owner_clicked", {}),
]


def _run(trips: list[dict[str, Any]]) -> rules.OutreachDecision:
    """Merge the trips; where two trips set the same fact the EARLIER one wins (an earlier rule is the one
    under test). A pause is the only config trip."""
    merged: dict[str, Any] = {}
    cfg: dict[str, Any] = {}
    for trip in trips:
        for key, value in trip.items():
            if key == "cfg":
                cfg.update(value)
            else:
                merged.setdefault(key, value)
    return _gate(_cfg(**cfg), **merged)


def test_the_linkedin_reason_table_is_the_gates_own_order():
    assert [r for _d, r, _t in _CASES] == list(rules.LINKEDIN_OUTREACH_REASONS)


@pytest.mark.parametrize(("decision", "reason", "trip"), _CASES, ids=[c[1] for c in _CASES])
def test_each_linkedin_reason_alone(decision, reason, trip):
    verdict = _run([trip])
    assert (verdict.decision, verdict.reason) == (decision, reason)
    assert verdict.detail and "\n" not in verdict.detail


@pytest.mark.parametrize("index", range(len(_CASES) - 1), ids=[c[1] for c in _CASES[:-1]])
def test_an_earlier_linkedin_reason_beats_every_later_one(index):
    decision, reason, _ = _CASES[index]
    verdict = _run([trip for _d, _r, trip in _CASES[index:]])
    assert (verdict.decision, verdict.reason) == (decision, reason)


def test_the_answers_the_contract_words_exactly_and_every_linkedin_answer_says_what_its_counts_are():
    note = " (sent_today and daily_max count email only.)"
    assert _gate(owner_approved=False).detail == (
        "The user has not pressed Send on this person's card for this draft. Do not send; it waits on Needs you."
        + note
    )
    assert _gate(claimed_by_caller=False).detail == (
        "Take this person first: own_browser_queue take, then check_outreach with your session." + note
    )
    assert _gate().detail == (
        "The user pressed Send for this draft: paste it on LinkedIn exactly, send, then record outreach_sent."
        + note
    )
    assert all(_run([t]).detail.endswith(note) for _d, _r, t in _CASES[3:])
    other = rules.may_send_outreach(_cfg(), _facts(channel="other"), rules.OutreachCounts(sent_today=0), NOW)
    assert (other.decision, other.reason) == ("stop", "not_email_channel")
    assert other.detail == (
        "Only email, or LinkedIn after the user's click on that person's card, can be sent by you. "
        "The user sends other-channel messages."
    )


@pytest.mark.parametrize("value", ["", "draft_only", "auto"])
def test_outreach_send_never_changes_a_linkedin_answer(value):
    """``auto`` must not send a LinkedIn draft without the click, and ``draft_only`` must not stop a clicked one."""
    cfg = _cfg(outreach_send=value)
    assert _gate(cfg, owner_approved=False).reason == "needs_owner_click"
    assert _gate(cfg, owner_approved=False).decision == "ask"
    assert _gate(cfg, claimed_by_caller=False).reason == "needs_claim"
    assert (_gate(cfg).decision, _gate(cfg).reason) == ("send", "owner_clicked")


def test_note_and_message_limits_and_the_invite_limit_hit_only_their_own_kind():
    assert _gate(linkedin_state="not_connected", text_chars=200).reason == "owner_clicked"
    assert _gate(linkedin_state="not_connected", text_chars=201, premium=True).reason == "owner_clicked"
    assert _gate(linkedin_state="not_connected", text_chars=301, premium=True).reason == "note_too_long"
    assert _gate(text_chars=400).reason == "owner_clicked"  # connected: a message, 400 allowed
    assert _gate(text_chars=300, linkedin_state="not_connected").reason == "note_too_long"
    limit = NOW + timedelta(hours=5)
    assert _gate(invite_limit_until=limit).reason == "owner_clicked"  # a message to a connection is not an invite
    assert _gate(linkedin_state="not_connected", invite_limit_until=NOW - timedelta(seconds=1)).reason == "owner_clicked"


def test_the_gap_is_the_take_gap_and_the_day_is_the_take_day(monkeypatch):
    assert _gate(last_linkedin_sent_at=NOW - timedelta(seconds=own_browser.LINKEDIN_MIN_GAP_SECONDS)).reason == "owner_clicked"
    assert _gate(last_linkedin_sent_at=NOW - timedelta(seconds=119)).reason == "too_soon"
    assert _gate(linkedin_sent_today=own_browser.LINKEDIN_DAILY_MAX - 1).reason == "owner_clicked"
    monkeypatch.setattr(own_browser, "LINKEDIN_DAILY_MAX", 3)
    assert _gate(linkedin_sent_today=3).reason == "linkedin_daily_cap_reached"


# ── helpers for the real doors ───────────────────────────────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _profile_edit(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _person(
    client: AsyncClient, n: int = 0, *, state: str | None = "connected", text: str = "Hi, a short note",
    url: bool = True,
) -> tuple[int, int, int]:
    """(app_id, contact_id, message_id) for one LinkedIn person with one drafted message."""
    resp = await client.post(
        "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
    )
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    body: dict[str, Any] = {"name": f"Priya {n}", "role": "Recruiter"}
    if url:
        body["linkedin_url"] = f"https://www.linkedin.com/in/priya{n}"
    added = await client.post(f"/api/applications/{app_id}/contacts", json=body)
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    if state:
        patched = await client.patch(f"/api/contacts/{contact_id}", json={"linkedin_state": state})
        assert patched.status_code == 200, patched.text
    return app_id, contact_id, await _draft(client, app_id, contact_id, text)


async def _draft(client: AsyncClient, app_id: int, contact_id: int, text: str, channel: str = "linkedin") -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "text": text, "contact_id": contact_id, "channel": channel},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _click(client: AsyncClient, contact_id: int, message_id: int) -> None:
    resp = await client.post(
        f"/api/contacts/{contact_id}/own-browser-send", json={"message_id": message_id, "decision": "approve"}
    )
    assert resp.status_code == 200, resp.text


async def _take(c: AsyncClient, contact_id: int | None, session: str = "desk") -> dict[str, Any]:
    body: dict[str, Any] = {"session": session}
    if contact_id is not None:
        body["contact_id"] = contact_id
    resp = await c.post("/api/own-browser/take", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(c: AsyncClient, contact_id: int, reason: str = "other", session: str = "desk") -> None:
    resp = await c.post(
        "/api/own-browser/release", json={"session": session, "task_id": f"contact:{contact_id}", "reason": reason}
    )
    assert resp.status_code == 200, resp.text


async def _check(
    client: AsyncClient, contact_id: int, message_id: int, session: str | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"message_id": message_id}
    if session is not None:
        params["session"] = session
    resp = await client.get(f"/api/contacts/{contact_id}/outreach-check", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _verdict(got: dict[str, Any]) -> tuple[str, str]:
    return got["decision"], got["reason"]


async def _held_check(c: AsyncClient, contact_id: int, message_id: int, session: str = "desk") -> tuple[str, str]:
    """What a session that takes the person first hears from the gate, then gives the person back."""
    took = await _take(c, contact_id, session)
    assert took["status"] in ("task", "already_yours"), took
    try:
        return _verdict(await _check(c, contact_id, message_id, session))
    finally:
        await _release(c, contact_id, "other", session)


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": f"agent{id(object())}"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


def _sql(sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


async def _sent(client: AsyncClient, contact_id: int) -> None:
    resp = await client.post(f"/api/contacts/{contact_id}/outreach", json={"entry": "sent", "channel": "linkedin"})
    assert resp.status_code == 201, resp.text


def _age_sends(minutes: int = 5) -> None:
    _sql(
        "UPDATE contact_outreach SET recorded_at = ? WHERE entry = 'sent'",
        ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),),
    )


async def _send_browser(client: AsyncClient, contact_id: int) -> str:
    person = (await client.get(f"/api/people?contact_id={contact_id}")).json()["person"]
    return str(person["send_browser"]["kind"])


# ── the click, the claim, the route and the MCP tool ─────────────────────────


@pytest.mark.asyncio
async def test_the_click_and_a_claim_turn_ask_into_send_on_the_route_and_the_mcp_tool(
    authenticated_async_context, fixture_user_id
):
    from src.api import mcp_server

    check_tool = mcp_server.build_server()._tool_manager.get_tool("check_outreach")
    queue_tool = mcp_server.build_server()._tool_manager.get_tool("own_browser_queue")
    assert check_tool is not None and queue_tool is not None
    # a session user: the same actor as the web client, so the claim below is the caller's on both doors
    user = mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True)
    async with authenticated_async_context() as client:
        _app, cid, mid = await _person(client)
        before = await _check(client, cid, mid, "desk")
        await _click(client, cid, mid)
        clicked = await _check(client, cid, mid, "desk")
        token = mcp_server._current_user.set(user)
        try:
            taken = await queue_tool.fn(action="take", session="desk", contact_id=cid)
            via_tool = await check_tool.fn(contact_id=cid, message_id=mid, session="desk")
            tool_other = await check_tool.fn(contact_id=cid, message_id=mid, session="laptop")
        finally:
            mcp_server._current_user.reset(token)
        via_route = await _check(client, cid, mid, "desk")
    assert _verdict(before) == ("ask", "needs_owner_click")
    assert _verdict(clicked) == ("stop", "needs_claim")
    assert clicked["detail"].startswith("Take this person first: own_browser_queue take, then check_outreach with your session.")
    assert taken["status"] == "task" and taken["task"]["contact_id"] == cid
    assert _verdict(via_tool) == ("send", "owner_clicked") == _verdict(via_route)
    assert _verdict(tool_other) == ("stop", "claimed_by_other_session")
    assert set(via_route) == {"decision", "reason", "detail", "sent_today", "daily_max"}
    assert via_route["detail"].startswith("The user pressed Send for this draft")
    assert via_route["detail"].endswith("(sent_today and daily_max count email only.)")


@pytest.mark.asyncio
async def test_a_declined_click_never_sends(authenticated_async_context):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _person(client)
        await _click(client, cid, mid)
        resp = await client.post(
            f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "decline"}
        )
        assert resp.status_code == 200, resp.text
        assert _verdict(await _check(client, cid, mid, "desk")) == ("ask", "needs_owner_click")
        assert (await _take(client, cid))["status"] == "empty"  # nothing to claim without a click


@pytest.mark.asyncio
async def test_a_rewritten_draft_needs_a_new_click(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid, old = await _person(client)
        await _click(client, cid, old)
        assert await _held_check(client, cid, old) == ("send", "owner_clicked")
        new = await _draft(client, app_id, cid, "A different, rewritten note")
        assert _verdict(await _check(client, cid, old, "desk")) == ("stop", "not_latest_draft")
        assert _verdict(await _check(client, cid, new, "desk")) == ("ask", "needs_owner_click")
        await _click(client, cid, new)
        assert await _held_check(client, cid, new) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_auto_mode_never_sends_linkedin_without_the_click(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _profile_edit(client, INBOX, "auto")
        await _profile_edit(client, SEND, "auto")
        _app, cid, mid = await _person(client)
        assert (await client.get("/api/profile")).json()["assistant_settings"]["outreach_send"]["effective"] == "auto"
        assert _verdict(await _check(client, cid, mid, "desk")) == ("ask", "needs_owner_click")
        assert (await _take(client, cid))["status"] == "empty"
        await _profile_edit(client, SEND, "draft_only")
        await _click(client, cid, mid)
        assert await _held_check(client, cid, mid) == ("send", "owner_clicked")  # the click, not the setting


@pytest.mark.asyncio
async def test_two_callers_without_a_claim_both_need_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _person(client)
        await _click(client, cid, mid)
        async with await _agent(client) as one, await _agent(client) as two:
            assert _verdict(await _check(one, cid, mid, "a")) == ("stop", "needs_claim")
            assert _verdict(await _check(two, cid, mid, "b")) == ("stop", "needs_claim")
            assert _verdict(await _check(two, cid, mid)) == ("stop", "needs_claim")


@pytest.mark.asyncio
async def test_two_sessions_racing_for_one_person_only_one_gets_send(authenticated_async_context):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _person(client)
        await _click(client, cid, mid)
        async with await _agent(client) as one, await _agent(client) as two:
            got = await asyncio.gather(_take(one, cid, "a"), _take(two, cid, "b"))
            assert sorted(g["status"] for g in got) == ["busy", "task"]
            verdicts = [_verdict(await _check(one, cid, mid, "a")), _verdict(await _check(two, cid, mid, "b"))]
    assert sorted(verdicts) == [("send", "owner_clicked"), ("stop", "claimed_by_other_session")]


@pytest.mark.asyncio
async def test_the_pace_is_still_serialised_across_two_people(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, first, m1 = await _person(client, 0)
        _b, second, m2 = await _person(client, 1)
        await _click(client, first, m1)
        await _click(client, second, m2)
        async with await _agent(client) as one, await _agent(client) as two:
            got = await asyncio.gather(_take(one, first, "a"), _take(two, second, "b"))
            assert sorted(g["status"] for g in got) == ["task", "wait"]  # one person at a time
            holder, waiting = (one, two) if got[0]["status"] == "task" else (two, one)
            assert waiting is not holder
        # the other person was never claimed, and the holder's unreleased claim holds them back like a send
        taken_id = got[0]["task"]["contact_id"] if got[0]["status"] == "task" else got[1]["task"]["contact_id"]
        other_id = second if taken_id == first else first
        other_mid = m2 if other_id == second else m1
        assert _verdict(await _check(client, other_id, other_mid, "b")) == ("stop", "too_soon")


@pytest.mark.asyncio
async def test_what_the_page_showed_must_be_saved_before_a_send(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, no_url, m1 = await _person(client, 0, url=False)
        _b, unknown, m2 = await _person(client, 1, state=None)
        _c, pending, m3 = await _person(client, 2, state="pending")
        for cid, mid in ((no_url, m1), (unknown, m2), (pending, m3)):
            await _click(client, cid, mid)
        assert _verdict(await _check(client, no_url, m1, "desk")) == ("stop", "no_linkedin_url")
        assert _verdict(await _check(client, unknown, m2, "desk")) == ("stop", "linkedin_state_unknown")
        assert _verdict(await _check(client, pending, m3, "desk")) == ("stop", "linkedin_pending")
        # saving what the page showed (update_contact) is what unblocks it
        fixed = await client.patch(f"/api/contacts/{unknown}", json={"linkedin_state": "connected"})
        assert fixed.status_code == 200, fixed.text
        assert await _held_check(client, unknown, m2) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_a_sent_or_answered_person_stops(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, cid, mid = await _person(client)
        await _click(client, cid, mid)
        await _sent(client, cid)
        assert _verdict(await _check(client, cid, mid, "desk")) == ("stop", "already_sent")


@pytest.mark.asyncio
async def test_note_length_is_200_and_a_message_is_400(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, ok200, m1 = await _person(client, 0, state="not_connected", text="n" * 200)
        _b, long201, m2 = await _person(client, 1, state="not_connected", text="n" * 201)
        _c, msg400, m3 = await _person(client, 2, text="m" * 400)
        _d, msg401, m4 = await _person(client, 3, text="m" * 401)
        for cid, mid in ((ok200, m1), (long201, m2), (msg400, m3), (msg401, m4)):
            await _click(client, cid, mid)
        assert await _held_check(client, ok200, m1) == ("send", "owner_clicked")
        assert _verdict(await _check(client, long201, m2, "desk")) == ("stop", "note_too_long")
        assert await _held_check(client, msg400, m3) == ("send", "owner_clicked")
        assert _verdict(await _check(client, msg401, m4, "desk")) == ("stop", "message_too_long")


@pytest.mark.asyncio
async def test_premium_widens_the_note_to_300_only_when_the_user_set_it_themselves(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _person(client, 0, state="not_connected", text="n" * 250)
        await _click(client, cid, mid)
        assert _verdict(await _check(client, cid, mid, "desk")) == ("stop", "note_too_long")
        # an assistant (a personal token) sets Premium: the gate still holds the free limit
        async with await _agent(client) as agent:
            await _profile_edit(agent, "preferences.linkedin_premium", True)
        assert (await client.get("/api/profile")).json()["preferences"]["linkedin_premium"] is True
        assert _verdict(await _check(client, cid, mid, "desk")) == ("stop", "note_too_long")
        # the user sets it on the web: now 300 is theirs
        await _profile_edit(client, "preferences.linkedin_premium", True)
        assert await _held_check(client, cid, mid) == ("send", "owner_clicked")
        too_long = await _draft(client, app_id, cid, "n" * 301)
        await _click(client, cid, too_long)
        assert _verdict(await _check(client, cid, too_long, "desk")) == ("stop", "note_too_long")


@pytest.mark.asyncio
async def test_the_weekly_invitation_limit_blocks_notes_and_not_messages_from_either_browser(
    authenticated_async_context
):
    async with authenticated_async_context() as client:
        _a, first, m1 = await _person(client, 0, state="not_connected")
        _b, note, m2 = await _person(client, 1, state="not_connected")
        _c, message, m3 = await _person(client, 2)
        for cid, mid in ((first, m1), (note, m2), (message, m3)):
            await _click(client, cid, mid)
        assert await _held_check(client, note, m2) == ("send", "owner_clicked")
        # Job360's browser (a session that took the person) reports the limit by releasing it
        assert (await _take(client, first))["status"] == "task"
        await _release(client, first, "weekly_limit")
        assert _verdict(await _check(client, note, m2, "desk")) == ("stop", "invite_limit_reached")
        assert await _held_check(client, message, m3) == ("send", "owner_clicked")
        # seven days on, the limit is over
        _sql(
            "UPDATE contact_outreach SET recorded_at = ? WHERE entry = 'send_released'",
            ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),),
        )
        assert await _held_check(client, note, m2) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_the_chip_flips_to_the_own_browser_after_job360s_browser_gives_the_person_back(
    authenticated_async_context
):
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _person(client)
        await _click(client, cid, mid)
        assert await _send_browser(client, cid) == "job360"
        assert (await _take(client, cid))["status"] == "task"
        assert await _send_browser(client, cid) == "job360"  # held, nothing failed yet
        await _release(client, cid, "login_wall")
        assert await _send_browser(client, cid) == "own_browser"
        queued = (await client.get("/api/own-browser/queue")).json()["tasks"]
        assert [t["contact_id"] for t in queued] == [cid] and queued[0]["browser"]["kind"] == "own_browser"
        # a reason that is not a browser failure leaves the chip alone; a new draft starts over
        assert (await _take(client, cid))["status"] == "task"
        await _release(client, cid, "weekly_limit")
        assert await _send_browser(client, cid) == "job360"
        assert (await _take(client, cid))["status"] == "task"
        await _release(client, cid, "captcha")
        assert await _send_browser(client, cid) == "own_browser"
        await _draft(client, app_id, cid, "Another version")
        assert await _send_browser(client, cid) == "job360"


# ── another session, the gap and the day ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_person_another_session_holds_stops_and_the_holder_goes(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, cid, mid = await _person(client)
        await _click(client, cid, mid)
        async with await _agent(client) as agent:
            assert (await _take(agent, None))["status"] == "task"
            # the holder's own fresh claim must not make its own check "too soon"
            assert _verdict(await _check(agent, cid, mid, "desk")) == ("send", "owner_clicked")
            assert _verdict(await _check(agent, cid, mid, "laptop")) == ("stop", "claimed_by_other_session")
            assert _verdict(await _check(agent, cid, mid)) == ("stop", "claimed_by_other_session")
            # the user's web session is a different actor even with the same label
            assert _verdict(await _check(client, cid, mid, "desk")) == ("stop", "claimed_by_other_session")
            bad = await agent.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid, "session": "no/good"})
            assert bad.status_code == 422, bad.text
            await _release(agent, cid)
        assert await _held_check(client, cid, mid, "laptop") == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_two_minutes_between_people(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, first, _m = await _person(client, 0)
        _b, second, m2 = await _person(client, 1)
        await _click(client, second, m2)
        assert (await _take(client, second))["status"] == "task"
        assert _verdict(await _check(client, second, m2, "desk")) == ("send", "owner_clicked")
        await _sent(client, first)  # someone else's message just went
        assert _verdict(await _check(client, second, m2, "desk")) == ("stop", "too_soon")
        _age_sends(own_browser.LINKEDIN_MIN_GAP_SECONDS // 60 + 1)
        assert _verdict(await _check(client, second, m2, "desk")) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_an_unreleased_claim_on_someone_else_counts_like_take_counts_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, first, m1 = await _person(client, 0)
        _b, second, m2 = await _person(client, 1)
        await _click(client, first, m1)
        await _click(client, second, m2)
        async with await _agent(client) as agent:
            taken = await _take(agent, first)
            assert taken["status"] == "task" and taken["task"]["contact_id"] == first
            # a send nobody recorded: the other person must wait, as a take would make them
            assert _verdict(await _check(agent, second, m2, "desk")) == ("stop", "too_soon")
            assert (await _take(agent, second))["status"] == "wait"


@pytest.mark.asyncio
async def test_the_eleventh_send_of_the_day_stops(authenticated_async_context):
    async with authenticated_async_context() as client:
        for n in range(own_browser.LINKEDIN_DAILY_MAX):
            _a, done, _m = await _person(client, n)
            await _sent(client, done)
        _age_sends()
        _b, last, mid = await _person(client, 50)
        await _click(client, last, mid)
        assert (await _take(client, last))["status"] == "daily_limit"
        got = await _check(client, last, mid, "desk")
        assert _verdict(got) == ("stop", "linkedin_daily_cap_reached")
        assert str(own_browser.LINKEDIN_DAILY_MAX) in got["detail"]


@pytest.mark.asyncio
async def test_the_tenth_send_of_the_day_is_allowed(authenticated_async_context):
    async with authenticated_async_context() as client:
        for n in range(own_browser.LINKEDIN_DAILY_MAX - 1):
            _a, done, _m = await _person(client, n)
            await _sent(client, done)
        _age_sends()
        _b, last, mid = await _person(client, 50)
        await _click(client, last, mid)
        assert await _held_check(client, last, mid) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_the_day_is_the_users_own_day_not_utc(authenticated_async_context, fixture_user_id):
    """Ten sends before the user's local midnight are not today's, though their UTC date is the gate's UTC today."""
    from src.api.routes import browser as browser_routes

    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)  # 02:00 on 27 Oct in Pacific/Kiritimati (UTC+14)
    async with authenticated_async_context() as client:
        for n in range(own_browser.LINKEDIN_DAILY_MAX):
            _a, done, _m = await _person(client, n)
            await _sent(client, done)
        _sql("UPDATE users SET timezone = ? WHERE id = ?", ("Pacific/Kiritimati", fixture_user_id))
        before_midnight = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc).isoformat()  # 23:30 on the 26th there
        after_midnight = datetime(2026, 10, 26, 10, 30, tzinfo=timezone.utc).isoformat()  # 00:30 on the 27th there
        _sql("UPDATE contact_outreach SET recorded_at = ? WHERE entry = 'sent'", (before_midnight,))
        async with browser_routes._db() as db:  # noqa: SLF001
            _last, yesterday_count = await own_browser.pace_facts(db, fixture_user_id, now)
            _sql("UPDATE contact_outreach SET recorded_at = ? WHERE entry = 'sent'", (after_midnight,))
            _last, today_count = await own_browser.pace_facts(db, fixture_user_id, now)
            _sql("UPDATE users SET timezone = ? WHERE id = ?", ("UTC", fixture_user_id))
            _last, utc_count = await own_browser.pace_facts(db, fixture_user_id, now)
    assert (yesterday_count, today_count) == (0, own_browser.LINKEDIN_DAILY_MAX)
    assert utc_count == own_browser.LINKEDIN_DAILY_MAX  # the same instants are all "today" on the UTC clock


async def _second_user_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    return str(lr.cookies.get("job360_session"))


@pytest.mark.asyncio
async def test_a_person_of_another_user_is_not_found_on_every_door(authenticated_async_context):
    async with authenticated_async_context() as client:
        _a, cid, mid = await _person(client)
        await _click(client, cid, mid)
        client.cookies.set("job360_session", await _second_user_cookie())
        gate = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid, "session": "desk"})
        assert gate.status_code == 404 and gate.json()["detail"] == "contact not found"
        # the second user cannot claim the first user's person either
        taken = await _take(client, cid)
        assert taken["status"] == "empty"
        click = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "decline"})
        assert click.status_code == 404
