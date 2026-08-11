"""JSearch migrated off RapidAPI to OpenWeb Ninja's own infrastructure.

Two traps this pins down, both found live on 2026-08-11:

1. HOST + HEADER. A key issued by the OpenWeb Ninja portal returns HTTP 403
   against the old jsearch.p.rapidapi.com host. The source now calls
   api.openwebninja.com and sends `x-api-key`. Both header styles are sent so
   either flavour of key keeps working.

2. ENDPOINT VARIANT. `/jsearch/search-v2` and `/jsearch/search` BOTH return
   HTTP 200, so a smoke test that only checks status would pass either way —
   but v2 returns `data` as a dict while `/search` returns a flat list of job
   objects. Pointing at v2 yields a healthy-looking response and zero jobs.
"""
import asyncio
import re

import aiohttp
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.sources.apis_keyed.jsearch import _JSEARCH_URL, JSearchSource


def _run(coro):
    return asyncio.run(coro)


SC = SearchConfig(
    job_titles=["Data Engineer"],
    search_queries=["data engineer uk"],
    relevance_keywords=["python", "data", "engineer"],
    primary_skills=["Python"],
    secondary_skills=["SQL"],
)

_PAYLOAD = {
    "status": "OK",
    "data": [
        {
            "job_title": "Data Engineer",
            "employer_name": "Acme Data Ltd",
            "job_apply_link": "https://example.com/apply/1",
            "job_city": "London",
            "job_country": "GB",
            "job_description": "Python, SQL and dbt on a modern data stack.",
            "job_posted_at_datetime_utc": "2026-08-10T09:00:00Z",
        }
    ],
}


def test_calls_openwebninja_not_rapidapi():
    """The old host 403s OpenWeb Ninja keys — we must not call it."""
    assert "openwebninja.com" in _JSEARCH_URL
    assert "rapidapi.com" not in _JSEARCH_URL


def test_uses_the_flat_search_endpoint_not_v2():
    """search-v2 returns data as a dict; our parser needs the flat list."""
    assert _JSEARCH_URL.endswith("/jsearch/search"), (
        "must use /jsearch/search — /search-v2 returns HTTP 200 with a dict "
        "under `data`, which parses to zero jobs while looking healthy"
    )


def test_sends_the_openwebninja_auth_header():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.openwebninja\.com/jsearch/search.*"),
                      payload=_PAYLOAD, repeat=True)
                src = JSearchSource(session, api_key="test-key-123", search_config=SC)
                jobs = await src.fetch_jobs()

                sent = [k for k in m.requests if "openwebninja" in str(k[1])]
                assert sent, "no request reached api.openwebninja.com"
                headers = m.requests[sent[0]][0].kwargs.get("headers") or {}
                assert headers.get("x-api-key") == "test-key-123"

            assert len(jobs) >= 1
            assert jobs[0].title == "Data Engineer"
            assert jobs[0].company == "Acme Data Ltd"
            assert jobs[0].apply_url == "https://example.com/apply/1"
        finally:
            await session.close()
    _run(_test())


def test_no_key_skips_without_calling_out():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            src = JSearchSource(session, api_key="", search_config=SC)
            assert await src.fetch_jobs() == []
        finally:
            await session.close()
    _run(_test())
