"""Review fixes for page reading (#914): an embedded form keeps room in the element budget."""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _site_resolver, _to_site, needs_chromium

CROWDED = """<!doctype html><html><head><title>Careers - Acme</title></head><body>
""" + "".join(f'<a href="/team/{i}">Team page {i}</a><br>' for i in range(200)) + """
<iframe style="width:600px;height:200px" srcdoc="<label for='e'>Work email</label><input id='e'>"></iframe>
</body></html>"""


@pytest.fixture
async def crowded_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=CROWDED, content_type="text/html")

    app = web.Application()
    app.router.add_get("/careers", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_page_full_of_links_leaves_room_for_the_embedded_form(crowded_site):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(crowded_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/careers")
        assert any(e["name"] == "Work email" for e in page["elements"])   # 200 links did not crowd it out
    finally:
        await pool.shutdown()
