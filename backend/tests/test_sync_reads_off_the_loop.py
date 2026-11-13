"""Sync Postgres reads must not run ON the event loop (2026-11-13, home page ~23 blocking connects).

Production runs ONE uvicorn process and ``pgsync.connect()`` opens a new connection per call, so a sync profile
read inside an ``async def`` freezes every other request while it waits. Each test wraps the real sync function
with a recorder of ``threading.get_ident()`` and asserts it ran on a thread other than the loop's.

The last two tests assert the OPPOSITE for the two reads a write depends on (take back, confirm): they must
stay on the loop, because an await between the read and its write lets an assistant write land in the gap.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {"title": "AI Engineer", "company": "AppZen", "location": "London", "country": "GB",
       "apply_url": "https://jobs.lever.co/appzen/1", "description": "Build agents. " * 8}


def _recorded(real: Callable[..., Any], seen: list[int]) -> Callable[..., Any]:
    def stub(*args: Any, **kwargs: Any) -> Any:
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    return stub


def _assert_off_loop(seen: list[int]) -> None:
    assert seen, "the sync function was never called"
    assert threading.get_ident() not in seen, "a sync Postgres read ran on the event-loop thread"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["settings_view", "load_profile"])
async def test_assistant_settings_read_is_off_the_loop(authenticated_async_context, monkeypatch, name):
    from src.api.routes import assistant_settings as mod

    seen: list[int] = []
    monkeypatch.setattr(mod, name, _recorded(getattr(mod, name), seen))
    async with authenticated_async_context() as client:
        assert (await client.get("/api/assistant-settings")).status_code == 200
    _assert_off_loop(seen)


@pytest.mark.asyncio
async def test_assistant_settings_history_is_off_the_loop(authenticated_async_context, monkeypatch):
    from src.services.profile import edits

    seen: list[int] = []
    monkeypatch.setattr(edits, "path_history", _recorded(edits.path_history, seen))
    async with authenticated_async_context() as client:
        resp = await client.get("/api/assistant-settings/history", params={"path": "assistant_settings.daily_cap"})
        assert resp.status_code == 200, resp.text
    _assert_off_loop(seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["settings_view", "load_profile"])
async def test_morning_check_settings_read_is_off_the_loop(authenticated_async_context, monkeypatch, name):
    from src.api.routes import morning_check as mod

    seen: list[int] = []
    monkeypatch.setattr(mod, name, _recorded(getattr(mod, name), seen))
    async with authenticated_async_context() as client:
        assert (await client.get("/api/morning-check")).status_code == 200
    _assert_off_loop(seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(("module", "name"), [("setting_requests", "list_waiting"), ("ask_once", "open_questions")])
async def test_needs_you_count_reads_are_off_the_loop(authenticated_async_context, monkeypatch, module, name):
    from src.services.profile import ask_once, setting_requests

    owner = {"setting_requests": setting_requests, "ask_once": ask_once}[module]
    seen: list[int] = []
    monkeypatch.setattr(owner, name, _recorded(getattr(owner, name), seen))
    async with authenticated_async_context() as client:
        assert (await client.get("/api/needs-you-count")).status_code == 200
    _assert_off_loop(seen)


@pytest.mark.asyncio
async def test_needs_you_count_still_times_the_two_phases(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/needs-you-count")
    timing = resp.headers.get("server-timing", "")
    assert "settings;dur=" in timing and "answer_once;dur=" in timing, timing


@pytest.mark.asyncio
async def test_list_applications_profile_read_is_off_the_loop(authenticated_async_context, monkeypatch):
    """SPINE's own read. ``visa.user_work_countries`` reads the same function in the same request, so it is
    stubbed out here (it has its own test below) and every remaining call must be spine's."""
    from src.services.applications import visa
    from src.services.profile import storage

    async def _no_visa_read(db: Any, user_id: str) -> Any:
        return visa.WorkCountries()

    monkeypatch.setattr(visa, "user_work_countries", _no_visa_read)
    seen: list[int] = []
    monkeypatch.setattr(storage, "load_profile", _recorded(storage.load_profile, seen))
    async with authenticated_async_context() as client:
        assert (await client.get("/api/applications")).status_code == 200
    _assert_off_loop(seen)


@pytest.mark.asyncio
async def test_visa_work_countries_read_is_off_the_loop(authenticated_async_context, monkeypatch,
                                                        fixture_user_id):
    """The second profile read on the applications list (``spine.py:3003`` -> ``visa.py``). Called directly,
    so the assert names this one call site and not whatever else the route reads."""
    from src.api import dependencies as api_deps
    from src.services.applications import visa
    from src.services.profile import storage

    async with authenticated_async_context():
        seen: list[int] = []
        monkeypatch.setattr(storage, "load_profile", _recorded(storage.load_profile, seen))
        assert await visa.user_work_countries(await api_deps.get_db(), fixture_user_id) == []
    _assert_off_loop(seen)


@pytest.mark.asyncio
async def test_asks_saved_answers_read_is_off_the_loop(authenticated_async_context, monkeypatch):
    from src.api.main import app
    from src.services.profile import storage

    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            made = await agent.post("/api/asks", json={"question": "AppZen form", "application_id": app_id,
                                                       "kind": "question", "questions": [
                {"key": "city", "question": "City?", "source": "guess", "answer": "London"}]})
            assert made.status_code == 201, made.text
        seen: list[int] = []
        monkeypatch.setattr(storage, "load_profile", _recorded(storage.load_profile, seen))
        assert (await client.get("/api/asks")).status_code == 200
    _assert_off_loop(seen)


@pytest.mark.asyncio
async def test_memory_closes_profile_read_is_off_the_loop(authenticated_async_context, monkeypatch,
                                                          fixture_user_id):
    """``GET /asks`` also runs ``memory_closes.close_covered``, whose overlay read is sync. Safe off the loop
    although writes follow: every write it makes is guarded in the database (``asks.withdraw_ask`` raises 409
    when the row is no longer open), and awaits already separated the read from them. Called directly, so the
    assert names this call site; the route's other reads are tested above."""
    from src.api import dependencies as api_deps
    from src.api.main import app
    from src.services.applications import memory_closes
    from src.services.profile import storage

    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            made = await agent.post("/api/asks", json={"question": "AppZen form", "application_id": app_id,
                                                       "kind": "question", "questions": [
                {"key": "city", "question": "City?", "source": "guess", "answer": "London"}]})
            assert made.status_code == 201, made.text
        seen: list[int] = []
        monkeypatch.setattr(
            storage, "load_profile_with_overlay", _recorded(storage.load_profile_with_overlay, seen)
        )
        assert await memory_closes.close_covered(await api_deps.get_db(), fixture_user_id) == 0
    _assert_off_loop(seen)


# ─── the other half of the rule: a read the WRITE after it depends on stays ON the loop ───────────────
# ``update_profile`` runs fully sync on the loop under ``user_edit_lock``, so before these two reads were
# threaded nothing could land between a read and the write that depends on it. An await there lets an
# assistant write slip into the gap and be silently overwritten (take back) or decided twice (confirm).
# These two tests fail if anyone threads them again, and this is the reason why.


@pytest.mark.asyncio
async def test_take_back_reads_the_history_on_the_loop(authenticated_async_context, monkeypatch):
    from src.services.profile import edits

    seen: list[int] = []
    monkeypatch.setattr(edits, "path_history", _recorded(edits.path_history, seen))
    async with authenticated_async_context() as client:
        saved = await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.daily_cap", "value": 7}]}
        )
        assert saved.status_code == 200, saved.text
        seen.clear()
        back = await client.post("/api/assistant-settings/take-back",
                                 json={"path": "assistant_settings.daily_cap"})
        assert back.status_code == 200, back.text
    assert seen, "take back never read the history"
    assert seen[0] == threading.get_ident(), "the read the take-back write depends on must stay on the loop"


@pytest.mark.asyncio
async def test_confirm_reads_the_request_on_the_loop(authenticated_async_context, monkeypatch, fixture_user_id):
    from src.services.profile import setting_requests

    [waiting] = setting_requests.create_requests(
        fixture_user_id, "token:claude-code", [("assistant_settings.apply_min_score", 10)]
    )
    seen: list[int] = []
    monkeypatch.setattr(setting_requests, "get_request", _recorded(setting_requests.get_request, seen))
    async with authenticated_async_context() as client:
        done = await client.post(f"/api/assistant-settings/requests/{waiting['id']}/confirm")
        assert done.status_code == 200, done.text
    assert seen, "confirm never read the request"
    assert seen[0] == threading.get_ident(), "the read the confirm write depends on must stay on the loop"

