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
from src.sources.remotive import RemotiveSource
from src.sources.jooble import JoobleSource
from src.sources.linkedin import LinkedInSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.pinpoint import PinpointSource
from src.sources.recruitee import RecruiteeSource
from src.sources.indeed import JobSpySource
from src.sources.workday import WorkdaySource
from src.sources.google_jobs import GoogleJobsSource
from src.sources.devitjobs import DevITJobsSource
from src.sources.landingjobs import LandingJobsSource


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
                <a href="/details/123">AI Engineer - Government Digital Service</a>
                <li class="company">GDS</li>
                <a href="/details/456">ML Engineer - HMRC</a>
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


def test_remotive_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remotive\.com/api/remote-jobs.*"), payload={"jobs": [{
                    "id": 901, "title": "AI Engineer",
                    "company_name": "RemotiveAI", "candidate_required_location": "Worldwide",
                    "description": "AI and ML role with Python and PyTorch",
                    "url": "https://remotive.com/jobs/901",
                    "tags": ["ai", "python"],
                    "publication_date": "2024-01-15",
                    "salary": "70000-90000",
                }]})
                source = RemotiveSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "remotive"
                assert jobs[0].title == "AI Engineer"
        finally:
            await session.close()
    _run(_test())


def test_jooble_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://jooble\.org/api/.*"), payload={"totalCount": 1, "jobs": [{
                    "id": "1001", "title": "ML Engineer",
                    "company": "JoobleCo", "location": "London, UK",
                    "snippet": "Machine learning role with Python",
                    "link": "https://jooble.org/jobs/1001",
                    "updated": "2024-01-10",
                }]}, repeat=True)
                source = JoobleSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "jooble"
                assert jobs[0].title == "ML Engineer"
        finally:
            await session.close()
    _run(_test())


def test_jooble_skips_without_key():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = JoobleSource(session, api_key="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_linkedin_parses_html():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            html = """
            <div>
                <h3 class="base-search-card__title">AI Engineer</h3>
                <h4 class="base-search-card__subtitle">DeepTech Ltd</h4>
                <span class="job-search-card__location">London, UK</span>
                <a href="https://uk.linkedin.com/jobs/view/1234567890">View</a>
                <h3 class="base-search-card__title">ML Engineer</h3>
                <h4 class="base-search-card__subtitle">DataCorp</h4>
                <span class="job-search-card__location">Cambridge, UK</span>
                <a href="https://uk.linkedin.com/jobs/view/9876543210">View</a>
            </div>
            """
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=html, content_type="text/html", repeat=True)
                source = LinkedInSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "linkedin"
                assert "linkedin.com" in jobs[0].apply_url
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.smartrecruiters\.com/.*"), payload={"content": [{
                    "id": "sr-101", "name": "AI Research Scientist",
                    "location": {"city": "London", "country": "GB"},
                    "ref": "https://jobs.smartrecruiters.com/wise/sr-101",
                    "releasedDate": "2024-01-15",
                }]})
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "smartrecruiters"
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-201", "title": "Machine Learning Engineer",
                    "description": "ML role with deep learning and Python",
                    "url": "https://test.pinpointhq.com/postings/pp-201",
                    "location": {"name": "London, UK"},
                    "compensation": {"min": 65000, "max": 85000},
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "pinpoint"
                assert jobs[0].salary_min == 65000
        finally:
            await session.close()
    _run(_test())


def test_recruitee_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-301", "title": "NLP Engineer",
                    "description": "NLP and AI role with transformers",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/nlp-engineer",
                    "published_at": "2024-01-12",
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "recruitee"
        finally:
            await session.close()
    _run(_test())


def test_jobspy_parses_dataframe():
    """Test JobSpySource by mocking the scrape_jobs function."""
    import sys
    from unittest.mock import MagicMock, patch
    import pandas as pd

    df = pd.DataFrame([{
        "title": "AI Engineer",
        "company": "TechCo",
        "location": "London, UK",
        "description": "AI and machine learning role with Python and PyTorch",
        "job_url": "https://indeed.co.uk/jobs/123",
        "min_amount": 70000,
        "max_amount": 95000,
        "date_posted": "2024-01-15",
        "is_remote": False,
        "site": "indeed",
    }, {
        "title": "Data Scientist",
        "company": "DataCo",
        "location": "Cambridge, UK",
        "description": "Data science role with deep learning",
        "job_url": "https://glassdoor.co.uk/jobs/456",
        "min_amount": None,
        "max_amount": None,
        "date_posted": "2024-01-14",
        "is_remote": False,
        "site": "glassdoor",
    }])

    async def _test():
        session = aiohttp.ClientSession()
        try:
            mock_module = MagicMock()
            mock_module.scrape_jobs = MagicMock(return_value=df)
            with patch.dict(sys.modules, {"jobspy": mock_module}):
                source = JobSpySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 2
                indeed_jobs = [j for j in jobs if j.source == "indeed"]
                glassdoor_jobs = [j for j in jobs if j.source == "glassdoor"]
                assert len(indeed_jobs) >= 1
                assert len(glassdoor_jobs) >= 1
                assert indeed_jobs[0].title == "AI Engineer"
                assert indeed_jobs[0].salary_min == 70000
        finally:
            await session.close()
    _run(_test())


WORKDAY_PAYLOAD = {
    "total": 2,
    "jobPostings": [
        {
            "title": "AI Engineer",
            "externalPath": "/job/London/AI-Engineer_JR123",
            "locationsText": "London, UK",
            "postedOn": "Posted Today",
            "bulletFields": ["JR123"],
        },
        {
            "title": "Marketing Manager",
            "externalPath": "/job/London/Marketing-Manager_JR456",
            "locationsText": "London, UK",
            "postedOn": "Posted 3 Days Ago",
            "bulletFields": ["JR456"],
        },
    ],
}


def test_workday_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            companies = [{"tenant": "testco", "wd": "wd5", "site": "Careers", "name": "TestCo"}]
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://testco\.wd5\.myworkdayjobs\.com/.*"),
                    payload=WORKDAY_PAYLOAD,
                    repeat=True,
                )
                source = WorkdaySource(session, companies=companies)
                jobs = await source.fetch_jobs()
                # Only AI Engineer should pass relevance filter; Marketing Manager should not
                ai_jobs = [j for j in jobs if "AI" in j.title]
                assert len(ai_jobs) >= 1
                assert ai_jobs[0].company == "TestCo"
                assert ai_jobs[0].source == "workday"
                assert "myworkdayjobs.com" in ai_jobs[0].apply_url
        finally:
            await session.close()
    _run(_test())


GOOGLE_JOBS_PAYLOAD = {"jobs_results": [{
    "title": "AI Engineer",
    "company_name": "DeepMind",
    "location": "London, UK",
    "description": "AI and machine learning role with Python and PyTorch",
    "detected_extensions": {"posted_at": "3 days ago", "salary": "70,000-100,000"},
    "apply_options": [{"link": "https://deepmind.com/careers/ai-engineer"}],
}]}

DEVITJOBS_PAYLOAD = [
    {
        "name": "ML Engineer",
        "company": "Revolut",
        "actualCity": "London",
        "annualSalaryFrom": 65000,
        "annualSalaryTo": 95000,
        "hasVisaSponsorship": True,
        "expLevel": "Senior",
        "jobUrl": "https://devitjobs.uk/jobs/revolut-ml-engineer",
        "publishedAt": "2024-01-15",
    },
    {
        "name": "Marketing Manager",
        "company": "SomeCo",
        "actualCity": "London",
        "annualSalaryFrom": 40000,
        "annualSalaryTo": 55000,
        "hasVisaSponsorship": False,
        "expLevel": "Mid",
        "jobUrl": "https://devitjobs.uk/jobs/someco-marketing",
    },
]

LANDINGJOBS_PAYLOAD = [{
    "title": "NLP Engineer",
    "company_id": "LangTech",
    "locations": [{"city": "London", "country_code": "GB"}],
    "remote": False,
    "tags": ["python", "nlp", "transformers"],
    "url": "https://landing.jobs/job/nlp-engineer",
    "published_at": "2024-01-12",
}]


def test_google_jobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://serpapi\.com/search.*"),
                      payload=GOOGLE_JOBS_PAYLOAD, repeat=True)
                source = GoogleJobsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "AI Engineer"
                assert jobs[0].company == "DeepMind"
                assert jobs[0].source == "google_jobs"
                assert "deepmind.com" in jobs[0].apply_url
        finally:
            await session.close()
    _run(_test())


def test_google_jobs_skips_without_key():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = GoogleJobsSource(session, api_key="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"),
                      payload=DEVITJOBS_PAYLOAD)
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                # Only ML Engineer should pass relevance filter
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].company == "Revolut"
                assert jobs[0].source == "devitjobs"
                assert jobs[0].salary_min == 65000
                assert jobs[0].salary_max == 95000
                assert jobs[0].visa_flag is True
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"),
                      payload=LANDINGJOBS_PAYLOAD)
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "NLP Engineer"
                assert jobs[0].company == "LangTech"
                assert jobs[0].source == "landingjobs"
                assert "London" in jobs[0].location
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_skips_non_uk():
    """Landing.jobs should skip jobs not in UK and not remote."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [{
                "title": "ML Engineer",
                "company_id": "GermanCo",
                "locations": [{"city": "Berlin", "country_code": "DE"}],
                "remote": False,
                "tags": ["python", "ml"],
                "url": "https://landing.jobs/job/ml-engineer",
                "published_at": "2024-01-12",
            }]
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"),
                      payload=payload)
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
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
