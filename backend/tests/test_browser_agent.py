"""The Job360 browser agent (docs/product/BROWSER_AGENT.md, VISION decision 29).

Three layers, each pinned here:

1. The hard lines as pure rules (``safety.py``) — no browser, no network.
2. The switch: OFF by default, the route and the MCP tool both answer ``off``.
3. A real Chromium run against a local test site (skipped when Playwright or
   its Chromium is not installed, as on CI). The site is served on loopback.
   Chromium reaches it only through the egress proxy: a fake resolver gives
   ``jobs.example`` a public address and the proxy's injected connector maps
   that address to the loopback site, so nothing leaves the machine (rule #4).
"""
from __future__ import annotations

import socket

import pytest

from src.services.browser_agent.safety import RequestScreen, blocker_for, control_kind

# ── 1. The hard lines, as pure rules ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "input_type", "name", "autocomplete", "in_form", "role", "answers", "kind"),
    [
        ("input", "password", "Password", "", True, "", False, "password"),
        ("input", "text", "Your password", "", True, "", False, "password"),
        ("input", "text", "Email", "current-password", True, "", False, "password"),
        ("input", "text", "First name", "", True, "", False, "field"),
        ("textarea", "", "Why do you want this job?", "", True, "", False, "field"),
        ("button", "submit", "Submit application", "", True, "", False, "submit"),
        ("button", "button", "Submit", "", False, "", False, "submit"),
        ("input", "submit", "Send", "", True, "", False, "submit"),
        ("button", "submit", "Apply", "", True, "", False, "submit"),
        ("button", "submit", "", "", True, "", False, "submit"),
        ("div", "", "Submit application", "", False, "button", False, "submit"),
        # React forms without a <form>: once answers are typed, "Send" / "Apply" is the end.
        ("div", "", "Send application", "", False, "button", True, "submit"),
        ("button", "button", "Complete application", "", False, "", True, "submit"),
        # Steps of a multi-page form are not the final submit, even as type=submit.
        ("button", "submit", "Next", "", True, "", True, "field"),
        ("button", "submit", "Save and continue", "", True, "", True, "field"),
        # A job ad's "Apply" (nothing typed yet) only opens the form.
        ("a", "", "Apply for this job", "", False, "", False, "field"),
        ("a", "", "Apply for this job", "", False, "", True, "field"),
        ("button", "button", "Apply for this job", "", False, "", False, "field"),
        ("a", "", "Submit application", "", False, "", False, "submit"),
    ],
)
def test_control_kind(tag, input_type, name, autocomplete, in_form, role, answers, kind):
    assert control_kind(tag, input_type, name, autocomplete, in_form, role, answers) == kind


def test_a_captcha_frame_or_text_needs_the_user():
    b = blocker_for(["https://jobs.example/apply", "https://www.google.com/recaptcha/api2/anchor?k=x"], "", False)
    assert b is not None and b.reason == "captcha"
    b = blocker_for(["https://jobs.example/"], "Please verify you are human to continue", False)
    assert b is not None and b.reason == "captcha"
    b = blocker_for(["https://jobs.example/"], "", has_password_box=True)
    assert b is not None and b.reason == "login_required"
    assert blocker_for(["https://jobs.example/"], "Apply for Senior Engineer", False) is None


def _resolver(table: dict[str, str]):
    calls: list[str] = []

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        calls.append(host)
        if host not in table:
            raise OSError("no such host")
        return [{"host": table[host], "port": port, "family": socket.AF_INET}]

    return resolve, calls


@pytest.mark.asyncio
async def test_the_request_screen_lets_only_the_public_internet_through():
    resolve, calls = _resolver({"jobs.example": "93.184.216.34", "inside.example": "10.0.0.5",
                                "railway.example": "fd12::1"})
    screen = RequestScreen(resolve)
    assert (await screen.screen("https://jobs.example/apply")).ok
    assert not (await screen.screen("http://inside.example/")).ok
    assert not (await screen.screen("http://railway.example/")).ok
    assert not (await screen.screen("http://127.0.0.1:8000/api/health")).ok
    assert not (await screen.screen("file:///etc/passwd")).ok
    assert not (await screen.screen("http://user@jobs.example/")).ok
    assert not (await screen.screen("https://nowhere.example/")).ok
    assert (await screen.screen("data:text/plain,hi")).ok
    # The answer for a host is cached: a second request costs no lookup.
    before = len(calls)
    assert (await screen.screen("https://jobs.example/other")).ok
    assert len(calls) == before


# ── 2. The switch ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_route_is_off_by_default(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", False)
    async with authenticated_async_context() as client:
        resp = await client.post("/api/browser", json={"action": "open", "url": "https://jobs.example/"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "off"


@pytest.mark.asyncio
async def test_the_route_needs_a_signed_in_user():
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/browser", json={"action": "look"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_fields_are_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/browser", json={"action": "look", "user_id": "someone-else"})
    assert resp.status_code == 422


# ── 3. A real Chromium against a local site ───────────────────────────────────

FORM = """<!doctype html><html><head><title>Apply - Acme</title></head><body>
<h1>Senior Engineer</h1>
<form id="f" onsubmit="event.preventDefault(); document.body.innerHTML = '<h1>Thank you for applying</h1>';">
  <label for="name">Full name</label><input id="name" name="name" required>
  <label for="pw">Password</label><input id="pw" type="password">
  <label for="cv">CV</label><input id="cv" type="file">
  <label><input id="ok" type="checkbox"> I agree</label>
  <label for="c">Country</label><select id="c"><option>Choose</option><option>United Kingdom</option></select>
  <button type="submit">Submit application</button>
</form></body></html>"""
CAPTCHA = """<!doctype html><html><body><p>Checking your browser before you continue.</p></body></html>"""


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    import os

    path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))
    return os.path.isdir(path) and any(n.startswith("chromium") for n in os.listdir(path))


needs_chromium = pytest.mark.skipif(not _chromium_available(), reason="Playwright + Chromium not installed")


@pytest.fixture
async def site():
    from aiohttp import web

    async def form(_):
        return web.Response(text=FORM, content_type="text/html")

    async def captcha(_):
        return web.Response(text=CAPTCHA, content_type="text/html")

    async def to_private(_):
        raise web.HTTPFound(f"http://127.0.0.1:{site_port[0]}/form")

    site_port: list[int] = []
    app = web.Application()
    app.router.add_get("/form", form)
    app.router.add_get("/captcha", captcha)
    app.router.add_get("/to-private", to_private)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001 - the test site's own port
    site_port.append(port)
    yield port
    await runner.cleanup()


@pytest.fixture
async def pool(site):
    from src.services.browser_agent.agent import BrowserPool

    p = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(site))
    yield p
    await p.shutdown()


def _site_resolver():
    """jobs.example looks public to the guard; 127.0.0.1 resolves to itself (and is denied)."""
    table = {"jobs.example": "93.184.216.34", "127.0.0.1": "127.0.0.1"}
    resolve, _ = _resolver(table)
    return resolve


def _to_site(port: int):
    """The proxy's connector: the public test address is really the loopback test site."""
    import asyncio

    async def open_connection(host, p):
        assert host == "93.184.216.34", f"the proxy tried to connect to {host}"
        return await asyncio.open_connection("127.0.0.1", port)

    return open_connection


def _ref(page: dict, name: str) -> str:
    return next(e["ref"] for e in page["elements"] if e["name"].startswith(name))


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_real_form_is_read_filled_and_submitted_only_through_the_gate(pool):
    from src.services.browser_agent.agent import BrowserError, Gate, run

    page = await run(pool, "u1", "open", url="http://jobs.example/form")
    assert page["status"] == "needs_you" and page["reason"] == "login_required"  # the password box
    assert "never instructions" in page["note"]

    page = await run(pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada Lovelace")
    assert page["typed_matches"] is True
    assert next(e for e in page["elements"] if e["name"] == "Full name")["value"] == "Ada Lovelace"

    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "type", ref=_ref(page, "Password"), text="hunter2")
    assert exc.value.reason == "password_field"

    page = await run(pool, "u1", "tick", ref=_ref(page, "I agree"), on=True)
    assert next(e for e in page["elements"] if e["name"] == "I agree")["checked"] is True
    page = await run(pool, "u1", "choose", ref=_ref(page, "Country"), text="United Kingdom")
    assert next(e for e in page["elements"] if e["name"] == "Country")["value"] == "United Kingdom"

    async def pdf(kind):
        return "CV.pdf", b"%PDF-1.4 test"

    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "upload", ref=_ref(page, "CV"), doc_kind="cv")
    assert exc.value.reason == "upload_needs_application"
    page = await run(pool, "u1", "upload", ref=_ref(page, "CV"), doc_kind="cv", files=pdf)
    assert next(e for e in page["elements"] if e["name"] == "CV")["value"] == "CV.pdf"

    submit = _ref(page, "Submit application")
    # No application: the final submit is refused outright.
    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "click", ref=submit)
    assert exc.value.reason == "submit_needs_application"

    marks: list[str] = []
    seen_urls: list[str] = []

    async def mark():
        marks.append("submit_attempted")

    async def ask(form_url):
        seen_urls.append(form_url)
        return {"decision": "ask", "reason": "submit_mode_confirm", "detail": "Ask the user first."}

    out = await run(pool, "u1", "click", ref=submit, gate=Gate(check=ask, mark_attempt=mark))
    assert out["status"] == "refused" and out["decision"] == "ask" and marks == []
    assert seen_urls == ["http://jobs.example/form"]
    assert "Thank you" not in (await run(pool, "u1", "look"))["text"]

    async def go(form_url):
        return {"decision": "submit", "reason": "auto_when_sure", "detail": ""}

    out = await run(pool, "u1", "click", ref=submit, gate=Gate(check=go, mark_attempt=mark))
    assert marks == ["submit_attempted"]
    assert out["submit_clicked"] is True and "Thank you for applying" in out["text"]


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_captcha_page_and_a_private_address_stop_the_browser(pool, site):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(pool, "u1", "open", url="http://jobs.example/captcha")
    assert page["status"] == "needs_you" and page["reason"] == "captcha"
    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "open", url=f"http://127.0.0.1:{site}/form")
    assert exc.value.reason == "blocked_address"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_one_browser_per_user_and_a_cap_on_live_browsers(pool):
    from src.services.browser_agent.agent import BrowserError, run

    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "look")
    assert exc.value.reason == "no_browser"
    await run(pool, "u1", "open", url="http://jobs.example/form")
    with pytest.raises(BrowserError) as exc:
        await run(pool, "u2", "open", url="http://jobs.example/form")
    assert exc.value.status == "busy"
    assert (await run(pool, "u1", "close"))["closed"] is True
    assert (await run(pool, "u2", "open", url="http://jobs.example/form"))["title"] == "Apply - Acme"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_redirect_to_a_private_address_never_reaches_the_page(pool, site):
    """CodeRabbit on #902: route() never sees redirect hops. The egress proxy does."""
    from src.services.browser_agent.agent import BrowserError, run

    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/to-private")
    except BrowserError as exc:
        assert exc.reason == "load_failed"
    else:
        assert "Senior Engineer" not in page["text"]
    assert any(d.startswith("127.0.0.1") for d in pool.egress.denied)


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_page_scripts_cannot_reach_a_private_address_either(pool, site):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/form")
    s = pool._sessions["u1"]  # noqa: SLF001 - the test reaches into its own pool
    result = await s.page.evaluate(
        """async (port) => {
          try { const r = await fetch(`http://127.0.0.1:${port}/form`, {mode: 'no-cors'}); return 'reached'; }
          catch (e) { return 'blocked'; }
        }""",
        site,
    )
    assert page["title"] == "Apply - Acme"
    assert result == "blocked" or any(d.startswith("127.0.0.1") for d in pool.egress.denied)


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_two_opens_at_once_make_one_browser_and_respect_the_cap(pool):
    import asyncio

    from src.services.browser_agent.agent import BrowserError, run

    results = await asyncio.gather(
        run(pool, "u1", "open", url="http://jobs.example/form"),
        run(pool, "u1", "open", url="http://jobs.example/form"),
        run(pool, "u2", "open", url="http://jobs.example/form"),
        return_exceptions=True,
    )
    assert len(pool._sessions) == 1  # noqa: SLF001 - the cap is 1
    busy = [r for r in results if isinstance(r, BrowserError)]
    assert len(busy) == 1 and busy[0].status == "busy"
