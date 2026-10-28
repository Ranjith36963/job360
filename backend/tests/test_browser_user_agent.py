"""The cloud browser does not announce "HeadlessChrome": it is Chrome, so it says Chrome.

Only the User-Agent token changes. No stealth, no webdriver patch, no bot-check workaround.
"""
from __future__ import annotations

import socket

import pytest

HEADLESS_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/131.0.6778.33 Safari/537.36"
)


class _Page:
    async def evaluate(self, _expr):
        return HEADLESS_UA

    async def close(self):
        pass


class _Ctx:
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
        pass


class _Browser:
    def __init__(self):
        self.context_kwargs: list[dict] = []
        self.probes = 0

    def is_connected(self):
        return True

    async def new_page(self):
        self.probes += 1
        return _Page()

    async def new_context(self, **kw):
        self.context_kwargs.append(kw)
        return _Ctx()


async def _no_dns(host, port=0, family=socket.AF_UNSPEC):
    return []


@pytest.mark.asyncio
async def test_context_user_agent_says_chrome_not_headless(monkeypatch):
    from src.services.browser_agent import agent

    async def _quiet(_busy):
        return None

    monkeypatch.setattr(agent.beacon, "sync", _quiet)
    pool = agent.BrowserPool(max_sessions=3, idle_s=600, resolve=_no_dns)
    browser = _Browser()

    async def _ensure():
        return browser

    pool._ensure_browser = _ensure  # noqa: SLF001
    await pool.get(agent.browser_key("u1", 1), create=True)
    await pool.get(agent.browser_key("u1", 2), create=True)

    ua = browser.context_kwargs[0]["user_agent"]
    assert "Headless" not in ua
    assert "Chrome/131.0.6778.33" in ua
    assert browser.context_kwargs[1]["user_agent"] == ua
    assert browser.probes == 1  # read once, kept
    assert browser.context_kwargs[0]["locale"] == "en-GB"
    assert browser.context_kwargs[0]["timezone_id"] == "Europe/London"
