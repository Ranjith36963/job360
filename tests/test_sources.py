import re
import asyncio
import aiohttp
from aioresponses import aioresponses

from src.sources.reed import ReedSource
from src.sources.adzuna import AdzunaSource
from src.sources.jsearch import JSearchSource
from src.sources.arbeitnow import ArbeitnowSource
from src.sources.remoteok import RemoteOKSource
from src.sources.jobicy import JobicySource
from src.sources.himalayas import HimalayasSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.lever import LeverSource
from src.sources.workable import WorkableSource
from src.sources.ashby import AshbySource
from src.sources.findajob import FindAJobSource


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


REED_PAYLOAD = {"results": [{
    "jobId": 123, "jobTitle": "AI Engineer",
    "employerName": "DeepMind", "locationName": "London",
    "minimumSalary": 70000, "maximumSalary": 100000,
    "jobDescription": "AI role", "jobUrl": "/jobs/123", "date": "2024-01-01",
}]}

ADZUNA_PAYLOAD = {"results": [{
    "id": "456", "title": "ML Engineer",
    "company": {"display_name": "Revolut"},
    "location": {"display_name": "London"},
    "salary_min": 60000, "salary_max": 80000,
    "description": "ML role",
    "redirect_url": "https://adzuna.co.uk/jobs/456",
}]}

JSEARCH_PAYLOAD = {"data": [{
    "job_id": "789", "job_title": "GenAI Engineer",
    "employer_name": "Anthropic",
    "job_city": "London", "job_country": "UK",
    "job_description": "GenAI role",
    "job_apply_link": "https://anthropic.com/jobs/789",
    "job_min_salary": None, "job_max_salary": None,
}]}


def test_reed_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=REED_PAYLOAD, repeat=True)
                source = ReedSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "AI Engineer"
                assert jobs[0].company == "DeepMind"
                assert jobs[0].source == "reed"
        finally:
            await session.close()
    _run(_test())


def test_reed_skips_without_key():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = ReedSource(session, api_key="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_adzuna_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=ADZUNA_PAYLOAD, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].source == "adzuna"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jsearch\.p\.rapidapi\.com/search.*"), payload=JSEARCH_PAYLOAD, repeat=True)
                source = JSearchSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "GenAI Engineer"
                assert jobs[0].source == "jsearch"
        finally:
            await session.close()
    _run(_test())


def test_arbeitnow_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.arbeitnow\.com/api/job-board-api.*"), payload={"data": [{
                    "slug": "ai-eng-1", "title": "AI Engineer",
                    "company_name": "TechCo", "location": "Remote",
                    "description": "AI and ML role with Python and PyTorch",
                    "url": "https://arbeitnow.com/jobs/ai-eng-1",
                    "tags": ["ai", "python"],
                }]})
                source = ArbeitnowSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "arbeitnow"
        finally:
            await session.close()
    _run(_test())


def test_remoteok_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remoteok\.com/api.*"), payload=[
                    {"legal": "notice"},
                    {"id": "101", "position": "ML Engineer",
                     "company": "RemoteCo", "location": "Remote",
                     "description": "ML role with Python",
                     "url": "https://remoteok.com/jobs/101",
                     "tags": ["python", "ml"],
                     "salary_min": 50000, "salary_max": 70000},
                ])
                source = RemoteOKSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].source == "remoteok"
        finally:
            await session.close()
    _run(_test())


def test_jobicy_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jobicy\.com/api/v2/remote-jobs.*"), payload={"jobs": [{
                    "id": 201, "jobTitle": "Data Scientist",
                    "companyName": "DataCo", "jobGeo": "UK",
                    "url": "https://jobicy.com/jobs/201",
                    "annualSalaryMin": 50000, "annualSalaryMax": 70000,
                    "jobExcerpt": "Data science role",
                }]})
                source = JobicySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "jobicy"
        finally:
            await session.close()
    _run(_test())


def test_himalayas_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://himalayas\.app/jobs/api.*"), payload={"jobs": [{
                    "id": "301", "title": "NLP Engineer",
                    "companyName": "LangCo",
                    "locationRestrictions": ["UK"],
                    "excerpt": "NLP role with Python and Transformers",
                    "applicationUrl": "https://himalayas.app/jobs/301",
                    "minSalary": 55000, "maxSalary": 75000,
                    "categories": ["AI", "Machine Learning"],
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "himalayas"
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 401, "title": "AI Research Engineer",
                    "location": {"name": "London, UK"},
                    "absolute_url": "https://boards.greenhouse.io/deepmind/jobs/401",
                    "content": "<p>AI research role</p>",
                }]})
                source = GreenhouseSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "greenhouse"
        finally:
            await session.close()
    _run(_test())


def test_lever_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "501", "text": "Computer Vision Engineer",
                    "categories": {"location": "London", "team": "Engineering"},
                    "hostedUrl": "https://jobs.lever.co/tractable/501",
                    "descriptionPlain": "CV role with Python and PyTorch",
                }])
                source = LeverSource(session, companies=["tractable"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "lever"
        finally:
            await session.close()
    _run(_test())


def test_workable_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://apply\.workable\.com/.*"), payload={"results": [{
                    "shortcode": "ABC123", "title": "MLOps Engineer",
                    "location": {"city": "London", "country": "UK"},
                    "shortDescription": "MLOps role with Python and machine learning",
                }]})
                source = WorkableSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "workable"
        finally:
            await session.close()
    _run(_test())


def test_ashby_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "601", "title": "AI Safety Engineer",
                    "location": "London",
                    "applicationUrl": "https://ashby.com/anthropic/601",
                    "descriptionPlain": "AI safety research role",
                }]})
                source = AshbySource(session, companies=["anthropic"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "ashby"
        finally:
            await session.close()
    _run(_test())


def test_findajob_parses_html():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            html = """<html><body>
            <div class="search-results">
                <a href="/job/123-ai-engineer">AI Engineer - Government Digital Service</a>
                <li class="company">GDS</li>
                <a href="/job/456-ml-engineer">ML Engineer - HMRC</a>
                <li class="company">HMRC</li>
            </div>
            </body></html>"""
            with aioresponses() as m:
                m.get(re.compile(r"https://findajob\.dwp\.gov\.uk/search.*"),
                      body=html, content_type="text/html", repeat=True)
                source = FindAJobSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "findajob"
                assert "findajob.dwp.gov.uk" in jobs[0].apply_url
        finally:
            await session.close()
    _run(_test())


def test_source_returns_empty_on_error():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.arbeitnow\.com/.*"), status=500, repeat=True)
                source = ArbeitnowSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())
