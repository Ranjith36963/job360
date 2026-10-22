"""A non-English final submit on a form with no <form> (Ashby-style) always reaches the gate.

Ashby draws its form in plain <div>s, with a plain <button> at the end. On such a page the gate
(safety.control_kind) has no form to go by: only the button's name says it is the end. "Invia
candidatura", "Verstuur sollicitatie" and "Bewerbung abschicken" used to read as ordinary buttons,
so a click went through with no check_submit. The look (page_js.SNAPSHOT_JS) builds its submit regex
from the gate's own patterns, so the two cannot name the end differently.
"""
from __future__ import annotations

import json
import re

import pytest

from src.services.browser_agent.safety import (
    _APPLICATION_WORD,
    _STEP_WORDS,
    _STRONG_SUBMIT,
    _SUBMIT_WORDS,
    control_kind,
)
from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

# Each is the last button of a real-world EU application form.
FINAL_SUBMITS = (
    "Bewerbung absenden", "Envoyer ma candidature", "Enviar solicitud",
    "Invia candidatura", "Verstuur sollicitatie", "Bewerbung abschicken", "Bewerbung einreichen",
    "Sollicitatie indienen", "Valider ma candidature", "Postular", "Solliciteer",
)
STEPS = ("Weiter", "Zurück", "Suivant", "Précédent", "Continuer", "Siguiente", "Anterior", "Continuar",
         "Avanti", "Indietro", "Volgende", "Vorige", "Weiter zur Bewerbung", "Continuar con la solicitud")

ASHBY = """<!doctype html><html><head><title>Apply - Acme</title></head><body>
<div class="ashby-application-form-container">
  <label for="n">Name</label><input id="n">
  <button {typ} onclick="document.title='SUBMITTED'">{name}</button>
</div></body></html>"""


@pytest.fixture
async def ashby_site():
    from aiohttp import web

    async def page(request):
        html = ASHBY.replace("{name}", request.query["name"]).replace("{typ}", request.query.get("typ", ""))
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/apply", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
@pytest.mark.parametrize("typ", ["", 'type="button"'])
@pytest.mark.parametrize("name", FINAL_SUBMITS[:6])
async def test_a_non_english_submit_with_no_form_is_never_clicked_without_the_gate(ashby_site, name, typ):
    from urllib.parse import urlencode

    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(ashby_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/apply?" + urlencode({"name": name, "typ": typ}))
        page = await run(pool, "u1", "type", ref=_ref(page, "Name"), text="Ada Lovelace")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=_ref(page, name))
        assert exc.value.reason == "submit_needs_application"
        assert await pool._sessions["u1"].page.title() == "Apply - Acme"  # noqa: SLF001
    finally:
        await pool.shutdown()


@pytest.mark.parametrize("name", FINAL_SUBMITS)
@pytest.mark.parametrize("input_type", ["", "submit", "button"])
def test_the_gate_reads_each_final_submit_on_a_page_with_answers(name, input_type):
    assert control_kind("button", input_type, name, "", False, "", True, owns_form=False) == "submit"
    assert control_kind("div", "", name, "", False, "button", True) == "submit"


@pytest.mark.parametrize("name", STEPS)
def test_a_next_or_back_button_is_a_step_even_when_it_submits_its_form(name):
    # Multi-page forms use <button type=submit> for "Weiter": a false 'submit' refuses the click mid-form.
    assert control_kind("button", "submit", name, "", True, "", True, owns_form=True) == "field"
    assert control_kind("button", "", name, "", False, "", True, owns_form=False) == "field"


def test_a_step_word_does_not_hide_a_send_of_the_application():
    assert control_kind("button", "", "Weiter und Bewerbung absenden", "", True, "", True) == "submit"
    assert control_kind("button", "", "Continuar y enviar solicitud", "", True, "", True) == "submit"


def test_a_job_ad_button_naming_the_application_still_opens_the_form():
    # Nothing typed and no form: the ad's button, as before.
    assert control_kind("button", "button", "Bewerbung starten", "", False, "", False) == "field"
    assert control_kind("a", "", "Valider ma candidature", "", False, "", True) == "field"
    # A tab or a section toggle named only by the noun is not the end.
    assert control_kind("button", "button", "Application", "", False, "tab", True) == "field"
    assert control_kind("button", "button", "Ma candidature", "", False, "tab", True) == "field"
    assert control_kind("button", "button", "Bewerbung", "", False, "", True) == "field"


def test_the_look_builds_its_submit_regex_from_the_gates_patterns():
    from src.services.browser_agent.page_js import SNAPSHOT_JS

    for pattern in (_STRONG_SUBMIT, _SUBMIT_WORDS, _APPLICATION_WORD):
        assert json.dumps(pattern.pattern) in SNAPSHOT_JS, pattern.pattern
    assert "apply|send|finish" not in SNAPSHOT_JS.replace(json.dumps(_SUBMIT_WORDS.pattern), "")
    # JavaScript's \b is ASCII only: a word starting or ending in "ü" would never match in the look.
    for pattern in (_SUBMIT_WORDS, _APPLICATION_WORD):
        for word in re.search(r"\((.*)\)", pattern.pattern).group(1).split("|"):
            assert re.fullmatch(r"[a-z](?:.*[a-z])?", word), word
    assert _STEP_WORDS.search("zurück")


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_look_and_the_gate_name_the_same_buttons_as_the_end():
    from playwright import async_api  # needs_chromium already guards this test
    names = (*FINAL_SUBMITS, *STEPS, "Submit", "Upload CV", "Bewerbungsunterlagen", "Apply", "Senden")
    async with async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            js = await page.evaluate(
                "([pats, names]) => names.map((n) => pats.some((p) => new RegExp(p, 'i').test(n)))",
                [[_STRONG_SUBMIT.pattern, _SUBMIT_WORDS.pattern, _APPLICATION_WORD.pattern], list(names)],
            )
            py = [any(p.search(n) for p in (_STRONG_SUBMIT, _SUBMIT_WORDS, _APPLICATION_WORD)) for n in names]
            assert js == py, list(zip(names, js, py))
        finally:
            await browser.close()


TALL_FRAME = """<!doctype html><html><body>
""" + "".join(f'<label for="q{i}">Question {i}</label><input id="q{i}"><br>' for i in range(60)) + """
<button>{name}</button></body></html>"""


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
@pytest.mark.parametrize("name", FINAL_SUBMITS[:3])
async def test_a_tall_frame_keeps_its_non_english_submit_on_the_list(name):
    # A frame's part of the screen shows only its first questions; its submit is always listed.
    from playwright import async_api  # needs_chromium already guards this test

    from src.services.browser_agent.page_js import SNAPSHOT_JS

    async with async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(TALL_FRAME.replace("{name}", name))
            snap = await page.evaluate(
                SNAPSHOT_JS, {"maxElements": 5, "maxText": 200, "vTop": 0, "vBottom": 100, "keepSubmit": True},
            )
            assert name in [e["name"] for e in snap["elements"]], snap["elements"]
        finally:
            await browser.close()
