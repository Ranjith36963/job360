"""Review fixes for acting on the right thing (audit of main, 2026-10-20):

* a form section the site copies (cloneNode) copies our ref too: each read gives the copy its own
  ref, and an action on a ref that now matches two controls is refused, never sent to the first;
* an upload is confirmed only by the file name newly shown on the page, never by the file box
  alone, and an error the site writes next to the field is reported (as page text);
* a tall, auto-sized frame (no scroll of its own) is read from what the user sees after the outer
  page scrolls, and its submit button is always listed.
"""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

CLONE = """<!doctype html><html><head><title>Jobs - Acme</title></head><body>
<form id="f"><div class="job"><label>Employer <input class="employer"></label></div>
<button type="button" id="add">Add another job</button></form>
<script>
document.getElementById('add').onclick = () => {
  const s = document.querySelector('.job');
  s.parentNode.insertBefore(s.cloneNode(true), document.getElementById('add'));
};
</script></body></html>"""
UPLOAD_FAILS = """<!doctype html><html><head><title>Upload - Acme</title></head><body>
<div><label for="cv">Resume</label><input id="cv" type="file"><span id="msg"></span></div>
<script>document.getElementById('cv').onchange = () => {
  document.getElementById('msg').textContent = 'Upload failed: file type not supported. Please try again.';
};</script></body></html>"""
# The file name is on the page BEFORE the upload, and the site then shows nothing new.
UPLOAD_SEEN = """<!doctype html><html><head><title>Upload - Acme</title></head><body>
<p>Your last upload: Ada_CV.pdf</p>
<div><label for="cv">Resume</label><input id="cv" type="file"></div></body></html>"""
# An auto-sized frame as tall as its 200 questions: the frame never scrolls, the outer page does.
TALL = """<!doctype html><html><head><title>Tall - Acme</title></head><body><h1>Apply</h1>
<iframe src="/tall-inner" style="width:800px;height:6100px;border:0" scrolling="no"></iframe></body></html>"""
TALL_INNER = "<!doctype html><html><body style='margin:0'><form onsubmit='event.preventDefault()'>" + "".join(
    f"<div style='height:30px'><label for='q{i}'>Question {i}</label><input id='q{i}'></div>" for i in range(200)
) + "<button type='submit'>Submit application</button></form></body></html>"


@pytest.fixture
async def actions_site():
    from aiohttp import web

    def page(html):
        async def handler(_):
            return web.Response(text=html, content_type="text/html")

        return handler

    app = web.Application()
    for path, html in (("/clone", CLONE), ("/upload-fails", UPLOAD_FAILS), ("/upload-seen", UPLOAD_SEEN),
                       ("/tall", TALL), ("/tall-inner", TALL_INNER)):
        app.router.add_get(path, page(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@pytest.fixture
async def pool(actions_site):
    from src.services.browser_agent.agent import BrowserPool

    p = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(actions_site))
    yield p
    await p.shutdown()


async def _cv(kind):
    return "Ada_CV.pdf", b"%PDF-1.4 test"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_copied_form_section_gets_its_own_ref_and_typing_lands_in_the_copy(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/clone")
    page = await run(pool, "u1", "click", ref=_ref(page, "Add another job"))   # the copy carries the ref
    refs = [e["ref"] for e in page["elements"] if e["name"] == "Employer"]
    assert len(refs) == 2 and len(set(refs)) == 2, refs
    await run(pool, "u1", "type", ref=refs[1], text="Acme")
    values = await pool._sessions["u1"].page.eval_on_selector_all(  # noqa: SLF001
        ".employer", "els => els.map(e => e.value)")
    assert values == ["", "Acme"]


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_ref_that_matches_two_controls_is_refused_not_sent_to_the_first(pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(pool, "u1", "open", url="http://jobs.example/clone")
    ref = _ref(page, "Employer")
    s = pool._sessions["u1"]  # noqa: SLF001
    await s.page.click("#add")   # the site copies the section with no look in between
    with pytest.raises(BrowserError) as exc:
        await run(pool, "u1", "type", ref=ref, text="Acme")
    assert exc.value.reason == "ref_not_found" and "look" in exc.value.detail
    assert await s.page.eval_on_selector_all(".employer", "els => els.map(e => e.value)") == ["", ""]


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_an_upload_the_site_says_failed_is_not_confirmed(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/upload-fails")
    page = await run(pool, "u1", "upload", ref=_ref(page, "Resume"), doc_kind="cv", files=_cv)
    assert next(e for e in page["elements"] if e["name"] == "Resume")["value"] == "Ada_CV.pdf"  # the box has it
    assert page["upload_confirmed"] is False
    assert "Upload failed" in page["upload_error"] and page["upload_error"].startswith("<<page-text ")


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_file_name_already_on_the_page_does_not_confirm_an_upload(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/upload-seen")
    page = await run(pool, "u1", "upload", ref=_ref(page, "Resume"), doc_kind="cv", files=_cv)
    assert page["upload_confirmed"] is False
    assert "upload_error" not in page


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_tall_frame_is_read_from_what_the_outer_scroll_shows(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/tall")
    names = [e["name"] for e in page["elements"]]
    assert "Question 0" in names and "Submit application" in names   # its submit is always listed
    for _ in range(7):
        page = await run(pool, "u1", "scroll", text="down")
    names = [e["name"] for e in page["elements"]]
    assert "Question 199" in names and "Question 0" not in names, names[:3]
    assert page["more_elements"]["above"] > 0
