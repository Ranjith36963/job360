"""Review fixes for site rules (#922): the bot-trap ("leave this blank") check is narrow, and every
action that would fill a trap field is refused, not only `type`."""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium


def test_only_a_this_or_the_field_blank_label_is_a_trap():
    from src.services.browser_agent.sites import is_honeypot

    assert is_honeypot("Leave this field blank") and is_honeypot("If you are human, leave the box empty")
    assert is_honeypot("Do not fill this in")
    for ordinary in ("Middle name (leave blank)", "Suffix, leave blank.", "Leave this blank if none",
                     "Leave date", "Do not fill if you are a returning candidate"):
        assert not is_honeypot(ordinary), ordinary


@pytest.mark.parametrize(("label", "trap"), [
    # "Do not fill" is a trap only for this/the field, box or input, or with nothing after it.
    ("Do not fill this in", True),
    ("Please do not fill this in", True),
    ("Do not fill in this field", True),
    ("Don't fill this field", True),
    ("Do not fill in the box", True),
    ("Do not fill in.", True),
    ("Do not fill in", True),
    ("Do not fill in your old address if same", False),
    ("Do not fill in the rest if you are a student", False),
    ("Do not fill this in if you already applied", False),
    # Classic bot traps: "fill this out", or "if you are / you're human".
    ("Don't fill this out if you're human", True),
    ("Don\u2019t fill this out if you\u2019re human", True),
    ("Do not fill this out", True),
    ("Do not fill in if you are human", True),
    ("Do not fill out this field", True),
    ("Do not fill in the rest if you are a current employee", False),
])
def test_do_not_fill_is_a_trap_only_for_the_field_itself(label, trap):
    from src.services.browser_agent.sites import is_honeypot

    assert is_honeypot(label) is trap


TRAPS = """<!doctype html><html><head><title>Traps - Acme</title></head><body><form>
<label><input type="checkbox" id="t1"> Leave this box blank</label>
<label for="t2">Leave this field blank</label><select id="t2"><option>-</option><option>Yes</option></select>
<label for="mn">Middle name (leave blank)</label><input id="mn">
</form></body></html>"""


@pytest.fixture
async def traps_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=TRAPS, content_type="text/html")

    app = web.Application()
    app.router.add_get("/traps", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_tick_and_choose_never_fill_a_trap_field(traps_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(traps_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/traps")
        for action, name, extra in (("tick", "Leave this box blank", {"on": True}),
                                    ("choose", "Leave this field blank", {"text": "Yes"})):
            with pytest.raises(BrowserError) as exc:
                await run(pool, "u1", action, ref=_ref(page, name), **extra)
            assert exc.value.reason == "leave_blank"
        page = await run(pool, "u1", "type", ref=_ref(page, "Middle name"), text="Augusta")
        assert page["typed_matches"] is True   # an ordinary optional field is not a trap
    finally:
        await pool.shutdown()
