"""Hard-line fixes from the review of the browser agent stack (docs/product/BROWSER_AGENT.md).

* Enter is pressed only inside an OPEN suggestion list: a closed combobox in a form submits on
  Enter (implicit submission), which would skip check_submit.
* Greenhouse's invisible reCAPTCHA is not a CAPTCHA; its image challenge and a checkbox widget are.
"""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium


def test_only_a_recaptcha_challenge_or_checkbox_needs_the_user():
    from src.services.browser_agent.safety import blocker_for

    page = ["https://job-boards.greenhouse.io/acme/jobs/1"]
    invisible = "https://www.google.com/recaptcha/enterprise/anchor?ar=1&k=abc&size=invisible&cb=x"
    checkbox = "https://www.google.com/recaptcha/api2/anchor?ar=1&k=abc&size=normal"
    challenge = "https://www.google.com/recaptcha/enterprise/bframe?hl=en&k=abc"
    assert blocker_for([*page, invisible], "Apply for this job", False) is None
    assert blocker_for([*page, checkbox], "", False).reason == "captcha"
    assert blocker_for([*page, invisible, challenge], "", False).reason == "captcha"
    assert blocker_for([*page, "https://newassets.hcaptcha.com/captcha/v1/x"], "", False).reason == "captcha"


CLOSED_COMBO = """<!doctype html><html><head><title>Combo - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';">
  <label for="loc">Location</label>
  <input id="loc" role="combobox" aria-expanded="false" aria-autocomplete="list">
  <button type="submit">Submit application</button>
</form></body></html>"""


@pytest.fixture
async def closed_combo_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=CLOSED_COMBO, content_type="text/html")

    app = web.Application()
    app.router.add_get("/combo", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_enter_in_a_closed_combobox_is_refused_because_it_would_submit(closed_combo_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(
        max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(closed_combo_site)
    )
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/combo")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "key", ref=_ref(page, "Location"), text="Enter")
        assert exc.value.reason == "enter_may_submit"
        assert await pool._sessions["u1"].page.title() == "Combo - Acme"  # noqa: SLF001 - never submitted
    finally:
        await pool.shutdown()


# A form drawn in open shadow roots (SmartRecruiters' spl-* widgets) with no <form>, and a page
# answered only by picks: the gate must still see the answers, and a button inside a shadow root
# still sits in the <form> around its host.
SHADOW_FORM = """<!doctype html><html><head><title>Shadow - Acme</title></head><body>
<spl-form id="host"></spl-form>
<script>
  const root = document.getElementById('host').attachShadow({ mode: 'open' });
  root.innerHTML = '<label>First name <input id="first"></label>'
    + '<div role="button" tabindex="0" onclick="document.title=\\'SUBMITTED\\'">Send application</div>';
</script></body></html>"""

SHADOW_IN_FORM = """<!doctype html><html><head><title>Shadow form - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';"><spl-step id="host"></spl-step></form>
<script>
  const root = document.getElementById('host').attachShadow({ mode: 'open' });
  root.innerHTML = '<button onclick="document.title=\\'SUBMITTED\\'">Envoyer ma candidature</button>';
</script></body></html>"""

PICKS = """<!doctype html><html><head><title>Picks - Acme</title></head><body>
<label for="when">Notice period</label>
<select id="when"><option value="">Select…</option><option>1 month</option><option>3 months</option></select>
<label><input type="radio" name="visa" value="yes"> Yes</label>
<label><input type="radio" name="visa" value="no"> No</label>
<div id="agree" role="checkbox" aria-checked="false" tabindex="0">I agree</div>
<button type="button" onclick="document.title='SUBMITTED'">Send application</button>
</body></html>"""


@pytest.fixture
async def gate_site():
    from aiohttp import web

    pages = {"/shadow": SHADOW_FORM, "/shadow-form": SHADOW_IN_FORM, "/picks": PICKS}

    async def page(request):
        return web.Response(text=pages[request.path], content_type="text/html")

    app = web.Application()
    for path in pages:
        app.router.add_get(path, page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_shadow_dom_form_and_a_picks_only_page_still_reach_the_gate(gate_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, _kind, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(gate_site))
    try:
        # Shadow DOM, no <form>: the typed answer lives in the shadow root.
        page = await run(pool, "u1", "open", url="http://jobs.example/shadow")
        await run(pool, "u1", "type", ref=_ref(page, "First name"), text="Ada")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=_ref(page, "Send application"))
        assert exc.value.reason == "submit_needs_application"
        tab = pool._sessions["u1"].page  # noqa: SLF001
        assert await tab.title() == "Shadow - Acme"   # never submitted

        # A native button in a shadow root whose host sits in a <form> submits that form.
        page = await run(pool, "u1", "open", url="http://jobs.example/shadow-form")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=_ref(page, "Envoyer ma candidature"))
        assert exc.value.reason == "submit_needs_application"
        assert await tab.title() == "Shadow form - Acme"

        # Nothing typed, only picks: a chosen option, a ticked radio, an aria-checked box each count.
        page = await run(pool, "u1", "open", url="http://jobs.example/picks")
        send = tab.locator(f'[data-j360-ref="{_ref(page, "Send application")}"]')
        assert await _kind(send) == "field"   # nothing answered yet: an ad's button opens the form
        page = await run(pool, "u1", "choose", ref=_ref(page, "Notice period"), text="3 months")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=_ref(page, "Send application"))
        assert exc.value.reason == "submit_needs_application"
        await tab.evaluate("() => { document.getElementById('when').selectedIndex = 0; }")
        assert await _kind(send) == "field"
        await tab.check("input[value=yes]")
        assert await _kind(send) == "submit"
        await tab.evaluate("() => { document.querySelector('input[value=yes]').checked = false; "
                           "document.getElementById('agree').setAttribute('aria-checked', 'true'); }")
        assert await _kind(send) == "submit"
        assert await tab.title() == "Picks - Acme"
    finally:
        await pool.shutdown()
