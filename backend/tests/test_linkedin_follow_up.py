"""One LinkedIn follow-up (owner 2026-11-03): when the message after an accepted request had no reply for
``LINKEDIN_FOLLOW_UP_DAYS`` (6), Needs you asks the assistant to draft ONE follow-up. It goes under the same rule as
the message (no new Send when the request was approved, past the same checks), never a second time, and a reply stops
it. Real doors (HTTP) with value asserts; helpers are copied from sibling modules, never imported."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.api import dependencies as api_deps
from src.core import settings
from src.repositories import pgsync
from src.services.applications import blockers
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}
MESSAGE = "Hi Priya, thanks for connecting. I am a Python data engineer and the Northwind role fits me."
FOLLOW = "Hi Priya, a short follow-up on the Northwind data engineer role. Happy to talk any time."


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


def _sql(sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _age(entries: tuple[str, ...], days: float) -> None:
    marks = ",".join("?" for _ in entries)
    _sql(
        f"UPDATE contact_outreach SET recorded_at = ? WHERE entry IN ({marks})",  # noqa: S608 - placeholders only
        ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), *entries),
    )


async def _post(client: AsyncClient, url: str, body: dict[str, Any], code: int = 200) -> dict[str, Any]:
    resp = await client.post(url, json=body)
    assert resp.status_code == code, resp.text
    return resp.json()


async def _check(client: AsyncClient, cid: int, mid: int, session: str = "desk") -> tuple[str, str]:
    resp = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid, "session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()["decision"], resp.json()["reason"]


async def _draft(client: AsyncClient, app_id: int, cid: int, text: str) -> int:
    body = {"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": text, "draft_kind": "after_connect"}
    return int((await _post(client, f"/api/applications/{app_id}/artifacts", body, 201))["artifact_id"])


async def _take(client: AsyncClient, cid: int) -> str:
    return str((await _post(client, "/api/own-browser/take", {"session": "desk", "contact_id": cid}))["status"])


async def _queued(client: AsyncClient) -> list[int]:
    resp = await client.get("/api/own-browser/queue")
    assert resp.status_code == 200, resp.text
    return [t["contact_id"] for t in resp.json()["tasks"] if t["kind"] == "linkedin_outreach"]


async def _messaged_person(client: AsyncClient) -> tuple[int, int]:
    """(app_id, contact_id): the user pressed Send on the request; they accepted; the message went 1 hour ago."""
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    body = {"name": "Priya", "role": "Recruiter", "linkedin_url": "https://www.linkedin.com/in/priya"}
    cid = int((await _post(client, f"/api/applications/{app_id}/contacts", body, 201))["contact"]["id"])
    assert (await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "not_connected"})).status_code == 200
    mid = await _draft(client, app_id, cid, MESSAGE)
    await _post(client, f"/api/contacts/{cid}/own-browser-send", {"message_id": mid, "decision": "approve"})
    assert await _take(client, cid) == "task"
    await _post(client, "/api/own-browser/release", {"session": "desk", "task_id": f"contact:{cid}", "reason": "invited"})
    assert (await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "connected"})).status_code == 200
    _age(("send_claimed", "send_released"), 1)
    assert await _take(client, cid) == "task"
    assert await _check(client, cid, mid) == ("send", "auto_after_accept")
    await _post(client, f"/api/contacts/{cid}/outreach", {"entry": "sent", "channel": "linkedin"}, 201)
    _sql("UPDATE applications SET status = 'applied' WHERE id = ?", (app_id,))
    _age(("send_claimed", "send_released", "sent"), 1 / 24)
    return app_id, cid


async def _follow_up_blockers(user_id: str, app_id: int) -> list[tuple[str, str, Any]]:
    got = await blockers.blockers_for(await api_deps.get_db(), user_id, [app_id])
    return [(b["actor"], b["label"], b["ref"]) for b in got[app_id] if b["kind"] == "follow_up"]


@pytest.mark.asyncio
async def test_a_follow_up_is_due_after_six_days_and_never_a_second_time(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _messaged_person(client)
        assert await _follow_up_blockers(fixture_user_id, app_id) == []  # not due yet
        _age(("sent",), settings.LINKEDIN_FOLLOW_UP_DAYS)
        assert await _follow_up_blockers(fixture_user_id, app_id) == [
            ("assistant", "draft 1 LinkedIn follow-up", {"contact_ids": [cid], "reason": "linkedin_follow_up"})
        ]
        follow = await _draft(client, app_id, cid, FOLLOW)
        assert await _follow_up_blockers(fixture_user_id, app_id) == []  # drafted: no longer the assistant's job
        assert cid in await _queued(client)  # due: the drafted follow-up waits on the user's PC
        assert await _check(client, cid, follow) == ("stop", "needs_claim")
        assert await _take(client, cid) == "task"
        assert await _check(client, cid, follow) == ("send", "auto_follow_up")  # the approved request carries
        await _post(client, f"/api/contacts/{cid}/outreach", {"entry": "sent", "channel": "linkedin"}, 201)
        _age(("send_claimed", "sent"), settings.LINKEDIN_FOLLOW_UP_DAYS * 3)
        assert await _follow_up_blockers(fixture_user_id, app_id) == []  # never a second
        second = await _draft(client, app_id, cid, FOLLOW + " Again.")
        assert await _check(client, cid, second) == ("stop", "follow_up_used")
        assert cid not in await _queued(client)
        assert await _take(client, cid) == "empty"


@pytest.mark.asyncio
async def test_a_reply_stops_the_follow_up(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, cid = await _messaged_person(client)
        follow = await _draft(client, app_id, cid, FOLLOW)
        assert await _check(client, cid, follow) == ("stop", "follow_up_not_due")
        assert cid not in await _queued(client)
        _age(("sent",), settings.LINKEDIN_FOLLOW_UP_DAYS + 1)
        assert cid in await _queued(client)
        await _post(client, f"/api/contacts/{cid}/outreach", {"entry": "reply", "channel": "linkedin"}, 201)
        assert await _check(client, cid, follow) == ("stop", "replied")
        assert cid not in await _queued(client)
        assert await _take(client, cid) == "empty"
        assert await _follow_up_blockers(fixture_user_id, app_id) == []


def test_the_follow_up_takes_the_same_guardrails_and_needs_the_click_without_an_approved_request():
    auto = rules.effective(AssistantSettings(outreach_send="auto"))
    draft_only = rules.effective(AssistantSettings())
    due = {"already_sent": True, "follow_up": True, "linkedin_sent_to_person": 1,
           "days_since_sent": settings.LINKEDIN_FOLLOW_UP_DAYS}

    def gate(cfg: rules.EffectiveSettings, **kw: Any) -> str:
        base: dict[str, Any] = {
            "message_found": True, "latest_draft": True, "channel": "linkedin", "has_linkedin_url": True,
            "linkedin_state": "connected", "text_chars": 90, "claimed_by_caller": True, **due,
        }
        facts = rules.OutreachFacts(**{**base, **kw})
        return rules.may_send_outreach(cfg, facts, rules.OutreachCounts(sent_today=0), NOW).reason

    approved = {"invited": True, "invite_owner_approved": True}
    assert gate(draft_only, **approved) == "auto_follow_up"
    assert gate(auto, invited=True) == "auto_follow_up"
    assert gate(draft_only) == "needs_owner_click"  # connected from the start: the user's Send
    assert gate(draft_only, owner_approved=True) == "owner_clicked"
    assert gate(draft_only, days_since_sent=settings.LINKEDIN_FOLLOW_UP_DAYS - 1, **approved) == "follow_up_not_due"
    assert gate(draft_only, linkedin_sent_to_person=2, **approved) == "follow_up_used"
    assert gate(draft_only, replied=True, **approved) == "replied"
    assert gate(draft_only, unsupported_claims=True, **approved) == "unsupported_claims"
    assert gate(draft_only, sensitive=True, **approved) == "sensitive_topic"
    assert gate(draft_only, linkedin_sent_today=settings.LINKEDIN_SENDS_PER_DAY_DEFAULT, **approved) == (
        "linkedin_daily_cap_reached"
    )
    assert gate(draft_only, follow_up=False, **approved) == "already_sent"


@pytest.mark.asyncio
async def test_a_person_with_no_job_shows_the_follow_up_on_their_card(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        body = {"name": "Ravi", "company": "Contoso", "linkedin_url": "https://www.linkedin.com/in/ravi"}
        cid = int((await _post(client, "/api/contacts", body, 201))["contact"]["id"])
        assert (await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "not_connected"})).status_code == 200
        draft = {"entry": "message", "channel": "linkedin", "text": MESSAGE, "draft_kind": "after_connect"}
        mid = int((await _post(client, f"/api/contacts/{cid}/outreach", draft, 201))["outreach"]["id"])
        await _post(client, f"/api/contacts/{cid}/own-browser-send", {"message_id": mid, "decision": "approve"})
        assert await _take(client, cid) == "task"
        released = {"session": "desk", "task_id": f"contact:{cid}", "reason": "invited"}
        await _post(client, "/api/own-browser/release", released)
        assert (await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "connected"})).status_code == 200
        _age(("send_claimed", "send_released"), 1)
        assert await _take(client, cid) == "task"
        assert await _check(client, cid, mid) == ("send", "auto_after_accept")
        await _post(client, f"/api/contacts/{cid}/outreach", {"entry": "sent", "channel": "linkedin"}, 201)

        async def due() -> bool:
            return bool((await client.get(f"/api/people?contact_id={cid}")).json()["person"]["linkedin_follow_up_due"])

        assert await due() is False
        _age(("send_claimed", "send_released", "sent"), settings.LINKEDIN_FOLLOW_UP_DAYS)
        assert await due() is True
        follow = dict(draft, text=FOLLOW)
        fid = int((await _post(client, f"/api/contacts/{cid}/outreach", follow, 201))["outreach"]["id"])
        assert await due() is False
        assert await _take(client, cid) == "task"
        assert await _check(client, cid, fid) == ("send", "auto_follow_up")


def test_the_users_send_carries_to_a_follow_up_only_after_that_exact_message_went():
    from src.services.applications import own_browser as ob

    def row(i: int, entry: str, text: str = "", channel: str = "linkedin") -> dict[str, Any]:
        return {"id": i, "entry": entry, "text": text, "channel": channel, "recorded_at": NOW.isoformat()}

    base = [row(1, "message"), row(2, ob.APPROVED, "1"), row(3, ob.CLAIMED, "desk"), row(4, ob.RELEASED, "invited")]
    assert ob.owner_yes_carries(base, 1) is True
    swapped = base + [row(5, "message")]
    assert ob.owner_yes_carries(swapped, 5) is False  # a text the user never saw
    went = base + [row(5, "sent"), row(6, "message")]
    assert ob.owner_yes_carries(went, 6) is True  # the follow-up after the approved message went
    other_went = swapped + [row(6, ob.APPROVED, "5"), row(7, "sent"), row(8, "message")]
    assert ob.owner_yes_carries(other_went, 8) is False  # the approved message itself never went
    auto_invite = [row(1, "message"), row(2, ob.CLAIMED, "desk"), row(3, ob.RELEASED, "invited"), row(4, "sent")]
    assert ob.owner_yes_carries(auto_invite + [row(5, "message")], 5) is False


def test_sensitive_words_read_every_token_and_the_phrases():
    from src.services.applications import cv_checks

    assert cv_checks.sensitive_words("Happy to talk visa-sponsorship.") == ["visa", "sponsorship"]
    assert cv_checks.sensitive_words("What about salary/benefits?") == ["salary"]
    assert cv_checks.sensitive_words("I hold a Work Permit.") == ["work permit", "permit"]
    assert cv_checks.sensitive_words("I have the right to work in the UK.") == ["right to work"]
    assert cv_checks.sensitive_words("A Skilled Worker route; relocation fine.") == ["skilled worker", "relocation"]
    assert cv_checks.sensitive_words("I manage pipelines at a startup.") == []


def test_the_fact_check_reads_bare_names_spelled_numbers_and_ignores_case():
    from src.services.applications import cv_checks

    profile = UserProfile(cv_data=CVData(raw_text="Python data engineer at Acme Data for 7 years."))
    assert cv_checks.outreach_claims_unsupported("I WORKED AT Acme data on pipelines.", profile) is False
    assert cv_checks.outreach_claims_unsupported("I led pipelines at Globex.", profile) is True
    assert cv_checks.outreach_claims_unsupported("I like the role at Northwind.", profile, "Priya Northwind") is False
    assert cv_checks.outreach_claims_unsupported("I have seven years of Python.", profile) is False
    assert cv_checks.outreach_claims_unsupported("I have twelve years of Python.", profile) is True
    assert cv_checks.outreach_claims_unsupported("I shipped forty pipelines.", profile) is True
