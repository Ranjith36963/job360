"""Gaps a claim-by-claim check of the hard lines found (docs/product/BROWSER_AGENT.md "Hard lines").

* A password box is known by its name in the common European languages and with odd spacing, and by
  autocomplete current-password / new-password / one-time-code, not only by type=password.
* The CAPTCHA vendors GeeTest, AWS WAF, Friendly Captcha, MTCaptcha and Yandex SmartCaptcha are told
  by their frame's HOST (an employer board path that says "geetest" is not one), and an image-text
  box ("type the characters you see") by its words.
* A key with no ref goes where the focus is: into a hidden challenge frame it is refused.
* Enter whose handler calls form.submit() (no submit event) is stopped too.
* A plain click on a "leave this blank" box is refused, like tick.
"""
from __future__ import annotations

import pytest

from src.services.browser_agent.safety import blocker_for, control_kind
from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

# ── 1. Password boxes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "autocomplete", "kind"),
    [
        ("Passwort", "", "password"),
        ("Neues Passwort wiederholen", "", "password"),
        ("Kennwort", "", "password"),
        ("Mot de passe", "", "password"),
        ("Mot  de passe", "", "password"),
        ("Contraseña", "", "password"),
        ("Contraseña", "", "password"),  # the same word, its ñ as n + a combining tilde
        ("Senha", "", "password"),
        ("Palavra-passe", "", "password"),
        ("Wachtwoord", "", "password"),
        ("Hasło", "", "password"),
        ("HASŁO", "", "password"),
        ("Lösenord", "", "password"),
        ("Pass word", "", "password"),
        ("P a s s w o r d", "", "password"),
        ("Pass​word", "", "password"),
        ("PASS-WORD", "", "password"),
        ("Code", "one-time-code", "password"),
        ("Login", "current-password", "password"),
        ("Login", "section-login new-password", "password"),
        ("Login", "NEW-PASSWORD", "password"),
        # A hint is part of setting a password: the user's to write, the safe side.
        ("Password hint", "", "password"),
        # Ordinary fields that only look close.
        ("Passport number", "", "field"),
        ("Passport", "", "field"),
        ("Compass direction", "", "field"),
        ("Senhor / Senhora", "", "field"),
        ("Postcode", "postal-code", "field"),
        ("Email", "email", "field"),
        ("Haslett office", "", "field"),
    ],
)
def test_a_password_box_is_known_by_its_name_in_any_common_language(name, autocomplete, kind):
    assert control_kind("input", "text", name, autocomplete, True) == kind


# ── 2. CAPTCHA vendors and image-text boxes ───────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://static.geetest.com/v4/gt4.js",
        "https://gcaptcha4.geetest.com/load?captcha_id=x",
        "https://api.geetest.com/get.php?gt=x",
        "https://ab12cd.eu-west-1.captcha.awswaf.com/ab12cd/jsapi.js",
        "https://captcha.awswaf.com/",
        "https://ab12cd.eu-west-1.token.awswaf.com/ab12cd/captcha/problem",
        "https://api.friendlycaptcha.com/api/v1/puzzle",
        "https://global.frcapi.com/api/v2/captcha/widget",
        "https://friendlycaptcha.com/widget",
        "https://service.mtcaptcha.com/mtcv1/client/iframe.html?v=1",
        "https://smartcaptcha.yandexcloud.net/checkbox?sitekey=x",
        "HTTPS://SmartCaptcha.YandexCloud.net:443/x",
    ],
)
def test_each_captcha_vendor_frame_needs_the_user(url):
    b = blocker_for(["https://jobs.example/apply", url], "", False)
    assert b is not None and b.reason == "captcha", url


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.ashbyhq.com/geetest-careers",
        "https://jobs.lever.co/mtcaptcha/1234",
        "https://boards.greenhouse.io/friendlycaptcha/jobs/1",
        "https://jobs.example/apply?ref=smartcaptcha.yandexcloud.net",
        "https://jobs.example/captcha.awswaf.com/careers",
        "https://geetest.com.jobs.example/careers",
        "https://ab12cd.eu-west-1.token.awswaf.com/ab12cd/challenge.js",  # the silent check asks nothing
    ],
)
def test_a_vendor_word_in_an_employer_address_is_not_a_captcha(url):
    assert blocker_for(["https://jobs.example/apply", url], "Apply for Senior Engineer", False) is None


@pytest.mark.parametrize(
    "text",
    [
        "Type the characters you see in the picture",
        "Please enter the characters shown",
        "Enter the characters in the image",
        "Enter the code shown",
        "Enter the code in the image below",
        "Enter the code above to continue",
        "Solve this puzzle to continue",
    ],
)
def test_an_image_text_box_needs_the_user(text):
    b = blocker_for(["https://jobs.example/apply"], text, False)
    assert b is not None and b.reason == "captcha", text


def test_ordinary_code_wording_is_not_a_captcha():
    for text in ("Enter the code we sent to your email", "Promo code", "Postcode", "Enter the characters' names"):
        assert blocker_for(["https://jobs.example/apply"], text, False) is None, text


# ── 3-5. In a real Chromium ───────────────────────────────────────────────────

PASSWORDS = """<!doctype html><html><head><title>Sign up - Acme</title></head><body><form>
<label for="pw">Passwort</label><input id="pw" type="text">
<label for="otp">Code</label><input id="otp" type="text" autocomplete="one-time-code">
<label for="pp">Passport number</label><input id="pp" type="text">
</form></body></html>"""
# The invisible kind's challenge frame, parked off the page, holding a box that counts its keys.
HIDDEN_CHALLENGE = """<!doctype html><html><head><title>Apply - Hidden</title></head><body>
<form><label for="nm">Full name</label><input id="nm"></form>
<div style="position:absolute;top:-10000px;left:0">
  <iframe style="width:400px;height:300px" src="/recaptcha/api2/bframe?hl=en&k=abc"></iframe></div>
</body></html>"""
CHALLENGE = """<!doctype html><html><body><p>Select all images with a bus</p>
<input id="answer" onkeydown="window.__keys = (window.__keys || 0) + 1"></body></html>"""
# An option is highlighted, and the site's Enter handler submits the form with no submit event.
SUBMIT_CALL = """<!doctype html><html><head><title>Enter submit - Acme</title></head><body>
<form action="/sent" method="get">
  <label for="city">City</label>
  <input id="city" name="city" role="combobox" aria-expanded="true" aria-controls="lb" aria-activedescendant="o1"
    onkeydown="if (event.key === 'Enter') this.form.submit()">
  <ul id="lb" role="listbox"><li id="o1" role="option">Paris</li></ul>
  <button type="submit">Submit application</button>
</form></body></html>"""
# The same, with requestSubmit (which fires a submit event) on a second form.
REQUEST_SUBMIT = """<!doctype html><html><head><title>Enter request - Acme</title></head><body>
<form id="other" action="/sent" method="get"><input type="hidden" name="x" value="1"></form>
<form>
  <label for="city">City</label>
  <input id="city" role="combobox" aria-expanded="true" aria-controls="lb" aria-activedescendant="o1"
    onkeydown="if (event.key === 'Enter') document.getElementById('other').requestSubmit()">
  <ul id="lb" role="listbox"><li id="o1" role="option">Paris</li></ul>
</form></body></html>"""
SENT = "<!doctype html><html><head><title>SENT</title></head><body><h1>Thank you for applying</h1></body></html>"
TRAP_CLICK = """<!doctype html><html><head><title>Trap click - Acme</title></head><body><form>
<label><input type="checkbox" id="t1"> Leave this box blank</label>
<label><input type="radio" id="t2" name="r"> Leave this field empty</label>
<div role="checkbox" id="t3" aria-checked="false" tabindex="0" style="min-height:20px"
  onclick="this.setAttribute('aria-checked', 'true')">Do not fill this in</div>
<label><input type="checkbox" id="ok"> I agree</label>
</form></body></html>"""
PAGES = {"/passwords": PASSWORDS, "/hidden": HIDDEN_CHALLENGE, "/recaptcha/api2/bframe": CHALLENGE,
         "/submit-call": SUBMIT_CALL, "/request-submit": REQUEST_SUBMIT, "/sent": SENT, "/trap-click": TRAP_CLICK}


def _html(text):
    from aiohttp import web

    async def handler(_):
        return web.Response(text=text, content_type="text/html")

    return handler


@pytest.fixture
async def lines_pool():
    from aiohttp import web

    from src.services.browser_agent.agent import BrowserPool

    app = web.Application()
    for path, html in PAGES.items():
        app.router.add_get(path, _html(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(port))
    yield pool
    await pool.shutdown()
    await runner.cleanup()


def _page(pool):
    return pool._sessions["u1"].page  # noqa: SLF001


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_password_box_in_another_language_is_never_typed_into(lines_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(lines_pool, "u1", "open", url="http://jobs.example/passwords")
    for name in ("Passwort", "Code"):
        with pytest.raises(BrowserError) as exc:
            await run(lines_pool, "u1", "type", ref=_ref(page, name), text="hunter2")
        assert exc.value.reason == "password_field", name
    assert await _page(lines_pool).evaluate("() => pw.value + otp.value") == ""
    page = await run(lines_pool, "u1", "type", ref=_ref(page, "Passport number"), text="123456789")
    assert page["typed_matches"] is True


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_key_with_no_ref_never_reaches_a_hidden_challenge_frame(lines_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(lines_pool, "u1", "open", url="http://jobs.example/hidden")
    assert page["status"] == "ok", page.get("reason")  # hidden: it asks nothing yet
    frame = next(f for f in _page(lines_pool).frames if "recaptcha/api2/bframe" in f.url)
    await frame.locator("#answer").focus()  # the page moved the focus into the challenge
    for key in ("Tab", "ArrowDown", "ArrowUp", "Escape"):
        with pytest.raises(BrowserError) as exc:
            await run(lines_pool, "u1", "key", text=key)
        assert exc.value.reason == "captcha", key
    assert await frame.evaluate("() => window.__keys || 0") == 0
    # A key in the page's own form still works.
    page = await run(lines_pool, "u1", "key", ref=_ref(page, "Full name"), text="Tab")
    assert page["status"] == "ok"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_enter_that_calls_form_submit_is_stopped_and_refused(lines_pool):
    from src.services.browser_agent.agent import BrowserError, run

    for path, title in (("/submit-call", "Enter submit - Acme"), ("/request-submit", "Enter request - Acme")):
        page = await run(lines_pool, "u1", "open", url=f"http://jobs.example{path}")
        with pytest.raises(BrowserError) as exc:
            await run(lines_pool, "u1", "key", ref=_ref(page, "City"), text="Enter")
        assert exc.value.reason == "enter_may_submit", path
        assert (await run(lines_pool, "u1", "look"))["title"] == title  # never submitted
        # The guard came off: the form's own submit is the browser's again.
        assert await _page(lines_pool).evaluate(
            "() => HTMLFormElement.prototype.submit.toString().includes('[native code]')"
        )


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_click_never_ticks_a_trap_box(lines_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(lines_pool, "u1", "open", url="http://jobs.example/trap-click")
    for name in ("Leave this box blank", "Leave this field empty", "Do not fill this in"):
        with pytest.raises(BrowserError) as exc:
            await run(lines_pool, "u1", "click", ref=_ref(page, name))
        assert exc.value.reason == "leave_blank", name
    assert await _page(lines_pool).evaluate(
        "() => t1.checked || t2.checked || t3.getAttribute('aria-checked') === 'true'"
    ) is False
    page = await run(lines_pool, "u1", "click", ref=_ref(page, "I agree"))
    assert await _page(lines_pool).evaluate("() => ok.checked") is True
