"""Pillar 3 ATS-source fixes (worker A): ashby.py, lever.py, greenhouse.py.

Every fix below was verified against a LIVE API response before this batch
(see the task brief) -- these tests just pin the new behaviour so it cannot
silently regress:

1. ashby.py    -- apply_url must prefer applyUrl (real form), not the
                  non-existent applicationUrl key.
2. lever.py    -- apply_url must prefer applyUrl (real form) over
                  hostedUrl (info page); country == GB must be trusted
                  as a UK signal even when free-text location looks foreign.
3. greenhouse.py -- first_published must populate posted_at/date_confidence
                  (not hardcoded None/low); company_name must be preferred
                  over the slug-derived fallback name.
"""

import re

import aiohttp
from aioresponses import aioresponses

from src.sources.ats.ashby import AshbySource
from src.sources.ats.greenhouse import GreenhouseSource
from src.sources.ats.lever import LeverSource
from tests.test_sources import _run


def test_ashby_prefers_apply_url_over_application_url():
    """Live API has no applicationUrl key; applyUrl is the real form link."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "701", "title": "AI Safety Engineer",
                    "location": "London",
                    "applyUrl": "https://jobs.ashbyhq.com/anthropic/701/apply",
                    "jobUrl": "https://jobs.ashbyhq.com/anthropic/701",
                    "descriptionPlain": "AI safety research role",
                }]})
                source = AshbySource(session, companies=["anthropic"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://jobs.ashbyhq.com/anthropic/701/apply"
        finally:
            await session.close()
    _run(_test())


def test_ashby_falls_back_to_job_url_when_apply_url_missing():
    """If a posting lacks applyUrl, fall back to jobUrl."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "702", "title": "AI Safety Engineer",
                    "location": "London",
                    "jobUrl": "https://jobs.ashbyhq.com/anthropic/702",
                    "descriptionPlain": "AI safety research role",
                }]})
                source = AshbySource(session, companies=["anthropic"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://jobs.ashbyhq.com/anthropic/702"
        finally:
            await session.close()
    _run(_test())


def test_lever_prefers_apply_url_over_hosted_url():
    """Live API's applyUrl (= hostedUrl + /apply) is the real form link."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "501", "text": "Computer Vision Engineer",
                    "categories": {"location": "London", "team": "Engineering"},
                    "hostedUrl": "https://jobs.lever.co/tractable/501",
                    "applyUrl": "https://jobs.lever.co/tractable/501/apply",
                    "descriptionPlain": "CV role with Python and PyTorch",
                }])
                source = LeverSource(session, companies=["tractable"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://jobs.lever.co/tractable/501/apply"
        finally:
            await session.close()
    _run(_test())


def test_lever_trusts_gb_country_code_over_ambiguous_location_text():
    """country: GB must let a job through even if free-text location
    would otherwise be filtered as foreign -- country is the more reliable
    signal, verified 100% fill on the live API."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "502", "text": "Data Scientist",
                    # "EMEA" alone is not a UK/remote term, so the old
                    # text-only gate would drop this job.
                    "categories": {"location": "EMEA - Remote-first", "team": "Data"},
                    "country": "GB",
                    "hostedUrl": "https://jobs.lever.co/tractable/502",
                }])
                source = LeverSource(session, companies=["tractable"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].title == "Data Scientist"
        finally:
            await session.close()
    _run(_test())


def test_lever_still_filters_non_uk_when_country_absent():
    """Fallback behaviour must be unchanged: no country field, non-UK/remote
    location text still gets filtered out (regression guard)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "503", "text": "Backend Engineer",
                    "categories": {"location": "Toronto, Canada", "team": "Engineering"},
                    "hostedUrl": "https://jobs.lever.co/test/503",
                }])
                source = LeverSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 0
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_reads_first_published_as_posted_at():
    """first_published is a real ISO publish timestamp, filled 100% live --
    it must flow through normalize_posted_at() into posted_at/date_confidence
    instead of the old hardcoded None/low."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 402, "title": "AI Research Engineer",
                    "location": {"name": "London, UK"},
                    "absolute_url": "https://boards.greenhouse.io/deepmind/jobs/402",
                    "content": "<p>AI research role</p>",
                    "first_published": "2026-07-20T09:00:00-04:00",
                    "updated_at": "2026-08-01T12:00:00-04:00",
                }]})
                source = GreenhouseSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].posted_at is not None
                assert jobs[0].posted_at.startswith("2026-07-20")
                assert jobs[0].date_confidence == "high"
                assert jobs[0].date_posted_raw == "2026-08-01T12:00:00-04:00"
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_prefers_company_name_field_over_slug():
    """company_name is a clean official name filled 100% live -- prefer it
    over the slug-derived / COMPANY_NAME_OVERRIDES fallback."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 403, "title": "Backend Engineer",
                    "location": {"name": "London, UK"},
                    "absolute_url": "https://boards.greenhouse.io/monzoxyz/jobs/403",
                    "content": "<p>Backend role</p>",
                    "company_name": "Monzo",
                }]})
                source = GreenhouseSource(session, companies=["monzoxyz"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].company == "Monzo"
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_falls_back_to_slug_when_company_name_missing():
    """Fallback behaviour must be unchanged when company_name is absent."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 404, "title": "Backend Engineer",
                    "location": {"name": "London, UK"},
                    "absolute_url": "https://boards.greenhouse.io/somecoxyz/jobs/404",
                    "content": "<p>Backend role</p>",
                }]})
                source = GreenhouseSource(session, companies=["somecoxyz"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].company == "Somecoxyz"
        finally:
            await session.close()
    _run(_test())
