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
