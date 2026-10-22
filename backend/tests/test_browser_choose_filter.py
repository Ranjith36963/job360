"""`choose` on a long or virtualized custom dropdown (a ~250-country phone list): when no drawn option
matches, the agent types the asked-for text into the field once (real keyboard, old text cleared),
lets the list redraw, reads the options again and picks again. Same error as before if still no match.
"""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium
from tests.test_browser_hardening_typing import COMBO, _html

# Only the first 5 rows are drawn until the field is typed into, then only the matches are drawn.
VIRTUAL = """<!doctype html><html><head><title>Virtual - Acme</title></head><body>
<label for="cc">Country code</label>
<input id="cc" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="vl"
  oninput="draw(this.value)" onfocus="draw(this.value)" onclick="draw(this.value)">
<ul id="vl" role="listbox" hidden></ul>
<script>
const ALL = ["Afghanistan", "Albania", "Algeria", "Andorra", "Angola"]
  .concat(Array.from({length: 240}, (_, i) => "Country " + i), ["Zimbabwe", "Zambia Republic"]);
function draw(q) {
  const list = document.getElementById('vl');
  const rows = q ? ALL.filter(c => c.toLowerCase().includes(q.toLowerCase())) : ALL.slice(0, 5);
  list.innerHTML = '';
  for (const c of rows) {
    const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = c;
    li.onclick = () => { document.getElementById('cc').value = c; list.hidden = true; };
    list.appendChild(li);
  }
  list.hidden = false;
  document.getElementById('cc').setAttribute('aria-expanded', 'true');
}
</script></body></html>"""
# A combobox box holding its own search input; the pick is drawn beside it (the input stays empty).
INNER = """<!doctype html><html><head><title>Inner - Acme</title></head><body>
<div role="combobox" aria-label="Phone country" aria-expanded="false" tabindex="-1">
  <span id="chosen"></span><input id="q" aria-label="search" oninput="draw(this.value)" onfocus="draw(this.value)"></div>
<ul id="il" role="listbox" hidden></ul>
<script>
function draw(q) {
  const list = document.getElementById('il'); list.innerHTML = '';
  const rows = q ? ["Bahamas", "Bahrain"].filter(x => x.toLowerCase().includes(q.toLowerCase())) : ["Albania"];
  for (const c of rows) {
    const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = c;
    li.onclick = () => { document.getElementById('chosen').textContent = c; list.hidden = true; };
    list.appendChild(li);
  }
  list.hidden = false;
}
</script></body></html>"""
PAGES = {"/virtual": VIRTUAL, "/inner": INNER, "/combo": "<html><body>" + COMBO + "</body></html>"}


@pytest.fixture
async def filter_site():
    from aiohttp import web

    app = web.Application()
    for path, html in PAGES.items():
        app.router.add_get(path, _html(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


def _pool(site):
    from src.services.browser_agent.agent import BrowserPool

    return BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(site))


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_choose_types_once_to_filter_a_virtualized_list(filter_site):
    from src.services.browser_agent.agent import BrowserError, run

    pool = _pool(filter_site)
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/virtual")
        await run(pool, "u1", "choose", ref=_ref(page, "Country code"), text="Zimbabwe")   # not drawn until typed
        tab = pool._sessions["u1"].page  # noqa: SLF001
        assert await tab.eval_on_selector("#cc", "el => el.value") == "Zimbabwe"
        # The old text is cleared before typing; an ambiguous filter ends in the same error, listing the
        # options seen after filtering.
        page = await run(pool, "u1", "look")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "choose", ref=_ref(page, "Country code"), text="Z")
        assert exc.value.reason == "option_not_found"
        assert "Zambia Republic" in exc.value.detail and "Country 5" not in exc.value.detail
        assert await tab.eval_on_selector("#cc", "el => el.value") == "Z"
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_choose_types_into_the_input_inside_a_combobox_box(filter_site):
    from src.services.browser_agent.agent import BrowserError, run

    pool = _pool(filter_site)
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/inner")
        await run(pool, "u1", "choose", ref=_ref(page, "Phone country"), text="Bahamas")
        tab = pool._sessions["u1"].page  # noqa: SLF001
        assert (await tab.locator("#chosen").inner_text()) == "Bahamas"   # proven by the nearby text
        page = await run(pool, "u1", "look")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "choose", ref=_ref(page, "Phone country"), text="Narnia")
        assert exc.value.reason == "option_not_found"
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_choose_does_not_type_into_a_box_that_is_not_typeable(filter_site):
    from src.services.browser_agent.agent import BrowserError, run

    pool = _pool(filter_site)
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/combo")
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "choose", ref=_ref(page, "City"), text="Berlin")   # a div: nothing to type into
        assert exc.value.reason == "option_not_found" and "Paris" in exc.value.detail
    finally:
        await pool.shutdown()
