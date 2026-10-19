"""The Job360 browser agent (docs/product/BROWSER_AGENT.md, VISION decision 29).

Pinned here (the route, the MCP tool and the end-to-end Chromium-through-proxy
run land with part 2, #902):

1. The hard lines as pure rules (``safety.py``) — no browser, no network.
2. The page scripts in a real Chromium via ``page.set_content`` (skipped when
   Playwright or its Chromium is not installed, as on CI).
3. The engine's own guards: the launch flags, the upload file name, and the
   egress proxy's address order (a fake resolver and an injected connector to
   a loopback site, so nothing leaves the machine — rule #4).
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


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_chosen_answer_alone_puts_send_application_behind_the_gate():
    # A React form with no <form> whose only answers are choices: "Send
    # application" must still count as the final submit. A box ticked by the
    # page itself is not an answer, so the ad's "Apply" still only opens.
    async_api = pytest.importorskip("playwright.async_api")
    from src.services.browser_agent.page_js import ELEMENT_FACTS_JS

    page_html = """<!doctype html><html><body>
    <label><input type="checkbox" id="news" checked> Keep me posted</label>
    <select id="auth"><option value="">Choose</option><option value="yes">Yes</option></select>
    <label><input type="radio" name="r" id="r1" value="a"> A</label>
    <div contenteditable="true" id="letter"></div>
    <div role="button" id="send">Send application</div>
    </body></html>"""

    async def kind(page):
        facts = await page.locator("#send").evaluate(ELEMENT_FACTS_JS)
        return control_kind(facts["tag"], facts["type"], facts["name"], facts["autocomplete"],
                            facts["in_form"], facts["role"], facts["page_has_answers"])

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 — no Chromium on this machine
            pytest.skip(f"Chromium not available: {e}")
        page = await browser.new_page()
        for choose in (None, "select", "radio", "untick", "rich text"):
            await page.set_content(page_html)
            if choose is None:
                assert await kind(page) == "field"
                continue
            if choose == "select":
                await page.select_option("#auth", "yes")
            elif choose == "radio":
                await page.check("#r1")
            elif choose == "rich text":
                await page.fill("#letter", "Dear team")
            else:
                await page.uncheck("#news")
            assert await kind(page) == "submit", choose
        await browser.close()


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_gate_and_look_name_a_button_the_same_way():
    # A button named only by another element (aria-labelledby), outside any
    # <form>: `look` shows "Submit application", so the gate must see it too.
    async_api = pytest.importorskip("playwright.async_api")
    from src.services.browser_agent.page_js import ELEMENT_FACTS_JS, SNAPSHOT_JS

    page_html = """<!doctype html><html><body>
    <span id="l">Submit application</span>
    <button id="by" aria-labelledby="l"><svg width="10" height="10"></svg></button>
    <label>Submit my application <span role="button" id="wrapped" tabindex="0">→</span></label>
    <button id="tip" title="Opens a new tab">Submit application</button>
    <button id="pic"><img alt="Submit application" src="data:,"></button>
    <form id="apply"><input type="hidden" name="job" value="1"></form>
    <button id="outside" type="submit" form="apply">Apply</button>
    </body></html>"""

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 — no Chromium on this machine
            pytest.skip(f"Chromium not available: {e}")
        page = await browser.new_page()
        await page.set_content(page_html)
        seen = await page.evaluate(SNAPSHOT_JS, {"maxElements": 50, "maxText": 1000})
        look_names = {e["name"] for e in seen["elements"]}
        for el_id in ("by", "wrapped", "tip", "pic", "outside"):
            facts = await page.locator("#" + el_id).evaluate(ELEMENT_FACTS_JS)
            assert facts["name"] in look_names, (el_id, facts["name"], look_names)
            assert control_kind(facts["tag"], facts["type"], facts["name"], facts["autocomplete"],
                                facts["in_form"], facts["role"], facts["page_has_answers"]) == "submit", el_id
        await browser.close()


@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_look_lists_a_bare_rich_text_box_and_type_reads_it_back():
    async_api = pytest.importorskip("playwright.async_api")
    from src.services.browser_agent.page_js import SNAPSHOT_JS, TYPED_VALUE_JS

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 — no Chromium on this machine
            pytest.skip(f"Chromium not available: {e}")
        page = await browser.new_page()
        await page.set_content('<div contenteditable id="letter" aria-label="Cover letter"></div>'
                               '<input id="name" aria-label="Name">')
        seen = await page.evaluate(SNAPSHOT_JS, {"maxElements": 50, "maxText": 1000})
        assert "Cover letter" in {e["name"] for e in seen["elements"]}
        await page.fill("#letter", "Dear team")
        await page.fill("#name", "Ada")
        assert (await page.locator("#letter").evaluate(TYPED_VALUE_JS)).strip() == "Dear team"
        assert await page.locator("#name").evaluate(TYPED_VALUE_JS) == "Ada"
        await browser.close()


@pytest.mark.asyncio
async def test_a_dead_chromium_frees_every_session_it_held():
    # One shared Chromium: when it dies, every session in it is dead and must not
    # keep counting against max_sessions (CodeRabbit on #908).
    import types

    from src.services.browser_agent.agent import BrowserPool, Session

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return []

    class _Ctx:
        closed = False

        async def close(self):
            self.closed = True

    pool = BrowserPool(max_sessions=2, idle_s=600, resolve=resolve)
    pool._browser = types.SimpleNamespace(is_connected=lambda: False)
    ctxs = [_Ctx(), _Ctx()]
    pool._sessions = {"a": Session(context=ctxs[0], page=None), "b": Session(context=ctxs[1], page=None)}
    assert await pool.get("a", create=False) is None
    assert pool._sessions == {} and all(c.closed for c in ctxs)


def test_chromium_launches_with_webrtc_kept_behind_the_proxy(monkeypatch):
    # WebRTC's UDP does not use --proxy-server; without this flag a page could
    # reach any address around the SSRF screen (CodeRabbit on #908).
    import asyncio
    import sys
    import types

    from src.services.browser_agent.agent import BrowserPool

    launched: dict = {}

    class _Chromium:
        async def launch(self, **kw):
            launched.update(kw)
            return types.SimpleNamespace(is_connected=lambda: True)

    class _Starter:
        async def start(self):
            return types.SimpleNamespace(chromium=_Chromium())

    fake = types.ModuleType("playwright.async_api")
    fake.async_playwright = lambda: _Starter()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return []

    pool = BrowserPool(max_sessions=1, idle_s=60, resolve=resolve)
    pool.egress.port = 1  # no proxy is started: only the flags are read
    asyncio.run(pool._ensure_browser())
    args = launched["args"]
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--proxy-server=http://127.0.0.1:1" in args


@pytest.mark.parametrize(("name", "kind", "safe"), [
    ("Ranjith CV.pdf", "cv", "Ranjith CV.pdf"),
    ("/etc/cron.d/x", "cv", "x"),
    ("../../app/main.py", "cover_letter", "main.py"),
    ("..\\..\\boot.ini", "cv", "boot.ini"),
    ("..", "cv", "cv.pdf"),
    ("", "cover_letter", "cover_letter.pdf"),
])
def test_an_upload_is_written_inside_its_temp_folder(name, kind, safe):
    from src.services.browser_agent.agent import upload_name

    assert upload_name(name, kind) == safe


@pytest.mark.asyncio
@pytest.mark.parametrize("v6_works,v4_works,tried,status", [
    (False, True, ["93.184.216.34"], b"200"),                       # IPv4 first; IPv6 never needed
    (True, False, ["93.184.216.34", "2606:2800:220:1::248"], b"200"),  # IPv4 down: next approved address
    (False, False, ["93.184.216.34", "2606:2800:220:1::248"], b"403"),  # all down: refused
])
async def test_the_proxy_tries_ipv4_first_then_every_approved_address(v6_works, v4_works, tried, status):
    import asyncio

    from src.services.browser_agent.egress import EgressProxy

    async def site(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(site, "127.0.0.1", 0)
    site_port = server.sockets[0].getsockname()[1]
    attempts: list[str] = []

    async def open_connection(address, port):
        attempts.append(address)
        if (":" in address and v6_works) or (":" not in address and v4_works):
            return await asyncio.open_connection("127.0.0.1", site_port)
        raise OSError("no route")

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return [{"host": "2606:2800:220:1::248", "port": port, "family": socket.AF_INET6},
                {"host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    proxy = EgressProxy(resolve, open_connection)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"GET http://jobs.example/ HTTP/1.1\r\nHost: jobs.example\r\n\r\n")
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), 10)
        writer.close()
    finally:
        await proxy.close()
        server.close()
    assert status in first, first
    assert attempts == tried
