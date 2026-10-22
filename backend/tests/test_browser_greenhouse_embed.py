"""`open` on a Greenhouse job page goes to the light embed form (forms.md, Greenhouse row)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.browser_agent.sites import greenhouse_embed_url

EMBED = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4012345"


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme/jobs/4012345",
    "https://job-boards.greenhouse.io/acme/jobs/4012345",
    "https://job-boards.greenhouse.io/acme/jobs/4012345/",
    "https://boards.greenhouse.io/acme/jobs/4012345?gh_jid=4012345&gh_src=abc",
    "http://BOARDS.greenhouse.io/acme/jobs/4012345#app",
])
def test_greenhouse_job_pages_map_to_the_embed_form(url):
    assert greenhouse_embed_url(url) == EMBED


@pytest.mark.parametrize("url", [
    "https://careers.acme.com/jobs?gh_jid=4012345",  # company-hosted: no board name
    "https://acme.com/careers/4012345?gh_jid=4012345",
    "https://boards.eu.greenhouse.io/acme/jobs/4012345",  # EU embed host unconfirmed: untouched
    "https://job-boards.eu.greenhouse.io/acme/jobs/4012345",
    "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4012345",  # already the embed
    "https://boards.greenhouse.io/acme",  # board page, not a job
    "https://boards.greenhouse.io/acme/jobs/notanumber",
    "https://evil.example/boards.greenhouse.io/acme/jobs/4012345",
    "https://boards.greenhouse.io.evil.example/acme/jobs/4012345",
    "https://boards.greenhouse.io:8443/acme/jobs/4012345",
    "https://user@boards.greenhouse.io/acme/jobs/4012345",
    "ftp://boards.greenhouse.io/acme/jobs/4012345",
    "https://jobs.lever.co/acme/4012345",
    "",
    "not a url",
])
def test_everything_else_is_unchanged(url):
    assert greenhouse_embed_url(url) == url


@pytest.mark.asyncio
async def test_open_screens_and_loads_the_embed_url_and_says_so(monkeypatch):
    from src.services.browser_agent import agent

    calls: list[tuple[str, str]] = []

    class Page:
        url = "about:blank"

        def is_closed(self):
            return False

        async def goto(self, url, **_):
            calls.append(("goto", url))

    class Screen:
        async def screen(self, url):
            calls.append(("screen", url))
            return SimpleNamespace(ok=True, reason="")

    async def fake_look(_s, **_):
        return {"status": "ok"}

    async def no_settle(_page):
        return None

    monkeypatch.setattr(agent, "look", fake_look)
    monkeypatch.setattr(agent, "_settle", no_settle)
    page = Page()
    s = SimpleNamespace(page=page, blocked_tabs={}, blocked_urls={}, context=SimpleNamespace(pages=[page]))
    pool = SimpleNamespace(screen=Screen())

    async def open_(url):
        return await agent._act(pool, s, "open", url=url, ref="", text="", on=True, doc_kind="", gate=None, files=None)

    result = await open_("https://boards.greenhouse.io/acme/jobs/4012345?gh_jid=4012345")
    assert calls == [("screen", EMBED), ("goto", EMBED)]  # the final URL is screened, then loaded
    assert "embed" in result["opened_as"]

    calls.clear()
    plain = "https://careers.acme.com/jobs?gh_jid=4012345"
    result = await open_(plain)
    assert calls == [("screen", plain), ("goto", plain)] and "opened_as" not in result
