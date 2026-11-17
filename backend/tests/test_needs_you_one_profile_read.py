"""One Needs-you badge request reads the profile ONCE (2026-11-17, #1622-#1624 merged).

Production, large owner account, ``GET /api/needs-you-count``: 1058-1277 ms. ``pgsync.connect()`` opens a NEW
Postgres connection per call, and the badge opened six of them for profile data alone: ``user_score_line``, the
blockers' own read, ``blockers._auto_states``, ``own_browser.pc_idle``, the questionnaire count and, when an
``otp_needed`` block is open, ``blocked.codes_mine``. They all want the same profile (with the overlay), so
``derive_memo`` now holds the request's one read (``profile_or_load``) and each reader reuses it.

The timing is not asserted (it flakes): the CONNECTION count is, the body must equal the unshared reading byte
for byte, one request never sees another user's profile, and the reads that remain run off the event loop.
"""
from __future__ import annotations

import json
import threading
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient

from tests.test_needs_you_derive_memo import _body, _seed, _store

_AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB",
       "description": "Build the pipelines."}


class _Connects:
    """Every new sync Postgres connection (``pgsync.connect`` -> ``psycopg.connect``), with its thread."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.threads: list[int] = []
        real = psycopg.connect

        def counting(*args: Any, **kwargs: Any) -> Any:
            self.threads.append(threading.get_ident())
            return real(*args, **kwargs)

        monkeypatch.setattr(psycopg, "connect", counting)

    def __len__(self) -> int:
        return len(self.threads)


async def _edit(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type,
                                                                          "payload": payload or {}})
    assert resp.status_code == 201, resp.text


async def _draft(client: AsyncClient, n: int, send_click: bool) -> None:
    """One applied job with one LinkedIn draft; ``send_click`` queues it for the user's own browser (your_pc)."""
    resp = await client.post("/api/jobs/bring", json={**_AD, "title": f"Engineer {100 + n}",
                                                      "apply_url": f"https://northwind.example/c/{n}"})
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    added = await client.post(f"/api/applications/{app_id}/contacts", json={
        "name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"})
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    draft = await client.post(f"/api/applications/{app_id}/artifacts", json={
        "kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"})
    assert draft.status_code == 201, draft.text
    await _event(client, app_id, "applied")
    if send_click:
        person = await client.get(f"/api/people?contact_id={contact_id}")
        message_id = int(person.json()["person"]["outreach"]["messages"][-1]["id"])
        click = await client.post(f"/api/contacts/{contact_id}/own-browser-send",
                                  json={"message_id": message_id, "decision": "approve"})
        assert click.status_code == 200 and click.json()["state"] == "queued", click.text


async def _big_account(client: AsyncClient, uid: str) -> None:
    """Every reader on the badge path has something to read: queued jobs with fills and asks, a waiting setting
    request, outreach on auto (an unsent note + one waiting for the PC), and an open ``otp_needed`` block."""
    from src.services.profile import setting_requests

    _store(uid)
    await _seed(client, 0, 4)
    await _edit(client, "assistant_settings.use_email_codes", True)
    await _edit(client, "assistant_settings.outreach_send", "auto")
    await _edit(client, "preferences.daily_check", "auto")
    setting_requests.create_requests(uid, "token:claude-code", [("assistant_settings.apply_min_score", 10)])
    resp = await client.get("/api/applications")
    first = int((resp.json()["applications"] if isinstance(resp.json(), dict) else resp.json())[0]["id"])
    await _event(client, first, "blocked", {"reason": "otp_needed", "step": "code box"})
    await _draft(client, 0, send_click=False)
    await _draft(client, 1, send_click=True)


@pytest.mark.asyncio
async def test_one_badge_request_opens_two_sync_connections_not_six(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        await _big_account(client, uid)
        connects = _Connects(monkeypatch)
        body = await _body(client)
    # value-presence: every reader really ran (rule #21)
    assert body["answer_once"] >= 1 and body["by_actor"]["your_pc"] >= 1, body
    # ONE profile read (with the overlay) + ``setting_requests.list_waiting``'s own table read, which lives in
    # services/profile (owner-merge lane) and takes a connection it opens itself. Before: 6 + 1.
    assert len(connects) <= 2, f"{len(connects)} sync connections for one badge request"


@pytest.mark.asyncio
async def test_the_whole_body_is_the_same_with_the_sharing_off(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.applications import derive_memo

    uid = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        await _big_account(client, uid)
        shared = await _body(client)
        monkeypatch.setattr(derive_memo, "ENABLED", False)
        unshared = await _body(client)
    assert json.dumps(shared, sort_keys=True) == json.dumps(unshared, sort_keys=True)
    assert shared["answer_once"] >= 1 and shared["other"] >= 1 and shared["by_actor"]["your_pc"] >= 1


@pytest.mark.asyncio
async def test_a_scope_never_hands_one_users_profile_to_another(authenticated_async_context: Any) -> None:
    from src.services.applications import derive_memo
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    for who, name in (("user-a", "Ada"), ("user-b", "Bea")):
        save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name=name)), who, source_action="cv_upload")
    with derive_memo.scope("user-a"):
        a = await derive_memo.profile_or_load("user-a")
        assert a is not None and a.cv_data.name == "Ada"
        assert await derive_memo.profile_or_load("user-a") is a  # the same object: no second read
        b = await derive_memo.profile_or_load("user-b")  # another user in the same scope: its own read, never A's
        assert b is not None and b.cv_data.name == "Bea"
        assert (await derive_memo.profile_or_load("user-a")) is a  # B's read did not replace A's
    with derive_memo.scope("user-b"):
        again = await derive_memo.profile_or_load("user-b")
        assert again is not None and again.cv_data.name == "Bea" and again is not a
    assert derive_memo.current() is None


@pytest.mark.asyncio
async def test_the_remaining_profile_reads_run_off_the_event_loop(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        await _big_account(client, uid)
        connects = _Connects(monkeypatch)
        await _body(client)
    assert connects.threads, "the badge opened no connection: the spy never ran"
    assert threading.get_ident() not in connects.threads, "a sync Postgres read ran on the event-loop thread"


@pytest.mark.asyncio
async def test_the_profile_is_read_once_even_when_the_badge_has_nothing_queued(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No queued job: blockers read none, so the questionnaire count is the first reader and loads it for the rest."""
    uid = authenticated_async_context.fixture_user_id
    _store(uid)
    async with authenticated_async_context() as client:
        connects = _Connects(monkeypatch)
        resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    assert "answer_once;dur=" in resp.headers.get("server-timing", "")  # the questionnaire count ran
    assert isinstance(resp.json()["n"], int)
    assert len(connects) <= 2, f"{len(connects)} sync connections"
