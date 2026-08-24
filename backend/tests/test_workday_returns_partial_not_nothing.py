"""Workday must hand back what it collected instead of losing it at the ceiling.

Production 2026-08-24, three consecutive runs: workday recorded exactly 240.0s —
the full ATS ceiling — an error, and ZERO jobs, while 542 of its listings sat in
the catalog going stale.

The upstream is not the problem. Probed live: HTTP 200 in 0.5-1.6s, correct
payload shape, `total` of 1,245 for a single tenant. The adapter simply cannot
finish: 20 tenants x up to 8 queries is up to 160 POSTs at concurrent=2 /
delay=1.5, and `search_titles` comes from the UNION of every user's profile, so
it grows with the user base.

The detail cap was tuned to 198s against ONE profile's config. A count cap cannot
hold that line, because the thing that grows is the number of users. A clock can.
"""

import time

import aiohttp
import pytest
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.sources.ats import workday as workday_mod
from src.sources.ats.workday import WorkdaySource

TENANTS = [
    {"tenant": "alpha", "wd": "wd3", "site": "Careers", "name": "Alpha"},
    {"tenant": "bravo", "wd": "wd3", "site": "Careers", "name": "Bravo"},
    {"tenant": "charlie", "wd": "wd3", "site": "Careers", "name": "Charlie"},
]


def _posting(title):
    return {
        "title": title,
        "externalPath": "/job/London/" + title.replace(" ", "-"),
        "locationsText": "London, United Kingdom",
        "postedOn": "Posted 2 Days Ago",
        "bulletFields": ["R-123"],
    }


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _mock_boards(m, per_board=2):
    for t in TENANTS:
        m.post(
            f"https://{t['tenant']}.wd3.myworkdayjobs.com/wday/cxs/{t['tenant']}/Careers/jobs",
            payload={"jobPostings": [_posting(f"{t['name']} Engineer {i}") for i in range(per_board)],
                     "total": per_board},
            repeat=True,
        )


def test_all_companies_are_read_when_there_is_time():
    """Baseline: with budget to spare, nothing is skipped."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                _mock_boards(m)
                sc = SearchConfig()
                sc.job_titles = ["engineer"]
                src = WorkdaySource(session, companies=TENANTS, search_config=sc)
                return await src.fetch_jobs()
        finally:
            await session.close()

    jobs = _run(_test())
    companies = {j.company for j in jobs}
    assert companies == {"Alpha", "Bravo", "Charlie"}, (
        f"every company should be read when the budget allows, got {companies}"
    )


def test_an_exhausted_budget_stops_cleanly_instead_of_raising(monkeypatch, caplog):
    """The point of the change: stop and hand back, never raise.

    A ceiling of ~0 means the budget is spent before the first company, so the
    loop should break immediately, log WHY, and return normally. What must NOT
    happen is an exception — that is the production behaviour being removed,
    where hitting 240s discarded every job already collected and the run recorded
    workday as errored with ZERO.

    Deliberately asserting the stop and the log rather than an exact job count:
    faking the clock finely enough to land mid-run made the test depend on how
    many times the loop happens to read the clock, which is not the behaviour
    anyone cares about.
    """
    import src.core.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SOURCE_FETCH_TIMEOUT_ATS", 240, raising=False)
    # Force the deadline into the PAST. A tiny real ceiling does not work here:
    # every request is mocked, so a whole board is read in less time than any
    # plausible budget, and the check never fires. A negative fraction is a
    # test-only device to reach the exhausted branch deterministically, rather
    # than racing a clock that the loop reads an implementation-defined number
    # of times.
    monkeypatch.setattr(workday_mod, "_FETCH_BUDGET_FRACTION", -1.0)
    caplog.set_level("WARNING", logger="job360.sources.workday")

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                _mock_boards(m)
                sc = SearchConfig()
                sc.job_titles = ["engineer"]
                return await WorkdaySource(session, companies=TENANTS, search_config=sc).fetch_jobs()
        finally:
            await session.close()

    jobs = _run(_test())  # must not raise

    assert isinstance(jobs, list), "a spent budget must still return a list"
    assert jobs == [], "with the deadline already past, no board should be read"

    stopped = [r.getMessage() for r in caplog.records if "stopping at" in r.getMessage()]
    assert stopped, (
        "stopping must be announced — a silent early return is the same invisible "
        "shape as the zero-jobs-no-error bug this whole batch is about"
    )
    assert "rather than losing them" in stopped[0]


def test_no_configured_ceiling_means_no_budget_not_a_crash(monkeypatch):
    """An ABSENT limit must not break the source.

    The first version computed `None * 0.8` and every fetch died with a
    TypeError — a source failing because a LIMIT was missing. The test conftest
    leaves SOURCE_FETCH_TIMEOUT_ATS unset, which is exactly that state.
    """
    import src.core.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SOURCE_FETCH_TIMEOUT_ATS", None, raising=False)

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                _mock_boards(m)
                sc = SearchConfig()
                sc.job_titles = ["engineer"]
                return await WorkdaySource(session, companies=TENANTS, search_config=sc).fetch_jobs()
        finally:
            await session.close()

    jobs = _run(_test())
    assert {j.company for j in jobs} == {"Alpha", "Bravo", "Charlie"}


def test_the_budget_leaves_headroom_under_the_ceiling():
    """Stopping AT the ceiling would still be killed mid-flight."""
    assert 0 < workday_mod._FETCH_BUDGET_FRACTION < 1, (
        "the fetch budget must be a real fraction of the ceiling so there is time "
        "to return cleanly"
    )


@pytest.mark.parametrize("bad", [0, 1])
def test_budget_fraction_is_not_degenerate(bad):
    assert workday_mod._FETCH_BUDGET_FRACTION != bad
