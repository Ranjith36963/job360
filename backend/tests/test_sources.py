import asyncio
import logging
import re

import aiohttp
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.services.uk_gate import check_uk
from src.sources.apis_free.aijobs import AIJobsSource
from src.sources.apis_free.arbeitnow import ArbeitnowSource
from src.sources.apis_free.devitjobs import DevITJobsSource
from src.sources.apis_free.himalayas import HimalayasSource
from src.sources.apis_free.hn_jobs import HNJobsSource
from src.sources.apis_free.jobicy import JobicySource
from src.sources.apis_free.landingjobs import LandingJobsSource
from src.sources.apis_free.remoteok import RemoteOKSource
from src.sources.apis_free.remotive import RemotiveSource
from src.sources.apis_free.teaching_vacancies import TeachingVacanciesSource
from src.sources.apis_keyed.adzuna import AdzunaSource
from src.sources.apis_keyed.careerjet import CareerjetSource
from src.sources.apis_keyed.findwork import FindworkSource
from src.sources.apis_keyed.google_jobs import GoogleJobsSource
from src.sources.apis_keyed.gov_apprenticeships import GovApprenticeshipsSource
from src.sources.apis_keyed.jooble import JoobleSource
from src.sources.apis_keyed.jsearch import JSearchSource
from src.sources.apis_keyed.reed import ReedSource
from src.sources.ats.ashby import AshbySource
from src.sources.ats.greenhouse import GreenhouseSource
from src.sources.ats.lever import LeverSource
from src.sources.ats.personio import PersonioSource
from src.sources.ats.pinpoint import PinpointSource
from src.sources.ats.recruitee import RecruiteeSource
from src.sources.ats.rippling import RipplingSource
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.ats.workable import WorkableSource
from src.sources.ats.workday import WorkdaySource
from src.sources.base import _is_uk_or_remote
from src.sources.feeds.biospace import BioSpaceSource
from src.sources.feeds.jobs_ac_uk import JobsAcUkSource
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.feeds.nhs_jobs_xml import NHSJobsXMLSource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
from src.sources.feeds.workanywhere import WorkAnywhereSource
from src.sources.other.hackernews import HackerNewsSource
from src.sources.other.indeed import JobSpySource
from src.sources.other.nofluffjobs import NoFluffJobsSource
from src.sources.other.themuse import TheMuseSource
from src.sources.scrapers.aijobs_ai import AIJobsAISource
from src.sources.scrapers.bcs_jobs import BCSJobsSource
from src.sources.scrapers.climatebase import ClimatebaseSource
from src.sources.scrapers.eightykhours import EightyKHoursSource
from src.sources.scrapers.linkedin import LinkedInSource


def _make_search_config(queries: list[str]) -> SearchConfig:
    """Return a minimal SearchConfig with the given search queries."""
    return SearchConfig(search_queries=queries)


def _sc_ai_defaults() -> SearchConfig:
    """Batch 3.5.4 shared helper for parser tests.

    Since commit a01c1b3 emptied core/keywords.py's PRIMARY/SECONDARY/
    TERTIARY_SKILLS + JOB_TITLES, sources that iterate `self.job_titles`
    or `self.search_queries` without a SearchConfig get empty defaults
    and never loop. Tests now inject this SC to exercise the fetch path.
    """
    return SearchConfig(
        job_titles=["AI Engineer", "ML Engineer"],
        search_queries=["AI engineer"],
        relevance_keywords=["python", "machine learning", "ai", "ml"],
        primary_skills=["Python"],
        secondary_skills=["Docker"],
    )


def _run(coro):
    return asyncio.run(coro)


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
                source = ReedSource(session, api_key="test-key", search_config=_sc_ai_defaults())
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


def test_reed_caps_title_fanout():
    """S2 regression: Reed used to loop job_titles[:12] x 3 locations (36
    requests, >72s of limiter sleeps). A 20-title profile must not exceed
    the capped 8 distinct title queries."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=REED_PAYLOAD, repeat=True)
                titles = [f"Job Title {i}" for i in range(20)]
                sc = SearchConfig(job_titles=titles, relevance_keywords=["python"])
                source = ReedSource(session, api_key="test-key", search_config=sc)
                await source.fetch_jobs()
                queried_titles = {
                    call.kwargs.get("params", {}).get("keywords")
                    for calls in m.requests.values() for call in calls
                }
                assert len(queried_titles) <= 8, f"expected <=8 distinct titles, got {queried_titles}"
        finally:
            await session.close()
    _run(_test())


# DfE "Find an apprenticeship" Display Advert API v2 (keyed). Real contract:
# GET https://api.apprenticeships.education.gov.uk/vacancies/vacancy
# headers Ocp-Apim-Subscription-Key + X-Version: 2; response {"vacancies": [...]}.
GOV_APPR_PAYLOAD = {
    "vacancies": [
        {
            "title": "Software Developer Apprentice",
            "description": "Build web apps with our team.",
            "vacancyReference": "VAC2000034031",
            "vacancyUrl": "https://www.findapprenticeship.service.gov.uk/apprenticeship/VAC2000034031",
            "applicationUrl": "https://apply.example.com/apprenticeship/123",
            "postedDate": "2026-06-01T09:00:00Z",
            "closingDate": "2026-07-15T23:59:59Z",
            "employerName": "Acme Ltd",
            "wage": {"wageType": "Custom", "wageAmount": 18000, "wageUnit": "Annually"},
            "addresses": [
                {"addressLine1": "1 Tech Street", "postcode": "EC2A 4BT",
                 "latitude": 51.5224, "longitude": -0.0806},
            ],
        },
    ],
    "total": 1,
    "totalFiltered": 1,
    "totalPages": 1,
}

_GOV_APPR_URL = re.compile(
    r"https://api\.apprenticeships\.education\.gov\.uk/vacancies/vacancy.*"
)


def test_gov_apprenticeships_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=GOV_APPR_PAYLOAD, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].title == "Software Developer Apprentice"
                assert jobs[0].company == "Acme Ltd"
                assert jobs[0].source == "gov_apprenticeships"
                # Prefer the external apply link over the gov.uk page.
                assert jobs[0].apply_url == "https://apply.example.com/apprenticeship/123"
                # postedDate present → trustworthy date.
                assert jobs[0].posted_at == "2026-06-01T09:00:00Z"
                assert jobs[0].date_confidence == "high"
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_skips_without_key():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = GovApprenticeshipsSource(session, api_key="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_sends_subscription_headers():
    """The v2 API 401s without Ocp-Apim-Subscription-Key and 404s without
    X-Version: 2 — both headers must be on every request."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload={"vacancies": [], "totalPages": 1}, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="secret-key")
                await source.fetch_jobs()
                request_call = list(m.requests.values())[0][0]
                headers_sent = request_call.kwargs.get("headers") or {}
                assert headers_sent.get("Ocp-Apim-Subscription-Key") == "secret-key"
                assert str(headers_sent.get("X-Version")) == "2"
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_dedups_by_reference():
    """The same vacancyReference must not produce two jobs (paged repeats)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            dup_payload = {
                "vacancies": [
                    GOV_APPR_PAYLOAD["vacancies"][0],
                    GOV_APPR_PAYLOAD["vacancies"][0],
                ],
                "totalPages": 1,
            }
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=dup_payload, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
        finally:
            await session.close()
    _run(_test())


def test_adzuna_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=ADZUNA_PAYLOAD, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].source == "adzuna"
        finally:
            await session.close()
    _run(_test())


def test_adzuna_caps_title_fanout():
    """S2 regression: Adzuna used to loop the ENTIRE unbounded job_titles
    list with no slice. A 20-title profile must not exceed the capped
    8 distinct title queries (matches jsearch[:4]/careerjet[:6]/jooble[:8])."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=ADZUNA_PAYLOAD, repeat=True)
                titles = [f"Job Title {i}" for i in range(20)]
                sc = SearchConfig(job_titles=titles, relevance_keywords=["python"])
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=sc)
                await source.fetch_jobs()
                queried_titles = {
                    call.kwargs.get("params", {}).get("what")
                    for calls in m.requests.values() for call in calls
                }
                assert len(queried_titles) <= 8, f"expected <=8 distinct titles, got {queried_titles}"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jsearch\.p\.rapidapi\.com/search.*"), payload=JSEARCH_PAYLOAD, repeat=True)
                sc = _make_search_config(["GenAI Engineer UK"])
                source = JSearchSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "GenAI Engineer"
                assert jobs[0].source == "jsearch"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_skips_without_queries():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = JSearchSource(session, api_key="test-key")
            jobs = await source.fetch_jobs()
            assert jobs == []
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


def test_jobicy_request_omits_short_tag_param():
    """Jobicy's API 400s on 'tag' values under 3 chars; we must not send one.
    Regression for the live HTTP 400 ('tag=ai') seen 2026-06-10."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jobicy\.com/api/v2/remote-jobs.*"), payload={"jobs": []})
                source = JobicySource(session)
                await source.fetch_jobs()
                # aioresponses records calls keyed by (method, URL); params are in kwargs
                calls_list = list(m.requests.values())
                assert len(calls_list) == 1, "Expected exactly one GET to Jobicy"
                request_call = calls_list[0][0]
                params_sent = request_call.kwargs.get("params") or {}
                # Jobicy 400s on any tag shorter than 3 chars — must not send 'tag=ai'
                assert "tag" not in params_sent, (
                    f"'tag' key must not be in Jobicy request params; got {params_sent}"
                )
                # Fallback: if params were encoded into the URL key instead
                url_key = str(list(m.requests.keys())[0])
                assert "tag=" not in url_key, (
                    f"'tag=' must not appear in the request URL; got {url_key}"
                )
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
                # Batch 1: Greenhouse must NOT fabricate posted_at from updated_at
                assert jobs[0].posted_at is None
                assert jobs[0].date_confidence == "low"
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_requests_content_and_unescapes_it():
    """Job-understanding fix (2026-08-05): the board list endpoint returns NO
    `content` field unless `?content=true` is sent — 996 prod jobs (100% of the
    greenhouse slice) had empty descriptions because of it. And the content that
    DOES come back is HTML-entity-escaped (`&lt;p&gt;`), so tag-stripping must
    unescape first or the text keeps literal `&lt;h4&gt;` noise. Verified against
    the live API 2026-08-05 (deepmind board: absent without the param, 5,359
    chars with it, escaped)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            captured: list[str] = []
            with aioresponses() as m:
                def _cb(url, **kwargs):
                    captured.append(str(url))
                    from aioresponses import CallbackResult
                    return CallbackResult(payload={"jobs": [{
                        "id": 7, "title": "Research Engineer",
                        "location": {"name": "London, UK"},
                        "absolute_url": "https://boards.greenhouse.io/x/jobs/7",
                        # Entity-escaped HTML, exactly as the live API returns it.
                        "content": "&lt;h4&gt;Snapshot&lt;/h4&gt;&lt;p&gt;Deep learning research role using PyTorch.&lt;/p&gt;",
                    }]})
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), callback=_cb)
                source = GreenhouseSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()

            assert captured and "content=true" in captured[0], (
                "the list request must ask for content, or every description is empty"
            )
            assert len(jobs) == 1
            desc = jobs[0].description
            assert "Deep learning research role" in desc
            assert "&lt;" not in desc and "<" not in desc, (
                "escaped HTML must be unescaped then stripped, not stored as noise"
            )
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
                source = JoobleSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "jooble"
                assert jobs[0].title == "ML Engineer"
                # Batch 1: Jooble's "updated" is a mutation date — NOT posted_at
                assert jobs[0].posted_at is None
                assert jobs[0].date_confidence == "low"
                assert jobs[0].date_posted_raw == "2024-01-10"
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
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "linkedin"
                assert "linkedin.com" in jobs[0].apply_url
        finally:
            await session.close()
    _run(_test())


def test_linkedin_fetches_description_from_jsonld():
    """Job-understanding fix (2026-08-06): the job-view page's JSON-LD carries
    the FULL description for guests (verified live: 8,930 chars) — the assumed
    auth-wall does not apply to the SEO payload. The entity-escaped HTML must
    land in Job.description unescaped and tag-stripped."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            search_html = """
            <h3 class="base-search-card__title">AI Engineer</h3>
            <h4 class="base-search-card__subtitle">DeepTech Ltd</h4>
            <span class="job-search-card__location">London, UK</span>
            <a href="https://uk.linkedin.com/jobs/view/1234567890">View</a>
            """
            view_html = (
                '<html><script type="application/ld+json">'
                '{"@type":"JobPosting","description":"&lt;p&gt;Build RAG systems '
                'with LangChain and PyTorch.&lt;/p&gt;"}'
                "</script></html>"
            )
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=search_html, content_type="text/html", repeat=True)
                m.get("https://uk.linkedin.com/jobs/view/1234567890",
                      body=view_html, content_type="text/html", repeat=True)
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                desc = jobs[0].description
                assert "Build RAG systems" in desc
                assert "LangChain" in desc
                assert "<" not in desc and "&lt;" not in desc, (
                    "JSON-LD description must be unescaped then tag-stripped"
                )
        finally:
            await session.close()
    _run(_test())


def test_linkedin_missing_jsonld_degrades_to_empty():
    """A view page without JSON-LD (layout change, rate-limit shell) must leave
    description empty — never drop the job, never crash."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            search_html = """
            <h3 class="base-search-card__title">AI Engineer</h3>
            <h4 class="base-search-card__subtitle">DeepTech Ltd</h4>
            <span class="job-search-card__location">London, UK</span>
            <a href="https://uk.linkedin.com/jobs/view/555">View</a>
            """
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=search_html, content_type="text/html", repeat=True)
                m.get("https://uk.linkedin.com/jobs/view/555",
                      body="<html>no markup here</html>", content_type="text/html",
                      repeat=True)
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].description == ""
        finally:
            await session.close()
    _run(_test())


def test_linkedin_skips_without_queries():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = LinkedInSource(session)
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_linkedin_structure_changed_logs_error(caplog):
    # S4: a big, real-looking response with the base-search-card__title
    # anchor missing (DOM layout changed) must log a distinct, greppable
    # STRUCTURE CHANGED error — not just the normal "found 0 relevant jobs".
    html = "<div>" + ("<p>LinkedIn redesigned this page.</p>" * 30) + "</div>"
    assert len(html) > 500

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=html, content_type="text/html", repeat=True)
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                with caplog.at_level(logging.ERROR, logger="job360.sources.linkedin"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


def test_linkedin_normal_page_logs_no_structure_warning(caplog):
    # S4 counterpart: a normal, well-formed page must NOT trigger the
    # structure-changed alarm.
    html = """
    <div>
        <h3 class="base-search-card__title">AI Engineer</h3>
        <h4 class="base-search-card__subtitle">DeepTech Ltd</h4>
        <span class="job-search-card__location">London, UK</span>
        <a href="https://uk.linkedin.com/jobs/view/1234567890">View</a>
    </div>
    """

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=html, content_type="text/html", repeat=True)
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                with caplog.at_level(logging.ERROR, logger="job360.sources.linkedin"):
                    jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert not any("STRUCTURE CHANGED" in r.message for r in caplog.records)
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


def test_smartrecruiters_fetches_posting_detail_text():
    """Job-understanding fix (2026-08-05): the list endpoint has no posting
    text (150 prod jobs, 100% empty descriptions); the public detail endpoint
    carries the full jobAd sections (verified live: 6,445 chars). The detail
    text must land in Job.description, tag-stripped."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-101", "name": "AI Research Scientist",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-101",
                        "releasedDate": "2024-01-15",
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-101",
                    payload={"jobAd": {"sections": {
                        "jobDescription": {"text": "<p>Deep learning research with PyTorch.</p>"},
                        "qualifications": {"text": "<ul><li>PhD in ML</li></ul>"},
                    }}},
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                desc = jobs[0].description
                assert "Deep learning research" in desc
                assert "PhD in ML" in desc
                assert "<" not in desc, "detail HTML must be tag-stripped"
        finally:
            await session.close()
    _run(_test())


def test_workday_fetches_job_description_from_detail():
    """Same fix for Workday (537 prod jobs, 100% empty): the CXS detail
    endpoint's jobPostingInfo.jobDescription (verified live: 12,746 chars)
    must land in Job.description, tag-stripped."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/ext/jobs"),
                    payload={"jobPostings": [{
                        "title": "ML Engineer",
                        "locationsText": "London, United Kingdom",
                        "externalPath": "/job/London/ML-Engineer_R123",
                        "postedOn": "Posted Today",
                    }]},
                    repeat=True,
                )
                m.get(
                    "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/London/ML-Engineer_R123",
                    payload={"jobPostingInfo": {
                        "jobDescription": "<h2>About</h2><p>Build ML pipelines with Python and Spark.</p>",
                    }},
                    repeat=True,
                )
                source = WorkdaySource(
                    session,
                    companies=[{"tenant": "acme", "wd": "wd1", "site": "ext", "name": "Acme"}],
                    search_config=_sc_ai_defaults(),
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                desc = jobs[0].description
                assert "Build ML pipelines" in desc
                assert "<" not in desc, "detail HTML must be tag-stripped"
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
                source = JobSpySource(session, search_config=_sc_ai_defaults())
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
                source = WorkdaySource(session, companies=companies, search_config=_sc_ai_defaults())
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
        # STRINGS, not booleans — this is what the live API actually
        # sends, and the mismatch is why a `bool("No") is True` bug
        # survived here for months: the fixture encoded a shape the
        # upstream never produced, so the test could not catch it.
        "hasVisaSponsorship": "Yes",
        "expLevel": "Senior",
        "jobUrl": "https://devitjobs.uk/jobs/revolut-ml-engineer",
        "publishedAt": "2024-01-15",
        # Structured fields the live jobsLight API provides (verified
        # 2026-08-05) — folded into the composed description.
        "technologies": ["Python", "PyTorch", "AWS"],
        "filterTags": ["machine-learning", "mlops"],
        "techCategory": "Data Science",
        "jobType": "Full-time",
        # `workplace` is the field the live API populates (2,377/2,377);
        # `remoteType` is null on 2,324 of them. Verified 2026-08-07.
        "workplace": "Hybrid",
        "remoteType": None,
        "companySize": "1000+",
    },
    {
        "name": "Marketing Manager",
        "company": "SomeCo",
        "actualCity": "London",
        "annualSalaryFrom": 40000,
        "annualSalaryTo": 55000,
        "hasVisaSponsorship": "No",
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
                source = GoogleJobsSource(session, api_key="test-key", search_config=_sc_ai_defaults())
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
                # Job-understanding fix (2026-08-05): jobsLight has no prose
                # description, but it DOES publish the tech stack + structured
                # attributes — 3,041 prod jobs (42% of the catalog) had EMPTY
                # descriptions while the API was handing us `technologies` all
                # along. The composed description makes them skill-matchable.
                desc = jobs[0].description
                assert "Python" in desc and "PyTorch" in desc, (
                    "the technologies field must reach the description"
                )
                assert "machine learning" in desc or "machine-learning" in desc
                assert "Hybrid" in desc
                assert "Visa sponsorship" in desc
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


def test_ashby_skips_non_uk():
    """Ashby should filter out jobs with non-UK locations."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "701", "title": "AI Safety Engineer",
                    "location": "San Francisco, CA",
                    "applicationUrl": "https://ashby.com/anthropic/701",
                    "descriptionPlain": "AI safety research role",
                }]})
                source = AshbySource(session, companies=["anthropic"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 0
        finally:
            await session.close()
    _run(_test())


def test_workday_skips_non_uk():
    """Workday should filter out jobs with non-UK locations."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {
                "total": 1,
                "jobPostings": [{
                    "title": "AI Engineer",
                    "externalPath": "/job/SF/AI-Engineer_JR999",
                    "locationsText": "San Francisco, CA",
                    "postedOn": "Posted Today",
                    "bulletFields": ["JR999"],
                }],
            }
            companies = [{"tenant": "testco", "wd": "wd5", "site": "Careers", "name": "TestCo"}]
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://testco\.wd5\.myworkdayjobs\.com/.*"),
                    payload=payload,
                    repeat=True,
                )
                source = WorkdaySource(session, companies=companies)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 0
        finally:
            await session.close()
    _run(_test())


def test_greenhouse_skips_non_uk():
    """Greenhouse should filter out jobs with non-UK locations."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 801, "title": "AI Research Engineer",
                    "location": {"name": "Berlin, Germany"},
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/801",
                    "content": "<p>AI research role</p>",
                }]})
                source = GreenhouseSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 0
        finally:
            await session.close()
    _run(_test())


def test_lever_skips_non_uk():
    """Lever should filter out jobs with non-UK locations."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "901", "text": "Computer Vision Engineer",
                    "categories": {"location": "Toronto, Canada", "team": "Engineering"},
                    "hostedUrl": "https://jobs.lever.co/test/901",
                    "descriptionPlain": "CV role with Python and PyTorch",
                }])
                source = LeverSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 0
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


# ---- AI-Jobs.net ----

AIJOBS_PAYLOAD = [
    {
        "title": "ML Engineer",
        "company": "DeepTech",
        "location": "London, UK",
        "description": "Machine learning engineer role with Python and PyTorch",
        "url": "https://aijobs.net/jobs/ml-engineer",
        "date": "2024-01-15",
    },
    {
        "title": "Marketing Manager",
        "company": "SomeCo",
        "location": "London",
        "description": "Marketing role",
        "url": "https://aijobs.net/jobs/marketing",
        "date": "2024-01-15",
    },
]


def test_aijobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.net/api/list-jobs/.*"),
                      payload=AIJOBS_PAYLOAD)
                source = AIJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].company == "DeepTech"
                assert jobs[0].source == "aijobs"
        finally:
            await session.close()
    _run(_test())


def test_aijobs_skips_non_uk():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [{
                "title": "ML Engineer",
                "company": "USCo",
                "location": "San Francisco, CA",
                "description": "Machine learning role",
                "url": "https://aijobs.net/jobs/ml-us",
                "date": "2024-01-15",
            }]
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.net/api/list-jobs/.*"),
                      payload=payload)
                source = AIJobsSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- The Muse ----

THEMUSE_PAYLOAD = {"results": [{
    "name": "Data Scientist",
    "company": {"name": "MuseCo"},
    "locations": [{"name": "London, UK"}],
    "contents": "<p>Data science and machine learning role with Python</p>",
    "refs": {"landing_page": "https://www.themuse.com/jobs/museco/data-scientist"},
    "publication_date": "2024-01-12",
    "levels": [{"name": "Mid Level"}],
}]}


def test_themuse_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.themuse\.com/api/public/jobs.*"),
                      payload=THEMUSE_PAYLOAD, repeat=True)
                source = TheMuseSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "Data Scientist"
                assert jobs[0].company == "MuseCo"
                assert jobs[0].source == "themuse"
                assert jobs[0].experience_level == "Mid Level"
        finally:
            await session.close()
    _run(_test())


def test_themuse_skips_non_uk():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [{
                "name": "Data Scientist",
                "company": {"name": "USCo"},
                "locations": [{"name": "New York, NY"}],
                "contents": "<p>Data science and machine learning role</p>",
                "refs": {"landing_page": "https://themuse.com/jobs/usco/ds"},
                "publication_date": "2024-01-12",
                "levels": [],
            }]}
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.themuse\.com/api/public/jobs.*"),
                      payload=payload, repeat=True)
                source = TheMuseSource(session)
                jobs = await source.fetch_jobs()
                # "New York, NY" survives the fetch filter now — see
                # test_uk_gate.py: the door reads it as a two-site ad because
                # the UK really does have a hamlet called New York, and only
                # ambiguity DATA can settle that. What this test still pins is
                # that the source stopped carrying a private city list.
                assert len(jobs) == 1
                assert jobs[0].location == "New York, NY"
        finally:
            await session.close()
    _run(_test())


# ---- Hacker News ----

HN_SEARCH_PAYLOAD = {"hits": [{"objectID": "99999"}]}

HN_ITEM_PAYLOAD = {
    "children": [
        {
            "text": "DeepMind | London, UK | Remote | https://deepmind.com/careers<br>We are looking for a machine learning engineer to work on AI research.",
            "created_at": "2024-01-01T12:00:00Z",
        },
        {
            "text": "SomeCo | New York | Onsite<br>Looking for a marketing manager.",
            "created_at": "2024-01-01T12:00:00Z",
        },
    ],
}


def test_hackernews_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/search.*"),
                      payload=HN_SEARCH_PAYLOAD)
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/items/.*"),
                      payload=HN_ITEM_PAYLOAD)
                source = HackerNewsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "hackernews"
                assert "DeepMind" in jobs[0].company
        finally:
            await session.close()
    _run(_test())


def test_hackernews_returns_empty_without_thread():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/search.*"),
                      payload={"hits": []})
                source = HackerNewsSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- Careerjet ----

CAREERJET_PAYLOAD = {"jobs": [{
    "title": "AI Engineer",
    "company": "TechCo",
    "locations": "London, UK",
    "description": "AI and machine learning role with Python",
    "url": "https://careerjet.co.uk/job/ai-engineer-123",
    "date": "2024-01-15",
    "salary_min": 70000,
    "salary_max": 100000,
}]}


def test_careerjet_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://search\.api\.careerjet\.net/.*"),
                      payload=CAREERJET_PAYLOAD, repeat=True)
                source = CareerjetSource(session, affid="test-affid", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "AI Engineer"
                assert jobs[0].source == "careerjet"
                assert jobs[0].salary_min == 70000
        finally:
            await session.close()
    _run(_test())


def test_careerjet_skips_without_affid():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = CareerjetSource(session, affid="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- Findwork ----

FINDWORK_PAYLOAD = {"results": [{
    "role": "ML Engineer",
    "company_name": "FindworkCo",
    "location": "London, UK",
    "text": "Machine learning engineer role with Python and deep learning",
    "url": "https://findwork.dev/job/ml-engineer-123",
    "date_posted": "2024-01-14",
}]}


def test_findwork_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://findwork\.dev/api/jobs/.*"),
                      payload=FINDWORK_PAYLOAD)
                source = FindworkSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].company == "FindworkCo"
                assert jobs[0].source == "findwork"
        finally:
            await session.close()
    _run(_test())


def test_findwork_skips_without_key():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = FindworkSource(session, api_key="")
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- NoFluffJobs ----

NOFLUFFJOBS_PAYLOAD = [
    {
        "id": "ml-engineer-abc",
        "title": "ML Engineer",
        "company": "NoFluffCo",
        "category": "AI",
        "technology": ["python", "pytorch"],
        "location": {"places": [{"city": "London"}]},
        "remote": True,
        "posted": "2024-01-13",
        "salary": {"from": 60000, "to": 85000},
    },
    {
        "id": "marketing-xyz",
        "title": "Marketing Manager",
        "company": "OtherCo",
        "category": "Marketing",
        "technology": [],
        "location": {"places": [{"city": "Warsaw"}]},
        "remote": False,
        "posted": "2024-01-13",
    },
]


def test_nofluffjobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/.*"),
                      payload=NOFLUFFJOBS_PAYLOAD, repeat=True)
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "ML Engineer"
                assert jobs[0].source == "nofluffjobs"
                assert jobs[0].salary_min == 60000
                assert jobs[0].salary_max == 85000
                assert "Remote" in jobs[0].location
        finally:
            await session.close()
    _run(_test())


def test_nofluffjobs_skips_non_uk():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [{
                "id": "ml-de",
                "title": "ML Engineer",
                "company": "GermanCo",
                "category": "AI",
                "technology": ["python"],
                "location": {"places": [{"city": "Berlin"}]},
                "remote": False,
                "posted": "2024-01-13",
            }]
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/.*"),
                      payload=payload, repeat=True)
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()
                # Same as Rippling: a bare foreign city passes the fetch
                # filter and is refused at the one door.
                assert len(jobs) == 1
                assert check_uk(jobs[0].location, "nofluffjobs",
                                description=jobs[0].description).allowed is False
        finally:
            await session.close()
    _run(_test())


# ---- HN Jobs (YC Startup Jobs) ----

HN_JOBS_IDS = [1001, 1002]

HN_JOBS_ITEM_1 = {
    "id": 1001,
    "title": "DeepTech AI is hiring ML Engineers",
    "url": "https://deeptech.ai/careers",
    "text": "We need machine learning engineers with Python and PyTorch experience.",
    "time": 1704067200,
}

HN_JOBS_ITEM_2 = {
    "id": 1002,
    "title": "SomeCo is hiring a Marketing Manager",
    "url": "https://someco.com/jobs",
    "text": "Looking for a marketing manager.",
    "time": 1704067200,
}


def test_hn_jobs_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://hacker-news.firebaseio.com/v0/jobstories.json",
                      payload=HN_JOBS_IDS)
                m.get(re.compile(r"https://hacker-news\.firebaseio\.com/v0/item/.*"),
                      payload=HN_JOBS_ITEM_1, repeat=True)
                source = HNJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "hn_jobs"
                assert "DeepTech" in jobs[0].company
        finally:
            await session.close()
    _run(_test())


def test_hn_jobs_returns_empty_on_no_ids():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://hacker-news.firebaseio.com/v0/jobstories.json",
                      payload=[])
                source = HNJobsSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- jobs.ac.uk ----

JOBS_AC_UK_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>jobs.ac.uk - Computer Sciences</title>
<item>
  <title>AI Research Fellow - University of Oxford</title>
  <link>https://www.jobs.ac.uk/job/ABC123</link>
  <description>Machine learning research position in deep learning and NLP</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
<item>
  <title>Administrative Assistant - University of Oxford</title>
  <link>https://www.jobs.ac.uk/job/DEF456</link>
  <description>Office administration role</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_jobs_ac_uk_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.jobs\.ac\.uk/feeds/.*"),
                      body=JOBS_AC_UK_RSS, content_type="application/xml", repeat=True)
                source = JobsAcUkSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "jobs_ac_uk"
                assert "AI Research Fellow" in jobs[0].title
                assert jobs[0].company == "University of Oxford"
        finally:
            await session.close()
    _run(_test())


# ---- NHS Jobs ----

NHS_JOBS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<vacancies>
  <vacancy>
    <id>12345</id>
    <title>Data Scientist - NHS Digital</title>
    <employer>NHS Digital</employer>
    <location>Leeds</location>
    <salary>40000 - 55000</salary>
    <closingDate>2024-02-15</closingDate>
    <advertUrl>https://www.jobs.nhs.uk/candidate/jobadvert/12345</advertUrl>
  </vacancy>
  <vacancy>
    <id>67890</id>
    <title>Administrative Officer</title>
    <employer>NHS Trust</employer>
    <location>London</location>
    <salary>25000 - 30000</salary>
    <closingDate>2024-02-10</closingDate>
    <advertUrl>https://www.jobs.nhs.uk/candidate/jobadvert/67890</advertUrl>
  </vacancy>
</vacancies>"""


def test_nhs_jobs_parses_xml():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.jobs\.nhs\.uk/api/v1/search_xml.*"),
                      body=NHS_JOBS_XML, content_type="application/xml", repeat=True)
                sc = _make_search_config(["data scientist"])
                source = NHSJobsSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "nhs_jobs"
                assert "Data Scientist" in jobs[0].title
                assert jobs[0].company == "NHS Digital"
                assert jobs[0].salary_min == 40000
                assert jobs[0].salary_max == 55000
                # Batch 1: closingDate is a deadline, not posted_at
                assert jobs[0].posted_at is None
                assert jobs[0].date_confidence == "low"
        finally:
            await session.close()
    _run(_test())


def test_nhs_jobs_skips_without_queries():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = NHSJobsSource(session)
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- Personio ----

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>101</id>
    <name>Machine Learning Engineer</name>
    <office>London, UK</office>
    <department>Engineering</department>
    <jobDescriptions>
      <jobDescription>
        <name>About</name>
        <value>ML role with Python and deep learning experience</value>
      </jobDescription>
    </jobDescriptions>
  </position>
  <position>
    <id>102</id>
    <name>Office Manager</name>
    <office>Berlin, Germany</office>
    <department>Operations</department>
    <jobDescriptions></jobDescriptions>
  </position>
</workzag-jobs>"""


def test_personio_parses_xml():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.jobs\.personio\.de/xml.*"),
                      body=PERSONIO_XML, content_type="application/xml", repeat=True)
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "personio"
                assert "Machine Learning Engineer" in jobs[0].title
                assert "London" in jobs[0].location
        finally:
            await session.close()
    _run(_test())


def test_personio_skips_non_uk():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            xml = """<?xml version="1.0"?>
            <workzag-jobs>
              <position>
                <id>103</id>
                <name>ML Engineer</name>
                <office>San Francisco, CA</office>
                <department>AI</department>
                <jobDescriptions>
                  <jobDescription><name>About</name><value>Machine learning role</value></jobDescription>
                </jobDescriptions>
              </position>
            </workzag-jobs>"""
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.jobs\.personio\.de/xml.*"),
                      body=xml, content_type="application/xml", repeat=True)
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- WorkAnywhere ----

WORKANYWHERE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>WorkAnywhere - Data/AI</title>
<item>
  <title>ML Engineer at RemoteTech</title>
  <link>https://workanywhere.pro/job/ml-engineer</link>
  <description>Machine learning engineer role with Python. Remote - UK/Europe timezone.</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_workanywhere_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://workanywhere\.pro/rss.*"),
                      body=WORKANYWHERE_RSS, content_type="application/xml", repeat=True)
                source = WorkAnywhereSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "workanywhere"
                assert "ML Engineer" in jobs[0].title
                assert jobs[0].company == "RemoteTech"
        finally:
            await session.close()
    _run(_test())


# ---- WeWorkRemotely ----

WEWORKREMOTELY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>We Work Remotely</title>
<item>
  <title>DataCo: Senior AI Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/dataco-ai-eng</link>
  <description>AI engineer role with Python and deep learning. UK/EMEA timezone preferred.</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
  <region>Anywhere in the World</region>
</item>
</channel>
</rss>"""


def test_weworkremotely_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://weworkremotely.com/remote-jobs.rss",
                      body=WEWORKREMOTELY_RSS, content_type="application/xml")
                source = WeWorkRemotelySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "weworkremotely"
                assert "Senior AI Engineer" in jobs[0].title
                assert jobs[0].company == "DataCo"
        finally:
            await session.close()
    _run(_test())


# ---- RealWorkFromAnywhere ----

REALWORKFROMANYWHERE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Real Work From Anywhere</title>
<item>
  <title>Data Scientist at GlobalAI</title>
  <link>https://www.realworkfromanywhere.com/job/ds-globalai</link>
  <description>Data science role with machine learning. Remote, Europe/UK timezone.</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_realworkfromanywhere_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://www.realworkfromanywhere.com/rss.xml",
                      body=REALWORKFROMANYWHERE_RSS, content_type="application/xml")
                source = RealWorkFromAnywhereSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "realworkfromanywhere"
                assert "Data Scientist" in jobs[0].title
                assert jobs[0].company == "GlobalAI"
        finally:
            await session.close()
    _run(_test())


# ---- BioSpace ----

BIOSPACE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>BioSpace Jobs</title>
<item>
  <title>AI Research Scientist at PharmaCo</title>
  <link>https://www.biospace.com/job/ai-research-123</link>
  <description>Bioinformatics and machine learning role in London drug discovery lab.</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_biospace_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.biospace\.com/rss/.*"),
                      body=BIOSPACE_RSS, content_type="application/xml", repeat=True)
                source = BioSpaceSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "biospace"
                assert "AI Research Scientist" in jobs[0].title
        finally:
            await session.close()
    _run(_test())


# ---- Climatebase ----

CLIMATEBASE_HTML = """<html><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"jobs":[
  {"id":"123","title":"Data Scientist","name_of_employer":"ClimateCo","locations":["London, UK"]}
]}}}</script>
</body></html>"""


def test_climatebase_parses_html():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://climatebase\.org/jobs.*"),
                      body=CLIMATEBASE_HTML, content_type="text/html", repeat=True)
                source = ClimatebaseSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "climatebase"
                assert "Data Scientist" in jobs[0].title
                assert jobs[0].company == "ClimateCo"
        finally:
            await session.close()
    _run(_test())


def test_climatebase_structure_changed_logs_error(caplog):
    # S4: a big, real-looking response missing the __NEXT_DATA__ SSR island
    # (Climatebase changed its rendering) must log a distinct STRUCTURE
    # CHANGED error, not just silently fall back to 0 jobs.
    html = "<html><body>" + ("<p>Climatebase redesigned this page.</p>" * 30) + "</body></html>"
    assert len(html) > 500

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://climatebase\.org/jobs.*"),
                      body=html, content_type="text/html", repeat=True)
                source = ClimatebaseSource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.climatebase"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


def test_climatebase_normal_page_logs_no_structure_warning(caplog):
    # S4 counterpart: a normal __NEXT_DATA__ page must NOT trigger the alarm.
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://climatebase\.org/jobs.*"),
                      body=CLIMATEBASE_HTML, content_type="text/html", repeat=True)
                source = ClimatebaseSource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.climatebase"):
                    jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert not any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


# ---- 80,000 Hours ----

EIGHTYKHOURS_ALGOLIA_RESPONSE = {
    "hits": [
        {
            "objectID": "123",
            "title": "AI Safety Researcher",
            "company_name": "SafetyOrg",
            "locations": [{"name": "London, UK"}],
            "description_short": "Research role in AI safety",
        }
    ],
    "nbHits": 1,
}


def test_eightykhours_parses_algolia():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://w6km1udib3-dsn\.algolia\.net/.*"),
                       payload=EIGHTYKHOURS_ALGOLIA_RESPONSE, repeat=True)
                sc = _make_search_config(["AI safety researcher"])
                source = EightyKHoursSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "eightykhours"
                assert "AI Safety" in jobs[0].title
                assert jobs[0].company == "SafetyOrg"
        finally:
            await session.close()
    _run(_test())


def test_eightykhours_skips_without_queries():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = EightyKHoursSource(session)
            jobs = await source.fetch_jobs()
            assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_eightykhours_structure_changed_logs_error(caplog):
    # S4: a real Algolia response always carries a "hits" key (empty list on
    # zero matches). If that key is gone, the Algolia index/response schema
    # changed, not just a real zero-results query — must be logged loudly.
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://w6km1udib3-dsn\.algolia\.net/.*"),
                       payload={"nbHits": 0}, repeat=True)
                sc = _make_search_config(["AI safety researcher"])
                source = EightyKHoursSource(session, search_config=sc)
                with caplog.at_level(logging.ERROR, logger="job360.sources.eightykhours"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


def test_eightykhours_normal_response_logs_no_structure_warning(caplog):
    # S4 counterpart: a normal Algolia response (with "hits") must NOT
    # trigger the alarm, even on a genuine zero-results query.
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://w6km1udib3-dsn\.algolia\.net/.*"),
                       payload={"hits": [], "nbHits": 0}, repeat=True)
                sc = _make_search_config(["AI safety researcher"])
                source = EightyKHoursSource(session, search_config=sc)
                with caplog.at_level(logging.ERROR, logger="job360.sources.eightykhours"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert not any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


# ---- BCS Jobs ----

BCS_HTML = """<html><body>
<div class="job-card">
  <a href="/jobs/data-engineer-bcs-123">Data Engineer</a>
  <span class="company">TechCorp</span>
  <span class="location">Birmingham, UK</span>
</div>
</body></html>"""


def test_bcs_jobs_parses_html():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.bcs\.org/jobs.*"),
                      body=BCS_HTML, content_type="text/html", repeat=True)
                source = BCSJobsSource(session)
                jobs = await source.fetch_jobs()
                # BCS might not match our regex patterns exactly in mocked HTML,
                # so just verify it returns a list without errors
                assert isinstance(jobs, list)
                if jobs:
                    assert jobs[0].source == "bcs_jobs"
        finally:
            await session.close()
    _run(_test())


def test_bcs_jobs_structure_changed_logs_error(caplog):
    # S4: a big, real-looking board page with ZERO job|vacanc|career|
    # position|opportunity anchors (BCS changed its markup) must log a
    # distinct STRUCTURE CHANGED error, not just the normal "found 0" log.
    html = (
        "<html><body>"
        + ("<a href=\"/about\">About us</a><p>filler content here</p>" * 60)
        + "</body></html>"
    )
    assert len(html) > 2000

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.bcs\.org/jobs.*"),
                      body=html, content_type="text/html", repeat=True)
                source = BCSJobsSource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.bcs_jobs"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


def test_bcs_jobs_normal_page_logs_no_structure_warning(caplog):
    # S4 counterpart: a page with real job anchors must NOT trigger the alarm,
    # even padded past the structural-check size threshold.
    html = (
        "<html><body>"
        + ("<p>filler content here</p>" * 90)
        + BCS_HTML
        + "</body></html>"
    )
    assert len(html) > 2000

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.bcs\.org/jobs.*"),
                      body=html, content_type="text/html", repeat=True)
                source = BCSJobsSource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.bcs_jobs"):
                    jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert not any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


# ---- University Jobs ----

UNI_JOBS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>University of Cambridge Jobs</title>
<item>
  <title>Research Associate in Machine Learning</title>
  <link>https://www.jobs.cam.ac.uk/job/12345/</link>
  <description>AI and deep learning research position in the Computer Science department</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_uni_jobs_parses_rss():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r".*jobs\.cam\.ac\.uk.*"),
                      body=UNI_JOBS_RSS, content_type="application/xml")
                m.get(re.compile(r".*hr-jobs\.lancs\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.kent\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.royalholloway\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.surrey\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*uukjobs\.co\.uk.*"), status=404)
                source = UniJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "uni_jobs"
                assert "Machine Learning" in jobs[0].title
                assert jobs[0].company == "University of Cambridge"
        finally:
            await session.close()
    _run(_test())


# ---- SuccessFactors ----

SUCCESSFACTORS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://jobs.baesystems.com/careers/ai-engineer-london</loc>
  </url>
  <url>
    <loc>https://jobs.baesystems.com/careers/marketing-manager</loc>
  </url>
</urlset>"""


def test_successfactors_parses_sitemap():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            companies = [{"name": "BAE Systems", "sitemap_url": "https://jobs.baesystems.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://jobs.baesystems.com/sitemap.xml",
                      body=SUCCESSFACTORS_SITEMAP, content_type="application/xml")
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
                # Sitemap parsing extracts titles from URLs
                assert isinstance(jobs, list)
                if jobs:
                    assert all(j.source == "successfactors" for j in jobs)
        finally:
            await session.close()
    _run(_test())


# ---- AI Jobs AI ----

AIJOBS_AI_HTML = """<html><body>
<div class="job-card">
  <a href="/job/deep-learning-researcher-456">Deep Learning Researcher</a>
  <span class="company">AILab</span>
  <span class="location">Cambridge, UK</span>
</div>
</body></html>"""


# The CURRENT (2026-07) aijobs.ai markup: each listing is a card whose <a>
# wraps nested <div>s instead of plain text. The old regex required
# `>text</a>` with no tags inside, so it matched ZERO and the source went
# silently blind in prod (Sentry PYTHON-FASTAPI-7). Structure below is copied
# from the live page — title div, age, job type, then company in a
# `*card-title*` span. Note: the live page carries NO location in the card.
AIJOBS_AI_CARD_HTML = """<html><body>
<div class="col-xl-4 col-md-6 fade-in-bottom rt-mb-24 cat-1 cat-3">
  <div class="tw-relative tw-h-full">
    <a href="https://aijobs.ai/job/senior-robotics-systems-engineer"
       class="tw-h-full card tw-card tw-block jobcardStyle1 ">
      <div class="tw-p-6 tw-h-full">
        <div class="tw-mb-1.5 d-flex justify-content-between">
          <div class="tw-text-[#18191C] tw-text-lg tw-font-medium">
            Senior Robotics Systems Engineer
          </div>
          <div class="tw-text-sm tw-text-[#767F8C] mt-1 tw-pl-3">0D</div>
        </div>
        <div class="tw-text-sm">Full Time</div>
        <span class="iconbox-icon"><div><img src="/x.jpeg" alt="" draggable="false"></div></span>
        <div class="iconbox-content"><div class="tw-mb-1 tw-inline-flex">
          <span class="tw-text-base tw-font-medium tw-card-title">RoboForce</span>
        </div></div>
      </div>
    </a>
  </div>
</div>
<div class="col-xl-4">
  <a href="/job/mission-operations-lead" class="card jobcardStyle1">
    <div class="tw-p-6">
      <div class="tw-text-lg tw-font-medium">Mission Operations &amp; AI Enablement Lead</div>
      <div class="tw-text-sm">2D</div>
      <div class="iconbox-content">
        <span class="tw-card-title">OceanX</span>
      </div>
    </div>
  </a>
</div>
<a href="/about">About us</a>
</body></html>"""


def test_aijobs_ai_parses_card_layout():
    """VALUE-presence (rule #21): the live card markup must yield real jobs.

    Regression pin for the 2026-07 breakage — asserting a non-empty list with
    correct field VALUES, not merely `isinstance(jobs, list)`.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/.*"),
                      body=AIJOBS_AI_CARD_HTML, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                jobs = await source.fetch_jobs()

            assert len(jobs) == 2, f"expected 2 cards parsed, got {len(jobs)}"
            by_title = {j.title: j for j in jobs}

            first = by_title["Senior Robotics Systems Engineer"]
            assert first.company == "RoboForce"
            assert first.apply_url == "https://aijobs.ai/job/senior-robotics-systems-engineer"
            assert first.source == "aijobs_ai"

            # HTML entities decoded, and a relative href absolutised.
            second = by_title["Mission Operations & AI Enablement Lead"]
            assert second.company == "OceanX"
            assert second.apply_url == "https://aijobs.ai/job/mission-operations-lead"

            # The nav link must never become a job.
            assert all("/about" not in j.apply_url for j in jobs)
        finally:
            await session.close()
    _run(_test())


def test_aijobs_ai_parses_html():
    """Legacy plain-text anchor layout must keep working (backward compat)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/.*"),
                      body=AIJOBS_AI_HTML, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                jobs = await source.fetch_jobs()

            # Previously `if jobs:` — which passed on ZERO jobs and is exactly
            # why the prod breakage never turned a test red.
            assert len(jobs) == 1
            assert jobs[0].title == "Deep Learning Researcher"
            assert jobs[0].apply_url == "https://aijobs.ai/job/deep-learning-researcher-456"
            assert all(j.source == "aijobs_ai" for j in jobs)
        finally:
            await session.close()
    _run(_test())


def test_aijobs_ai_structure_changed_logs_error(caplog):
    # S4: a big, real-looking page with ZERO /job/ or /jobs/ link anchors
    # (site changed its markup) must log a distinct STRUCTURE CHANGED error,
    # not just the normal "found 0" log.
    html = (
        "<html><body>"
        + ("<a href=\"/about\">About us</a><p>filler content here</p>" * 60)
        + "</body></html>"
    )
    assert len(html) > 2000

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/.*"),
                      body=html, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.aijobs_ai"):
                    jobs = await source.fetch_jobs()
                assert jobs == []
                assert any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


def test_aijobs_ai_normal_page_logs_no_structure_warning(caplog):
    # S4 counterpart: a page with real job link anchors must NOT trigger the
    # alarm, even padded past the structural-check size threshold.
    html = (
        "<html><body>"
        + ("<p>filler content here</p>" * 90)
        + AIJOBS_AI_HTML
        + "</body></html>"
    )
    assert len(html) > 2000

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/.*"),
                      body=html, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                with caplog.at_level(logging.ERROR, logger="job360.sources.aijobs_ai"):
                    jobs = await source.fetch_jobs()
                assert isinstance(jobs, list)
                assert not any("STRUCTURE CHANGED" in r.message for r in caplog.records)
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# Batch 3 new sources: Teaching Vacancies, GOV.UK Apprenticeships,
# NHS Jobs XML, Rippling ATS, Comeet ATS
# =============================================================================


# ---- Teaching Vacancies ----
# Rate-limit note: no documented cap per
# https://teaching-vacancies.service.gov.uk/pages/api_specification
# We poll on the 15-min rss tier → well within politeness envelope.

TEACHING_VACANCIES_PAYLOAD = {
    "jobs": [
        {
            "title": "Secondary Mathematics Teacher",
            "hiringOrganization": {"name": "Camden School for Girls"},
            "jobLocation": {"address": {"addressLocality": "London"}},
            "datePosted": "2026-04-15T09:00:00Z",
            "url": "https://teaching-vacancies.service.gov.uk/jobs/abc",
            "description": "Full-time mathematics teacher position",
        },
        {
            "title": "Primary Teacher",
            "hiringOrganization": {"name": "St. Paul's Primary"},
            "jobLocation": {"address": {"addressLocality": "Manchester"}},
            "datePosted": "2026-04-14T10:00:00Z",
            "url": "https://teaching-vacancies.service.gov.uk/jobs/def",
            "description": "KS2 teacher",
        },
    ]
}


def test_teaching_vacancies_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, payload=TEACHING_VACANCIES_PAYLOAD)
                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 2
                assert jobs[0].source == "teaching_vacancies"
                assert jobs[0].title == "Secondary Mathematics Teacher"
                assert jobs[0].company == "Camden School for Girls"
                assert jobs[0].location == "London"
                # Batch 1 contract: real datePosted → high confidence
                assert jobs[0].date_confidence == "high"
                assert jobs[0].posted_at == "2026-04-15T09:00:00Z"
        finally:
            await session.close()
    _run(_test())


def test_teaching_vacancies_returns_empty_on_no_data():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, payload={"jobs": []})
                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_teaching_vacancies_handles_http_error():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, status=503, repeat=True)
                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- NHS Jobs XML ----

NHS_JOBS_XML_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<vacancies>
  <vacancy>
    <id>V001</id>
    <title>Clinical Data Scientist</title>
    <employer>NHS Digital</employer>
    <location>Leeds</location>
    <createdDate>2026-04-16T08:30:00Z</createdDate>
    <advertUrl>https://www.jobs.nhs.uk/candidate/jobadvert/V001</advertUrl>
  </vacancy>
  <vacancy>
    <id>V002</id>
    <title>Senior ML Engineer</title>
    <employer>NHS England</employer>
    <location>London</location>
    <createdDate>2026-04-15T14:00:00Z</createdDate>
    <advertUrl>https://www.jobs.nhs.uk/candidate/jobadvert/V002</advertUrl>
  </vacancy>
</vacancies>"""


def test_nhs_jobs_xml_parses_feed():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(NHSJobsXMLSource.FEED_URL, body=NHS_JOBS_XML_FEED,
                      content_type="application/xml")
                source = NHSJobsXMLSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 2
                assert jobs[0].source == "nhs_jobs_xml"
                # Batch 1 contract: createdDate is a real posting date → high
                assert jobs[0].date_confidence == "high"
                assert jobs[0].posted_at == "2026-04-16T08:30:00Z"
                assert jobs[0].company == "NHS Digital"
        finally:
            await session.close()
    _run(_test())


def test_nhs_jobs_xml_empty_feed():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(NHSJobsXMLSource.FEED_URL,
                      body="<?xml version='1.0'?><vacancies/>",
                      content_type="application/xml")
                source = NHSJobsXMLSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_nhs_jobs_xml_parse_error():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(NHSJobsXMLSource.FEED_URL, body="not xml at all",
                      content_type="text/plain")
                source = NHSJobsXMLSource(session)
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- Rippling ATS ----

RIPPLING_PAYLOAD = {
    "jobs": [
        {
            "id": "r-123",
            "name": "Backend Engineer",
            "locations": [{"name": "London, UK"}],
            "createdAt": "2026-04-17T10:00:00Z",
            "hostedUrl": "https://ats.rippling.com/rippling/apply/r-123",
            "description": "Python + distributed systems",
        },
        {
            "id": "r-124",
            "name": "US Only Role",
            "locations": [{"name": "San Francisco"}],
            "createdAt": "2026-04-17T10:00:00Z",
            "hostedUrl": "https://ats.rippling.com/rippling/apply/r-124",
            "description": "US-only",
        },
    ]
}


def test_rippling_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://ats\.rippling\.com/api/board/.*/jobs"),
                    payload=RIPPLING_PAYLOAD, repeat=True,
                )
                source = RipplingSource(session, companies=["rippling"])
                jobs = await source.fetch_jobs()
                # A bare foreign CITY ("San Francisco") is no longer dropped
                # here: the fetch filter stopped carrying its own hand-typed
                # city list (rule #30) and now only skips text that NAMES a
                # foreign country or state. The door refuses the row instead —
                # asserted below, so the guarantee still has a test.
                assert len(jobs) == 2
                assert jobs[0].source == "rippling"
                assert jobs[0].title == "Backend Engineer"
                assert jobs[0].date_confidence == "high"
                assert jobs[0].posted_at == "2026-04-17T10:00:00Z"
                assert check_uk(jobs[1].location, "rippling",
                                description=jobs[1].description).allowed is False
                assert check_uk(jobs[0].location, "rippling",
                                description=jobs[0].description).allowed is True
        finally:
            await session.close()
    _run(_test())


def test_rippling_empty_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://ats\.rippling\.com/api/board/.*/jobs"),
                    payload={"jobs": []}, repeat=True,
                )
                source = RipplingSource(session, companies=["rippling"])
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


def test_rippling_http_error():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://ats\.rippling\.com/api/board/.*/jobs"),
                    status=500, repeat=True,
                )
                source = RipplingSource(session, companies=["rippling"])
                jobs = await source.fetch_jobs()
                assert jobs == []
        finally:
            await session.close()
    _run(_test())


# ---- JobSpy glassdoor disabled (2026-06: Glassdoor blocks the location lookup) ----

def test_jobspy_default_sites_exclude_glassdoor():
    """Glassdoor querying is disabled by default: Glassdoor fronts the
    findPopularLocationAjax.htm lookup with an anti-bot 403 ("Security |
    Glassdoor") for every term, so jobspy logged "Glassdoor: location not
    parsed" at ERROR on every query (6 lines per run) and returned zero
    glassdoor jobs. Probed 2026-06-11 — the block is term-independent, so
    no location format fixes it. scrape_jobs must be called with
    site_name=["indeed"] only; explicit sites=... overrides remain
    possible for a future re-enable."""
    import sys
    from unittest.mock import MagicMock, patch

    import pandas as pd

    async def _test():
        session = aiohttp.ClientSession()
        try:
            mock_module = MagicMock()
            mock_module.scrape_jobs = MagicMock(return_value=pd.DataFrame())
            with patch.dict(sys.modules, {"jobspy": mock_module}):
                source = JobSpySource(session, search_config=_sc_ai_defaults())
                await source.fetch_jobs()
                assert mock_module.scrape_jobs.call_count > 0
                for call in mock_module.scrape_jobs.call_args_list:
                    site_name = call.kwargs.get("site_name")
                    assert site_name == ["indeed"], (
                        f"scrape_jobs called with site_name={site_name!r}; "
                        "glassdoor must not be queried by default"
                    )
        finally:
            await session.close()
    _run(_test())


# ---- S3 fix — _is_uk_or_remote word-boundary matching (FABLE_FINDINGS.md) ----
#
# Before the fix, membership used plain substring `in`, so "uk" (then a
# hand-typed UK term) matched inside unrelated words like "Milwaukee" or
# "Ukraine" and short-circuited the function to True before the foreign check
# could fire. Those hand-typed sets are gone (rule #30, 2026-08-12) — the
# filter now asks `uk_gate.names_foreign_place`, which segments the string
# and matches whole segments against gazetteer data. These cases must still
# come out the same way, which is why the tests stayed.


def test_is_uk_or_remote_milwaukee_false_positive_fixed():
    """"uk" is a substring of "Milwaukee" but must not match as a token —
    the real foreign indicator "usa" should decide this location is not UK."""
    assert _is_uk_or_remote("Milwaukee, USA") is False


def test_is_uk_or_remote_ukraine_false_positive_fixed():
    """"uk" is a substring of "Ukraine" but must not match as a token —
    the real foreign indicator "usa" should decide this location is not UK."""
    assert _is_uk_or_remote("Kyiv, Ukraine — USA-based team") is False


def test_is_uk_or_remote_true_uk_city_still_matches():
    """Regression guard: genuine UK terms must still match as whole tokens."""
    assert _is_uk_or_remote("London, UK") is True
    assert _is_uk_or_remote("Manchester") is True


def test_is_uk_or_remote_remote_term_still_matches():
    assert _is_uk_or_remote("Remote") is True


def test_is_uk_or_remote_unknown_location_defaults_true():
    assert _is_uk_or_remote("") is True
    assert _is_uk_or_remote("Some Town Nobody Has Heard Of") is True


# ---- S7 fix — eightykhours must declare a niche DOMAINS (FABLE_FINDINGS.md) ----


def test_eightykhours_declares_niche_domain():
    """Without a DOMAINS override, EightyKHoursSource silently inherited the
    base class's {"general"} default and fired for every user's search —
    injecting AI-safety/EA job noise into e.g. a nurse or professor search.
    It must declare a specific domain so _build_sources() domain filtering
    can exclude it for unrelated profiles."""
    assert EightyKHoursSource.DOMAINS != {"general"}
    assert EightyKHoursSource.DOMAINS == {"tech"}


def test_smartrecruiters_detail_fetches_are_budgeted(monkeypatch):
    """Timeout-regression guard (2026-08-06): the nightly union refresh blew
    the 240s ATS ceiling because detail fetches were uncapped — the source
    errored and stored ZERO jobs. The per-run budget must bound them."""
    async def _test():
        import src.sources.ats.smartrecruiters as sr_mod
        monkeypatch.setattr(sr_mod, "_MAX_DETAIL_FETCHES", 2)
        session = aiohttp.ClientSession()
        try:
            postings = [{"id": f"sr-{i}", "name": f"AI Engineer {i}",
                         "location": {"city": "London", "country": "GB"},
                         "ref": f"https://jobs.smartrecruiters.com/wise/sr-{i}",
                         "releasedDate": "2024-01-15"} for i in range(5)]
            detail_calls = []
            with aioresponses() as m:
                m.get(re.compile(r".*postings\?.*"), payload={"content": postings})
                def _detail_cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(payload={"jobAd": {"sections": {
                        "jobDescription": {"text": "<p>role text</p>"}}}})
                m.get(re.compile(r".*postings/sr-\d+$"), callback=_detail_cb, repeat=True)
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
            assert len(jobs) == 5, "past-budget jobs must still be KEPT"
            assert len(detail_calls) == 2, f"budget must cap details, got {len(detail_calls)}"
            with_desc = [j for j in jobs if j.description]
            assert len(with_desc) == 2
        finally:
            await session.close()
    _run(_test())


def test_workday_detail_fetches_are_budgeted(monkeypatch):
    """Same guard for workday (537 jobs zeroed by the uncapped detail pass)."""
    async def _test():
        import src.sources.ats.workday as wd_mod
        monkeypatch.setattr(wd_mod, "_MAX_DETAIL_FETCHES", 1)
        session = aiohttp.ClientSession()
        try:
            postings = [{"title": f"ML Engineer {i}",
                         "locationsText": "London, United Kingdom",
                         "externalPath": f"/job/London/ML-{i}",
                         "postedOn": "Posted Today"} for i in range(3)]
            detail_calls = []
            with aioresponses() as m:
                m.post(re.compile(r".*wday/cxs/acme/ext/jobs"),
                       payload={"jobPostings": postings}, repeat=True)
                def _detail_cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(payload={"jobPostingInfo": {
                        "jobDescription": "<p>text</p>"}})
                m.get(re.compile(r".*wday/cxs/acme/ext/job/London/ML-\d+"),
                      callback=_detail_cb, repeat=True)
                source = WorkdaySource(
                    session,
                    companies=[{"tenant": "acme", "wd": "wd1", "site": "ext", "name": "Acme"}],
                    search_config=_sc_ai_defaults(),
                )
                jobs = await source.fetch_jobs()
            assert len(jobs) == 3, "past-budget jobs must still be KEPT"
            assert len(detail_calls) == 1, f"budget must cap details, got {len(detail_calls)}"
        finally:
            await session.close()
    _run(_test())
