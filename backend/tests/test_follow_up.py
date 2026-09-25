"""Follow-up dates (owner decision, 2026-09-25).

Job360 stores a follow-up date and serves "what's due"; the user's OWN agent
(a scheduled ChatGPT/Claude task with its Gmail connector) does the daily
email check and writes back through the existing MCP tools (record_event,
list_applications). Job360 reads no email, runs no worker, sends no push.

Written test-first (rule 5 / TDD): every test here is expected to fail with
an AttributeError/ImportError/404 until the feature lands.
"""
from __future__ import annotations

import datetime as _datetime_module
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _record_event(client: AsyncClient, application_id: int, event_type: str, **extra: Any):
    body = {"event_type": event_type, **extra}
    return await client.post(f"/api/applications/{application_id}/events", json=body)


async def _get_application(client: AsyncClient, application_id: int, **params: Any):
    return await client.get(f"/api/applications/{application_id}", params=params)


async def _list_applications(client: AsyncClient, **params: Any):
    return await client.get("/api/applications", params=params)


def _set_user_timezone(user_id: str, tz: str) -> None:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, user_id))
    conn.commit()
    conn.close()


def _set_last_event_at(application_id: int, iso: str) -> None:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE applications SET last_event_at = ? WHERE id = ?", (iso, application_id))
    conn.commit()
    conn.close()


class _FrozenClock:
    """A minimal freezegun-lite: monkeypatches ``spine``'s ``datetime`` name
    to a subclass whose ``now()`` returns a fixed instant. Everything else
    (``fromisoformat``, arithmetic, …) is inherited from the real class, so
    every OTHER date computation in the module keeps working normally —
    only "what time is it right now" is pinned."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, when: datetime):
        from src.services.applications import spine

        frozen = when

        class _Frozen(_datetime_module.datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return frozen.astimezone(tz) if tz else frozen

        monkeypatch.setattr(spine, "datetime", _Frozen)
        self.when = frozen


# ═══════════════════════════════════════════════════════════════════════════
# record_event: set / clear / omit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_set_stores_value_and_event_payload(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        target = _today_iso()

        resp = await _record_event(client, app_id, "note", detail="chase them", follow_up_on=target)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["follow_up_on"] == target

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == target
        assert detail["follow_up_due"] is True

        event = next(e for e in detail["events"] if e["id"] == body["event_id"])
        assert event["payload"]["follow_up_on"] == target


@pytest.mark.asyncio
async def test_clear_removes_the_slot_and_keeps_both_events(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        target = _today_iso()

        set_resp = await _record_event(client, app_id, "note", detail="chase", follow_up_on=target)
        assert set_resp.status_code == 201, set_resp.text
        clear_resp = await _record_event(client, app_id, "note", detail="cleared", follow_up_on="")
        assert clear_resp.status_code == 201, clear_resp.text
        assert clear_resp.json()["follow_up_on"] is None

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] is None
        assert detail["follow_up_due"] is False
        # Both events survive — clearing is a new row, never a rewrite (M3).
        follow_up_events = [e for e in detail["events"] if "follow_up_on" in e["payload"]]
        assert len(follow_up_events) == 2
        assert follow_up_events[0]["payload"]["follow_up_on"] == target
        assert follow_up_events[1]["payload"]["follow_up_on"] is None


@pytest.mark.asyncio
async def test_omitted_leaves_the_slot_untouched(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        target = _today_iso()
        await _record_event(client, app_id, "note", detail="chase", follow_up_on=target)

        resp = await _record_event(client, app_id, "note", detail="unrelated note")
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] == target

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == target


# ═══════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bad_format_is_422_naming_the_field(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        resp = await _record_event(client, app_id, "note", follow_up_on="3rd October")
        assert resp.status_code == 422, resp.text
        assert "follow_up_on" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_out_of_bounds_is_422_naming_the_setting(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        too_far = (datetime.now(timezone.utc).date() + timedelta(days=1000)).isoformat()
        resp = await _record_event(client, app_id, "note", follow_up_on=too_far)
        assert resp.status_code == 422, resp.text
        assert "APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS" in resp.json()["detail"]

        too_old = (datetime.now(timezone.utc).date() - timedelta(days=1000)).isoformat()
        resp2 = await _record_event(client, app_id, "note", follow_up_on=too_old)
        assert resp2.status_code == 422, resp2.text
        assert "APPLICATION_FOLLOW_UP_MAX_PAST_DAYS" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_a_foreign_application_id_is_404(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _record_event(client, 999_999, "note", follow_up_on=_today_iso())
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_duplicate_source_does_not_touch_the_slot(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        original = _today_iso()
        await _record_event(client, app_id, "note", detail="chase", follow_up_on=original)

        source = {"kind": "email", "message_id": "dup-1", "sender": "a@b.com", "subject": "hi"}
        first = await _record_event(
            client, app_id, "replied", source=source,
            follow_up_on=(datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat(),
        )
        assert first.status_code == 201, first.text
        assert first.json()["already_existed"] is False

        again = await _record_event(
            client, app_id, "replied", source=source,
            follow_up_on=(datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat(),
        )
        assert again.status_code == 201, again.text
        assert again.json()["already_existed"] is True
        # Nothing written — the slot holds whatever the FIRST call set, not
        # the (never-applied) date the duplicate call asked for.
        first_value = first.json()["follow_up_on"]
        assert again.json()["follow_up_on"] == first_value

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == first_value


# ═══════════════════════════════════════════════════════════════════════════
# list_applications: due / quiet_days
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_due_filter_uses_the_users_own_timezone(authenticated_async_context, fixture_user_id, monkeypatch):
    # A frozen instant where the UTC calendar date and Auckland's differ —
    # computed from the SAME instant, never a hardcoded guess about DST.
    frozen = datetime(2026, 9, 25, 20, 0, 0, tzinfo=timezone.utc)
    auckland_today = frozen.astimezone(ZoneInfo("Pacific/Auckland")).date()
    utc_today = frozen.date()
    assert auckland_today != utc_today, "test instant must straddle a day boundary — pick another hour"

    _FrozenClock(monkeypatch, frozen)
    _set_user_timezone(fixture_user_id, "Pacific/Auckland")

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        # follow_up_on = Auckland's today. A UTC-naive implementation would
        # see this as tomorrow (utc_today < auckland_today) and exclude it.
        await _record_event(client, app_id, "note", follow_up_on=auckland_today.isoformat())

        resp = await _list_applications(client, due=True)
        assert resp.status_code == 200, resp.text
        ids = [a["id"] for a in resp.json()["applications"]]
        assert app_id in ids


@pytest.mark.asyncio
async def test_due_excludes_closed_statuses(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _record_event(client, app_id, "note", follow_up_on=_today_iso())
        closed = await _record_event(client, app_id, "rejected")
        assert closed.status_code == 201, closed.text

        resp = await _list_applications(client, due=True)
        assert resp.status_code == 200, resp.text
        ids = [a["id"] for a in resp.json()["applications"]]
        assert app_id not in ids


@pytest.mark.asyncio
async def test_due_orders_soonest_first(authenticated_async_context):
    async with authenticated_async_context() as client:
        # normalized_key() dedupes on (company, title) — NOT the URL (rule #1)
        # — so these must differ by title to land as two distinct jobs/applications.
        soon_ad = {**_AD, "title": "Data Engineer (soon)", "apply_url": "https://northwind.example/careers/soon"}
        later_ad = {**_AD, "title": "Data Engineer (later)", "apply_url": "https://northwind.example/careers/later"}
        soon_id = (await _bring(client, soon_ad))["application_id"]
        later_id = (await _bring(client, later_ad))["application_id"]

        # Both must actually be DUE (<= today) for "soonest first" to mean
        # anything — an application whose date hasn't arrived yet doesn't
        # show up in the `due` list at all (that is
        # test_due_excludes_closed_statuses' sibling case, covered by the
        # plain "set stores value" test's follow_up_due assertion).
        today = datetime.now(timezone.utc).date()
        await _record_event(client, later_id, "note", follow_up_on=(today - timedelta(days=1)).isoformat())
        await _record_event(client, soon_id, "note", follow_up_on=(today - timedelta(days=5)).isoformat())

        resp = await _list_applications(client, due=True)
        ids = [a["id"] for a in resp.json()["applications"]]
        assert ids.index(soon_id) < ids.index(later_id)


@pytest.mark.asyncio
async def test_quiet_days_filter(authenticated_async_context):
    async with authenticated_async_context() as client:
        # See test_due_orders_soonest_first — normalized_key() dedupes on
        # (company, title), so these need different titles to be two jobs.
        quiet_ad = {**_AD, "title": "Data Engineer (quiet)", "apply_url": "https://n.example/quiet"}
        fresh_ad = {**_AD, "title": "Data Engineer (fresh)", "apply_url": "https://n.example/fresh"}
        quiet_id = (await _bring(client, quiet_ad))["application_id"]
        fresh_id = (await _bring(client, fresh_ad))["application_id"]

        old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _set_last_event_at(quiet_id, old_iso)

        resp = await _list_applications(client, quiet_days=7)
        assert resp.status_code == 200, resp.text
        ids = [a["id"] for a in resp.json()["applications"]]
        assert quiet_id in ids
        assert fresh_id not in ids


@pytest.mark.asyncio
async def test_quiet_days_over_cap_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _list_applications(client, quiet_days=100_000)
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_offer_stays_open_for_due_and_quiet_days(authenticated_async_context):
    """Coordinator correction (2026-09-25): APPLICATION_FOLLOW_UP_CLOSED_STATUSES
    is rejected/withdrawn/ghosted ONLY — there is no "accepted"/"hired" status
    in the real vocabulary, and `offer` stays OPEN (an offer still has a reply
    deadline, so it must keep showing up as due and as gone-quiet)."""
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        offer = await _record_event(client, app_id, "offer")
        assert offer.status_code == 201, offer.text

        # due: still open, so a follow-up on it still surfaces.
        await _record_event(client, app_id, "note", follow_up_on=_today_iso())
        due_resp = await _list_applications(client, due=True)
        assert app_id in [a["id"] for a in due_resp.json()["applications"]]

        # quiet_days: an untouched offer is exactly the case a seeker needs
        # nudging on — it must NOT be silently excluded for having moved past
        # "applied".
        old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _set_last_event_at(app_id, old_iso)
        quiet_resp = await _list_applications(client, quiet_days=7)
        assert app_id in [a["id"] for a in quiet_resp.json()["applications"]]


@pytest.mark.asyncio
async def test_quiet_days_covers_every_open_status_not_just_applied(authenticated_async_context):
    """Coordinator correction (2026-09-25): the quiet_days check must not be
    scoped to `status == "applied"` — an interview in progress that has gone
    quiet is exactly as worth flagging."""
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        moved = await _record_event(client, app_id, "interview_requested")
        assert moved.status_code == 201, moved.text
        assert moved.json()["status"] == "interview_requested"

        old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _set_last_event_at(app_id, old_iso)

        resp = await _list_applications(client, quiet_days=7)
        assert resp.status_code == 200, resp.text
        assert app_id in [a["id"] for a in resp.json()["applications"]]


def test_closed_statuses_are_exactly_rejected_withdrawn_ghosted():
    """Coordinator correction (2026-09-25) — pin the exact set: no
    "accepted"/"hired" status exists in APPLICATION_STATUS_EVENT_TYPES, so it
    can never be added here as if it did."""
    from src.core import settings

    assert set(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES) == {"rejected", "withdrawn", "ghosted"}
    assert "offer" not in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES
    assert not {"accepted", "hired"} & set(settings.APPLICATION_STATUS_EVENT_TYPES)


# next_step's `follow_up` code is pinned in test_next_step.py's parametrized
# table (owner decision, 2026-09-25 rows) — not duplicated here.


# ═══════════════════════════════════════════════════════════════════════════
# Timezone route
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_timezone_route_rejects_unknown_zone(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.put("/api/auth/me/timezone", json={"timezone": "Mars/Olympus"})
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_timezone_route_stores_a_valid_zone(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.put("/api/auth/me/timezone", json={"timezone": "Europe/London"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["timezone"] == "Europe/London"

        me = await client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["timezone"] == "Europe/London"


# ═══════════════════════════════════════════════════════════════════════════
# MCP parity — record_event(follow_up_on=...) + list_applications(due=True)
# ═══════════════════════════════════════════════════════════════════════════

pytest.importorskip("mcp")


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result) -> dict:
    import json as _json

    assert not result.is_error, result.content[0].text
    return _json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_record_event_and_list_applications_due_parity(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token_resp = await client.post("/api/tokens", json={"name": "agent"})
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

        bring_resp = await client.post("/api/jobs/bring", json=_AD)
        application_id = bring_resp.json()["application_id"]

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            recorded = _payload(
                await mcp.call_tool(
                    "record_event",
                    {
                        "application_id": application_id,
                        "event_type": "note",
                        "detail": "recruiter promised news",
                        "follow_up_on": _today_iso(),
                    },
                )
            )
            assert recorded["follow_up_on"] == _today_iso()

            listed = _payload(await mcp.call_tool("list_applications", {"due": True}))
            ids = [a["id"] for a in listed["applications"]]
            assert application_id in ids
            row = next(a for a in listed["applications"] if a["id"] == application_id)
            assert row["follow_up_on"] == _today_iso()
            assert row["follow_up_due"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Coordinator review (2026-09-25) — 4 real bugs, tests written first
# ═══════════════════════════════════════════════════════════════════════════

# ── Bug 1 (P1): MCP list_applications bypasses the Query ge/le bounds on
# quiet_days — mcp_server.py calls the route FUNCTION directly, never through
# FastAPI's request parsing, so Query(ge=1, le=...) never runs for that path.
# quiet_days=0 silently flags everything; an absurd value underflows
# `datetime.now() - timedelta(days=...)` past `datetime.min` (OverflowError,
# not a readable error). Fix belongs in spine.list_applications itself, the
# one place both HTTP and MCP funnel through.


@pytest.mark.asyncio
async def test_mcp_list_applications_rejects_quiet_days_zero(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token_resp = await client.post("/api/tokens", json={"name": "agent"})
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("list_applications", {"quiet_days": 0})
            assert result.is_error, "quiet_days=0 bypasses Query(ge=1) via MCP — must still be rejected"


@pytest.mark.asyncio
async def test_mcp_list_applications_rejects_quiet_days_over_cap_without_crashing(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token_resp = await client.post("/api/tokens", json={"name": "agent"})
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("list_applications", {"quiet_days": 10_000_000})
            assert result.is_error, "an absurd quiet_days must read as a normal error, never an OverflowError crash"
            assert "APPLICATION_QUIET_DAYS_MAX" in result.content[0].text


# ── Bug 2 (owner decision): news that moves the application's status clears
# an OVERDUE follow-up automatically (never a future one), unless the caller
# explicitly named a follow_up_on on the same call. Note/fit/artifact events
# never touch it.


@pytest.mark.asyncio
async def test_overdue_follow_up_auto_clears_on_a_status_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        overdue = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
        await _record_event(client, app_id, "note", follow_up_on=overdue)

        resp = await _record_event(client, app_id, "interview_requested")
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] is None

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] is None
        assert detail["follow_up_due"] is False
        assert detail["next_step"]["code"] != "follow_up"

        clearing_event = next(e for e in detail["events"] if e["event_type"] == "interview_requested")
        assert clearing_event["payload"]["follow_up_on"] is None


@pytest.mark.asyncio
async def test_future_follow_up_is_not_touched_by_a_status_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        future = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
        await _record_event(client, app_id, "note", follow_up_on=future)

        resp = await _record_event(client, app_id, "replied")
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] == future

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == future


@pytest.mark.asyncio
async def test_explicit_follow_up_on_wins_over_auto_clear(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        overdue = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
        await _record_event(client, app_id, "note", follow_up_on=overdue)

        new_date = _today_iso()
        resp = await _record_event(client, app_id, "interview_requested", follow_up_on=new_date)
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] == new_date

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == new_date


@pytest.mark.asyncio
async def test_a_note_event_never_auto_clears_an_overdue_follow_up(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        overdue = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
        await _record_event(client, app_id, "note", follow_up_on=overdue)

        resp = await _record_event(client, app_id, "note", detail="unrelated")
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] == overdue

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == overdue


# ── Bug 3 (P2): correcting the event that set a follow-up date must revert
# the slot (same replay idea `replay_status` already uses for status), when
# the correcting call itself doesn't name a follow_up_on.


@pytest.mark.asyncio
async def test_correcting_the_only_event_that_set_a_date_reverts_to_none(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        target = _today_iso()
        set_resp = await _record_event(client, app_id, "note", detail="chase", follow_up_on=target)
        assert set_resp.status_code == 201, set_resp.text
        event_id = set_resp.json()["event_id"]

        correct_resp = await _record_event(
            client, app_id, "note", detail="oops, wrong date", corrects_event_id=event_id
        )
        assert correct_resp.status_code == 201, correct_resp.text
        assert correct_resp.json()["follow_up_on"] is None

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] is None


@pytest.mark.asyncio
async def test_correcting_the_latest_setter_reverts_to_the_previous_date(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        first = _today_iso()
        second = (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat()
        await _record_event(client, app_id, "note", detail="chase", follow_up_on=first)
        second_resp = await _record_event(client, app_id, "note", detail="reschedule", follow_up_on=second)
        assert second_resp.status_code == 201, second_resp.text
        second_event_id = second_resp.json()["event_id"]

        correct_resp = await _record_event(
            client, app_id, "note", detail="undo reschedule", corrects_event_id=second_event_id
        )
        assert correct_resp.status_code == 201, correct_resp.text
        assert correct_resp.json()["follow_up_on"] == first

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] == first


# ── Bug 4 (P2): the caller's own free-form `payload` must never be the
# door for `follow_up_on` — it silently lands in history while the slot (and
# `follow_up_due`) stay untouched, which reads as "Job360 dropped it".


@pytest.mark.asyncio
async def test_follow_up_on_inside_payload_is_rejected(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        resp = await _record_event(client, app_id, "note", payload={"follow_up_on": _today_iso()})
        assert resp.status_code == 422, resp.text
        assert "payload" in resp.json()["detail"]

        detail = (await _get_application(client, app_id)).json()
        assert detail["follow_up_on"] is None


@pytest.mark.asyncio
async def test_mcp_follow_up_on_inside_payload_is_rejected(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token_resp = await client.post("/api/tokens", json={"name": "agent"})
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

        bring_resp = await client.post("/api/jobs/bring", json=_AD)
        application_id = bring_resp.json()["application_id"]

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool(
                "record_event",
                {
                    "application_id": application_id,
                    "event_type": "note",
                    "payload": {"follow_up_on": _today_iso()},
                },
            )
            assert result.is_error
