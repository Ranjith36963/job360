"""Resource fixes from the audit of the browser agent (docs/product/BROWSER_AGENT.md).

* Two assistant sessions of one user (Send pressed twice, a minute apart) share the user's one
  browser: a call with another `session` label is refused (busy, browser_in_use), never let in to
  overwrite the first session's form.
* A frame that never answers costs at most ``FRAME_READ_TIMEOUT_S`` (the Enter focus scan, finding a
  ref), and one action at most ``ACT_TIMEOUT_S``: the user's browser lock is never held forever.
* Idle browsers are closed on a clock, not only on the next request.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from tests.test_browser_agent import FORM, _site_resolver, _to_site, needs_chromium


@pytest.fixture
async def form_site():
    from aiohttp import web

    async def form(_):
        return web.Response(text=FORM, content_type="text/html")

    app = web.Application()
    app.router.add_get("/form", form)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_second_session_is_refused_the_browser_another_session_holds(
    authenticated_async_context, form_site, monkeypatch
):
    from src.core import settings
    from src.services.browser_agent import agent

    pool = agent.BrowserPool(max_sessions=2, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(form_site))
    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    try:
        async with authenticated_async_context() as client:
            first = await client.post(
                "/api/browser", json={"action": "open", "url": "http://jobs.example/form", "session": "run-a"}
            )
            assert first.json()["status"] == "needs_you", first.text  # the form (it has a password box)
            second = await client.post(
                "/api/browser", json={"action": "open", "url": "http://jobs.example/form", "session": "run-b"}
            )
            assert second.json()["status"] == "busy" and second.json()["reason"] == "browser_in_use", second.text
            again = await client.post("/api/browser", json={"action": "look", "session": "run-b"})
            assert again.json()["reason"] == "browser_in_use"
            # The holder goes on; a call with no label (older assistants) is not refused.
            assert (await client.post("/api/browser", json={"action": "look", "session": "run-a"})).json()["url"]
            assert (await client.post("/api/browser", json={"action": "look"})).json()["url"]
            # Closed, the browser is free for the next session.
            await client.post("/api/browser", json={"action": "close", "session": "run-a"})
            later = await client.post(
                "/api/browser", json={"action": "open", "url": "http://jobs.example/form", "session": "run-b"}
            )
            assert later.json()["status"] == "needs_you", later.text
    finally:
        await pool.shutdown()


class _Hung:
    """A cross-origin frame that never answers."""

    url = "https://ads.example/"

    async def evaluate(self, *_a):
        await asyncio.Event().wait()

    def locator(self, _sel):
        return types.SimpleNamespace(count=self.evaluate)


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_hung_frame_does_not_hang_the_enter_focus_scan(monkeypatch):
    from src.services.browser_agent import agent

    monkeypatch.setattr(agent, "FRAME_READ_TIMEOUT_S", 0.2)

    class _Main:
        async def evaluate(self, script, *_a):
            return script == "() => document.hasFocus()"  # focused, but not inside a suggestion list

    main = _Main()
    page = types.SimpleNamespace(main_frame=main, frames=[main, _Hung()], is_closed=lambda: False)
    s = agent.Session(context=None, page=page)
    with pytest.raises(agent.BrowserError) as exc:
        await asyncio.wait_for(agent._press(s, "", "Enter"), 5)  # noqa: SLF001
    assert exc.value.reason == "enter_may_submit"


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_hung_frame_does_not_hang_finding_a_ref(monkeypatch):
    from src.services.browser_agent import agent

    monkeypatch.setattr(agent, "FRAME_READ_TIMEOUT_S", 0.2)

    async def one():
        return 1

    good = types.SimpleNamespace(locator=lambda _sel: types.SimpleNamespace(count=one, first="the field"))
    page = types.SimpleNamespace(main_frame=_Hung(), frames=[_Hung(), good], is_closed=lambda: False)
    s = agent.Session(context=None, page=page)
    frame, loc = await asyncio.wait_for(agent._find(s, "e1_ab"), 5)  # noqa: SLF001
    assert frame is good and loc == "the field"


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_one_action_is_stopped_after_its_time_and_frees_the_browser(monkeypatch):
    from src.services.browser_agent import agent

    async def resolve(host, port=0, family=0):
        return []

    async def hang(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr(agent, "ACT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(agent, "_act", hang)
    pool = agent.BrowserPool(max_sessions=1, idle_s=600, resolve=resolve)
    s = agent.Session(context=None, page=None)
    pool._sessions["u1"] = s  # noqa: SLF001
    with pytest.raises(agent.BrowserError) as exc:
        await asyncio.wait_for(agent.run(pool, "u1", "look"), 5)
    assert exc.value.status == "error" and exc.value.reason == "timeout"
    assert not s.lock.locked()


@pytest.mark.asyncio
async def test_idle_browsers_are_closed_on_a_clock_not_only_on_the_next_request(monkeypatch):
    from src.services.browser_agent import agent

    sweeps: list[int] = []

    class _Pool:
        async def reap(self):
            sweeps.append(1)
            if len(sweeps) == 1:
                raise RuntimeError("one bad sweep")  # logged; the sweeping goes on

    monkeypatch.setattr(agent, "get_pool", lambda: _Pool())
    task = asyncio.create_task(agent.reap_forever())
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(sweeps) >= 3


@pytest.mark.asyncio
async def test_the_app_starts_the_idle_sweep_only_when_the_browser_is_on(monkeypatch):
    from src.api import main
    from src.core import settings
    from src.services.browser_agent import agent

    sweeps: list[int] = []

    class _Pool:
        async def reap(self):
            sweeps.append(1)

    monkeypatch.setattr(agent, "get_pool", lambda: _Pool())
    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", False)
    assert main._start_browser_reaper() is None  # noqa: SLF001
    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    task = main._start_browser_reaper()  # noqa: SLF001
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sweeps
