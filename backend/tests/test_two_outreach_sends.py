"""Owner 2026-11-02 - two kinds of outreach send.

JOB outreach: people at a company the user applied to (a contact with an ``application_id``), up to
``outreach_per_company`` sends per company in ``OUTREACH_COMPANY_WINDOW_DAYS``. NETWORK: people with no job,
``network_per_hour`` sends an hour; the network duty is due hourly for 5 new people. The limits are the user's
own assistant settings; an operator backstop (core/settings ``*_MAX``) holds above them whatever is stored.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import assistant_settings as rules
from src.services.profile import schedule
from src.services.profile.models import AssistantSettings
from tests._outreach_review import seed_strong_review
from tests.test_outreach_gate import _AD, _auto_ready, _bring, _check, _contact, _draft, _patch, _sent

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
PER_COMPANY = "assistant_settings.outreach_per_company"
LINKEDIN_DAY = "assistant_settings.linkedin_sends_per_day"
EMAIL_DAY = "assistant_settings.email_sends_per_day"


# ── the network duty ─────────────────────────────────────────────────────────


def _due_facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
        "people_hunt": True,
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _network(answer: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in answer["due"] if d["duty"] == "network"]


def test_the_network_duty_is_due_every_hour_for_five_people():
    assert (settings.NETWORK_EVERY_HOURS, settings.NETWORK_MAX_PER_RUN, settings.REACH_MAX_PER_RUN) == (1, 5, 5)
    ran = NOW - timedelta(minutes=30)
    waiting = schedule.due_now(_due_facts(stamps={"network": ran}), NOW)
    assert _network(waiting) == []
    assert waiting["next_due_at"]["network"] == (ran + timedelta(hours=1)).isoformat(timespec="seconds")
    elapsed = schedule.due_now(_due_facts(stamps={"network": NOW - timedelta(minutes=61)}), NOW)
    assert _network(elapsed) == [
        {"duty": "network", "recipe": "reach", "max_people": 5, "reason": "interval_elapsed"}
    ]


# ── job outreach: per company ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_five_job_sends_to_one_company_pass_and_the_sixth_stops(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, _cid, _mid = await _auto_ready(client, fixture_user_id)
        for n in range(5):
            person = await _contact(client, app_id, email=f"p{n}@northwind.example", name=f"Person {n}")
            draft = await _draft(client, app_id, person)
            seed_strong_review(person)  # owner 2026-11-09: an automatic send needs a strong review
            verdict = await _check(client, person, draft)
            assert (n, verdict["decision"], verdict["reason"]) == (n, "send", "auto_mode")
            await _sent(client, person)
        sixth = await _contact(client, app_id, email="p6@northwind.example", name="Person Six")
        verdict = await _check(client, sixth, await _draft(client, app_id, sixth))
    assert (verdict["decision"], verdict["reason"]) == ("stop", "company_cap_reached")
    assert "outreach_per_company" in verdict["detail"]
    assert (verdict["job_sent_today"], verdict["network_sent_today"]) == (5, 0)


# ── the two kinds never count against each other ─────────────────────────────


async def _cold(client: AsyncClient, n: int, company: str = "Northwind") -> tuple[int, int]:
    """A NETWORK contact (no application) with one saved email message."""
    made = await client.post(
        "/api/contacts",
        json={"name": f"Cold {n}", "email": f"cold{n}@northwind.example", "company": company, "found_via": "linkedin"},
    )
    assert made.status_code == 201, made.text
    contact_id = int(made.json()["contact"]["id"])
    msg = await client.post(
        f"/api/contacts/{contact_id}/outreach", json={"entry": "message", "channel": "email", "text": "Hello"}
    )
    assert msg.status_code == 201, msg.text
    return contact_id, int(msg.json()["outreach"]["id"])


@pytest.mark.asyncio
async def test_network_and_job_sends_never_count_against_each_other(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        for n in range(4):  # four network sends to people at the same company
            await _sent(client, (await _cold(client, n))[0])
        assert (await _check(client, cid, mid))["reason"] == "auto_mode"  # the company's job cap ignores them
        for n in range(5):  # five job sends at that company
            await _sent(client, await _contact(client, app_id, email=f"j{n}@northwind.example", name=f"J{n}"))
        assert (await _check(client, cid, mid))["reason"] == "company_cap_reached"
        cold, cold_msg = await _cold(client, 10)
        under = await _check(client, cold, cold_msg)  # 4 network sends this hour: job sends do not count
        assert (under["decision"], under["reason"]) == ("ask", "unlinked_contact")
        await _sent(client, (await _cold(client, 11))[0])
        over = await _check(client, cold, cold_msg)
    assert (over["decision"], over["reason"]) == ("stop", "network_hourly_cap_reached")
    assert (over["sent_today"], over["job_sent_today"], over["network_sent_today"]) == (10, 5, 5)


def test_the_kind_caps_in_the_pure_gate():
    cfg = rules.effective(AssistantSettings(outreach_send="auto"))
    job = rules.OutreachFacts(
        message_found=True, latest_draft=True, channel="email", has_email=True, linked=True,
        found_via="company_site", company="northwind",
    )
    network = rules.OutreachFacts(
        message_found=True, latest_draft=True, channel="email", has_email=True, found_via="linkedin", company="northwind",
    )
    many_network = rules.OutreachCounts(sent_today=5, network_sent_last_hour=5, company_sent_in_window=0)
    many_job = rules.OutreachCounts(sent_today=5, company_sent_in_window=5)
    assert rules.may_send_outreach(cfg, job, many_network, NOW).reason == "auto_mode"
    assert rules.may_send_outreach(cfg, network, many_job, NOW).reason == "unlinked_contact"
    assert rules.may_send_outreach(cfg, job, many_job, NOW).reason == "company_cap_reached"
    assert rules.may_send_outreach(cfg, network, many_network, NOW).reason == "network_hourly_cap_reached"


# ── per channel: LinkedIn 20 a day, email its own 40 ─────────────────────────


def _linkedin(**kw: Any) -> rules.OutreachFacts:
    base: dict[str, Any] = {
        "message_found": True, "latest_draft": True, "channel": "linkedin", "has_linkedin_url": True,
        "linkedin_state": "connected", "text_chars": 120, "owner_approved": True, "claimed_by_caller": True,
    }
    base.update(kw)
    return rules.OutreachFacts(**base)


def test_the_16th_linkedin_send_stops_while_an_email_still_passes():
    cfg = rules.effective(AssistantSettings(outreach_send="auto"))
    assert (cfg.linkedin_sends_per_day, cfg.email_sends_per_day) == (15, 40)
    counts = rules.OutreachCounts(sent_today=15, network_sent_last_hour=0)
    assert rules.may_send_outreach(cfg, _linkedin(linkedin_sent_today=14), counts, NOW).reason == "owner_clicked"
    stopped = rules.may_send_outreach(cfg, _linkedin(linkedin_sent_today=15), counts, NOW)
    assert (stopped.decision, stopped.reason) == ("stop", "linkedin_daily_cap_reached")
    email = rules.OutreachFacts(
        message_found=True, latest_draft=True, channel="email", has_email=True, linked=True,
        found_via="company_site", company="northwind",
    )
    assert rules.may_send_outreach(cfg, email, counts, NOW).reason == "auto_mode"
    assert rules.may_send_outreach(
        cfg, email, rules.OutreachCounts(sent_today=40, email_sent_today=40), NOW
    ).reason == "daily_outreach_cap_reached"


@pytest.mark.asyncio
async def test_the_own_browser_queue_reads_the_users_linkedin_day(authenticated_async_context, fixture_user_id):
    from src.api.routes import browser as browser_routes
    from src.services.applications import own_browser

    async with authenticated_async_context() as client:
        await _auto_ready(client, fixture_user_id)
        await _patch(client, LINKEDIN_DAY, 2)  # lowering is safer: the web click applies at once
        app2 = await _bring(client, {**_AD, "company": "Contoso", "apply_url": "https://contoso.example/1"})
        for n in range(2):
            person = await _contact(client, app2, email=f"l{n}@contoso.example", name=f"L{n}")
            sent = await client.post(f"/api/contacts/{person}/outreach", json={"entry": "sent", "channel": "linkedin"})
            assert sent.status_code == 201, sent.text
        async with browser_routes._db() as db:  # noqa: SLF001
            later = datetime.now(timezone.utc) + timedelta(minutes=10)  # past the 2-minute gap
            assert await own_browser._pace(db, fixture_user_id, later) == ("daily_limit", 0)  # noqa: SLF001
            await _patch(client, LINKEDIN_DAY, None)  # back to the default 15
            assert await own_browser._pace(db, fixture_user_id, later) is None  # noqa: SLF001


# ── the user's own settings ──────────────────────────────────────────────────


async def _limits(client: AsyncClient) -> dict[str, Any]:
    view = (await client.get("/api/assistant-settings")).json()
    return {name: view[name] for name in rules.OUTREACH_LIMITS}


@pytest.mark.asyncio
async def test_the_user_changes_a_limit_and_an_assistants_raise_waits_for_the_confirm(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        await _auto_ready(client, fixture_user_id)
        assert await _limits(client) == {
            "outreach_per_company": {"value": None, "effective": 5},
            "network_per_hour": {"value": None, "effective": 5},
            "linkedin_sends_per_day": {"value": None, "effective": 15},
            "email_sends_per_day": {"value": None, "effective": 40},
        }
        await _patch(client, PER_COMPANY, 3)  # the user's own web click applies at once
        assert (await _limits(client))["outreach_per_company"] == {"value": 3, "effective": 3}
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]

        async def agent_set(path: str, value: Any) -> dict[str, Any]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as agent:
                resp = await agent.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
            assert resp.status_code == 200, resp.text
            return resp.json()

        raised = await agent_set(PER_COMPANY, 8)
        assert raised["applied"] == [] and [w["path"] for w in raised["waiting"]] == [PER_COMPANY]
        assert (await _limits(client))["outreach_per_company"]["effective"] == 3
        confirmed = await client.post(f"/api/assistant-settings/requests/{raised['waiting'][0]['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert (await _limits(client))["outreach_per_company"]["effective"] == 8
        lowered = await agent_set(EMAIL_DAY, 10)  # lower than the default 40: safer, applies at once
        assert lowered["waiting"] == [] and [a["path"] for a in lowered["applied"]] == [EMAIL_DAY]
        assert (await _limits(client))["email_sends_per_day"] == {"value": 10, "effective": 10}
        cleared = await agent_set(PER_COMPANY, None)  # null = the default 5: lower than 8, applies at once
        assert cleared["waiting"] == [] and (await _limits(client))["outreach_per_company"]["effective"] == 5


def test_a_raise_is_riskier_and_a_lower_is_safer():
    for path in rules.OUTREACH_LIMIT_PATHS:
        assert path in rules.SETTING_PATHS and path in rules.GATED_PATHS
        assert rules.classify_change(path, 5, 6) == "riskier"
        assert rules.classify_change(path, 6, 5) == "safer"
        assert rules.classify_change(path, 5, 5) == "safer"


# ── the operator backstop ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_operator_backstop_holds_even_at_100000(authenticated_async_context, fixture_user_id, monkeypatch):
    async with authenticated_async_context() as client:
        await _auto_ready(client, fixture_user_id)
        for path in rules.OUTREACH_LIMIT_PATHS:
            refused = await client.patch("/api/profile", json={"edits": [{"path": path, "value": 100000}]})
            assert refused.status_code == 422 and path in refused.text, refused.text
    # A stored 100000 (a historical row, or a backstop the operator lowered later) still reads as the backstop.
    stored = rules.effective(AssistantSettings(**{name: 100000 for name in rules.OUTREACH_LIMITS}))
    assert (
        stored.outreach_per_company, stored.network_per_hour, stored.linkedin_sends_per_day, stored.email_sends_per_day
    ) == (10, 20, 100, 200)
    assert (
        settings.OUTREACH_COMPANY_MAX, settings.OUTREACH_NETWORK_MAX_PER_HOUR, settings.OUTREACH_LINKEDIN_MAX_PER_DAY,
        settings.OUTREACH_EMAIL_MAX_PER_DAY, settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY,
    ) == (10, 20, 100, 200, 300)
    email = rules.OutreachFacts(
        message_found=True, latest_draft=True, channel="email", has_email=True, linked=True,
        found_via="company_site", company="northwind",
    )
    assert rules.may_send_outreach(
        stored, email, rules.OutreachCounts(sent_today=200, email_sent_today=200), NOW
    ).reason == "daily_outreach_cap_reached"
    assert rules.may_send_outreach(
        stored, email, rules.OutreachCounts(sent_today=0, company_sent_in_window=10), NOW
    ).reason == "company_cap_reached"
    assert rules.may_send_outreach(
        stored, _linkedin(linkedin_sent_today=100), rules.OutreachCounts(sent_today=100), NOW
    ).reason == "linkedin_daily_cap_reached"
    # The gate re-applies the backstop even to settings built before the operator lowered it.
    monkeypatch.setattr(settings, "OUTREACH_EMAIL_MAX_PER_DAY", 3)
    assert rules.may_send_outreach(
        stored, email, rules.OutreachCounts(sent_today=3, email_sent_today=3), NOW
    ).reason == "daily_outreach_cap_reached"
