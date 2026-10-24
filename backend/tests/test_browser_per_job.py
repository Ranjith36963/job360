"""One cloud browser per job (owner decisions, 2026-10-24; docs/product/BROWSER_AGENT.md).

* Each application gets its own browser: a second job (even under a new session label) never takes or
  drops the first job's filled form. The same job's browser still refuses another session label.
* Caps: at most ``max_per_user`` per user; a new one closes the user's least recently used browser that is
  not held for them, and is refused (``browsers_held``) when every one waits on the user. The per-user cap
  never exceeds the pool, and a user with no browser takes another user's spare one (every user gets a
  slot); never another user's only, held or busy browser (review of #1176).
* ``form_swept`` holds that job's browser (it waits on the user's yes); Needs you can say for how long. A
  hold is not renewed while active, and one user holds at most ``max_sessions - 1`` at once. A form filled
  in the scratch browser is adopted by the job when the browser is on the swept form's page.
* The finish view acts on the named application's browser, and needs that application.
"""
from __future__ import annotations

import socket
import time
from types import SimpleNamespace

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium
from tests.test_browser_hardening_live import two_page_site  # noqa: F401 - the fixture


class _Ctx:
    closed = False

    def set_default_timeout(self, _ms):
        pass

    set_default_navigation_timeout = set_default_timeout

    async def route(self, *_a):
        pass

    async def new_page(self):
        return object()

    def on(self, *_a):
        pass

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self):
        self.contexts: list[_Ctx] = []

    def is_connected(self):
        return True

    async def new_context(self, **_kw):
        self.contexts.append(_Ctx())
        return self.contexts[-1]


async def _no_dns(host, port=0, family=socket.AF_UNSPEC):
    return []


def _pool(monkeypatch, **kw):
    from src.services.browser_agent import agent

    async def _quiet(_busy):
        return None

    monkeypatch.setattr(agent.beacon, "sync", _quiet)
    pool = agent.BrowserPool(idle_s=600, resolve=_no_dns, **kw)
    browser = _Browser()
    pool._browser = browser  # noqa: SLF001

    async def _ensure():
        return browser

    pool._ensure_browser = _ensure  # noqa: SLF001
    return pool


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_two_jobs_get_two_browsers_and_the_first_form_survives(two_page_site):  # noqa: F811
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=3, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(two_page_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/job", owner="s1", application_id=1)
        await run(pool, "u1", "type", ref=_ref(page, "First name"), text="Grace", owner="s1", application_id=1)
        # A new label on a new job: its own browser, not browser_in_use and not the first form.
        await run(pool, "u1", "open", url="http://jobs.example/job", owner="s2", application_id=2)
        seen = await run(pool, "u1", "look", owner="s1", application_id=1)
        assert next(e for e in seen["elements"] if e["name"].startswith("First name"))["value"] == "Grace"
        assert len(pool._sessions) == 2  # noqa: SLF001
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_the_same_job_under_another_label_is_refused(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=3)
    s = await pool.get(agent.browser_key("u1", 1), create=True)
    s.owner = "s1"
    with pytest.raises(agent.BrowserError) as busy:
        await agent.run(pool, "u1", "look", owner="s2", application_id=1)
    assert (busy.value.status, busy.value.reason) == ("busy", "browser_in_use")


@pytest.mark.asyncio
async def test_a_named_job_takes_the_scratch_browser_it_was_opened_in(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=3)
    scratch = await pool.get("u1", create=True)
    scratch.owner = "s1"
    pool.adopt("u1", agent.browser_key("u1", 5), "s2")  # another session's scratch browser stays its own
    assert pool._sessions == {"u1": scratch}  # noqa: SLF001
    pool.adopt("u1", agent.browser_key("u1", 5), "s1")
    assert pool._sessions == {agent.browser_key("u1", 5): scratch}  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_per_user_cap_closes_the_oldest_unheld_browser_and_refuses_when_all_are_held(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=10, max_per_user=2)
    first = await pool.get(agent.browser_key("u1", 1), create=True)
    second = await pool.get(agent.browser_key("u1", 2), create=True)
    first.last_used, second.last_used = time.monotonic() - 50, time.monotonic() - 10
    third = await pool.get(agent.browser_key("u1", 3), create=True)
    assert first.context.closed and not second.context.closed
    assert set(pool._sessions) == {agent.browser_key("u1", 2), agent.browser_key("u1", 3)}  # noqa: SLF001
    with pytest.raises(agent.BrowserError) as gone:
        await agent.run(pool, "u1", "look", application_id=1)
    assert gone.value.reason == "no_browser" and "another job" in gone.value.detail

    for s in (second, third):
        agent.hold_for_user(pool, "u1", 2 if s is second else 3)
    with pytest.raises(agent.BrowserError) as held:
        await pool.get(agent.browser_key("u1", 4), create=True)
    assert (held.value.status, held.value.reason) == ("busy", "browsers_held")
    assert "2 Job360 browsers" in held.value.detail and "waiting on them" in held.value.detail
    assert not second.context.closed and not third.context.closed


@pytest.mark.asyncio
async def test_the_total_cap_never_closes_another_users_browser(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=2)
    theirs = await pool.get(agent.browser_key("u2", 9), create=True)
    mine = await pool.get(agent.browser_key("u1", 1), create=True)
    mine.last_used = theirs.last_used = time.monotonic() - 100
    await pool.get(agent.browser_key("u1", 2), create=True)  # full: my own old one makes room
    assert mine.context.closed and not theirs.context.closed
    with pytest.raises(agent.BrowserError) as busy:
        await pool.get(agent.browser_key("u3", 1), create=True)  # nothing of u3's to close: refused as before
    assert busy.value.reason == "all_browsers_busy" and not theirs.context.closed


@pytest.mark.asyncio
async def test_the_finish_view_acts_on_the_named_jobs_browser(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=3)
    one = await pool.get(agent.browser_key("u1", 1), create=True)
    two = await pool.get(agent.browser_key("u1", 2), create=True)
    agent.hold_for_user(pool, "u1", 1)
    agent.hold_for_user(pool, "u1", 2)
    assert (await agent.live(pool, "u1", "done", finish=True, application_id=2)) == {"status": "ok", "done": True}
    assert two.held_until is None and one.held_until is not None
    assert 0 < agent.held_seconds(pool, "u1", 1) <= agent.HANDOFF_HOLD_SECONDS
    assert agent.held_seconds(pool, "u1", 2) is None
    with pytest.raises(agent.BrowserError) as none:
        await agent.live(pool, "u1", "shot", finish=True, application_id=3)
    assert none.value.reason == "no_form"


@pytest.mark.asyncio
async def test_form_swept_holds_that_jobs_browser_and_ready_says_how_long(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    answer = {"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        apps = []
        for n in (1, 2):
            brought = await client.post("/api/jobs/bring", json={
                "title": f"Engineer {n}", "company": "Acme", "location": "London",
                "description": "Build services. " * 10, "apply_url": f"https://jobs.acme.com/apply/{n}",
            })
            apps.append(brought.json()["application_id"])
        swept, other = [await pool.get(agent.browser_key(user_id, a), create=True) for a in apps]
        filled = {"form_url": "https://jobs.acme.com/apply/1", "fields_count": 1, "answers": [answer]}
        for kind, payload in (("form_filled", filled), ("form_swept", {"fields_checked": 1, "fixed": 0})):
            resp = await client.post(f"/api/applications/{apps[0]}/events", json={"event_type": kind, "payload": payload})
            assert resp.status_code == 201, resp.text
        assert swept.held_until is not None and other.held_until is None
        card = (await client.get("/api/ready-to-send", params={"application_id": apps[0]})).json()["items"][0]
        assert 0 < card["held_seconds"] <= agent.HANDOFF_HOLD_SECONDS

        refused = await client.post("/api/browser/live", json={"action": "shot", "mode": "finish"})
        assert refused.status_code == 422  # finish needs the application whose form it is
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as bot:
        resp = await bot.post("/api/browser/live", json={"action": "done", "mode": "finish", "application_id": apps[0]})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_user_with_no_browser_takes_another_users_spare_one_never_their_only(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=2)  # max_per_user 3 > the pool: capped at 2
    old = await pool.get(agent.browser_key("uA", 1), create=True)
    kept = await pool.get(agent.browser_key("uA", 2), create=True)
    old.last_used = time.monotonic() - 100
    theirs = await pool.get(agent.browser_key("uB", 1), create=True)  # A had two: its oldest makes room
    assert old.context.closed and not kept.context.closed and not theirs.context.closed
    assert set(pool._sessions) == {agent.browser_key("uA", 2), agent.browser_key("uB", 1)}  # noqa: SLF001
    with pytest.raises(agent.BrowserError) as busy:
        await pool.get(agent.browser_key("uC", 1), create=True)  # A and B each have one: neither is taken
    assert busy.value.reason == "all_browsers_busy"
    assert not kept.context.closed and not theirs.context.closed


@pytest.mark.asyncio
async def test_another_users_held_or_busy_browser_is_never_taken(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=3)
    held, busy, free = [await pool.get(agent.browser_key("uA", n), create=True) for n in (1, 2, 3)]
    held.last_used, busy.last_used, free.last_used = (time.monotonic() - t for t in (300, 200, 10))
    assert agent.hold_for_user(pool, "uA", 1)
    await busy.lock.acquire()
    try:
        await pool.get(agent.browser_key("uB", 1), create=True)  # only A's free one may go, though newest
        assert free.context.closed and not held.context.closed and not busy.context.closed
        with pytest.raises(agent.BrowserError) as full:
            await pool.get(agent.browser_key("uC", 1), create=True)
        assert full.value.reason == "all_browsers_busy"
        assert not held.context.closed and not busy.context.closed
    finally:
        busy.lock.release()


@pytest.mark.asyncio
async def test_a_user_at_the_pool_size_is_told_its_browsers_wait_on_it(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=2)
    await pool.get(agent.browser_key("uA", 1), create=True)
    busy = await pool.get(agent.browser_key("uA", 2), create=True)
    assert agent.hold_for_user(pool, "uA", 1)
    await busy.lock.acquire()
    try:
        with pytest.raises(agent.BrowserError) as held:
            await pool.get(agent.browser_key("uA", 3), create=True)
        assert (held.value.status, held.value.reason) == ("busy", "browsers_held")
    finally:
        busy.lock.release()


@pytest.mark.asyncio
async def test_a_hold_is_not_renewed_while_active_and_one_user_holds_at_most_pool_minus_one(monkeypatch):
    from src.services.browser_agent import agent

    pool = _pool(monkeypatch, max_sessions=3)
    one, two, three = [await pool.get(agent.browser_key("uA", n), create=True) for n in (1, 2, 3)]
    one.held_until = time.monotonic() + 60
    assert agent.hold_for_user(pool, "uA", 1)
    assert one.held_until < time.monotonic() + 61  # still the first hold's end, not 30 more minutes
    assert agent.hold_for_user(pool, "uA", 2)
    assert not agent.hold_for_user(pool, "uA", 3) and three.held_until is None  # 2 of 3 held: no more
    one.held_until = time.monotonic() - 1  # the first hold ran out: a new one may start
    assert agent.hold_for_user(pool, "uA", 3) and three.held_until is not None
    assert two.held_until is not None


@pytest.mark.asyncio
async def test_form_swept_adopts_the_scratch_browser_on_that_forms_page(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    answer = {"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        apps = []
        for n in (1, 2):
            brought = await client.post("/api/jobs/bring", json={
                "title": f"Engineer {n}", "company": "Acme", "location": "London",
                "description": "Build services. " * 10, "apply_url": f"https://jobs.acme.com/apply/{n}",
            })
            apps.append(brought.json()["application_id"])

        async def sweep(app_id: int, form_url: str) -> None:
            filled = {"form_url": form_url, "fields_count": 1, "answers": [answer]}
            swept = {"fields_checked": 1, "fixed": 0, "form_url": form_url}
            for kind, payload in (("form_filled", filled), ("form_swept", swept)):
                resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": kind, "payload": payload})
                assert resp.status_code == 201, resp.text

        scratch = await pool.get(user_id, create=True)
        scratch.page = SimpleNamespace(url="https://jobs.acme.com/apply/1?src=board#form")
        await sweep(apps[0], "https://jobs.acme.com/apply/1")
        assert pool._sessions.get(agent.browser_key(user_id, apps[0])) is scratch  # noqa: SLF001
        assert user_id not in pool._sessions and scratch.held_until is not None  # noqa: SLF001

        scratch.held_until = time.monotonic() + 60
        await sweep(apps[0], "https://jobs.acme.com/apply/1")  # a second sweep does not extend the hold
        assert scratch.held_until < time.monotonic() + 61

        other = await pool.get(user_id, create=True)
        other.page = SimpleNamespace(url="https://jobs.acme.com/apply/elsewhere")
        await sweep(apps[1], "https://jobs.acme.com/apply/2")
        assert pool._sessions.get(user_id) is other and other.held_until is None  # noqa: SLF001
        assert agent.browser_key(user_id, apps[1]) not in pool._sessions  # noqa: SLF001


async def _bring(client, n: int) -> int:
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Engineer {n}", "company": "Acme", "location": "London",
        "description": "Build services. " * 10, "apply_url": f"https://jobs.acme.com/apply/{n}",
    })
    return int(brought.json()["application_id"])


async def _swept(client, app_id: int, form_url: str) -> dict:
    answer = {"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}
    filled = {"form_url": form_url, "fields_count": 1, "answers": [answer]}
    for kind, payload in (("form_filled", filled), ("form_swept", {"fields_checked": 1, "fixed": 0})):
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": kind, "payload": payload})
        assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_captcha_inside_an_almost_over_swept_hold_gets_its_full_time(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)

    async def fake_run(p, user_id, action, **kw):
        return {"status": "needs_you", "reason": "captcha", "detail": "x", "url": "https://jobs.acme.com/apply/1"}

    monkeypatch.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client, 1)
        s = await pool.get(agent.browser_key(user_id, app_id), create=True)
        s.held_until = time.monotonic() + 60  # a form_swept hold with a minute left
        out = (await client.post("/api/browser", json={"action": "click", "ref": "e1_ab", "application_id": app_id})).json()
        assert out["blocked_recorded"] is True
        assert agent.held_seconds(pool, user_id, app_id) >= agent.HANDOFF_HOLD_SECONDS - 10


@pytest.mark.asyncio
async def test_one_swept_hold_per_form_page_and_a_refused_one_is_said(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)  # one user holds at most 2
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        apps = [await _bring(client, 1)]
        browsers = [await pool.get(agent.browser_key(user_id, apps[0]), create=True)]
        browsers[0].page = SimpleNamespace(url="https://jobs.acme.com/apply/1")

        first = await _swept(client, apps[0], "https://jobs.acme.com/apply/1")
        assert browsers[0].held_until is not None and not any("not held" in w for w in first["warnings"])
        browsers[0].held_until = time.monotonic() - 1  # the hold ran out
        again = await _swept(client, apps[0], "https://jobs.acme.com/apply/1")  # same form: no second hold
        assert browsers[0].held_until < time.monotonic()
        assert any("already had its 30-minute hold" in w for w in again["warnings"])
        browsers[0].page = SimpleNamespace(url="https://jobs.acme.com/apply/1/page-2")  # a new form page
        assert (await _swept(client, apps[0], "https://jobs.acme.com/apply/1/page-2"))["warnings"] == []
        assert browsers[0].held_until > time.monotonic()


@pytest.mark.asyncio
async def test_a_form_swept_past_the_held_cap_tells_the_assistant(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)  # one user holds at most 2
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        apps = [await _bring(client, n) for n in (1, 2, 3)]
        browsers = [await pool.get(agent.browser_key(user_id, a), create=True) for a in apps]
        for n in (1, 2):
            await _swept(client, apps[n - 1], f"https://jobs.acme.com/apply/{n}")
        over = await _swept(client, apps[2], "https://jobs.acme.com/apply/3")  # a third hold is past the cap
        assert browsers[2].held_until is None
        assert any("2 forms already wait on the user" in w and "10 idle minutes" in w for w in over["warnings"])


# ── Holds trust only Job360's own CAPTCHA block, and a refill never renews (review of #1176) ──

_FORM = "https://jobs.acme.com/apply/1"
_FILL = {"action": "fill", "fields": [{"ref": "e1_ab", "text": "Grace"}]}


def _scripted(monkeypatch, pool, replies: list[dict]):
    from src.services.browser_agent import agent

    async def fake_run(p, user_id, action, **kw):
        return replies.pop(0) if replies else {"status": "ok", "filled": []}

    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)


@pytest.mark.asyncio
async def test_a_callers_captcha_look_alike_never_holds_the_browser(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)
    _scripted(monkeypatch, pool, [])
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client, 1)
        s = await pool.get(agent.browser_key(user_id, app_id), create=True)
        s.page = SimpleNamespace(url=_FORM)
        forged = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "blocked", "payload": {"reason": "captcha", "step": "Job360 browser"},
        })
        assert forged.status_code == 201, forged.text
        for _ in range(3):
            out = (await client.post("/api/browser", json={**_FILL, "application_id": app_id})).json()
            assert out["held_for_you"] is False
        assert s.held_until is None


@pytest.mark.asyncio
async def test_a_caller_cannot_write_the_browsers_own_mark(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        resp = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "blocked", "payload": {"reason": "captcha", "step": "Job360 browser", "by": "browser"},
        })
        assert resp.status_code == 422, resp.text
        assert "written by Job360 itself" in resp.text
        assert (await client.get(f"/api/applications/{app_id}/controls")).json().get("blocked") is None


@pytest.mark.asyncio
async def test_a_refill_never_renews_the_captcha_hold_and_reholds_once_per_page(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = _pool(monkeypatch, max_sessions=3)
    _scripted(monkeypatch, pool, [{"status": "needs_you", "reason": "captcha", "detail": "x", "url": _FORM}])
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client, 1)
        s = await pool.get(agent.browser_key(user_id, app_id), create=True)
        s.page = SimpleNamespace(url=_FORM)
        out = (await client.post("/api/browser", json={"action": "click", "ref": "e1_ab", "application_id": app_id})).json()
        assert out["blocked_recorded"] is True and s.held_until is not None  # the browser's own CAPTCHA holds
        asks = (await client.get("/api/asks")).json()["asks"]
        assert [a["finish_here"] for a in asks if a["application_id"] == app_id] == [True]

        end = time.monotonic() + 60
        s.held_until = end  # a minute left on that hold
        for _ in range(3):
            out = (await client.post("/api/browser", json={**_FILL, "application_id": app_id})).json()
            assert out["held_for_you"] is True
        assert s.held_until == end  # never renewed by a refill

        s.held_until, s.swept_on = None, None  # the user pressed Done; the block is still open
        out = (await client.post("/api/browser", json={**_FILL, "application_id": app_id})).json()
        assert out["held_for_you"] is True and s.held_until is not None  # one new hold for this page
        s.held_until = time.monotonic() - 1  # it ran out
        out = (await client.post("/api/browser", json={**_FILL, "application_id": app_id})).json()
        assert out["held_for_you"] is False and s.held_until < time.monotonic()  # no second one on this page
