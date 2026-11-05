"""Owner report 2026-11-05 (#9, #14) - lean replies for an agent (and voice mode).

#9: get_profile's brief sent the whole ``search_brief.plan`` (30 rows on a real profile) on every
call; only the hunt reads it, so it now rides ``search_plan=true`` (and the full view).
#14: whats_new listed 60+ inbox items, mostly old job alerts, with a recruiter's reply lost among
them. Now: a person's reply first, the rest grouped by sender, one page with an offset, and job
alerts older than INBOX_ALERTS_STALE_DAYS left out unless asked for (never deleted).

Real doors: the streamable-HTTP MCP endpoint with a bearer token, and ``GET /api/whats-new``.
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

PLACES = ["GB", "IE", "DE", "NL", "FR", "ES", "PT", "SE", "DK", "ANY"]
RIGHT_TO_WORK = {"countries": [
    {"country": cc, "work_authorization": "citizen" if cc in ("GB", "IE") else "needs_sponsorship",
     "remote_ok": True, "onsite": "yes" if cc in ("GB", "IE") else "only_if_sponsored", "contract_ok": True,
     "remote_priority": "high" if cc != "ANY" else "highest", "onsite_priority": "medium",
     "contract_priority": "low"}
    for cc in PLACES
]}


async def _mcp(authenticated_async_context, fixture_user_id):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(
            UserProfile(
                cv_data=CVData(
                    raw_text="Senior data engineer. " * 50, name="Ada Lovelace", location="London",
                    skills=["Python", "SQL", "dbt", "Airflow", "AWS"], job_titles=["Senior Data Engineer"],
                ),
                preferences=UserPreferences(target_job_titles=["Senior Data Engineer", "Analytics Engineer"]),
            ),
            fixture_user_id, source_action="cv_upload",
        )
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _call(session, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(tool, args)
    assert not res.is_error, res.content[0].text
    return json.loads(res.content[0].text)


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str))


# -- #9 get_profile: the plan only on request ------------------------------------------------------


@pytest.mark.asyncio
async def test_brief_drops_the_plan_and_search_plan_brings_it_back(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    mcp = await _mcp(authenticated_async_context, fixture_user_id)
    async with mcp_runtime():
        async with mcp as session:
            await _call(session, "update_profile", {"edits": [
                {"path": "user_info.right_to_work", "value": RIGHT_TO_WORK, "source": "user_said", "remember": True},
            ]})
            brief = await _call(session, "get_profile", {})
            with_plan = await _call(session, "get_profile", {"search_plan": True})
            full = await _call(session, "get_profile", {"view": "full"})
    sb = brief["search_brief"]
    assert "plan" not in sb
    # The rows and gaps the hunt needs are still there, with their values.
    assert [r["country"] for r in sb["rows"]] == PLACES
    assert sb["rows"][0]["priority"] == {"remote": "high", "onsite": "medium", "contract": "low"}
    assert sb["work_from"] == ["GB", "IE"]
    # The plan is reachable on request: 3 places per country, ordered by the user's priority.
    plan = with_plan["search_brief"]["plan"]
    assert len(plan) == 3 * len(PLACES)
    assert (plan[0]["country"], plan[0]["kind"], plan[0]["priority"]) == ("ANY", "remote", "highest")
    assert 98 <= sum(p["effort_pct"] for p in plan) <= 102
    assert {k: v for k, v in with_plan["search_brief"].items() if k != "plan"} == sb
    assert full["search_brief"]["plan"] == plan  # the full view keeps everything
    before, after = _size(with_plan), _size(brief)
    print(f"\nget_profile brief on a 10-country profile: with plan={before} without plan={after}")
    assert after < before - 5_000


def test_hunt_recipe_asks_for_the_plan_it_reads():
    from src.api.routes.recipes import load_recipe

    hunt = load_recipe("hunt").text
    assert "get_profile(search_plan=true)" in hunt and "search_brief.plan" in hunt
    assert len(hunt.splitlines()) <= 70


# -- #14 whats_new inbox items: people first, stale alerts hidden, paged ------------------------------


def _mail(message_id: str, sender: str, received_at: str, subject: str = "Hello") -> dict[str, Any]:
    return {"message_id": message_id, "sender": sender, "subject": subject, "received_at": received_at}


async def _store(client: AsyncClient, mails: list[dict[str, Any]]) -> None:
    resp = await client.post("/api/applications/record-events", json={"unmatched": mails})
    assert resp.status_code == 200, resp.text
    assert all(r["ok"] for r in resp.json()["unmatched"])


def _iso(now: datetime, days: float) -> str:
    return (now - timedelta(days=days)).isoformat()


@pytest.mark.asyncio
async def test_a_persons_reply_comes_first_and_the_rest_is_grouped(authenticated_async_context):
    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        # The person's reply is stored FIRST (oldest row) and is the oldest mail: it still leads.
        await _store(client, [_mail("p-1", "Richard Samson <richard@talentco.example>", _iso(now, 2),
                                    "Re: agentic AI roles")])
        await _store(client, [
            _mail("a-1", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", _iso(now, 1)),
            _mail("n-1", "Greenhouse <no-reply@greenhouse.io>", _iso(now, 1)),
            _mail("a-2", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", _iso(now, 0.5)),
            _mail("r-1", "Pat Recruiter <messages-noreply@linkedin.com>", _iso(now, 3)),
        ])
        body = (await client.get("/api/whats-new")).json()
    items = body["inbox_items"]
    assert [i["message_id"] for i in items] == ["p-1", "r-1", "a-2", "a-1", "n-1"]
    assert [i["sender_kind"] for i in items] == ["person", "person", "alert", "alert", "automated"]
    page = body["inbox_page"]
    assert page["people"] == 2 and page["total"] == 5 and page["more"] is False and page["next_offset"] is None
    assert page["groups"] == [
        {"sender": "jobalerts-noreply@linkedin.com", "sender_kind": "alert", "count": 2},
        {"sender": "no-reply@greenhouse.io", "sender_kind": "automated", "count": 1},
    ]


@pytest.mark.asyncio
async def test_stale_alerts_are_hidden_by_default_and_reachable(authenticated_async_context):
    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        await _store(client, [
            _mail("old-1", "Indeed <alert@indeed.com>", _iso(now, 30)),
            _mail("old-2", "Glassdoor Jobs <noreply@glassdoor.com>", _iso(now, 20)),
            _mail("new-1", "Indeed <alert@indeed.com>", _iso(now, 1)),
            _mail("old-person", "Jane Hiring <jane@acme.example>", _iso(now, 40)),
        ])
        default = (await client.get("/api/whats-new")).json()
        everything = (await client.get("/api/whats-new", params={"inbox_stale_alerts": "true"})).json()
        open_list = (await client.get("/api/inbox-items")).json()
    assert [i["message_id"] for i in default["inbox_items"]] == ["old-person", "new-1"]  # an old reply stays
    assert default["inbox_page"]["stale_alerts_hidden"] == 2
    assert {i["message_id"] for i in everything["inbox_items"]} == {"old-1", "old-2", "new-1", "old-person"}
    assert everything["inbox_page"]["stale_alerts_hidden"] == 0
    assert open_list["open_count"] == 4  # never deleted: Needs you still lists all four


@pytest.mark.asyncio
async def test_inbox_items_page_with_an_offset(authenticated_async_context):
    from src.core import settings

    now = datetime.now(timezone.utc)
    mails = [_mail(f"m-{i:02d}", f"Person {i} <p{i}@firm{i}.example>", _iso(now, i / 24)) for i in range(15)]
    async with authenticated_async_context() as client:
        await _store(client, mails)
        first = (await client.get("/api/whats-new")).json()
        second = (await client.get(
            "/api/whats-new", params={"inbox_offset": first["inbox_page"]["next_offset"]})).json()
    size = settings.INBOX_ITEMS_WHATS_NEW_PAGE
    assert size == 10
    assert [i["message_id"] for i in first["inbox_items"]] == [f"m-{i:02d}" for i in range(10)]  # newest first
    assert first["inbox_page"] == {**first["inbox_page"], "total": 15, "shown": 10, "more": True, "next_offset": 10}
    assert [i["message_id"] for i in second["inbox_items"]] == [f"m-{i:02d}" for i in range(10, 15)]
    assert second["inbox_page"]["more"] is False and second["inbox_page"]["next_offset"] is None


@pytest.mark.asyncio
async def test_mcp_whats_new_pages_and_lists_stale_alerts(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    now = datetime.now(timezone.utc)
    mcp = await _mcp(authenticated_async_context, fixture_user_id)
    async with mcp_runtime():
        async with mcp as session:
            await _call(session, "record_events", {"unmatched": [
                _mail(f"al-{i:02d}", "Totaljobs <jobs@alerts.totaljobs.com>", _iso(now, 10 + i)) for i in range(12)
            ] + [_mail("rec-1", "Sam Recruiter <sam@hire.example>", _iso(now, 1), "Your CV")]})
            default = await _call(session, "whats_new", {})
            stale = await _call(session, "whats_new", {"inbox_stale_alerts": True})
            page2 = await _call(session, "whats_new", {"inbox_stale_alerts": True, "inbox_offset": 10})
    assert [i["message_id"] for i in default["inbox_items"]] == ["rec-1"]
    assert default["inbox_page"]["stale_alerts_hidden"] == 12
    assert stale["inbox_items"][0]["message_id"] == "rec-1" and stale["inbox_page"]["total"] == 13
    assert stale["inbox_page"]["next_offset"] == 10
    seen = [i["message_id"] for i in stale["inbox_items"] + page2["inbox_items"]]
    assert sorted(seen) == sorted(["rec-1", *(f"al-{i:02d}" for i in range(12))]) and len(seen) == 13


# -- the sender classifier: closed lists, no model, no regex ------------------------------------------


@pytest.mark.parametrize(("sender", "kind"), [
    ("Richard Samson <richard.samson@talentco.example>", "person"),
    ("richard@talentco.example", "person"),
    ("Richard Samson", "person"),  # no address at all: never hidden on a guess
    ("Richard Samson <messages-noreply@linkedin.com>", "person"),  # LinkedIn relays his own message
    ("Acme Hiring <x7f2@indeedemail.com>", "person"),
    ("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "alert"),
    ("Indeed <donotreply@jobalert.indeed.com>", "alert"),
    ("Careers <job-alerts@acme.example>", "alert"),
    ("Weekly <newsletter@startup.example>", "alert"),
    ("Greenhouse <no-reply@greenhouse.io>", "automated"),
    ("Workday <do_not_reply@myworkday.com>", "automated"),
    ("notifications@lever.co", "automated"),
    ("Evil <x@notglassdoor.com>", "person"),  # a look-alike domain is not the alert domain
])
def test_sender_kind(sender: str, kind: str):
    from src.services.applications.inbox_senders import sender_kind

    assert sender_kind(sender) == kind
