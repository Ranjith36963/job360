"""PC run pc-0911 (2026-11-18), two more findings.

a. The recipe kept the LinkedIn safety state (alert cursor, page counts, the 150-a-UK-day cap, LINKEDIN_PAUSED_UNTIL)
   in ``~/li_state.json``; that run's ``~`` was /root in a container wiped each run, so the cap could not hold. The
   state now lives server-side, one slot per user (``own_browser:li_state`` in ``assistant_duty_runs``), read and
   written with ``own_browser_queue`` actions ``state`` / ``save_state``.
b. ``take`` handed out ``duty:linkedin_search`` while 7 owner-clicked sends waited, and said nothing about them. The
   answer now carries ``held_back {people, why, until}`` naming the gate that held them.

Real rows through the real doors (rule #21); helpers copied, never imported from another test module.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.repositories.database import JobDatabase
from src.services.applications import own_browser

HUNT = "assistant_settings.hunt_every_hours"
_AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
       "description": "Build the pipelines."}
RECIPE = Path(__file__).resolve().parents[1] / "src" / "recipes" / "own_browser.md"


# ── helpers ──────────────────────────────────────────────────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _hunt_on(client: AsyncClient) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 1}]})
    assert resp.status_code == 200, resp.text


async def _clicked_person(client: AsyncClient, n: int) -> int:
    """A LinkedIn contact with a drafted message the owner pressed Send on; returns the contact id."""
    brought = await client.post("/api/jobs/bring", json={**_AD, "title": f"Engineer {n}",
                                                          "apply_url": f"https://northwind.example/c/{n}"})
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    added = await client.post(f"/api/applications/{app_id}/contacts", json={
        "name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"})
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    made = await client.post(f"/api/applications/{app_id}/artifacts", json={
        "kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"})
    assert made.status_code == 201, made.text
    got = await client.get(f"/api/people?contact_id={contact_id}")
    message_id = int(got.json()["person"]["outreach"]["messages"][-1]["id"])
    click = await client.post(f"/api/contacts/{contact_id}/own-browser-send",
                              json={"message_id": message_id, "decision": "approve"})
    assert click.status_code == 200, click.text
    return contact_id


async def _take(agent: AsyncClient, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(agent: AsyncClient, task_id: str, reason: str, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/release", json={"session": session, "task_id": task_id, "reason": reason})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sent_mark(user_id: str, contact_id: int, at: datetime) -> None:
    """A LinkedIn ``sent`` row for this person at ``at`` (what record_event outreach_sent stores)."""
    stamp = at.astimezone(timezone.utc).isoformat()
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(
        "INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, version_no, occurred_at, "
        "recorded_at, recorded_by, source_message_id, label) VALUES (?, ?, 'sent', 'linkedin', '', NULL, ?, ?, "
        "'assistant', '', '')",
        (user_id, contact_id, stamp, stamp),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def audit_capture():
    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[dict[str, Any]] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(dict(record.__dict__))

    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


# ── a. the LinkedIn state lives server-side ──────────────────────────────────


@pytest.mark.asyncio
async def test_li_state_round_trips_through_the_route(authenticated_async_context):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        empty = await agent.get("/api/own-browser/state")
        assert empty.status_code == 200, empty.text
        assert empty.json() == {"state": {}, "saved_at": None}
        state = {"alert_cursor": "urn:li:alert:42", "pages_today": 17, "uk_day": "2026-11-18",
                 "LINKEDIN_PAUSED_UNTIL": "2026-11-18T15:00:00+00:00"}
        saved = await agent.put("/api/own-browser/state", json={"state": state})
        assert saved.status_code == 200, saved.text
        assert saved.json()["state"] == state and saved.json()["saved_at"]
        again = (await agent.get("/api/own-browser/state")).json()
        assert again["state"] == state  # value-presence: the stored cursor and count come back (rule #21)
        newer = await agent.put("/api/own-browser/state", json={"state": {**state, "pages_today": 18}})
        assert newer.status_code == 200
        assert (await agent.get("/api/own-browser/state")).json()["state"]["pages_today"] == 18


@pytest.mark.asyncio
async def test_save_state_over_8_kb_is_422_and_keeps_the_old_state(authenticated_async_context):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        assert (await agent.put("/api/own-browser/state", json={"state": {"pages_today": 3}})).status_code == 200
        big = {"blob": "x" * (own_browser.LI_STATE_MAX_BYTES + 1)}
        resp = await agent.put("/api/own-browser/state", json={"state": big})
        assert resp.status_code == 422, resp.text
        assert (await agent.get("/api/own-browser/state")).json()["state"] == {"pages_today": 3}
        not_object = await agent.put("/api/own-browser/state", json={"state": ["a", "list"]})
        assert not_object.status_code == 422


@pytest.mark.asyncio
async def test_li_state_is_scoped_by_user_exported_and_erased(migrated_db_path):
    db = JobDatabase(migrated_db_path)
    await db.init_db()
    try:
        users = []
        for _ in range(2):
            uid = str(uuid.uuid4())
            await db._conn.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, 'h')",
                                   (uid, f"{uuid.uuid4().hex}@example.com"))
            users.append(uid)
        await db._conn.commit()
        mine, theirs = users
        now = datetime.now(timezone.utc)
        await own_browser.save_li_state(db, mine, {"alert_cursor": "c-mine", "pages_today": 5}, now)
        assert (await own_browser.load_li_state(db, mine))["state"] == {"alert_cursor": "c-mine", "pages_today": 5}
        assert (await own_browser.load_li_state(db, theirs)) == {"state": {}, "saved_at": None}

        out = await db.export_user_data(mine)
        rows = [r for r in out["assistant_duty_runs"] if r["duty"] == own_browser.LI_STATE_SLOT]
        assert len(rows) == 1 and json.loads(rows[0]["state"]) == {"alert_cursor": "c-mine", "pages_today": 5}
        assert "c-mine" not in json.dumps((await db.export_user_data(theirs))["assistant_duty_runs"])

        await db.hard_delete_user(mine)
        cur = await db._conn.execute("SELECT COUNT(*) FROM assistant_duty_runs WHERE user_id = ?", (mine,))
        assert (await cur.fetchone())[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_li_state_slot_is_not_a_pc_activity_sign_nor_a_scheduled_duty(migrated_db_path):
    """A saved state is bookkeeping: it never makes the PC look alive (last_pc_activity_at) and whats_due never
    reads it as a duty."""
    db = JobDatabase(migrated_db_path)
    await db.init_db()
    try:
        uid = str(uuid.uuid4())
        await db._conn.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, 'h')",
                               (uid, f"{uuid.uuid4().hex}@example.com"))
        await db._conn.commit()
        await own_browser.save_li_state(db, uid, {"pages_today": 1}, datetime.now(timezone.utc))
        assert await own_browser.last_pc_activity_at(db, uid) is None
        assert own_browser.LI_STATE_SLOT not in settings.SCHEDULE_DUTIES
    finally:
        await db.close()


def test_pause_lets_the_linkedin_state_through_but_not_a_take():
    """Pause stops work, not bookkeeping: state / save_state pass like release; take is still refused."""
    from src.api.mcp_server import pause_blocks

    assert not pause_blocks("own_browser_queue", {"action": "state"})
    assert not pause_blocks("own_browser_queue", {"action": "save_state", "state": {"pages_today": 1}})
    assert pause_blocks("own_browser_queue", {"action": "take", "session": "pc"})


def test_recipe_keeps_the_linkedin_state_in_job360_not_in_a_home_file():
    text = RECIPE.read_text(encoding="utf-8")
    assert "li_state.json" not in text and "~/" not in text
    assert 'own_browser_queue(action="state")' in text
    assert 'own_browser_queue(action="save_state"' in text
    assert "py or python (never python3 on Windows)" in text
    assert "python3 or py" not in text
    assert len(text.splitlines()) <= 70


# ── b. take says which owner-clicked people it held back, and why ────────────


@pytest.fixture
def _open(monkeypatch):
    async def window(_db, _user_id, _now):
        return {"open_now": True, "why": "any_time", "opens_at": None}

    monkeypatch.setattr(own_browser, "window", window)


@pytest.mark.asyncio
async def test_duty_with_a_person_in_release_backoff_says_held_back(
    authenticated_async_context, fixture_user_id, audit_capture, _open
):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        contact_id = await _clicked_person(client, 1)
        async with await _agent(client) as agent:
            first = await _take(agent)
            assert first["task"]["contact_id"] == contact_id
            await _release(agent, f"contact:{contact_id}", "other")
            got = await _take(agent)
            assert got["status"] == "task" and got["task"]["kind"] == "linkedin_search", got  # take order unchanged
            held = got["held_back"]
            assert (held["people"], held["why"]) == (1, "release_backoff"), held
            until = datetime.fromisoformat(held["until"])
            left = (until - datetime.now(timezone.utc)).total_seconds()
            assert 0 < left <= own_browser.RELEASE_BACKOFF_MINUTES * 60
    lines = [r for r in audit_capture.records if r.get("event") == "own_browser_take_held_back"]
    assert lines and lines[-1]["people"] == 1 and lines[-1]["why"] == "release_backoff"
    assert lines[-1]["session"] == "pc" and lines[-1]["user_id"]


@pytest.mark.asyncio
async def test_duty_while_another_session_holds_a_person_says_claimed_elsewhere(
    authenticated_async_context, fixture_user_id, _open
):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        await _clicked_person(client, 1)
        await _clicked_person(client, 2)
        async with await _agent(client) as agent:
            assert (await _take(agent, "pc-a"))["task"]["kind"] == "linkedin_outreach"
            got = await _take(agent, "pc-b")
            assert got["task"]["kind"] == "linkedin_search", got
            held = got["held_back"]
            assert (held["people"], held["why"]) == (2, "claimed_elsewhere"), held
            assert held["until"] is not None  # the other session's claim ends then


@pytest.mark.asyncio
async def test_duty_inside_the_send_gap_says_pace_gap(authenticated_async_context, fixture_user_id, _open):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        sent_to = await _clicked_person(client, 1)
        await _clicked_person(client, 2)
        _sent_mark(fixture_user_id, sent_to, datetime.now(timezone.utc) - timedelta(seconds=30))
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["task"]["kind"] == "linkedin_search", got
            held = got["held_back"]
            assert (held["people"], held["why"]) == (1, "pace_gap"), held
            left = (datetime.fromisoformat(held["until"]) - datetime.now(timezone.utc)).total_seconds()
            assert 0 < left <= own_browser.LINKEDIN_MIN_GAP_SECONDS


@pytest.mark.asyncio
async def test_duty_at_the_daily_max_says_daily_max(authenticated_async_context, fixture_user_id, monkeypatch, _open):
    from src.services.profile import assistant_settings as rules

    monkeypatch.setattr(own_browser, "LINKEDIN_MIN_GAP_SECONDS", 0)
    monkeypatch.setattr(rules, "linkedin_daily_max", lambda _cfg: 1)
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        sent_to = await _clicked_person(client, 1)
        await _clicked_person(client, 2)
        _sent_mark(fixture_user_id, sent_to, datetime.now(timezone.utc))
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["task"]["kind"] == "linkedin_search", got
            held = got["held_back"]
            assert (held["people"], held["why"]) == (1, "daily_max"), held
            assert datetime.fromisoformat(held["until"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_empty_under_the_weekly_limit_says_weekly_limit(authenticated_async_context, fixture_user_id, _open):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        first = await _clicked_person(client, 1)
        await _clicked_person(client, 2)
        async with await _agent(client) as agent:
            assert (await _take(agent))["task"]["contact_id"] == first
            await _release(agent, f"contact:{first}", "weekly_limit")
            got = await _take(agent)
            assert got["status"] == "empty", got
            held = got["held_back"]
            assert (held["people"], held["why"]) == (2, "weekly_limit"), held
            left = datetime.fromisoformat(held["until"]) - datetime.now(timezone.utc)
            assert timedelta(days=6, hours=23) < left <= timedelta(days=7)


@pytest.mark.asyncio
async def test_no_held_back_when_no_owner_clicked_person_waits(authenticated_async_context, fixture_user_id, _open):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["task"]["kind"] == "linkedin_search"
            assert got.get("held_back") is None
