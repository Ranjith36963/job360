"""Two live failures of a fill-only apply run (2026-10-25).

1. BambooHR (Yonder Media, application 112): the ad's "Apply for This Job" opens the form on the same page,
   but the page also shows a "Link to This Job" box the site fills with the job's address. That box read as
   a typed answer, so the gate took the opener for the final submit and the form could never be opened.
   An opener on a job ad is clicked ungated now; every real submit is still gated.
2. Greenhouse (Globalization Partners, application 113): a react-select keeps its <input> empty and draws
   the pick in a sibling ``select__single-value`` div. The look read the field as empty, so the sweep marked
   it unreadable and check_submit answered fill / unreadable_fields forever.
"""
from __future__ import annotations

import pytest

from src.services.browser_agent.safety import control_kind
from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

# ── Bug 1: an Apply button that opens the form ────────────────────────────────

# BambooHR's job page as the browser read it live: a <button type=button> outside any <form>, and a share
# box the site fills itself. The click draws the form on the same page (its /apply address is a 404).
AD = """<!doctype html><html><head><title>BambooHR</title></head><body>
<a href="/privacy">Privacy Policy</a> <a href="/careers">Job Openings</a>
<h1>Applied LLM Engineer</h1><p>ABOUT THE ROLE You write the prompts and ship the code.</p>
<button type="button" id="apply" onclick="openForm()">Apply for This Job</button>
<label>Link to This Job <input type="text" value="https://jobs.example/careers/194"></label>
<button type="button">Share LinkedIn</button>
<div id="form"></div>
<script>
function openForm() {
  document.getElementById('form').innerHTML = '<label>First Name <input type="text" name="firstName"></label>'
    + '<label>Last Name <input type="text" name="lastName"></label>'
    + '<label>Email <input type="text" name="email"></label>'
    + '<label>Resume <input type="file" name="resume"></label>'
    + '<button type="button" onclick="sent()">Submit Application</button>';
}
function sent() { document.title = 'SENT'; }
</script>
</body></html>"""

# A real form whose own submit button reads only "Apply".
FORM_APPLY = """<!doctype html><html><head><title>Apply</title></head><body><form onsubmit="return false">
<label>Full name <input type="text" name="name"></label>
<label>Email <input type="text" name="email"></label>
<button type="submit">Apply</button>
</form></body></html>"""

# A React-style form with no <form>: two fields and a plain "Apply" button. Small enough to look like an ad.
REACT_APPLY = """<!doctype html><html><head><title>Apply</title></head><body>
<label>Full name <input type="text" name="name"></label>
<label>Email <input type="text" name="email"></label>
<button type="button" onclick="document.title = 'SENT'">Apply</button>
</body></html>"""

# A signed-in form the site filled itself (nothing typed by us), with no <form>: still a form.
PREFILLED = """<!doctype html><html><head><title>Apply</title></head><body>
<label>Full name <input type="text" name="name" value="Ada Lovelace"></label>
<label>Email <input type="text" name="email" value="ada@example.com"></label>
<label>Phone <input type="text" name="phone" value="+44 1234"></label>
<button type="button" onclick="document.title = 'SENT'">Apply</button>
</body></html>"""

# An Easy-Apply-style last step: no <form>, two fields the site filled itself (one required), nothing typed
# by us, a plain "Apply". Few fields and no file box, but a required field means it asks for an answer.
ONE_CLICK = """<!doctype html><html><head><title>Apply</title></head><body>
<label>Email <input type="text" name="email" value="ada@example.com" required></label>
<label>Phone <input type="text" name="phone" value="+44 1234"></label>
<button type="button" onclick="document.title = 'SENT'">Apply</button>
</body></html>"""

PAGES = {"/careers/194": AD, "/one-click": ONE_CLICK, "/form-apply": FORM_APPLY, "/react-apply": REACT_APPLY, "/prefilled": PREFILLED}


@pytest.fixture
async def pages_site():
    from aiohttp import web

    def handler(html):
        async def serve(_):
            return web.Response(text=html, content_type="text/html")
        return serve

    app = web.Application()
    for path, html in {**PAGES, **SELECT_PAGES}.items():
        app.router.add_get(path, handler(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001 - the test site's own port
    await runner.cleanup()


@pytest.fixture
async def pool(pages_site):
    from src.services.browser_agent.agent import BrowserPool

    p = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(pages_site))
    yield p
    await p.shutdown()


def _counting_gate(calls: list[str]):
    from src.services.browser_agent.agent import Gate

    async def check(form_url):
        calls.append(form_url)
        return {"decision": "fill", "reason": "form_not_recorded", "detail": "Fill the form first."}

    async def mark():
        calls.append("submit_attempted")

    return Gate(check=check, mark_attempt=mark)


@pytest.mark.parametrize(
    ("in_form", "answers", "page_is_ad", "extra", "name", "kind"),
    [
        # The ad's opener: outside any form, on a page that holds no form and no answer of ours.
        (False, True, True, {}, "Apply for This Job", "field"),
        (False, True, True, {}, "Apply now", "field"),
        (False, True, False, {}, "Apply for This Job", "submit"),   # the page is a form, or was answered
        # Inside a form, or able to submit one, "Apply" is the end whatever the page looks like.
        (True, True, True, {}, "Apply", "submit"),
        (False, True, True, {"owns_form": True}, "Apply", "submit"),
        # "Submit" and "send ... application" are never openers.
        (False, True, True, {}, "Submit application", "submit"),
        (False, True, True, {}, "Send application", "submit"),
        (False, True, True, {}, "Postuler ma candidature", "submit"),
    ],
)
def test_an_opener_on_an_ad_is_not_the_final_submit(in_form, answers, page_is_ad, extra, name, kind):
    assert control_kind("button", "button", name, "", in_form, "", answers, page_is_ad=page_is_ad, **extra) == kind


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_apply_for_this_job_opens_the_form_ungated(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/careers/194")
    assert page["application_form"] is False
    assert _el(page, "Link to This Job")["required"] is False   # as the live look read it, 2026-10-25
    calls: list[str] = []
    out = await run(pool, "u1", "click", ref=_ref(page, "Apply for This Job"), gate=_counting_gate(calls))
    assert out["status"] == "ok", out
    assert calls == []                                   # check_submit was never asked: it is not a submit
    assert out.get("submit_clicked") is None and out["page_changed"] is True
    assert {"First Name", "Email", "Resume"} <= {e["name"] for e in out["elements"]}
    # The form it opened keeps its final submit gated, once an answer is typed and before.
    submit = _ref(out, "Submit Application")
    refused = await run(pool, "u1", "click", ref=submit, gate=_counting_gate(calls))
    assert refused["status"] == "refused" and refused["gate_reason"] == "form_not_recorded"
    out = await run(pool, "u1", "type", ref=_ref(out, "First Name"), text="Ada")
    refused = await run(pool, "u1", "click", ref=submit, gate=_counting_gate(calls))
    assert refused["status"] == "refused" and refused["decision"] == "fill"
    assert len([c for c in calls if c != "submit_attempted"]) == 2 and "submit_attempted" not in calls
    assert (await run(pool, "u1", "look"))["title"] == "BambooHR"   # never sent


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_filled_forms_apply_button_is_still_gated(pool):
    from src.services.browser_agent.agent import run

    for path in ("/form-apply", "/react-apply"):
        page = await run(pool, "u1", "open", url=f"http://jobs.example{path}")
        page = await run(pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada Lovelace")
        calls: list[str] = []
        out = await run(pool, "u1", "click", ref=_ref(page, "Apply"), gate=_counting_gate(calls))
        assert out["status"] == "refused" and out["reason"] == "check_submit", (path, out)
        assert calls == [f"http://jobs.example{path}"], path
        assert (await run(pool, "u1", "look"))["title"] == "Apply", path


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_form_the_site_filled_itself_keeps_its_apply_gated(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/prefilled")
    calls: list[str] = []
    out = await run(pool, "u1", "click", ref=_ref(page, "Apply"), gate=_counting_gate(calls))
    assert out["status"] == "refused" and calls == ["http://jobs.example/prefilled"]
    assert (await run(pool, "u1", "look"))["title"] == "Apply"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_one_click_page_with_a_required_site_filled_field_keeps_its_apply_gated(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/one-click")
    calls: list[str] = []
    out = await run(pool, "u1", "click", ref=_ref(page, "Apply"), gate=_counting_gate(calls))
    assert out["status"] == "refused" and out["reason"] == "check_submit", out
    assert calls == ["http://jobs.example/one-click"]
    assert (await run(pool, "u1", "look"))["title"] == "Apply"   # never sent


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_submit_application_is_still_gated_on_the_ad_page(pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(pool, "u1", "open", url="http://jobs.example/careers/194")
    page = await run(pool, "u1", "click", ref=_ref(page, "Apply for This Job"))
    with pytest.raises(BrowserError) as gated:   # no application: refused outright, never clicked
        await run(pool, "u1", "click", ref=_ref(page, "Submit Application"))
    assert gated.value.reason == "submit_needs_application"


# ── Bug 2: react-select values read back ──────────────────────────────────────

# Greenhouse's react-select (classNamePrefix "select"): the <input> stays empty; the pick is drawn in a
# sibling div.select__single-value; a hidden input carries the value. Two of them, so one pick cannot
# be read as the other's.
_REACT_SELECT = """
<div class="field"><label id="{id}-l" for="{id}">{label}</label>
<div class="select__container css-b62m3t-container">
  <div class="select__control">
    <div class="select__value-container">
      <div class="select__placeholder" id="{id}-ph">Select...</div>
      <div class="select__input-container">
        <input class="select__input" id="{id}" role="combobox" aria-expanded="false" aria-autocomplete="list"
               aria-labelledby="{id}-l" value="" autocomplete="off">
      </div>
    </div>
  </div>
  <div class="select__menu" id="{id}-menu" hidden>
    <div class="select__option" role="option">Yes</div><div class="select__option" role="option">No</div>
  </div>
  <input type="hidden" name="{id}" value="">
</div></div>"""

SELECT_JS = """<script>
document.querySelectorAll('.select__container').forEach((box) => {
  const input = box.querySelector('.select__input'), menu = box.querySelector('.select__menu');
  input.addEventListener('mousedown', () => { menu.hidden = false; input.setAttribute('aria-expanded', 'true'); });
  box.querySelectorAll('.select__option').forEach((o) => o.addEventListener('click', () => {
    const vc = box.querySelector('.select__value-container');
    const old = vc.querySelector('.select__placeholder, .select__single-value');
    const one = document.createElement('div');
    one.className = 'select__single-value css-1dimb5e-singleValue';
    one.textContent = o.textContent;
    old.replaceWith(one);
    box.querySelector('input[type=hidden]').value = o.textContent;
    menu.hidden = true; input.setAttribute('aria-expanded', 'false');
    document.getElementById('live').textContent = 'option ' + o.textContent + ', selected.';
  }));
});
</script>"""

SELECTS = (
    "<!doctype html><html><head><title>Greenhouse</title></head><body><div id='live' aria-live='polite'></div>"
    + _REACT_SELECT.format(id="auth", label="Are you authorized to work in the UK?")
    + _REACT_SELECT.format(id="spons", label="Will you need sponsorship?")
    + "<label>First Name <input type='text' name='first'></label>"
    + SELECT_JS + "</body></html>"
)

# A react-select build that draws no single-value div: only the hidden input holds the pick.
HIDDEN_ONLY = """<!doctype html><html><head><title>Greenhouse</title></head><body>
<label id="c-l">Country</label>
<div class="select__container"><div class="select__control"><div class="select__value-container">
<div class="select__input-container"><input class="select__input" role="combobox" aria-labelledby="c-l" value="">
</div></div></div><input type="hidden" name="country" value="United Kingdom"></div>
</body></html>"""

SELECT_PAGES = {"/selects": SELECTS, "/hidden-only": HIDDEN_ONLY}


def _el(page: dict, name: str) -> dict:
    return next(e for e in page["elements"] if e["name"].startswith(name))


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_react_select_pick_is_read_back_as_its_value(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/selects")
    assert _el(page, "Are you authorized")["value"] == ""   # the placeholder is not a value
    page = await run(pool, "u1", "choose", ref=_el(page, "Are you authorized")["ref"], text="Yes")
    assert _el(page, "Are you authorized")["value"] == "Yes"
    assert _el(page, "Will you need sponsorship")["value"] == ""   # one pick never reads as the other's
    out = await run(pool, "u1", "fill", fields=[{"ref": _el(page, "Will you need sponsorship")["ref"], "text": "No"}])
    assert out["filled"] == [{"ref": _el(page, "Will you need sponsorship")["ref"], "ok": True, "value": "No",
                              "reason": ""}]
    assert _el(out, "Will you need sponsorship")["value"] == "No"
    assert _el(out, "Are you authorized")["value"] == "Yes"
    assert _el(out, "First Name")["value"] == ""


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_react_select_with_no_drawn_pick_reads_its_hidden_input(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/hidden-only")
    assert _el(page, "Country")["value"] == "United Kingdom"
