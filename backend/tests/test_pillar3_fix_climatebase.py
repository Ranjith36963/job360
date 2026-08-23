"""Climatebase returned ZERO jobs because of a swallowed TypeError.

Upstream sends salary as a STRING ("80000"), but Job.__post_init__
range-checks salary against ints, so building the Job raised
"'<' not supported between instances of 'str' and 'int'". That exception
was caught by the broad `except Exception` in _extract_jobs_from_next_data,
which returned [] — so the whole source silently produced nothing.
Confirmed live 2026-08-08: 0 jobs before, 63 after.
"""
import asyncio
import json

import aiohttp
from aioresponses import aioresponses

from src.sources.scrapers.climatebase import ClimatebaseSource, _to_number


def _run(coro):
    return asyncio.run(coro)


def _page(jobs):
    payload = {"props": {"pageProps": {"jobs": jobs}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


def test_string_salary_does_not_zero_the_source():
    """The regression itself: a string salary must not blow up the parse."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    __import__("re").compile(r"https://climatebase\.org/jobs.*"),
                    body=_page([{
                        "title": "Climate Data Scientist",
                        "name_of_employer": "Carbon Co",
                        "locations": ["London, UK"],
                        "id": "cb-1",
                        # strings, exactly as the live site sends them
                        "salary_from": "80000",
                        "salary_to": "95000",
                    }]),
                    repeat=True,
                )
                jobs = await ClimatebaseSource(session).fetch_jobs()
                assert len(jobs) >= 1, "string salary must not zero the source"
                job = jobs[0]
                assert job.title == "Climate Data Scientist"
                assert job.salary_min == 80000.0
                assert job.salary_max == 95000.0
        finally:
            await session.close()
    _run(_test())


def test_unparseable_salary_is_dropped_not_fatal():
    """Junk salary should become None, never raise and never zero the run."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    __import__("re").compile(r"https://climatebase\.org/jobs.*"),
                    body=_page([{
                        "title": "Sustainability Analyst",
                        "name_of_employer": "Green Ltd",
                        "locations": ["Remote"],
                        "id": "cb-2",
                        "salary_from": "competitive",
                        "salary_to": None,
                    }]),
                    repeat=True,
                )
                jobs = await ClimatebaseSource(session).fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


def test_to_number_coercion():
    assert _to_number("80000") == 80000.0
    assert _to_number("£80,000") == 80000.0
    assert _to_number(75000) == 75000.0
    assert _to_number(None) is None
    assert _to_number("") is None
    assert _to_number("competitive") is None
    # bools are ints in Python — must not become 1.0/0.0 salaries
    assert _to_number(True) is None
