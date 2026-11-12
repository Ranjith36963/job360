"""Connect with no note, then the message once they accept (owner 2026-11-03). A free LinkedIn account gets about one
note a month, so the request goes WITHOUT a note and the saved draft (``draft_kind="after_connect"``) is the message
sent once they accept. The request goes on the user's Send, or with ``outreach_send`` auto; the message after an
approved request goes without a new Send, but only past the fact and sensitive-topic checks. A pending person is
never messaged and nobody gets a second request. Real doors (HTTP and MCP) with value asserts; helpers are copied
from sibling modules, never imported."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.applications import cv_checks
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile
from tests._outreach_review import seed_strong_review

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
SEND = "assistant_settings.outreach_send"
INBOX = "preferences.daily_check"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}
MESSAGE = "Hi Priya, thanks for connecting. I am a Python data engineer and the Northwind pipelines role fits me. " * 2


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer at Acme Data.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _profile_edit(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _auto(client: AsyncClient) -> None:
    await _profile_edit(client, INBOX, "auto")
    await _profile_edit(client, SEND, "auto")


async def _person(client: AsyncClient, n: int = 0, state: str = "not_connected") -> tuple[int, int]:
    resp = await client.post(
        "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
    )
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    cid = int(added.json()["contact"]["id"])
    await _state(client, cid, state)
    return app_id, cid


async def _state(client: AsyncClient, cid: int, state: str) -> None:
    resp = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": state})
    assert resp.status_code == 200, resp.text


async def _save(client: AsyncClient, app_id: int, cid: int, text: str = MESSAGE, **body: Any):
    return await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": text, **body},
    )


async def _draft(client: AsyncClient, app_id: int, cid: int, text: str = MESSAGE) -> int:
    resp = await _save(client, app_id, cid, text, draft_kind="after_connect")
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _click(client: AsyncClient, cid: int, mid: int) -> None:
    resp = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "approve"})
    assert resp.status_code == 200, resp.text


async def _take(client: AsyncClient, cid: int | None = None, session: str = "desk") -> dict[str, Any]:
    body: dict[str, Any] = {"session": session}
    if cid is not None:
        body["contact_id"] = cid
    resp = await client.post("/api/own-browser/take", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(client: AsyncClient, task_id: str, reason: str, session: str = "desk"):
    return await client.post("/api/own-browser/release", json={"session": session, "task_id": task_id, "reason": reason})


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


def _age_sends() -> None:
    """Every LinkedIn send and request five minutes ago, so the 120-second gap is over."""
    _sql(
        "UPDATE contact_outreach SET recorded_at = ? WHERE entry IN ('sent', 'send_released', 'send_claimed')",
        ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),),
    )


async def _invite(client: AsyncClient, cid: int, mid: int) -> None:
    """Take the person, hear send from the gate, Connect without a note, release invited."""
    took = await _take(client, cid)
    assert took["status"] == "task" and took["task"]["no_note"] is True, took
    assert "Send without a note" in took["next"]
    assert (await _check(client, cid, mid))[0] == "send"
    resp = await _release(client, f"contact:{cid}", "invited")
    assert resp.status_code == 200, resp.text
    _age_sends()


async def _person_state(client: AsyncClient, cid: int) -> dict[str, Any]:
    return (await client.get(f"/api/people?contact_id={cid}")).json()["person"]


# ── A. the request without a note ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_after_connect_draft_saves_up_to_400_and_is_never_a_note_to_rewrite(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        assert len(MESSAGE) > settings.LINKEDIN_NOTE_MAX_CHARS
        assert (await _save(client, app_id, cid)).status_code == 422  # no kind: still a note, fail closed
        mid = await _draft(client, app_id, cid)
        over = await _save(client, app_id, cid, "x" * 401, draft_kind="after_connect")
        assert over.status_code == 422 and "at most 400" in over.text
        email = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "contact_id": cid, "channel": "other", "text": "hi", "draft_kind": "after_connect"},
        )
        assert email.status_code == 422, email.text
        person = await _person_state(client, cid)
        assert person["note_rewrite"] is None
        assert [m["id"] for m in person["outreach"]["messages"]] == [mid]
        await _click(client, cid, mid)
        [task] = await _tasks(client)
        assert (task["contact_id"], task["no_note"], task["owner_yes"]) == (cid, True, True)
        assert task["browser"]["kind"] == "own_browser"  # LinkedIn never goes to Job360's browser


@pytest.mark.asyncio
async def test_the_users_send_on_a_no_note_request_passes_the_gate_and_leaves_them_pending(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        assert await _check(client, cid, mid) == ("ask", "needs_owner_click")
        await _click(client, cid, mid)
        assert await _check(client, cid, mid) == ("stop", "needs_claim")
        took = await _take(client, cid)
        assert took["status"] == "task"
        assert await _check(client, cid, mid) == ("send", "connect_no_note")
        assert (await _release(client, f"contact:{cid}", "invited")).status_code == 200
        person = await _person_state(client, cid)
        assert person["linkedin_state"] == "pending"
        # Pending: never messaged, never handed out.
        assert await _check(client, cid, mid) == ("stop", "linkedin_pending")
        assert [t["kind"] for t in await _tasks(client)] == ["linkedin_check_pending"]
        _age_sends()
        assert (await _take(client, cid))["status"] == "empty"
        # Nobody gets a second request: they read not_connected again later.
        await _state(client, cid, "not_connected")
        assert await _check(client, cid, mid) == ("stop", "already_invited")
        assert all(t.get("contact_id") != cid for t in await _tasks(client))


@pytest.mark.asyncio
async def test_invited_is_refused_for_a_note_draft(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        note = await _save(client, app_id, cid, "Hi Priya, a short note")
        assert note.status_code == 201
        await _click(client, cid, int(note.json()["artifact_id"]))
        took = await _take(client, cid)
        assert took["status"] == "task" and took["task"]["no_note"] is False
        refused = await _release(client, f"contact:{cid}", "invited")
        assert refused.status_code == 409 and "after_connect" in refused.text
        assert (await _person_state(client, cid))["linkedin_state"] == "not_connected"


@pytest.mark.asyncio
async def test_with_outreach_send_auto_the_request_goes_without_a_click(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        assert await _tasks(client) == []  # draft_only: it waits for the Send
        assert (await _take(client, cid))["status"] == "empty"
        await _auto(client)
        [task] = await _tasks(client)
        assert (task["contact_id"], task["no_note"], task["owner_yes"]) == (cid, True, False)
        assert task["browser"]["kind"] == "own_browser"
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, mid) == ("send", "auto_connect")
        assert (await _release(client, f"contact:{cid}", "other")).status_code == 200
        # A note draft to someone else still needs the click under auto.
        app2, other = await _person(client, 1)
        note = int((await _save(client, app2, other, "Hi Priya, a short note")).json()["artifact_id"])
        assert await _check(client, other, note, "laptop") == ("ask", "needs_owner_click")


@pytest.mark.asyncio
async def test_requests_count_toward_the_pace_and_the_weekly_backstop(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        a1, first = await _person(client, 0)
        a2, second = await _person(client, 1)
        m1, m2 = await _draft(client, a1, first), await _draft(client, a2, second)
        for cid, mid in ((first, m1), (second, m2)):
            await _click(client, cid, mid)
        assert (await _take(client, first))["status"] == "task"
        assert (await _release(client, f"contact:{first}", "invited")).status_code == 200
        # The request just went: the next one waits for the gap, at take and at the gate.
        took = await _take(client, second, "laptop")
        assert took["status"] == "wait" and took["wait_seconds"] > 0
        assert await _check(client, second, m2, "laptop") == ("stop", "too_soon")
        _age_sends()
        monkeypatch.setattr(settings, "LINKEDIN_INVITES_PER_WEEK_MAX", 1)
        assert (await _take(client, second, "laptop"))["status"] == "empty"
        assert await _check(client, second, m2, "laptop") == ("stop", "weekly_invite_cap_reached")


def test_every_cap_still_holds_on_the_requests_and_messages_that_need_no_click():
    auto = rules.effective(AssistantSettings(outreach_send="auto"))
    connect = {"linkedin_state": "not_connected", "after_connect": True, "text_chars": 380}
    after = {"invited": True, "invite_owner_approved": True}

    def gate(cfg: rules.EffectiveSettings, counts: dict[str, int] | None = None, **kw: Any) -> str:
        base: dict[str, Any] = {
            "message_found": True, "latest_draft": True, "channel": "linkedin", "has_linkedin_url": True,
            "linkedin_state": "connected", "text_chars": 120, "claimed_by_caller": True,
        }
        facts = rules.OutreachFacts(**{**base, **kw})
        return rules.may_send_outreach(cfg, facts, rules.OutreachCounts(sent_today=0, **(counts or {})), NOW).reason

    draft_only = rules.effective(AssistantSettings())
    assert gate(auto, **connect) == "auto_connect"
    assert gate(draft_only, **connect) == "needs_owner_click"
    assert gate(draft_only, owner_approved=True, **connect) == "connect_no_note"
    assert gate(draft_only, **after) == "auto_after_accept"
    assert gate(auto, invited=True) == "auto_after_accept"  # the request went under auto, auto is still on
    assert gate(draft_only, invited=True) == "needs_owner_click"  # it went under auto, auto is off now
    for kw in (connect, after):
        assert gate(auto, linkedin_sent_today=settings.LINKEDIN_SENDS_PER_DAY_DEFAULT, **kw) == "linkedin_daily_cap_reached"
        assert gate(auto, last_linkedin_sent_at=NOW - timedelta(seconds=30), **kw) == "too_soon"
        assert gate(auto, {"network_sent_last_hour": 5}, **kw) == "network_hourly_cap_reached"
        assert gate(auto, {"company_sent_in_window": 5}, linked=True, company="northwind", **kw) == "company_cap_reached"
        assert gate(auto, replied=True, **kw) == "replied"
    assert gate(auto, invites_this_week=settings.LINKEDIN_INVITES_PER_WEEK_MAX, **connect) == "weekly_invite_cap_reached"
    assert gate(auto, invite_limit_until=NOW + timedelta(days=1), **connect) == "invite_limit_reached"
    assert gate(auto, linkedin_state="pending", invited=True) == "linkedin_pending"
    # The guardrails ask; they never answer send.
    assert gate(auto, unsupported_claims=True, **after) == "unsupported_claims"
    assert gate(auto, sensitive=True, **after) == "sensitive_topic"
    assert gate(auto, claimed_by_caller=False, **after) == "needs_claim"


# ── B. accepted, then the message ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_after_the_users_send_on_the_request_the_message_goes_once_accepted(
    authenticated_async_context, fixture_user_id
):
    from src.api import mcp_server

    check_tool = mcp_server.build_server()._tool_manager.get_tool("check_outreach")
    user = mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        seed_strong_review(cid)  # owner 2026-11-09: the automatic message needs a strong review
        await _click(client, cid, mid)
        await _invite(client, cid, mid)
        assert all(t.get("contact_id") != cid for t in await _tasks(client))  # pending: not queued
        await _state(client, cid, "connected")  # the pending check saw the request accepted
        [task] = [t for t in await _tasks(client) if t["kind"] == "linkedin_outreach"]
        assert (task["contact_id"], task["message_id"], task["no_note"]) == (cid, mid, False)
        assert task["browser"]["kind"] == "own_browser"
        took = await _take(client, cid)
        assert took["status"] == "task" and "record_event outreach_sent" in took["next"]
        token = mcp_server._current_user.set(user)
        try:
            via_tool = await check_tool.fn(contact_id=cid, message_id=mid, session="desk")
        finally:
            mcp_server._current_user.reset(token)
        assert (via_tool["decision"], via_tool["reason"]) == ("send", "auto_after_accept")
        assert await _check(client, cid, mid) == ("send", "auto_after_accept")
        sent = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin"})
        assert sent.status_code == 201, sent.text
        assert await _check(client, cid, mid) == ("stop", "already_sent")
        log = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert [(i["kind"], i["answer"], i["application_id"]) for i in log] == [("linkedin_message", MESSAGE, app_id)]


@pytest.mark.asyncio
async def test_without_any_yes_the_message_after_acceptance_is_never_queued(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client, state="connected")
        mid = await _draft(client, app_id, cid)
        assert await _check(client, cid, mid) == ("ask", "needs_owner_click")
        assert await _tasks(client) == []
        assert (await _take(client, cid))["status"] == "empty"
        # The user's Send on the message itself: the old path, and the log stays empty (the user sent it).
        await _click(client, cid, mid)
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, mid) == ("send", "owner_clicked")
        assert (await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin"})).status_code == 201
        assert (await client.get("/api/sent-on-its-own")).json() == {"items": [], "cv_sample": []}


@pytest.mark.asyncio
async def test_a_sensitive_or_unsupported_message_after_acceptance_waits_for_the_send(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid, "Hi Priya, I would need visa sponsorship for this role.")
        await _click(client, cid, mid)
        await _invite(client, cid, mid)
        await _state(client, cid, "connected")
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, mid) == ("ask", "sensitive_topic")
        assert (await _release(client, f"contact:{cid}", "draft_rewritten")).status_code == 200
        rewritten = await _draft(client, app_id, cid, "Hi Priya, I worked at Globex for 9 years on pipelines.")
        _age_sends()
        # The user's Send was for the first text: a rewrite waits for a new Send (Needs you), never goes on its own.
        assert (await _take(client, cid))["status"] == "empty"
        assert await _check(client, cid, rewritten) == ("ask", "needs_owner_click")
        await _auto(client)  # with autonomy on, the rewrite may go, but only past the fact check
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, rewritten) == ("ask", "unsupported_claims")
        assert (await _release(client, f"contact:{cid}", "draft_rewritten")).status_code == 200
        _age_sends()
        fine = await _draft(client, app_id, cid, "Hi Priya, I worked at Acme Data as a Python data engineer.")
        seed_strong_review(cid)  # owner 2026-11-09: the automatic message needs a strong review
        assert await _check(client, cid, fine) == ("stop", "needs_claim")
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, fine) == ("send", "auto_after_accept")


@pytest.mark.asyncio
async def test_the_users_send_binds_to_the_text_the_user_saw_never_a_swapped_draft(
    authenticated_async_context, fixture_user_id
):
    """M1 approved, the request went; the assistant swaps in M2 and saves connected: M2 never goes without a Send."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _person(client)
        m1 = await _draft(client, app_id, cid)
        await _click(client, cid, m1)
        await _invite(client, cid, m1)
        m2 = await _draft(client, app_id, cid, "Hi Priya, a different text the user never saw.")
        await _state(client, cid, "connected")
        assert all(t.get("contact_id") != cid for t in await _tasks(client))
        assert (await _take(client, cid))["status"] == "empty"
        assert await _check(client, cid, m2) == ("ask", "needs_owner_click")
        assert await _check(client, cid, m1) == ("stop", "not_latest_draft")
        await _click(client, cid, m2)  # the user's own Send on M2: it goes as theirs
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, m2) == ("send", "owner_clicked")


@pytest.mark.asyncio
async def test_a_request_counts_toward_the_company_and_network_caps(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        await _click(client, cid, mid)
        await _invite(client, cid, mid)
        db = await api_deps.get_db()
        now = datetime.now(timezone.utc)
        job = await rules.load_outreach_counts(db, fixture_user_id, now, "northwind")
        assert (job.sent_today, job.job_sent_today, job.company_sent_in_window) == (1, 1, 1)
        cold = await client.post(
            "/api/contacts", json={"name": "Ravi", "company": "Contoso", "linkedin_url": "https://www.linkedin.com/in/ravi"}
        )
        assert cold.status_code == 201, cold.text
        other = int(cold.json()["contact"]["id"])
        await _state(client, other, "not_connected")
        note = await client.post(
            f"/api/contacts/{other}/outreach",
            json={"entry": "message", "channel": "linkedin", "text": MESSAGE, "draft_kind": "after_connect"},
        )
        assert note.status_code == 201, note.text
        await _click(client, other, int(note.json()["outreach"]["id"]))
        await _invite(client, other, int(note.json()["outreach"]["id"]))
        net = await rules.load_outreach_counts(db, fixture_user_id, datetime.now(timezone.utc))
        assert (net.sent_today, net.job_sent_today, net.network_sent_today, net.network_sent_last_hour) == (2, 1, 1, 1)


@pytest.mark.asyncio
async def test_with_auto_the_request_and_the_message_both_land_in_sent_on_its_own(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _auto(client)
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        seed_strong_review(cid)  # owner 2026-11-09: the automatic message needs a strong review
        await _invite(client, cid, mid)
        await _state(client, cid, "connected")
        assert (await _take(client, cid))["status"] == "task"
        assert await _check(client, cid, mid) == ("send", "auto_after_accept")
        assert (await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin"})).status_code == 201
        log = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert sorted(i["kind"] for i in log) == ["linkedin_message", "linkedin_request"]
        assert {i["question"] for i in log} == {"LinkedIn message to Priya 0", "LinkedIn request without a note to Priya 0"}


# ── the pending check in the user's own browser ─────────────────────────────


@pytest.mark.asyncio
async def test_the_pending_check_is_due_while_anyone_is_pending_and_counts_what_it_saved(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert all(t["kind"] != "linkedin_check_pending" for t in await _tasks(client))
        app_id, cid = await _person(client)
        _a2, still = await _person(client, 1, state="pending")
        mid = await _draft(client, app_id, cid)
        await _click(client, cid, mid)
        await _invite(client, cid, mid)
        [check] = [t for t in await _tasks(client) if t["kind"] == "linkedin_check_pending"]
        assert check["task_id"] == "duty:linkedin_check_pending" and check["contact_ids"] == [cid, still]
        assert check["browser"]["kind"] == "own_browser"
        took = await _take(client)
        assert took["status"] == "task" and took["task"]["kind"] == "linkedin_check_pending"
        assert "update_contact linkedin_state" in took["next"]
        await _state(client, cid, "connected")
        await _state(client, still, "pending")
        done = (await _release(client, "duty:linkedin_check_pending", "done")).json()
        assert (done["outcome"], done["count"], done["summary"]) == ("done", 2, "done, 2 people checked")
        # Not due again for LINKEDIN_PENDING_CHECK_HOURS; the accepted person's message is queued instead.
        kinds = [(t["kind"], t.get("contact_id")) for t in await _tasks(client)]
        assert kinds == [("linkedin_outreach", cid)]


# ── the checks themselves ────────────────────────────────────────────────────


def test_the_name_and_topic_checks_read_word_by_word():
    source = "Python data engineer at Acme Data. MSc, University of Leeds."
    assert cv_checks.unsupported_names("I worked at Acme Data and studied at University of Leeds.", source) is None
    got = cv_checks.unsupported_names("I worked at Globex Corp, then interned at Initech.", source)
    assert got is not None and got["detail"] == "Not found anywhere in the profile: Globex Corp, Initech."
    assert cv_checks.unsupported_names("I worked at a startup.", source) is None
    assert cv_checks.sensitive_words("Happy to discuss Salary; no visa needed. I manage pages.") == ["salary", "visa"]
    assert cv_checks.outreach_claims_unsupported("Hi, a Python engineer here.", None) is True
    assert cv_checks.outreach_claims_unsupported("Hi, 12 years of Python.", UserProfile(cv_data=CVData(raw_text=source)))


@pytest.mark.asyncio
async def test_a_second_invited_release_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _person(client)
        mid = await _draft(client, app_id, cid)
        await _click(client, cid, mid)
        await _invite(client, cid, mid)
        await _state(client, cid, "connected")
        assert (await _take(client, cid))["status"] == "task"  # the approved message, now to a connection
        again = await _release(client, f"contact:{cid}", "invited")
        assert again.status_code == 409 and "already" in again.text
        assert (await _person_state(client, cid))["linkedin_state"] == "connected"
