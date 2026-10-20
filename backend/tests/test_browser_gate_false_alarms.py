"""False 'submit' readings of the submit gate (review of #943, docs/product/BROWSER_AGENT.md).

A false 'submit' is not free: mid-form the click is refused, and late in the form check_submit can
record submit_attempted, so the REAL submit is then refused. Each row here was read wrong before.
"""
from __future__ import annotations

import pytest

from src.services.browser_agent.safety import blocker_for, control_kind
from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

_EU_VERBS = ("absenden", "senden", "bewerben", "envoyer", "postuler", "soumettre", "enviar", "solicitar",
             "inviare", "candidati", "verzenden", "solliciteren")


@pytest.mark.parametrize(
    ("tag", "input_type", "name", "in_form", "role", "answers", "extra", "kind"),
    [
        # A typeless button in a shadow root inside a form has no form owner: it cannot submit it.
        ("button", "", "Attach", True, "", False, {"owns_form": False}, "field"),
        ("button", "", "Attach", True, "", False, {"owns_form": True}, "submit"),
        ("button", "", "Envoyer ma candidature", True, "", False, {"owns_form": False}, "submit"),
        ("div", "", "Send application", False, "button", True, {"owns_form": False}, "submit"),
        # A link-like role names the end with the whole word "submit".
        ("div", "", "I have submitted before", False, "option", True, {}, "field"),
        ("div", "", "Submitted applications", False, "tab", True, {}, "field"),
        ("a", "", "Submitted applications", False, "", True, {}, "field"),
        ("div", "", "Submit application", False, "tab", True, {}, "submit"),
        ("span", "", "Submit", True, "switch", False, {}, "submit"),
        # Other languages: the end once answers are held; a link names it with the application.
        ("div", "", "Bewerbung absenden", False, "button", True, {}, "submit"),
        ("div", "", "Postuler", False, "button", True, {}, "submit"),
        ("div", "", "Postuler", False, "button", False, {}, "field"),
        ("button", "button", "Postuler", False, "", False, {}, "field"),
        ("a", "", "Bewerbung absenden", False, "", True, {}, "submit"),
        ("a", "", "Envoyer ma candidature", False, "", True, {}, "submit"),
        ("a", "", "Send application", False, "", True, {}, "submit"),
        ("a", "", "Postuler", False, "", True, {}, "field"),
        ("a", "", "Postuler", False, "", False, {}, "field"),
        # A pressed-or-not toggle (Ashby's Yes / No) is a choice, not a submit, just for being a button.
        ("button", "submit", "Yes", True, "", True, {"owns_form": True, "toggle": True}, "field"),
        ("button", "submit", "", True, "", True, {"owns_form": True, "toggle": True}, "field"),
        ("button", "submit", "Yes", True, "", True, {"owns_form": True}, "submit"),
        ("button", "submit", "Submit application", True, "", True, {"owns_form": True, "toggle": True}, "submit"),
    ],
)
def test_control_kind_false_alarms(tag, input_type, name, in_form, role, answers, extra, kind):
    assert control_kind(tag, input_type, name, "", in_form, role, answers, **extra) == kind


@pytest.mark.parametrize("verb", _EU_VERBS)
def test_every_eu_submit_verb_is_the_end_once_answers_are_held(verb):
    assert control_kind("div", "", verb.capitalize(), "", False, "button", True) == "submit"
    assert control_kind("div", "", verb.capitalize(), "", False, "button", False) == "field"


def test_turnstile_is_cloudflares_challenge_host_not_the_word():
    assert blocker_for(["https://jobs.ashbyhq.com/turnstile/abc"], "", False) is None
    frame = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv0/0/abc"
    assert blocker_for(["https://jobs.ashbyhq.com/turnstile/abc", frame], "", False).reason == "captcha"


# A job ad with a cookie switch and a view toggle already on when the page loads.
AD = """<!doctype html><html><head><title>Ad - Acme</title></head><body>
<div id="cookies" role="switch" aria-checked="true" tabindex="0">Allow cookies</div>
<button id="list" aria-pressed="true">List view</button>
<div id="agree" role="checkbox" aria-checked="false" tabindex="0">Keep me posted</div>
<button onclick="document.title='OPENED'">Apply</button>
</body></html>"""

# React-style: the framework ticks by setting the checked / selected ATTRIBUTE, which moves
# defaultChecked / defaultSelected too. No <form>.
TICKS = """<!doctype html><html><head><title>Ticks - Acme</title></head><body>
<label><input type="checkbox" id="consent"> I consent</label>
<label><input type="checkbox" id="news" checked> Newsletter</label>
<label for="when">Notice period</label>
<select id="when"><option value="">Select…</option><option id="m1">1 month</option></select>
<button type="button" onclick="document.title='SUBMITTED'">Send application</button>
</body></html>"""

SHADOW_IN_FORM = """<!doctype html><html><head><title>Shadow form - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';"><spl-step id="host"></spl-step></form>
<script>
  const root = document.getElementById('host').attachShadow({ mode: 'open' });
  root.innerHTML = '<button id="attach">Attach</button><button>Envoyer ma candidature</button>';
</script></body></html>"""


@pytest.fixture
async def alarm_site():
    from aiohttp import web

    pages = {"/ad": AD, "/ticks": TICKS, "/shadow-form": SHADOW_IN_FORM}

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
async def test_a_switch_on_at_load_is_not_an_answer_but_a_tick_is(alarm_site):
    from src.services.browser_agent.agent import BrowserPool, _kind, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(alarm_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/ad")
        tab = pool._sessions["u1"].page  # noqa: SLF001
        apply = tab.locator(f'[data-j360-ref="{_ref(page, "Apply")}"]')
        assert await _kind(apply) == "field"   # the ad's Apply opens the form
        page = await run(pool, "u1", "click", ref=_ref(page, "Apply"))
        assert await tab.title() == "OPENED"
        # Off and back on is where the page started: still not an answer.
        await tab.evaluate("() => { const c = document.getElementById('cookies'); "
                           "c.setAttribute('aria-checked', 'false'); c.setAttribute('aria-checked', 'true'); }")
        assert await _kind(apply) == "field"
        # A box the page held off, now on: an answer.
        await tab.evaluate("() => document.getElementById('agree').setAttribute('aria-checked', 'true')")
        assert await _kind(apply) == "submit"
        await tab.evaluate("() => document.getElementById('agree').setAttribute('aria-checked', 'false')")
        # An aria-pressed added after the first read was never stamped: it counts (the safe side).
        await tab.evaluate("() => { const b = document.createElement('button'); b.textContent = 'Yes'; "
                           "b.setAttribute('aria-pressed', 'true'); document.body.appendChild(b); }")
        await run(pool, "u1", "look")
        assert await _kind(apply) == "submit"
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_tick_set_through_the_checked_attribute_is_an_answer(alarm_site):
    from src.services.browser_agent.agent import BrowserPool, _kind, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(alarm_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/ticks")
        tab = pool._sessions["u1"].page  # noqa: SLF001
        send = tab.locator(f'[data-j360-ref="{_ref(page, "Send application")}"]')
        assert await _kind(send) == "field"   # the newsletter box was ticked at load
        await tab.evaluate("() => document.getElementById('consent').setAttribute('checked', '')")
        assert await _kind(send) == "submit"
        await tab.evaluate("() => { const c = document.getElementById('consent'); "
                           "c.removeAttribute('checked'); c.checked = false; }")
        assert await _kind(send) == "field"
        await tab.evaluate("() => document.getElementById('m1').setAttribute('selected', '')")
        assert await _kind(send) == "submit"
        assert await tab.title() == "Ticks - Acme"
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_shadow_button_has_no_form_owner_but_a_send_word_still_reaches_the_gate(alarm_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, _kind, run
    from src.services.browser_agent.page_js import ELEMENT_FACTS_JS

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(alarm_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/shadow-form")
        tab = pool._sessions["u1"].page  # noqa: SLF001
        attach = tab.locator(f'[data-j360-ref="{_ref(page, "Attach")}"]')
        facts = await attach.evaluate(ELEMENT_FACTS_JS)
        assert facts["in_form"] is True and facts["owns_form"] is False
        assert await _kind(attach) == "field"
        await run(pool, "u1", "click", ref=_ref(page, "Attach"))
        assert await tab.title() == "Shadow form - Acme"   # it really cannot submit the form
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=_ref(page, "Envoyer ma candidature"))
        assert exc.value.reason == "submit_needs_application"
    finally:
        await pool.shutdown()
