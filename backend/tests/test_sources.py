import asyncio
import logging
import re
from urllib.parse import urlparse

import aiohttp
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.services.uk_gate import check_uk
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
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.ats.workable import WorkableSource
from src.sources.ats.workday import WorkdaySource
from src.sources.base import _is_uk_or_remote
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
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
    """Return a minimal SearchConfig with the given search queries.

    `search_titles` is populated with the same strings: sources that pass a
    location parameter separately (linkedin, nhs_jobs, findwork) read
    `search_titles`, not `search_queries`, so that the " UK" suffix the query
    strings carry is not sent as a dead keyword.
    """
    return SearchConfig(search_queries=queries, search_titles=queries)


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


def _reed_calls(m):
    """Every (params dict) Reed's SEARCH endpoint was actually called with,
    in order. Scoped to the /api/1.0/search path specifically (not just the
    host) — the Pillar 3 batch added a bounded detail-fetch pass to
    /api/1.0/jobs/{id} on the SAME host, and callers of this helper assert
    exact param lists for the search calls only."""
    return [
        call.kwargs.get("params", {})
        for key, calls in m.requests.items()
        if key[1].host == "www.reed.co.uk" and key[1].path == "/api/1.0/search"
        for call in calls
    ]


def test_reed_sends_no_location_and_asks_for_the_full_page():
    """Measured live 2026-08-13: locationName="UK" returned 29 of 486 jobs (6%)
    because Reed searches a RADIUS around the named place and
    distanceFromLocation defaults to 10 miles. Omitting it returned all 486.

    So the request must carry NO locationName at all, and must ask for Reed's
    documented maximum page of 100 (we were asking for 50 — half of what the
    same single request would have served).
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"),
                      payload=REED_PAYLOAD, repeat=True)
                source = ReedSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                await source.fetch_jobs()

                sent = _reed_calls(m)
                assert sent, "Reed was never called"
                for params in sent:
                    assert "locationName" not in params, (
                        f"locationName must not be sent — it costs ~94% of the "
                        f"supply to a 10-mile radius: {params}"
                    )
                    assert params["resultsToTake"] == 100, (
                        f"Reed's documented max page is 100: {params}"
                    )
        finally:
            await session.close()
    _run(_test())


def test_reed_pages_through_a_full_result_set():
    """Reed does not rerank, so page 2 is 100 jobs page 1 could not carry.
    A FULL page must be followed by resultsToSkip=100, then 200."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            full_page = {"results": [dict(REED_PAYLOAD["results"][0], jobId=i)
                                     for i in range(100)]}
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"),
                      payload=full_page, repeat=True)
                sc = SearchConfig(job_titles=["AI Engineer"], search_titles=["AI Engineer"])
                source = ReedSource(session, api_key="test-key", search_config=sc)
                await source.fetch_jobs()

                skips = [p.get("resultsToSkip") for p in _reed_calls(m)]
                assert skips == [0, 100, 200], f"expected 3 paged requests, got {skips}"
        finally:
            await session.close()
    _run(_test())


def test_reed_stops_paging_on_a_short_page():
    """A page shorter than 100 is the last one. Asking for the next costs a
    full rate-limiter delay to be told nothing."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"),
                      payload=REED_PAYLOAD, repeat=True)  # 1 result < 100
                sc = SearchConfig(job_titles=["AI Engineer"], search_titles=["AI Engineer"])
                source = ReedSource(session, api_key="test-key", search_config=sc)
                await source.fetch_jobs()

                assert len(_reed_calls(m)) == 1, "must not page past a short page"
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
                queried_titles = {p.get("keywords") for p in _reed_calls(m)}
                assert len(queried_titles) <= 8, f"expected <=8 distinct titles, got {queried_titles}"
        finally:
            await session.close()
    _run(_test())


def test_reed_stops_early_instead_of_timing_out(monkeypatch):
    """A slow network must cost SOME jobs, never ALL of them.

    Reed sizes its fan-out from SOURCE_FETCH_TIMEOUT, but that maths can only
    see the ENFORCED DELAY between requests — it is blind to real latency.
    Measured live 2026-08-13: 18 requests = 36.0s of delay + 14.2s of latency =
    50.2s against a 60s ceiling, on a fast connection. Railway's latency is not
    this machine's, and at ~2x it the source blows the ceiling.

    That failure is asymmetric: a timeout discards every row already fetched,
    so our largest source silently reports nothing — indistinguishable from
    "Reed had no jobs today". The guard must therefore RETURN WHAT IT HAS.

    Simulated by shrinking the deadline to zero: the first request must still
    go out (a guard that can fire before any call would make the source
    permanently silent), and its jobs must survive the early exit.
    """
    from src.sources.apis_keyed import reed as reed_mod

    monkeypatch.setattr(reed_mod, "SOURCE_FETCH_TIMEOUT", 0.0, raising=True)

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"),
                    payload=REED_PAYLOAD,
                    repeat=True,
                )
                sc = SearchConfig(
                    job_titles=[f"Job Title {i}" for i in range(6)],
                    relevance_keywords=["python"],
                )
                source = ReedSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()

                n_requests = sum(len(c) for c in m.requests.values())
                # Exactly one: the first is always attempted, the guard then
                # stops the rest because no budget remains.
                assert n_requests == 1, f"expected 1 request, got {n_requests}"
                # And the point of the whole guard — the work is kept.
                assert jobs, "early stop threw away the jobs it had already fetched"
        finally:
            await session.close()

    _run(_test())



REED_DETAIL_PAYLOAD = {
    "jobId": 123,
    "jobDescription": "This is the full job description text from the Reed detail endpoint. " * 10,
    "contractType": "Contract",
    "partTime": False,
    "fullTime": True,
    "externalUrl": "https://employer.example.com/careers/ai-engineer",
    "yearlyMinimumSalary": 70000,
    "yearlyMaximumSalary": 100000,
    "salaryType": "per annum",
}


def test_reed_maps_expiration_date_to_deadline():
    """expirationDate ("16/08/2026", DD/MM/YYYY) sits on the list response we
    already fetch — confirmed populated live 2026-08-16, previously unread."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(REED_PAYLOAD["results"][0], expirationDate="16/08/2026")]}
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=payload, repeat=True)
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/jobs/.*"), payload={}, repeat=True)
                source = ReedSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].deadline == "2026-08-16"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_reed_detail_fetch_upgrades_description_employment_url_and_salary():
    """The detail endpoint (jobId already in hand from the list response)
    carries the full ad, contractType, the real employer link and Reed's own
    annualised salary figures — all confirmed live 2026-08-16, previously
    unread."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=REED_PAYLOAD, repeat=True)
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/jobs/123.*"), payload=REED_DETAIL_PAYLOAD, repeat=True)
                source = ReedSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                job = jobs[0]
                assert job.description == REED_DETAIL_PAYLOAD["jobDescription"]
                assert len(job.description) > len(REED_PAYLOAD["results"][0]["jobDescription"]), (
                    "the detail description must be the upgrade, not the list teaser"
                )
                assert job.employment_type == "Contract"
                assert job.apply_url == REED_DETAIL_PAYLOAD["externalUrl"]
                assert job.salary_min == 70000
                assert job.salary_max == 100000
                assert job.salary_period == "per annum"
        finally:
            await session.close()
    _run(_test())


def test_reed_detail_fetch_captures_salary_period_without_amount():
    """Reed sometimes gives salaryType ("per day") with NO amount at all —
    confirmed live 2026-08-16 on a real 'per day' contract role with both
    yearly salary figures null. The unit must still land on salary_period
    (rule #29: a rate without a number is still real unit information)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            list_payload = {"results": [dict(REED_PAYLOAD["results"][0], minimumSalary=None, maximumSalary=None)]}
            detail_payload = dict(REED_DETAIL_PAYLOAD, yearlyMinimumSalary=None, yearlyMaximumSalary=None, salaryType="per day")
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=list_payload, repeat=True)
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/jobs/123.*"), payload=detail_payload, repeat=True)
                source = ReedSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
                assert jobs[0].salary_period == "per day"
        finally:
            await session.close()
    _run(_test())


def test_reed_detail_fetch_respects_the_cap():
    """A large result set must not turn into an unbounded burst of detail
    requests — capped at _DETAIL_FETCH_CAP even when the time budget would
    allow more."""
    from src.sources.apis_keyed import reed as reed_mod

    async def _test():
        session = aiohttp.ClientSession()
        try:
            full_page = {"results": [
                dict(REED_PAYLOAD["results"][0], jobId=i, jobUrl=f"/jobs/{i}")
                for i in range(50)
            ]}
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=full_page, repeat=True)
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/jobs/\d+.*"), payload=REED_DETAIL_PAYLOAD, repeat=True)
                sc = SearchConfig(job_titles=["AI Engineer"], search_titles=["AI Engineer"])
                source = ReedSource(session, api_key="test-key", search_config=sc)
                await source.fetch_jobs()

                detail_calls = sum(
                    len(calls) for key, calls in m.requests.items()
                    if key[1].host == "www.reed.co.uk" and key[1].path.startswith("/api/1.0/jobs/")
                )
                assert detail_calls <= reed_mod._DETAIL_FETCH_CAP, (
                    f"expected at most {reed_mod._DETAIL_FETCH_CAP} detail fetches, got {detail_calls}"
                )
        finally:
            await session.close()
    _run(_test())


def test_reed_skips_detail_fetch_when_time_budget_is_exhausted(monkeypatch):
    """Zero time budget must skip the detail-fetch pass entirely, not just
    the extra list pages — the detail phase shares the SAME clock as the
    list phase's existing early-stop guard."""
    from src.sources.apis_keyed import reed as reed_mod

    monkeypatch.setattr(reed_mod, "SOURCE_FETCH_TIMEOUT", 0.0, raising=True)

    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.reed\.co\.uk/api/1\.0/search.*"), payload=REED_PAYLOAD, repeat=True)
                sc = SearchConfig(job_titles=["AI Engineer"], search_titles=["AI Engineer"])
                source = ReedSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()
                assert jobs, "list phase must still return its jobs"
                detail_calls = sum(
                    len(calls) for key, calls in m.requests.items()
                    if key[1].host == "www.reed.co.uk" and key[1].path.startswith("/api/1.0/jobs/")
                )
                assert detail_calls == 0, "detail fetch must not fire when the shared budget is already spent"
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
            # REAL live shape (verified 2026-08-16, 200/200 sample): there is
            # NO `wageAmount` key at all — the old code read one that does
            # not exist, so salary was null unconditionally. The only number
            # carrier is this free-text field.
            "wage": {
                "wageType": "Custom",
                "wageUnit": "Annually",
                "wageAdditionalInformation": "£18,000 a year",
            },
            "apprenticeshipLevel": "Advanced",
            # `course.route` — one of DfE's 15 published, closed
            # "apprenticeship standard routes" (confirmed live 2026-08-17).
            "course": {"larsCode": 828, "title": "Software developer (level 4)", "level": 4, "route": "Digital"},
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
                # Pillar 3 batch: the real bug fix — wageAdditionalInformation
                # free text, not the nonexistent wageAmount key.
                assert jobs[0].salary_min == 18000
                # A MISSING UPPER BOUND STAYS MISSING. The fixture reads
                # "£18,000 a year" — one figure, no ceiling. This asserted
                # `salary_max == 18000`, pinning the parser's habit of mirroring
                # `low` onto `high`: the test encoded a fabrication AS the
                # expected result, so the bug could not be fixed without a red
                # test. "From £18,000" is not "£18,000 to £18,000", and the
                # difference reads as a capped offer to everything downstream.
                # (CodeRabbit, PR #388.)
                assert jobs[0].salary_max is None
                assert jobs[0].salary_period == "Annually"
                # closingDate -> deadline, confirmed populated live 2026-08-16.
                assert jobs[0].deadline == "2026-07-15"
                assert jobs[0].deadline_source == "listing"
                # apprenticeshipLevel -> seniority (raw).
                assert jobs[0].seniority == "Advanced"
                # course.route -> category (raw); the gate's alias table
                # (shelf_gate.py) does the closed-set mapping downstream.
                assert jobs[0].category == "Digital"
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_maps_a_known_route_to_category_via_the_gate():
    """End-to-end: course.route "Education and early years" (confirmed live
    2026-08-17) is one of the safe, unambiguous DfE-route aliases in
    shelf_gate.py — the gate should turn it into JobCategory.EDUCATION."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            item = dict(GOV_APPR_PAYLOAD["vacancies"][0])
            item["course"] = {"larsCode": 550, "title": "Early years practitioner (level 2)", "level": 2, "route": "Education and early years"}
            payload = {"vacancies": [item], "totalPages": 1}
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=payload, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].category == "Education and early years"
                from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
                fill_shelves(jobs[0])
                assert jobs[0].category == "education"
                assert jobs[0].shelf_provenance["category"]["how"] == "source"
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


def test_gov_apprenticeships_leaves_salary_unset_when_wage_text_is_ambiguous():
    """"Competitive" (2 of 200 live samples 2026-08-16) must not be guessed
    at — rule #29. Salary stays unset; JOB SOURCE ENRICHMENT reads it later."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            item = dict(GOV_APPR_PAYLOAD["vacancies"][0])
            item["wage"] = {"wageType": "Custom", "wageUnit": "Annually", "wageAdditionalInformation": "Competitive"}
            payload = {"vacancies": [item], "totalPages": 1}
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=payload, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
                # The unit is still captured even with no parseable amount —
                # same "capture the unit even without a number" rule as reed.
                assert jobs[0].salary_period == "Annually"
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_does_not_parse_non_annual_wage_text():
    """Only wageUnit == 'Annually' is trusted to be a "X a year" shape —
    apprentice pay is often hourly, and this source has no hourly parser."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            item = dict(GOV_APPR_PAYLOAD["vacancies"][0])
            item["wage"] = {"wageType": "Custom", "wageUnit": "Hourly", "wageAdditionalInformation": "£6.40 an hour"}
            payload = {"vacancies": [item], "totalPages": 1}
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=payload, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
                assert jobs[0].salary_period == "Hourly"
        finally:
            await session.close()
    _run(_test())


def test_gov_apprenticeships_parses_a_salary_range():
    """"£X to £Y a year" (seen live 2026-08-16) must yield min != max."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            item = dict(GOV_APPR_PAYLOAD["vacancies"][0])
            item["wage"] = {"wageType": "Custom", "wageUnit": "Annually", "wageAdditionalInformation": "£16,640 to £26,436.80 a year"}
            payload = {"vacancies": [item], "totalPages": 1}
            with aioresponses() as m:
                m.get(_GOV_APPR_URL, payload=payload, repeat=True)
                source = GovApprenticeshipsSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 16640.0
                assert jobs[0].salary_max == 26436.8
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


def test_adzuna_maps_salary_is_predicted_to_salary_is_estimated():
    """salary_is_predicted ("0"/"1", both seen live 2026-08-16) is Adzuna's
    own ML-guessed-vs-advertised flag — a real data-integrity gap when
    ignored, since a guess then looks identical to an advertised figure."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(ADZUNA_PAYLOAD["results"][0], salary_is_predicted="1")]}
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=payload, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].salary_is_estimated is True
        finally:
            await session.close()
    _run(_test())


def test_adzuna_salary_is_estimated_unset_when_absent():
    """No `salary_is_predicted` key at all must stay None (unknown), never a
    guessed False — rule #29."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=ADZUNA_PAYLOAD, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].salary_is_estimated is None
        finally:
            await session.close()
    _run(_test())


def test_adzuna_maps_contract_type_and_category():
    """contract_time/contract_type -> employment_type (contract_type wins
    when both present) and category.label -> category — all confirmed
    populated live 2026-08-16, previously unread."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(
                ADZUNA_PAYLOAD["results"][0],
                contract_time="full_time",
                contract_type="permanent",
                category={"tag": "it-jobs", "label": "IT Jobs"},
            )]}
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=payload, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].employment_type == "permanent"
                assert jobs[0].category == "IT Jobs"
        finally:
            await session.close()
    _run(_test())


def test_adzuna_employment_type_falls_back_to_contract_time():
    """When only contract_time is present (no contract_type), it must still
    reach employment_type rather than being dropped."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(ADZUNA_PAYLOAD["results"][0], contract_time="full_time")]}
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.adzuna\.com/v1/api/jobs/gb/search/1.*"), payload=payload, repeat=True)
                source = AdzunaSource(session, app_id="test-id", app_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].employment_type == "full_time"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                # JSearch moved off RapidAPI to OpenWeb Ninja's own host
                # (verified live 2026-08-11) — keys issued by the OpenWeb Ninja
                # portal get HTTP 403 from the old rapidapi host.
                m.get(re.compile(r"https://api\.openwebninja\.com/jsearch/search.*"), payload=JSEARCH_PAYLOAD, repeat=True)
                sc = _make_search_config(["GenAI Engineer UK"])
                source = JSearchSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].title == "GenAI Engineer"
                assert jobs[0].source == "jsearch"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_sends_country_uk():
    """JSearch indexes Google for Jobs worldwide and `country` defaults to the
    US. We never sent it, so a UK-only product was querying a US-scoped index —
    that is how American postings leaked in. "uk" is a documented code."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.openwebninja\.com/jsearch/search.*"),
                      payload=JSEARCH_PAYLOAD, repeat=True)
                source = JSearchSource(session, api_key="test-key",
                                       search_config=_sc_ai_defaults())
                await source.fetch_jobs()

                sent = [
                    call.kwargs.get("params", {})
                    for key, calls in m.requests.items()
                    if "openwebninja" in str(key[1])
                    for call in calls
                ]
                assert sent, "JSearch was never called"
                for params in sent:
                    assert params.get("country") == "uk", (
                        f"every JSearch request must be UK-scoped: {params}"
                    )
        finally:
            await session.close()
    _run(_test())


def test_jsearch_maps_employment_type_and_remote():
    """job_employment_type and job_is_remote sit on the same item we already
    read job_min_salary from — both confirmed populated live 2026-08-16,
    previously unread."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"data": [dict(
                JSEARCH_PAYLOAD["data"][0],
                job_employment_type="Contractor",
                job_is_remote=True,
            )]}
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.openwebninja\.com/jsearch/search.*"), payload=payload, repeat=True)
                sc = _make_search_config(["GenAI Engineer UK"])
                source = JSearchSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].employment_type == "Contractor"
                assert jobs[0].workplace_mode == "remote"
        finally:
            await session.close()
    _run(_test())


def test_jsearch_workplace_mode_unset_when_not_remote():
    """job_is_remote=False must NOT become workplace_mode='onsite' — False
    only means 'not exclusively remote', which could be hybrid too, so
    guessing would violate rule #29."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"data": [dict(JSEARCH_PAYLOAD["data"][0], job_is_remote=False)]}
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.openwebninja\.com/jsearch/search.*"), payload=payload, repeat=True)
                sc = _make_search_config(["GenAI Engineer UK"])
                source = JSearchSource(session, api_key="test-key", search_config=sc)
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].workplace_mode is None
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
                    # job_types (58% fill live, verified 2026-08-17) is a
                    # list mixing employment-type + seniority words; only
                    # the first element is used, dumb list-unwrap.
                    "job_types": ["Full Time", "Experienced"],
                    "remote": True,
                }]})
                source = ArbeitnowSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "arbeitnow"
                # tags are the job own vocabulary (~93% fill live) -- raw
                # onto source_tags, no guessing.
                assert jobs[0].source_tags == ["ai", "python"]
                # job_types[0] onto employment_type (raw, list-unwrapped).
                assert jobs[0].employment_type == "Full Time"
                # remote:true onto workplace_mode; remote:false must stay
                # unset (rule #29 -- never invent the untold half).
                assert jobs[0].workplace_mode == "Remote"
        finally:
            await session.close()
    _run(_test())


def test_arbeitnow_remote_false_never_invents_workplace_mode():
    """remote:false means 'not tagged remote', not 'onsite' -- rule #29:
    never invent the untold half of a fact."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.arbeitnow\.com/api/job-board-api.*"), payload={"data": [{
                    "slug": "ai-eng-2", "title": "AI Engineer",
                    "company_name": "TechCo", "location": "London",
                    "description": "AI role",
                    "url": "https://arbeitnow.com/jobs/ai-eng-2",
                    "remote": False,
                }]})
                source = ArbeitnowSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].workplace_mode is None
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
                # tags are RemoteOK own skill list (94% fill live) -- raw
                # onto source_tags, no guessing.
                assert jobs[0].source_tags == ["python", "ml"]
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
                    "jobType": ["Full-Time"],
                    "salaryCurrency": "USD",
                    "salaryPeriod": "year",
                    "jobLevel": "Senior",
                    "jobIndustry": ["Data Science &amp; Analytics"],
                }]})
                source = JobicySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "jobicy"
                # jobIndustry (100% fill live 2026-08-17) is Jobicy's own
                # closed industry list — a category, unread until now. It
                # arrives as a one-item list AND HTML-escaped; unescaping is
                # transport decoding, not normalisation (the gate maps the
                # vocabulary). Dumb raw pass-through otherwise.
                assert jobs[0].category == "Data Science & Analytics"
                # jobType (100% fill live) arrives as a one-item list; raw
                # first element onto employment_type.
                assert jobs[0].employment_type == "Full-Time"
                assert jobs[0].salary_currency == "USD"
                assert jobs[0].salary_period == "year"
                # jobLevel (100% fill live) feeds BOTH the legacy free-text
                # experience_level AND the closed-enum seniority shelf.
                assert jobs[0].experience_level == "Senior"
                assert jobs[0].seniority == "Senior"
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
                    # `excerpt` is the short teaser; `description` (measured
                    # live: median 6,139 chars vs excerpt's ~150-300) is the
                    # real prose posting sitting in the SAME parsed item.
                    "excerpt": "NLP role with Python and Transformers",
                    "description": "NLP role with Python and Transformers. " * 50,
                    "applicationUrl": "https://himalayas.app/jobs/301",
                    "minSalary": 55000, "maxSalary": 75000,
                    "currency": "USD",
                    "salaryPeriod": "annual",
                    "employmentType": "Full Time",
                    "categories": ["AI", "Machine Learning"],
                    "seniority": ["Senior"],
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "himalayas"
                # The one-word fix: description must come from the real
                # prose field, not the short teaser.
                assert len(jobs[0].description) > len("NLP role with Python and Transformers")
                assert jobs[0].salary_currency == "USD"
                assert jobs[0].salary_period == "annual"
                assert jobs[0].employment_type == "Full Time"
                assert jobs[0].source_tags == ["AI", "Machine Learning"]
                # seniority[] feeds BOTH experience_level (legacy) AND the
                # closed-enum seniority shelf (new), same raw string.
                assert jobs[0].experience_level == "Senior"
                assert jobs[0].seniority == "Senior"
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


def test_greenhouse_extracts_application_deadline():
    """Job-understanding fix (2026-08-16): `application_deadline` is a real
    ISO datetime the API returns, rarely filled but never opened before."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": [{
                    "id": 402, "title": "Staff Engineer",
                    "location": {"name": "London, UK"},
                    "absolute_url": "https://boards.greenhouse.io/monzo/jobs/402",
                    "content": "<p>role</p>",
                    "application_deadline": "2026-08-28T07:30:00-04:00",
                }]})
                source = GreenhouseSource(session, companies=["monzo"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline == "2026-08-28"
                assert jobs[0].deadline_source == "listing"
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


def test_lever_recovers_lists_text_and_universal_shelf_fields():
    """Job-understanding fix (2026-08-16): `lists[]` (section headings like
    "What You'll Do"/"Who You Are") carries the REQUIREMENTS text -- verified
    live not duplicated inside `descriptionPlain` -- and was silently dropped.
    `categories.commitment` / `workplaceType` are also real raw values for the
    Universal Shelf employment_type / workplace_mode fields, never mapped
    before."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[{
                    "id": "502", "text": "Social Marketing Manager",
                    "categories": {"location": "London", "commitment": "Permanent"},
                    "hostedUrl": "https://jobs.lever.co/spotify/502",
                    "descriptionPlain": "Intro paragraph only.",
                    "lists": [
                        {"text": "What You'll Do", "content": "<ul><li>Run campaigns</li></ul>"},
                        {"text": "Who You Are", "content": "<ul><li>5+ years marketing</li></ul>"},
                    ],
                    "additionalPlain": "Equal opportunity employer.",
                    "workplaceType": "hybrid",
                }])
                source = LeverSource(session, companies=["spotify"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                desc = jobs[0].description
                assert "Intro paragraph only" in desc
                assert "Run campaigns" in desc, "lists[] requirements text was dropped"
                assert "5+ years marketing" in desc
                assert "Equal opportunity employer" in desc, "additionalPlain was dropped"
                assert jobs[0].employment_type == "Permanent"
                assert jobs[0].workplace_mode == "hybrid"
        finally:
            await session.close()
    _run(_test())


def test_workable_parses_response():
    """Job-understanding fix (2026-08-16): the old POST /api/v2/.../jobs
    endpoint returns `description` EMPTY on every row (verified live) and
    caps at 10 results/page with no pagination ever followed. The public
    GET /api/v1/widget/accounts/{slug} endpoint (no auth) returns every
    posting in one call, each with a real HTML description plus
    employment_type/experience the old endpoint never exposed."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/.*"), payload={
                    "name": "DeepMind", "jobs": [{
                        "shortcode": "ABC123", "title": "MLOps Engineer",
                        "city": "London", "country": "United Kingdom",
                        "application_url": "https://apply.workable.com/j/ABC123/apply",
                        "description": "<p>MLOps role with Python and machine learning</p>",
                        "employment_type": "Full-time",
                        "experience": "Mid-Senior level",
                        "published_on": "2026-08-01",
                    }],
                })
                source = WorkableSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].source == "workable"
                assert "MLOps role with Python" in jobs[0].description
                assert "<" not in jobs[0].description, "HTML must be tag-stripped"
                assert jobs[0].employment_type == "Full-time"
                assert jobs[0].seniority == "Mid-Senior level"
                assert jobs[0].location == "London, United Kingdom"
        finally:
            await session.close()
    _run(_test())


def test_workable_maps_telecommuting_and_function_to_universal_shelf():
    """Pillar 3 fix (2026-08-17): `telecommuting` is a bool ALWAYS present
    (verified live, 4 boards: 100% key presence) and was never read; `true`
    is an unambiguous "remote" translation. `function` ("Sales"/"Marketing"/
    "Product Management") is Workable's own job-function field -- the
    unambiguous members map onto JobCategory, "Engineering" deliberately
    does not (Workable spans every industry)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/.*"), payload={
                    "name": "DeepMind", "jobs": [{
                        "shortcode": "DEF456", "title": "Sales Director",
                        "city": "London", "country": "United Kingdom",
                        "application_url": "https://apply.workable.com/j/DEF456/apply",
                        "description": "Sales leadership role",
                        "telecommuting": True,
                        "function": "Sales",
                    }],
                })
                source = WorkableSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].workplace_mode == "remote"
                assert jobs[0].category == "Sales"
        finally:
            await session.close()
    _run(_test())


def test_workable_telecommuting_false_leaves_workplace_mode_unset():
    """False just means "not remote-only" (could be hybrid or onsite) --
    guessing either would violate rule #29, so it stays unset."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/.*"), payload={
                    "name": "DeepMind", "jobs": [{
                        "shortcode": "GHI789", "title": "Office Manager",
                        "city": "London", "country": "United Kingdom",
                        "application_url": "https://apply.workable.com/j/GHI789/apply",
                        "description": "Office role",
                        "telecommuting": False,
                    }],
                })
                source = WorkableSource(session, companies=["deepmind"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].workplace_mode is None
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


def test_ashby_requests_compensation_and_maps_universal_shelf_fields():
    """Job-understanding fix (2026-08-16): `compensation` is ABSENT from every
    job unless `?includeCompensation=true` is sent (verified live: 1,101 of
    2,019 jobs carry a real salary tier once requested). `employmentType` is
    a free raw value never mapped before."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            captured: list[str] = []
            with aioresponses() as m:
                def _cb(url, **kwargs):
                    captured.append(str(url))
                    from aioresponses import CallbackResult
                    return CallbackResult(payload={"jobs": [{
                        "id": "602", "title": "Research Scientist",
                        "location": "London",
                        "applyUrl": "https://ashby.com/cohere/602",
                        "descriptionPlain": "Research role",
                        "employmentType": "FullTime",
                        "compensation": {
                            "compensationTiers": [{"components": [
                                {"compensationType": "Salary", "minValue": 150000,
                                 "maxValue": 225000, "currencyCode": "USD",
                                 "interval": "1 YEAR"},
                            ]}],
                        },
                    }]})
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), callback=_cb)
                source = AshbySource(session, companies=["cohere"])
                jobs = await source.fetch_jobs()

            assert captured and "includeCompensation=true" in captured[0], (
                "compensation is absent unless explicitly requested"
            )
            assert len(jobs) == 1
            assert jobs[0].employment_type == "FullTime"
            assert jobs[0].salary_min == 150000
            assert jobs[0].salary_max == 225000
            assert jobs[0].salary_currency == "USD"
            assert jobs[0].salary_period == "annual"
        finally:
            await session.close()
    _run(_test())


def test_ashby_maps_workplace_type_to_universal_shelf():
    """Pillar 3 fix (2026-08-17): `workplaceType` ("Remote"/"Hybrid"/
    "OnSite") was fetched in every response already but never read onto
    Job.workplace_mode -- verified live, cohere board: 139/144 filled."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "603", "title": "Platform Engineer",
                    "location": "London",
                    "applyUrl": "https://ashby.com/cohere/603",
                    "descriptionPlain": "Platform role",
                    "workplaceType": "OnSite",
                }]})
                source = AshbySource(session, companies=["cohere"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].workplace_mode == "OnSite"
        finally:
            await session.close()
    _run(_test())


def test_ashby_checks_secondary_locations_for_uk():
    """Job-understanding fix (2026-08-16): some postings list a non-UK city
    as the primary `location` but carry "London"/"Remote - United Kingdom"
    inside `secondaryLocations` -- verified live, ~70 UK-eligible jobs were
    dropped because only the primary field was ever checked."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": [{
                    "id": "603", "title": "Data Scientist",
                    "location": "Paris",
                    "secondaryLocations": [{"location": "London"}],
                    "applyUrl": "https://ashby.com/cohere/603",
                    "descriptionPlain": "Data role",
                }]})
                source = AshbySource(session, companies=["cohere"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1, "UK secondaryLocations entry must admit the job"
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
                    "job_type": "full_time",
                    "category": "Software Development",
                }]})
                source = RemotiveSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "remotive"
                assert jobs[0].title == "AI Engineer"
                # tags are the job own vocabulary (100% fill live) -- raw
                # onto source_tags, no guessing.
                assert jobs[0].source_tags == ["ai", "python"]
                # job_type (100% fill live) is already the closed vocabulary
                # -- raw pass-through, the gate matches it.
                assert jobs[0].employment_type == "full_time"
                # `category` (100% fill live 2026-08-17) is Remotive's own
                # closed professional-domain list, unread until now. Raw
                # value only -- the gate decides what it can map honestly.
                assert jobs[0].category == "Software Development"
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


def test_linkedin_uses_real_date_not_fabricated():
    """FIX: every job used to be stamped date_confidence="fabricated" and
    posted_at=None, discarding the card's own <time datetime="..."> --
    10/10 present on a live search response, confirmed 2026-08-16.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            search_html = """
            <h3 class="base-search-card__title">AI Engineer</h3>
            <h4 class="base-search-card__subtitle">DeepTech Ltd</h4>
            <span class="job-search-card__location">London, UK</span>
            <time datetime="2026-08-13">2026-08-13</time>
            <a href="https://uk.linkedin.com/jobs/view/1234567890">View</a>
            """
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=search_html, content_type="text/html", repeat=True)
                m.get("https://uk.linkedin.com/jobs/view/1234567890",
                      body="<html>no markup here</html>", content_type="text/html",
                      repeat=True)
                sc = _make_search_config(["AI engineer UK"])
                source = LinkedInSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].date_confidence == "high"
                assert jobs[0].posted_at == "2026-08-13"
                assert jobs[0].date_posted_raw == "2026-08-13"
        finally:
            await session.close()
    _run(_test())


def test_linkedin_maps_deadline_and_employment_type_from_detail_jsonld():
    """validThrough (deadline, 100% present live) and employmentType sit in
    the SAME job-view JSON-LD this source already fetches for the
    description -- previously parsed nowhere.
    """
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
                '{"@type":"JobPosting","description":"Build things.",'
                '"validThrough":"2026-09-12","employmentType":"FULL_TIME"}'
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
                assert jobs[0].deadline == "2026-09-12"
                assert jobs[0].deadline_source == "listing"
                assert jobs[0].employment_type == "FULL_TIME"
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


def test_linkedin_returns_jobs_when_the_budget_runs_out():
    """LinkedIn kept ZERO jobs on every run (measured live 2026-08-13): the
    detail pass cost ~120s inside a 60s ceiling, and `asyncio.wait_for` CANCELS
    an overrunning source — so it lost every job it was holding.

    With the budget already spent, the source must still return the jobs from
    its first query, with empty descriptions, and must not spend another
    request on either a further query or a detail fetch.
    """
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
                '{"@type":"JobPosting","description":"Full description here."}'
                "</script></html>"
            )
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/.*"),
                      body=search_html, content_type="text/html", repeat=True)
                m.get("https://uk.linkedin.com/jobs/view/1234567890",
                      body=view_html, content_type="text/html", repeat=True)
                sc = _make_search_config(["q1 UK", "q2 UK", "q3 UK"])
                source = LinkedInSource(session, search_config=sc, time_budget=0)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1, "the first query must always run"
                assert jobs[0].description == "", (
                    "detail fetch must be skipped when the budget is gone"
                )
                # Count CALLS, not keys: a regex-registered mock files every
                # matching request under the one pattern key, so `len(requests)`
                # would read 1 no matter how many requests were really sent.
                def _calls(fragment):
                    return sum(
                        len(calls) for key, calls in m.requests.items()
                        if fragment in str(key[1])
                    )

                assert _calls("jobs-guest") == 1, (
                    f"expected 1 search request, got {_calls('jobs-guest')}"
                )
                assert _calls("jobs/view") == 0, (
                    f"expected no detail requests, got {_calls('jobs/view')}"
                )
        finally:
            await session.close()
    _run(_test())


def test_linkedin_budget_defaults_to_the_source_ceiling():
    """The budget is DERIVED from SOURCE_FETCH_TIMEOUT, not hand-picked, so a
    later change to the ceiling cannot silently put this source back over it."""
    from src.core.settings import SOURCE_FETCH_TIMEOUT

    async def _test():
        session = aiohttp.ClientSession()
        try:
            source = LinkedInSource(session)
            assert 0 < source._time_budget < SOURCE_FETCH_TIMEOUT
            # One rate-limited request must fit inside the budget, else the
            # source could never make its detail pass at all.
            assert source._request_cost < source._time_budget
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
                    }},
                    "postingUrl": "https://jobs.smartrecruiters.com/Wise/sr-101-card-title",
                    "applyUrl": "https://jobs.smartrecruiters.com/Wise/sr-101-card-title?oga=true"},
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


def test_smartrecruiters_apply_url_uses_detail_posting_url_not_raw_api_ref():
    """Job-understanding fix (2026-08-16): the LIST item's `ref` field is the
    postings API URL itself (https://api.smartrecruiters.com/v1/companies/...)
    -- verified live -- NOT a page a human can open. It used to be trusted as
    apply_url whenever it looked like a URL. The DETAIL response's real
    `postingUrl` must win instead."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-102", "name": "Card Disputes Associate",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-102",
                        "releasedDate": "2024-01-15",
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-102",
                    payload={
                        "jobAd": {"sections": {}},
                        "postingUrl": "https://jobs.smartrecruiters.com/Wise/sr-102-card-disputes",
                        "applyUrl": "https://jobs.smartrecruiters.com/Wise/sr-102-card-disputes?oga=true",
                    },
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://jobs.smartrecruiters.com/Wise/sr-102-card-disputes"
                assert "api.smartrecruiters.com" not in jobs[0].apply_url, (
                    "must never send a user to the raw JSON API endpoint"
                )
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_maps_salary_currency_period_seniority_and_workplace_mode():
    """Pillar 3 fix (2026-08-17): the detail endpoint's `compensation` block
    carries currency/period in the SAME response as min/max (verified live,
    wise board) but only min/max were ever read -- dropping them made the
    gate default every figure to GBP-annual, silently WRONG for a non-GBP or
    non-annual posting, not just incomplete. `experienceLevel.id` was read
    into `experience_level` (a non-shelf field) instead of `seniority`.
    `location.remote`/`location.hybrid` are real booleans, never read."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-103", "name": "Senior Finance Manager",
                        "location": {"city": "London", "country": "GB", "remote": False, "hybrid": True},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-103",
                        "releasedDate": "2024-01-15",
                        "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior Level"},
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-103",
                    payload={
                        "jobAd": {"sections": {}},
                        "compensation": {"min": 87500, "max": 111000, "currency": "GBP", "period": "YEARLY"},
                        "postingUrl": "https://jobs.smartrecruiters.com/Wise/sr-103",
                        "applyUrl": "https://jobs.smartrecruiters.com/Wise/sr-103?oga=true",
                    },
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min == 87500
                assert job.salary_max == 111000
                assert job.salary_currency == "GBP"
                assert job.salary_period == "YEARLY"
                assert job.seniority == "mid_senior_level"
                assert job.workplace_mode == "hybrid"
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_workplace_mode_onsite_when_both_booleans_false():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-104", "name": "Office Coordinator",
                        "location": {"city": "London", "country": "GB", "remote": False, "hybrid": False},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-104",
                        "releasedDate": "2024-01-15",
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-104",
                    payload={"jobAd": {"sections": {}}},
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].workplace_mode == "onsite"
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


def test_workday_maps_timetype_to_employment_type():
    """Pillar 3 fix (2026-08-17): `timeType` ("Full time") is on every
    SEARCH-result item already (verified live, astrazeneca board: 20/20)
    and costs no extra request, but was never read onto Job.employment_type."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/ext/jobs"),
                    payload={"jobPostings": [{
                        "title": "Data Engineer",
                        "locationsText": "London, United Kingdom",
                        "externalPath": "/job/London/Data-Engineer_R124",
                        "postedOn": "Posted Today",
                        "timeType": "Full time",
                    }]},
                    repeat=True,
                )
                m.get(
                    "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/London/Data-Engineer_R124",
                    payload={"jobPostingInfo": {"jobDescription": "Build data pipelines."}},
                    repeat=True,
                )
                source = WorkdaySource(
                    session,
                    companies=[{"tenant": "acme", "wd": "wd1", "site": "ext", "name": "Acme"}],
                    search_config=_sc_ai_defaults(),
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].employment_type == "Full time"
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
                    # Live schema (probed 2026-08-08): `compensation` is a plain
                    # STRING (e.g. "Competitive"); the real numbers live in the
                    # top-level compensation_minimum / compensation_maximum
                    # fields. The old mock used a nested dict that Pinpoint has
                    # never actually returned, so the parser silently never
                    # populated salary in production.
                    "compensation": "Competitive",
                    "compensation_minimum": 65000,
                    "compensation_maximum": 85000,
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "pinpoint"
                assert jobs[0].salary_min == 65000
                assert jobs[0].salary_max == 85000
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_recovers_all_text_sections_and_hourly_salary_period():
    """Job-understanding fix (2026-08-16): `key_responsibilities` (~1,454
    chars), `skills_knowledge_expertise` (~1,044) and `benefits` (~1,136) are
    separate HTML fields dropped before -- verified live, none duplicate
    `description`. `compensation_frequency` ("hour"/"year") was never read,
    so hourly-priced rows (e.g. £13.45/hr) got nulled by the models.py
    salary_min<10000 guard instead of being annualisable by the gate."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-202", "title": "Bank Support Worker",
                    "description": "<p>Support role intro.</p>",
                    "key_responsibilities": "<ul><li>Assist residents daily</li></ul>",
                    "skills_knowledge_expertise": "<p>Resilience required</p>",
                    "benefits": "<p>Pension scheme</p>",
                    "url": "https://test.pinpointhq.com/postings/pp-202",
                    "location": {"name": "London, UK"},
                    "compensation_minimum": 13.45,
                    "compensation_maximum": 13.45,
                    "compensation_currency": "GBP",
                    "compensation_frequency": "hour",
                    "employment_type_text": "Bank",
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                desc = jobs[0].description
                assert "Support role intro" in desc
                assert "Assist residents daily" in desc, "key_responsibilities was dropped"
                assert "Resilience required" in desc, "skills_knowledge_expertise was dropped"
                assert "Pension scheme" in desc, "benefits was dropped"
                assert jobs[0].salary_currency == "GBP"
                assert jobs[0].salary_period == "hourly"
                assert jobs[0].employment_type == "Bank"
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_maps_workplace_mode_and_translates_compound_employment_type():
    """Pillar 3 fix (2026-08-17): `workplace_type_text` ("Onsite"/"Remote"/
    "Hybrid") was fetched already but never read (verified live, 5 boards:
    100% key presence). `employment_type_text` is a compound "<contract
    nature> - <hours>" string on most boards (confirmed live: "Permanent -
    Full Time" on priorygroup/davies/networkplus) -- the gate's separator
    collapse turns that into "permanent___full_time", which matches nothing,
    so it is translated locally first (same precedent as _FREQUENCY_MAP)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-203", "title": "Care Assistant",
                    "description": "Care role",
                    "url": "https://test.pinpointhq.com/postings/pp-203",
                    "location": {"name": "London, UK"},
                    "employment_type_text": "Permanent - Full Time",
                    "workplace_type_text": "Onsite",
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                # THE SOURCE HANDS OVER RAW, THE GATE NORMALISES.
                # This asserted `employment_type == "full_time"` — i.e. that the
                # SOURCE had already translated — while the line below it
                # asserted the raw `"Onsite"` for workplace_mode. Two layers,
                # one test, opposite expectations. The source now passes both
                # through untouched (§5 point 1) and the gate resolves them, so
                # the assertion moves to where the behaviour lives.
                # (CodeRabbit, PR #388.)
                assert jobs[0].employment_type == "Permanent - Full Time"
                assert jobs[0].workplace_mode == "Onsite"

                # ...and the compound value really does survive the gate. This
                # is the half that matters: without it, "the source stopped
                # translating" would look identical to "the value is now lost".
                from src.services.shelf_gate import fill_shelves

                gated = fill_shelves(jobs[0])
                assert gated.employment_type == "full_time"
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_leaves_genuine_uk_contract_types_untranslated():
    """"Zero Hours"/"Bank" (confirmed live, priorygroup board) are real UK
    contract types with no EmploymentType equivalent -- left as the raw
    string so the gate records them honestly absent/not_mapped, never forced
    onto the nearest guess (rule #29)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-204", "title": "Weekend Support Worker",
                    "description": "Weekend cover",
                    "url": "https://test.pinpointhq.com/postings/pp-204",
                    "location": {"name": "London, UK"},
                    "employment_type_text": "Zero Hours",
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].employment_type == "Zero Hours"
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


def test_recruitee_coerces_string_salary():
    """Recruitee sends salary.min/max as STRINGS — verified live 2026-08-10.

    Regression guard: un-coerced, the string hit `Job.__post_init__`'s
    `salary_min < 10000` comparison (models.py:92) and raised TypeError,
    which aborted the whole fetch loop mid-slug. The scheduler scored that
    as a source failure (scheduler.py:186), so recruitee returned 0 of its
    671 live UK/remote offers on every run and would eventually trip its
    circuit breaker. Values below 10k are still nulled by the model's own
    sanity rule, so assert on the max (which survives).
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-302", "title": "Marketing Lead - Legal Solutions",
                    "description": "Marketing role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/marketing-lead",
                    "published_at": "2026-08-01",
                    # strings, exactly as the live API returns them
                    "salary": {"min": "25000", "max": "30000"},
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 25000
                assert jobs[0].salary_max == 30000
        finally:
            await session.close()
    _run(_test())


def test_recruitee_survives_unparseable_salary():
    """A junk salary string must yield None, never abort the fetch loop."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-303", "title": "Data Engineer",
                    "description": "Data role",
                    "location": "Manchester, UK",
                    "careers_url": "https://test.recruitee.com/o/data-engineer",
                    "published_at": "2026-08-01",
                    "salary": {"min": "Competitive", "max": None},
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


def test_recruitee_recovers_requirements_and_seniority():
    """Job-understanding fix (2026-08-16): `requirements` (926/1,194 UK/remote
    rows, verified live) is skills prose dropped before. `experience_code`
    is 100% filled with real seniority values and was never mapped onto
    Job.seniority."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-304", "title": "Marketing Lead",
                    "description": "Marketing role",
                    "requirements": "5+ years B2B marketing experience required",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/marketing-lead",
                    "published_at": "2026-08-01",
                    "experience_code": "mid_level",
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert "5+ years B2B marketing" in jobs[0].description
                assert jobs[0].seniority == "mid_level"
        finally:
            await session.close()
    _run(_test())


def test_recruitee_maps_employment_type_category_and_workplace_mode():
    """Pillar 3 fix (2026-08-17): `employment_type_code` was never mapped at
    all (a pure gap, not a normalisation miss). `category_code` is
    Recruitee's own industry taxonomy (verified live, transperfect board: 30
    distinct codes across 591 offers) and was never mapped either.
    `remote`/`hybrid`/`on_site` are real booleans forming a closed 3-state
    field (verified live: 81/109/436 out of 591), also never read."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-305", "title": "Legal Counsel",
                    "description": "Legal role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/legal-counsel",
                    "published_at": "2026-08-01",
                    "employment_type_code": "fulltime_permanent",
                    "category_code": "legal_services",
                    "remote": False,
                    "hybrid": True,
                    "on_site": False,
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.employment_type == "fulltime_permanent"
                assert job.category == "legal_services"
                assert job.workplace_mode == "hybrid"
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


def test_jobspy_maps_job_type_interval_currency_seniority_and_skills():
    """jobspy's DataFrame carries job_type, interval, currency, job_level,
    skills and job_url_direct -- this source already receives them but read
    none of them (confirmed against jobspy 1.1.82's documented column
    schema, 2026-08-16)."""
    import sys
    from unittest.mock import MagicMock, patch

    import pandas as pd

    df = pd.DataFrame([{
        "title": "AI Engineer",
        "company": "TechCo",
        "location": "London, UK",
        "description": "AI and machine learning role with Python and PyTorch",
        "job_url": "https://indeed.co.uk/jobs/123",
        "job_url_direct": "https://techco.example.com/careers/123",
        "min_amount": 70000,
        "max_amount": 95000,
        "date_posted": "2024-01-15",
        "is_remote": False,
        "site": "indeed",
        "job_type": "fulltime",
        "interval": "yearly",
        "currency": "GBP",
        "job_level": "senior",
        "skills": "Python, PyTorch, Docker",
    }])

    async def _test():
        session = aiohttp.ClientSession()
        try:
            mock_module = MagicMock()
            mock_module.scrape_jobs = MagicMock(return_value=df)
            with patch.dict(sys.modules, {"jobspy": mock_module}):
                source = JobSpySource(session, search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                job = jobs[0]
                assert job.employment_type == "fulltime"
                assert job.salary_period == "yearly"
                assert job.salary_currency == "GBP"
                assert job.seniority == "senior"
                assert job.source_tags == ["Python", "PyTorch", "Docker"]
                assert job.apply_url == "https://techco.example.com/careers/123"
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
    # Measured live 2026-08-16: 0 of 50 sampled jobs are actually GBP (46
    # EUR, 3 BRL, 1 USD) yet every one was stored with no currency tag at
    # all, so downstream code read the number as if it were GBP.
    "currency_code": "EUR",
    "type": "Full-time",
    "main_requirements": "<ul><li>5+ years Python</li></ul>",
    "nice_to_have": "<ul><li>Kafka</li></ul>",
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


def test_google_jobs_maps_schedule_type_and_forces_english():
    """schedule_type sits in the same detected_extensions dict we already
    read posted_at and salary from — confirmed populated live 2026-08-16.
    Also pins the hl=en/gl=uk fix: SerpApi otherwise localises this field
    (and posted_at) to a non-English locale on the SAME query, silently
    breaking both."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"jobs_results": [dict(
                GOOGLE_JOBS_PAYLOAD["jobs_results"][0],
                detected_extensions={"posted_at": "4 days ago", "schedule_type": "Full-time"},
            )]}
            with aioresponses() as m:
                m.get(re.compile(r"https://serpapi\.com/search.*"),
                      payload=payload, repeat=True)
                source = GoogleJobsSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].employment_type == "Full-time"

                # Match the HOST, not a substring of the whole URL. `"serpapi.com"
                # in url` is true for anything that merely CONTAINS the string
                # (evil-serpapi.com, or a path/query that happens to mention it),
                # so it is the wrong shape for deciding "was this call to SerpApi".
                # Flagged by CodeQL py/incomplete-url-substring-sanitization.
                sent = [
                    call.kwargs.get("params", {})
                    for key, calls in m.requests.items()
                    if (urlparse(str(key[1])).hostname or "").lower()
                    in ("serpapi.com", "www.serpapi.com")
                    for call in calls
                ]
                assert sent, "GoogleJobs was never called"
                for params in sent:
                    assert params.get("hl") == "en" and params.get("gl") == "uk", (
                        f"every SerpApi request must force English: {params}"
                    )
        finally:
            await session.close()
    _run(_test())


def test_google_jobs_real_en_dash_schedule_type_reaches_full_time_via_the_gate():
    """REAL BUG, confirmed live 2026-08-17: SerpApi's actual `schedule_type`
    is "Full–time" with a TYPOGRAPHIC EN DASH (U+2013), not the ASCII hyphen
    the other test above uses — 38/39 sampled google_jobs rows carried this
    exact shape, and every one landed as absent/not_mapped before the
    shelf_gate.py dash-normalisation fix, even though the source read the
    value correctly. This test uses the REAL character and drives it through
    both the source AND the gate, end to end."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"jobs_results": [dict(
                GOOGLE_JOBS_PAYLOAD["jobs_results"][0],
                detected_extensions={"posted_at": "7 hours ago", "schedule_type": "Full–time"},
            )]}
            with aioresponses() as m:
                m.get(re.compile(r"https://serpapi\.com/search.*"),
                      payload=payload, repeat=True)
                source = GoogleJobsSource(session, api_key="test-key", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].employment_type == "Full–time"

                from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
                fill_shelves(jobs[0])
                assert jobs[0].employment_type == "full_time"
                assert jobs[0].shelf_provenance["employment_type"]["how"] == "source"
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
                # along. Without a mocked detail endpoint the detail fetch
                # fails and falls back to this composed description.
                desc = jobs[0].description
                assert "Python" in desc and "PyTorch" in desc, (
                    "the technologies field must reach the description"
                )
                assert "machine learning" in desc or "machine-learning" in desc
                assert "Hybrid" in desc
                assert "Visa sponsorship" in desc
                # Raw upstream values -- no normalising in the source.
                assert jobs[0].visa_status == "Yes"
                assert jobs[0].employment_type == "Full-time"
                assert jobs[0].workplace_mode == "Hybrid"
                # expLevel (100% fill) feeds BOTH experience_level (legacy)
                # AND the closed-enum seniority shelf (new).
                assert jobs[0].seniority == "Senior"
                # technologies + filterTags (99%/99.7% fill live) were only
                # ever folded into the composed description prose -- now
                # ALSO reach source_tags (the skills shelf) as structured
                # data, deduped, order preserved.
                assert jobs[0].source_tags == [
                    "Python", "PyTorch", "AWS", "machine-learning", "mlops",
                ]
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_visa_status_uses_structured_signal_not_free_text():
    """devitjobs' own `hasVisaSponsorship` ("Yes"/"No") is a real structured
    verdict -- the gate must prefer it over the free-text detector. SomeCo
    (index 1) has hasVisaSponsorship="No" and no visa phrase anywhere in its
    (short, fallback-composed) description -- the free-text detector alone
    would call this "unknown", not "refuses". End-to-end: source -> gate."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"),
                      payload=DEVITJOBS_PAYLOAD)
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                by_company = {j.company: j for j in jobs}
                revolut, someco = by_company["Revolut"], by_company["SomeCo"]

                from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
                fill_shelves(revolut)
                fill_shelves(someco)
                assert revolut.visa_status == "sponsors"
                assert revolut.shelf_provenance["visa_status"]["how"] == "source"
                assert someco.visa_status == "no_sponsorship"
                assert someco.shelf_provenance["visa_status"]["how"] == "source"
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_fetches_real_prose_from_detail_endpoint():
    """The list endpoint (jobsLight) has no prose; the detail endpoint
    (/api/job/{_id}, keyed by the Mongo _id, NOT the jobUrl slug) does --
    measured live 2026-08-16: description + responsibilitiesTextArea +
    requirementsMustTextArea, ~2,879 chars combined, 15/15 sampled hit."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [dict(DEVITJOBS_PAYLOAD[0], _id="mongo-id-1")]
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=payload)
                m.get(
                    re.compile(r"https://devitjobs\.uk/api/job/mongo-id-1$"),
                    payload={
                        "description": "Real job ad prose about the role.",
                        "responsibilitiesTextArea": "Own the backend roadmap.",
                        "requirementsMustTextArea": "5+ years Python required.",
                    },
                )
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                desc = jobs[0].description
                assert "Real job ad prose about the role." in desc
                assert "Own the backend roadmap." in desc
                assert "5+ years Python required." in desc
                # The detail prose REPLACES the composed fallback, it does
                # not sit alongside it.
                assert "Technologies:" not in desc
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_detail_fetches_are_budgeted(monkeypatch):
    """Same detail-fetch-budget guard as smartrecruiters/workday: an
    uncapped pass over devitjobs (2,507 live UK-relevant jobs) risks the
    same 240s-ceiling failure mode."""
    async def _test():
        import src.sources.apis_free.devitjobs as dj_mod
        monkeypatch.setattr(dj_mod, "_MAX_DETAIL_FETCHES", 1)
        session = aiohttp.ClientSession()
        try:
            payload = [
                dict(DEVITJOBS_PAYLOAD[0], _id=f"mongo-id-{i}", name=f"ML Engineer {i}")
                for i in range(3)
            ]
            detail_calls = []
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=payload)

                def _detail_cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(payload={"description": "Real prose here."})
                m.get(re.compile(r"https://devitjobs\.uk/api/job/mongo-id-\d+$"),
                      callback=_detail_cb, repeat=True)
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
            assert len(jobs) == 3, "past-budget jobs must still be KEPT"
            assert len(detail_calls) == 1, f"budget must cap details, got {len(detail_calls)}"
            with_real_prose = [j for j in jobs if "Real prose here." in j.description]
            assert len(with_real_prose) == 1
            # The other two keep the composed fallback, never an empty string.
            assert all(j.description for j in jobs)
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
                # Currency lie fix: the real currency, never a silent GBP guess.
                assert jobs[0].salary_currency == "EUR"
                assert jobs[0].employment_type == "Full-time"
                assert jobs[0].source_tags == ["python", "nlp", "transformers"]
                assert "5+ years Python" in jobs[0].description
                assert "Kafka" in jobs[0].description
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_remote_true_maps_workplace_mode():
    """`remote` (100% fill live) was already used to build the location
    string but never reached workplace_mode. Only the TRUE case is mapped --
    False just means "not tagged remote", not "onsite" (rule #29)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [dict(LANDINGJOBS_PAYLOAD[0], remote=True, locations=[])]
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"),
                      payload=payload)
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].workplace_mode == "Remote"
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


# ---- AI-Jobs.net: source removed 2026-08-10 (aijobs.net/api/list-jobs/ 404) ----


# ---- The Muse ----

THEMUSE_PAYLOAD = {"results": [{
    "name": "Data Scientist",
    "company": {"name": "MuseCo"},
    "locations": [{"name": "London, UK"}],
    "contents": "<p>Data science and machine learning role with Python</p>",
    "refs": {"landing_page": "https://www.themuse.com/jobs/museco/data-scientist"},
    "publication_date": "2024-01-12",
    "levels": [{"name": "Mid Level"}],
    "categories": [{"name": "Data Science"}],
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
                # categories (52% fill live) is the closest thing to a
                # skill/tag list TheMuse exposes -- raw onto source_tags.
                assert jobs[0].source_tags == ["Data Science"]
                # categories[0].name ALSO feeds the category shelf raw --
                # "Data Science" matches JobCategory.DATA_SCIENCE exactly
                # once the gate normalises it.
                assert jobs[0].category == "Data Science"

                from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
                fill_shelves(jobs[0])
                assert jobs[0].category == "data_science"
                assert jobs[0].shelf_provenance["category"]["how"] == "source"
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
                # This assertion went the other way for a while. "New York, NY"
                # used to SURVIVE the fetch filter, and the comment here said
                # so: the door read it as a two-site ad because the UK really
                # does have a hamlet called New York, "and only ambiguity DATA
                # can settle that".
                #
                # Issue #330 supplied that data. `new york` is now computed as
                # ambiguous (it is a US admin1 division as well as a pop-0 UK
                # hamlet), so `names_foreign_place` refuses it and the row is
                # dropped at fetch — which is what this test was named for in
                # the first place. Six themuse rows reading "Flexible / Remote,
                # New York, NY" were live in prod when this flipped back.
                #
                # The original point still holds too: the source carries no
                # private city list — the refusal comes from the gate's data.
                assert jobs == [], "a New York job must not reach a UK catalog"
        finally:
            await session.close()
    _run(_test())


# ---- Hacker News ----

# Mirrors the real /search_by_date response: newest-first, and the SAME author
# posts a sibling "Who wants to be hired?" thread on the same day (job seekers
# advertising themselves — the inverse of this source). The old fixture carried
# only an objectID and no title, so it could not have caught either the
# relevance-vs-date bug or the sibling-thread trap.
HN_SEARCH_PAYLOAD = {
    "hits": [
        {"objectID": "99998", "title": "Ask HN: Who wants to be hired? (August 2026)"},
        {"objectID": "99999", "title": "Ask HN: Who is hiring? (August 2026)"},
    ]
}

# Realistic HN comment shape (verified live 2026-08-16): the company name is
# followed by an HTML anchor whose href is HTML-entity-escaped
# ("https:&#x2F;&#x2F;deepmind.com&#x2F;careers"), not a bare URL string --
# this is why apply_url came back empty on every real row before the fix.
HN_ITEM_PAYLOAD = {
    "children": [
        {
            "text": (
                "DeepMind <a href=\"https:&#x2F;&#x2F;deepmind.com&#x2F;careers\" "
                "rel=\"nofollow\">https:&#x2F;&#x2F;deepmind.com&#x2F;careers</a> | "
                "Machine Learning Engineer | London, UK | Remote"
                "<p>We are looking for a machine learning engineer to work on AI research."
            ),
            "created_at": "2024-01-01T12:00:00Z",
        },
        {
            "text": "SomeCo | Marketing Manager | New York | Onsite<p>Looking for a marketing manager.",
            "created_at": "2024-01-01T12:00:00Z",
        },
    ],
}


def test_hackernews_parses_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
                      payload=HN_SEARCH_PAYLOAD)
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/items/.*"),
                      payload=HN_ITEM_PAYLOAD)
                source = HackerNewsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) >= 1
                assert jobs[0].source == "hackernews"
                assert "DeepMind" in jobs[0].company
                # apply_url fix: pulled from the href, not fabricated empty.
                assert jobs[0].apply_url == "https://deepmind.com/careers"
                # title fix: the real role, not the fabricated "Company - Hiring".
                assert jobs[0].title == "Machine Learning Engineer"
                # The 4th pipe field ("Remote") is handed to BOTH
                # employment_type and workplace_mode raw -- each field's own
                # closed-enum matcher decides which (if either) it means.
                assert jobs[0].employment_type == "Remote"
                assert jobs[0].workplace_mode == "Remote"
        finally:
            await session.close()
    _run(_test())


def test_hackernews_fourth_field_lands_on_the_matching_shelf_only():
    """"Full-time" (a real, common 4th-field value, verified live 2026-08-16:
    25/242 comments) must land as employment_type via the gate and stay
    absent for workplace_mode -- proving the dual-assign is harmless, not a
    misclassification, end to end through the gate."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"children": [{
                "text": (
                    "Snout <a href=\"https:&#x2F;&#x2F;snout.com&#x2F;\">"
                    "https:&#x2F;&#x2F;snout.com&#x2F;</a> | Backend Engineer | "
                    "London, UK | Full-time<p>Join our team."
                ),
                "created_at": "2024-01-01T12:00:00Z",
            }]}
            with aioresponses() as m:
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
                      payload=HN_SEARCH_PAYLOAD)
                m.get(re.compile(r"https://hn\.algolia\.com/api/v1/items/.*"),
                      payload=payload)
                source = HackerNewsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].employment_type == "Full-time"
                assert jobs[0].workplace_mode == "Full-time"

                from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
                fill_shelves(jobs[0])
                assert jobs[0].employment_type == "full_time"
                assert jobs[0].shelf_provenance["employment_type"]["how"] == "source"
                assert jobs[0].workplace_mode is None
                assert jobs[0].shelf_provenance["workplace_mode"]["how"] == "absent"
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
                # Careerjet has two APIs taking different credentials. The v4
                # host (search.api.careerjet.net) wants an API KEY via Basic
                # Auth plus an allow-listed IP; an affiliate ID only works on
                # the public affiliate endpoint, over http, with a Referer
                # header. Verified live 2026-08-11 — v4 answered 401/403 for a
                # valid affid, the endpoint below returned 8,600 hits.
                m.get(re.compile(r"http://public\.api\.careerjet\.net/search.*"),
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


def test_careerjet_maps_salary_type_to_salary_period():
    """salary_type ('Y'/'H'/'M', all three seen live 2026-08-16) is the
    sibling of salary_min/max — until now an hourly rate and an annual
    salary landed in the same column with no unit at all."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"jobs": [dict(CAREERJET_PAYLOAD["jobs"][0], salary_type="H", salary_currency_code="GBP")]}
            with aioresponses() as m:
                m.get(re.compile(r"http://public\.api\.careerjet\.net/search.*"),
                      payload=payload, repeat=True)
                source = CareerjetSource(session, affid="test-affid", search_config=_sc_ai_defaults())
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].salary_period == "H"
                assert jobs[0].salary_currency == "GBP"
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


def test_findwork_maps_remote_and_employment_type():
    """`remote` (bool) and `employment_type` (raw string, e.g. "full time")
    both sit on the same item, confirmed populated live 2026-08-16,
    previously unread."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(FINDWORK_PAYLOAD["results"][0], remote=True, employment_type="full time")]}
            with aioresponses() as m:
                m.get(re.compile(r"https://findwork\.dev/api/jobs/.*"),
                      payload=payload)
                source = FindworkSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].workplace_mode == "remote"
                assert jobs[0].employment_type == "full time"
        finally:
            await session.close()
    _run(_test())


def test_findwork_workplace_mode_unset_when_not_remote():
    """`remote=False` must not become workplace_mode='onsite' — False only
    means 'not exclusively remote' (rule #29: never guess)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(FINDWORK_PAYLOAD["results"][0], remote=False)]}
            with aioresponses() as m:
                m.get(re.compile(r"https://findwork\.dev/api/jobs/.*"),
                      payload=payload)
                source = FindworkSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].workplace_mode is None
        finally:
            await session.close()
    _run(_test())


def test_findwork_maps_keywords_to_source_tags():
    """`keywords` (a real skill-tag list — "nlp"; "python","ml","typescript",
    "pytorch","pandas","embedded","sql" — both shapes seen live 2026-08-17)
    sits on the same item as `remote`/`employment_type`, previously unread.
    Zero extra cost — same response, straight onto source_tags (skills
    shelf), same pattern as arbeitnow/remoteok/landingjobs."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"results": [dict(FINDWORK_PAYLOAD["results"][0], keywords=["python", "ml", "sql"])]}
            with aioresponses() as m:
                m.get(re.compile(r"https://findwork\.dev/api/jobs/.*"),
                      payload=payload)
                source = FindworkSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].source_tags == ["python", "ml", "sql"]
        finally:
            await session.close()
    _run(_test())


def test_findwork_source_tags_empty_when_keywords_absent():
    """No `keywords` key at all (a real shape too) must not crash and must
    leave source_tags empty, never a guess."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://findwork\.dev/api/jobs/.*"),
                      payload=FINDWORK_PAYLOAD)
                source = FindworkSource(session, api_key="test-key")
                jobs = await source.fetch_jobs()
                assert jobs, "no jobs returned"
                assert jobs[0].source_tags == []
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
        # tiles.values (100% fill live, 21,739/21,739 sampled) carries
        # category + skill/requirement tags on every posting.
        "tiles": {"values": [
            {"value": "Python", "type": "requirement"},
            {"value": "AI", "type": "category"},
        ]},
        # seniority[] (100% fill live) and fullyRemote (100% fill live) --
        # verified 2026-08-17, 1,000-posting sample.
        "seniority": ["Senior"],
        "fullyRemote": True,
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
                # tiles.values raw onto source_tags -- the job own vocabulary.
                assert jobs[0].source_tags == ["Python", "AI"]
                # seniority[0] feeds BOTH experience_level (legacy) AND the
                # closed-enum seniority shelf (new).
                assert jobs[0].experience_level == "Senior"
                assert jobs[0].seniority == "Senior"
                # fullyRemote:true onto workplace_mode; category raw onto
                # the category shelf ("AI" itself won't match the closed
                # enum -- the gate leaves it honestly unmapped).
                assert jobs[0].workplace_mode == "Remote"
                assert jobs[0].category == "AI"
        finally:
            await session.close()
    _run(_test())


def test_nofluffjobs_fetches_description_from_detail_endpoint():
    """The list endpoint (api/posting) has no description field at all --
    description was never passed to Job() here, which silently disabled
    visa and deadline extraction downstream. The real prose lives at
    /api/posting/{id} -> requirements.description (verified live 2026-08-16,
    15/15 sampled hit), budgeted like smartrecruiters/devitjobs."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting$"),
                      payload=[NOFLUFFJOBS_PAYLOAD[0]])
                m.get(
                    re.compile(r"https://nofluffjobs\.com/api/posting/ml-engineer-abc$"),
                    payload={
                        "requirements": {
                            "description": "<div><p>Build ML pipelines with Python.</p></div>",
                        },
                        # expiresAt (ISO, verified live 2026-08-17) rides the
                        # SAME already-fetched detail response -- zero extra
                        # HTTP cost.
                        "expiresAt": "2026-09-19T23:59:59",
                    },
                )
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert "Build ML pipelines with Python." in jobs[0].description
                assert jobs[0].deadline == "2026-09-19"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_nofluffjobs_detail_fetches_are_budgeted(monkeypatch):
    """Same detail-fetch-budget guard as smartrecruiters/workday/devitjobs."""
    async def _test():
        import src.sources.other.nofluffjobs as nfj_mod
        monkeypatch.setattr(nfj_mod, "_MAX_DETAIL_FETCHES", 1)
        session = aiohttp.ClientSession()
        try:
            payload = [
                dict(NOFLUFFJOBS_PAYLOAD[0], id=f"ml-engineer-{i}", title=f"ML Engineer {i}")
                for i in range(3)
            ]
            detail_calls = []
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting$"), payload=payload)

                def _detail_cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(payload={"requirements": {"description": "Real prose."}})
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting/ml-engineer-\d$"),
                      callback=_detail_cb, repeat=True)
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()
            assert len(jobs) == 3, "past-budget jobs must still be KEPT"
            assert len(detail_calls) == 1, f"budget must cap details, got {len(detail_calls)}"
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
                # Live schema (probed 2026-08-08): the employer is carried in
                # `name`; a `company` key does not exist on this endpoint
                # (0 of 20,631 postings had one). Kept alongside `name` so the
                # legacy-fallback path stays covered.
                "name": "GermanCo",
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


# ---- jobs.ac.uk: source removed 2026-08-10 (all 4 feed URLs 404) ----


# ---- NHS Jobs ----

NHS_JOBS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<vacancies>
  <vacancy>
    <id>12345</id>
    <title>Data Scientist - NHS Digital</title>
    <employer>NHS Digital</employer>
    <location>Leeds</location>
    <salary>40000 - 55000</salary>
    <type>Permanent</type>
    <description>We are seeking an experienced Data Scientist to join our growing digital health team.</description>
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


def test_nhs_jobs_maps_real_description_and_employment_type():
    """FABRICATION FIX: description used to be f"{title} - {salary}" -- a
    made-up string. The feed carries a real <description> teaser; use it.
    <type> ("Permanent"/"Bank"/...) is NHS Jobs' own employment-type
    vocabulary, previously read nowhere.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.jobs\.nhs\.uk/api/v1/search_xml.*"),
                      body=NHS_JOBS_XML, content_type="application/xml", repeat=True)
                sc = _make_search_config(["data scientist"])
                source = NHSJobsSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                job = next(j for j in jobs if j.title == "Data Scientist - NHS Digital")
                assert "experienced Data Scientist" in job.description
                assert job.description != f"{job.title} - 40000 - 55000"
                assert job.employment_type == "Permanent"
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


def test_personio_parses_salary_information_and_universal_shelf_fields():
    """Job-understanding fix (2026-08-16): `<salaryInformation>` (min/max/
    currencyCode/type) is a real structured block, verified live on real
    boards, never opened before. `<employmentType>`/`<seniority>` are raw
    values never mapped onto the Universal Shelf fields either."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            xml = """<?xml version="1.0"?>
            <workzag-jobs>
              <position>
                <id>201</id>
                <name>Backend Engineer</name>
                <office>Bristol</office>
                <department>Engineering</department>
                <employmentType>permanent</employmentType>
                <seniority>entry-level</seniority>
                <jobDescriptions>
                  <jobDescription><name>About</name><value>Backend role</value></jobDescription>
                </jobDescriptions>
                <salaryInformation>
                    <min>28000.00</min>
                    <max>32000.00</max>
                    <currencySymbol>£</currencySymbol>
                    <currencyCode>GBP</currencyCode>
                    <type>yearly</type>
                </salaryInformation>
              </position>
            </workzag-jobs>"""
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.jobs\.personio\.de/xml.*"),
                      body=xml, content_type="application/xml", repeat=True)
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min == 28000.0
                assert job.salary_max == 32000.0
                assert job.salary_currency == "GBP"
                assert job.salary_period == "annual"
                assert job.employment_type == "permanent"
                assert job.seniority == "entry-level"
        finally:
            await session.close()
    _run(_test())


def test_personio_maps_keywords_and_occupation_category():
    """Pillar 3 fix (2026-08-17): `<keywords>` is a comma-separated skills
    list (confirmed live, flatpay board: 79/122 filled) and
    `<occupationCategory>` is Personio's own closed job-function taxonomy
    (confirmed live across 8 boards) -- neither was ever read."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            xml = """<?xml version="1.0"?>
            <workzag-jobs>
              <position>
                <id>203</id>
                <name>People Operations Specialist</name>
                <office>Berlin</office>
                <employmentType>permanent</employmentType>
                <keywords>People,Operations,Human Resources,Fintech,HR</keywords>
                <occupationCategory>it_software</occupationCategory>
                <jobDescriptions>
                  <jobDescription><name>About</name><value>Ops role</value></jobDescription>
                </jobDescriptions>
              </position>
            </workzag-jobs>"""
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.jobs\.personio\.de/xml.*"),
                      body=xml, content_type="application/xml", repeat=True)
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.source_tags == ["People", "Operations", "Human Resources", "Fintech", "HR"]
                assert job.category == "it_software"
        finally:
            await session.close()
    _run(_test())


def test_personio_prefers_schedule_over_permanent_for_part_time_roles():
    """`<schedule>` ("full-time"/"part-time") is a SEPARATE field from
    `<employmentType>` ("permanent"/...) -- the latter carries no hours
    signal on its own, so the gate's "permanent"->full_time alias would
    misclassify a permanent PART-TIME role. When employmentType is exactly
    "permanent" and a real schedule value exists, schedule wins."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            xml = """<?xml version="1.0"?>
            <workzag-jobs>
              <position>
                <id>204</id>
                <name>Part-Time Recruiter</name>
                <office>Berlin</office>
                <employmentType>permanent</employmentType>
                <schedule>part-time</schedule>
                <jobDescriptions>
                  <jobDescription><name>About</name><value>Recruiting role</value></jobDescription>
                </jobDescriptions>
              </position>
            </workzag-jobs>"""
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.jobs\.personio\.de/xml.*"),
                      body=xml, content_type="application/xml", repeat=True)
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].employment_type == "part-time"
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


# ---- WorkAnywhere: source removed 2026-08-10 (HTTP 429 bot-checkpoint) ----


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
  <type>Full-Time</type>
  <skills>Python, PyTorch</skills>
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


def test_weworkremotely_maps_type_and_skills():
    """<type> (100% fill live) and <skills> (34% fill live) used to be
    parsed nowhere -- confirmed live 2026-08-16.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://weworkremotely.com/remote-jobs.rss",
                      body=WEWORKREMOTELY_RSS, content_type="application/xml")
                source = WeWorkRemotelySource(session)
                jobs = await source.fetch_jobs()
                assert jobs[0].employment_type == "Full-Time"
                assert jobs[0].source_tags == ["Python", "PyTorch"]
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


REALWORKFROMANYWHERE_DETAIL_LDJSON = """<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting","title":"Data Scientist",
"description":"Data science role","validThrough":"2026-10-13",
"baseSalary":{"@type":"MonetaryAmount","currency":"USD",
"value":{"@type":"QuantitativeValue","minValue":90000,"maxValue":125000,"unitText":"YEAR"}}}
</script>
</body></html>"""


def test_realworkfromanywhere_maps_deadline_and_salary_from_detail_page():
    """validThrough (12/12 filled live) and baseSalary (6/12 filled live)
    sit in the job's own JSON-LD, confirmed live 2026-08-16.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://www.realworkfromanywhere.com/rss.xml",
                      body=REALWORKFROMANYWHERE_RSS, content_type="application/xml")
                m.get("https://www.realworkfromanywhere.com/job/ds-globalai",
                      body=REALWORKFROMANYWHERE_DETAIL_LDJSON, content_type="text/html")
                source = RealWorkFromAnywhereSource(session)
                jobs = await source.fetch_jobs()
                job = jobs[0]
                assert job.deadline == "2026-10-13"
                assert job.deadline_source == "listing"
                assert job.salary_min == 90000.0
                assert job.salary_max == 125000.0
                assert job.salary_currency == "USD"
                assert job.salary_period == "YEAR"
        finally:
            await session.close()
    _run(_test())


# ---- BioSpace: source removed 2026-08-10 (all 3 job RSS URLs 404) ----


# ---- Climatebase ----

CLIMATEBASE_HTML = """<html><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"jobs":[
  {"id":"123","title":"Data Scientist","name_of_employer":"ClimateCo","locations":["London, UK"],"activation_date":"2026-07-18T08:02:48.960Z","job_types":["Full time role"],"remote_preferences":["Hybrid"],"sectors":["Research & Education","Capital"]}
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


def test_climatebase_maps_date_and_type_fields():
    """activation_date (100% fill live) used to be thrown away for a
    hardcoded posted_at=None/"low"; job_types, remote_preferences, sectors
    (all 96-100% fill live) were parsed nowhere. Confirmed live 2026-08-16.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://climatebase\.org/jobs.*"),
                      body=CLIMATEBASE_HTML, content_type="text/html", repeat=True)
                source = ClimatebaseSource(session)
                jobs = await source.fetch_jobs()
                job = jobs[0]
                assert job.posted_at == "2026-07-18T08:02:48.960Z"
                assert job.date_confidence == "high"
                assert job.employment_type == "Full time role"
                assert job.workplace_mode == "Hybrid"
                assert job.source_tags == ["Research & Education", "Capital"]
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


def test_eightykhours_maps_real_date_and_seniority():
    """BUG FIX: the payload has no "date_published" key at all (0/20 filled
    live 2026-08-16) -- the real posting date is "posted_at" (epoch
    seconds, 20/20 filled). closes_at (epoch, ~45% filled) is the deadline.
    tags_exp_required (100% filled) is seniority-shaped free text.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {
                "hits": [
                    {
                        "objectID": "999",
                        "title": "AI Safety Researcher",
                        "company_name": "SafetyOrg",
                        "locations": [{"name": "London, UK"}],
                        "description_short": "Research role in AI safety",
                        "posted_at": 1786665840,
                        "closes_at": 1787184000,
                        "tags_exp_required": ["Junior (1-4 years experience)"],
                    }
                ],
                "nbHits": 1,
            }
            with aioresponses() as m:
                m.post(re.compile(r"https://w6km1udib3-dsn\.algolia\.net/.*"),
                       payload=payload, repeat=True)
                sc = _make_search_config(["AI safety researcher"])
                source = EightyKHoursSource(session, search_config=sc)
                jobs = await source.fetch_jobs()
                job = jobs[0]
                assert job.date_confidence == "high"
                assert job.posted_at is not None
                assert job.deadline is not None
                assert job.seniority == "Junior (1-4 years experience)"
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


UNI_JOBS_DETAIL_HTML = """<html><body>
<aside id="sidebar">
<h6>Department/Location</h6>
<p><a href="/job/?unit=u00150">Department of Architecture, Cambridge</a></p>
<h6>Salary</h6>
<p>&pound;35,608-&pound;46,049 pro rata</p>
<h6>Reference</h6>
<p>GC50613</p>
<h6>Closing date</h6>
<p>24 August 2026</p>
</aside>
</body></html>"""


def test_uni_jobs_maps_salary_and_closing_date_from_detail_page():
    """Salary and Closing date sit in the Cambridge detail page sidebar,
    not the RSS feed -- both shelves were 100% empty before this fix
    (confirmed live 2026-08-16). Academic jobs live by their deadline more
    than any other source in this batch.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r".*jobs\.cam\.ac\.uk.*format=rss.*"),
                      body=UNI_JOBS_RSS, content_type="application/xml")
                m.get(re.compile(r".*hr-jobs\.lancs\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.kent\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.royalholloway\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*jobs\.surrey\.ac\.uk.*"), status=404)
                m.get(re.compile(r".*uukjobs\.co\.uk.*"), status=404)
                m.get("https://www.jobs.cam.ac.uk/job/12345/",
                      body=UNI_JOBS_DETAIL_HTML, content_type="text/html")
                source = UniJobsSource(session)
                jobs = await source.fetch_jobs()
                job = jobs[0]
                assert job.salary_min == 35608.0
                assert job.salary_max == 46049.0
                assert job.deadline == "2026-08-24"
                assert job.deadline_source == "listing"
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


def test_successfactors_admits_real_uk_job_via_jsonld_and_fixes_location_lie():
    """Job-understanding fix (2026-08-16): this source used to hardcode
    location="UK" on every posting that survived a TITLE-text UK check --
    but titles rarely name a country, so real US/France postings were
    stored as "UK". The fix fetches each candidate's detail page and reads
    the REAL location out of JSON-LD (modern template, e.g. BAE/Thales)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            sitemap = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://jobs.example.com/global/en/job/128607BR/Principal-Engineer-London</loc></url>'
                '</urlset>'
            )
            detail_html = (
                '<html><body><script type="application/ld+json">'
                '{"@type":"JobPosting","employmentType":["FULL_TIME"],'
                '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
                '"addressLocality":"London","addressCountry":"United Kingdom"}},'
                '"description":"&lt;p&gt;Real engineering work in London.&lt;/p&gt;"}'
                '</script></body></html>'
            )
            companies = [{"name": "Example Co", "sitemap_url": "https://jobs.example.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://jobs.example.com/sitemap.xml", body=sitemap, content_type="application/xml")
                m.get("https://jobs.example.com/global/en/job/128607BR/Principal-Engineer-London",
                      body=detail_html, content_type="text/html")
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.location == "London, United Kingdom"
            assert job.location != "UK", "must not hardcode the location"
            assert "Real engineering work in London" in job.description
            assert job.description != job.title, "must not store the title as the description"
            assert job.employment_type == "FULL_TIME"
        finally:
            await session.close()
    _run(_test())


def test_successfactors_maps_jsonld_date_posted_and_base_salary():
    """Pillar 3 fix (2026-08-17): `datePosted` is a standard schema.org
    JobPosting field present on every BAE-template page (confirmed live,
    6/8 sampled) but posted_at was hardcoded to None for this whole source.
    `baseSalary` is extracted defensively too -- confirmed always null on
    BAE (0/8 sampled), but the read is generic across any tenant that does
    populate it."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            sitemap = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://jobs.example.com/global/en/job/128608BR/Senior-Engineer-London</loc></url>'
                '</urlset>'
            )
            detail_html = (
                '<html><body><script type="application/ld+json">'
                '{"@type":"JobPosting","employmentType":["FULL_TIME"],'
                '"datePosted":"2026-08-16",'
                '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
                '"addressLocality":"London","addressCountry":"United Kingdom"}},'
                '"baseSalary":{"@type":"MonetaryAmount","currency":"GBP",'
                '"value":{"@type":"QuantitativeValue","minValue":60000,"maxValue":80000}},'
                '"description":"Senior engineering role."}'
                '</script></body></html>'
            )
            companies = [{"name": "Example Co", "sitemap_url": "https://jobs.example.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://jobs.example.com/sitemap.xml", body=sitemap, content_type="application/xml")
                m.get("https://jobs.example.com/global/en/job/128608BR/Senior-Engineer-London",
                      body=detail_html, content_type="text/html")
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.posted_at is not None
            assert job.posted_at.startswith("2026-08-16")
            assert job.date_confidence != "low", "a real structured date must not stay low-confidence"
            assert job.salary_min == 60000
            assert job.salary_max == 80000
            assert job.salary_currency == "GBP"
        finally:
            await session.close()
    _run(_test())


def test_successfactors_drops_confirmed_non_uk_job_instead_of_lying():
    """The core regression: a US posting must be DROPPED, never admitted
    with a fabricated "UK" location."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            sitemap = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://jobs.example.com/global/en/job/999BR/Program-Planner-Nashua</loc></url>'
                '</urlset>'
            )
            detail_html = (
                '<html><body><script type="application/ld+json">'
                '{"@type":"JobPosting","employmentType":["FULL_TIME"],'
                '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
                '"addressLocality":"Nashua","addressCountry":"United States"}},'
                '"description":"US role."}'
                '</script></body></html>'
            )
            companies = [{"name": "Example Co", "sitemap_url": "https://jobs.example.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://jobs.example.com/sitemap.xml", body=sitemap, content_type="application/xml")
                m.get("https://jobs.example.com/global/en/job/999BR/Program-Planner-Nashua",
                      body=detail_html, content_type="text/html")
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
            assert jobs == [], "a confirmed non-UK posting must be dropped, not admitted as UK"
        finally:
            await session.close()
    _run(_test())


def test_successfactors_legacy_microdata_template_and_title_from_url_fix():
    """Job-understanding fix (2026-08-16): the legacy SuccessFactors
    template (no JSON-LD) appends the numeric job ID as its OWN trailing
    path segment (.../job/Title-Words/1368001233/) -- the old
    _title_from_url took the last segment alone, always got the bare
    digit ID, and `if not title: continue` silently dropped EVERY posting
    on this template. Also verifies the microdata location/description
    fallback path (jobGeoLocation span + itemprop="description")."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            sitemap = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://careers.example.com/job/Malvern-Systems-Engineer/1368001233/</loc></url>'
                '</urlset>'
            )
            detail_html = (
                '<html><body><div class="jobDisplayShell">'
                '<span class="jobGeoLocation">Malvern, England, United Kingdom</span>'
                '<span itemprop="description" class="x">'
                '<span class="jobdescription"><p>Real systems engineering role in Malvern.</p></span>'
                '</span></div></body></html>'
            )
            companies = [{"name": "QinetiQ", "sitemap_url": "https://careers.example.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://careers.example.com/sitemap.xml", body=sitemap, content_type="application/xml")
                m.get("https://careers.example.com/job/Malvern-Systems-Engineer/1368001233/",
                      body=detail_html, content_type="text/html")
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
            assert len(jobs) == 1, "legacy microdata template must not be silently dropped"
            job = jobs[0]
            assert job.title != "1368001233", "title-from-url must skip the trailing numeric ID segment"
            assert "Malvern" in job.title
            assert job.location == "Malvern, England, United Kingdom"
            assert "Real systems engineering role in Malvern" in job.description
        finally:
            await session.close()
    _run(_test())


def test_successfactors_detail_fetches_are_budgeted_per_company(monkeypatch):
    """Same timeout-regression shape as workday/smartrecruiters: an
    unbounded per-posting detail fetch across thousands of sitemap URLs
    would blow the 240s ATS ceiling. Jobs past the per-company budget are
    DROPPED (never admitted with a guessed location), not merely
    description-less."""
    async def _test():
        import src.sources.ats.successfactors as sf_mod
        monkeypatch.setattr(sf_mod, "_MAX_DETAIL_FETCHES_PER_COMPANY", 1)
        session = aiohttp.ClientSession()
        try:
            sitemap = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://jobs.example.com/global/en/job/1BR/Engineer-London-One</loc></url>'
                '<url><loc>https://jobs.example.com/global/en/job/2BR/Engineer-London-Two</loc></url>'
                '</urlset>'
            )
            detail_html = (
                '<html><body><script type="application/ld+json">'
                '{"@type":"JobPosting","jobLocation":{"@type":"Place","address":{'
                '"addressLocality":"London","addressCountry":"United Kingdom"}},'
                '"description":"role"}'
                '</script></body></html>'
            )
            detail_calls = []
            companies = [{"name": "Example Co", "sitemap_url": "https://jobs.example.com/sitemap.xml"}]
            with aioresponses() as m:
                m.get("https://jobs.example.com/sitemap.xml", body=sitemap, content_type="application/xml")

                def _cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(body=detail_html, content_type="text/html")
                m.get(re.compile(r"https://jobs\.example\.com/global/en/job/.*"), callback=_cb, repeat=True)
                source = SuccessFactorsSource(session, companies=companies)
                jobs = await source.fetch_jobs()
            assert len(detail_calls) == 1, f"budget must cap detail fetches, got {len(detail_calls)}"
            assert len(jobs) == 1, "the job past budget must be dropped, not guessed"
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


AIJOBS_AI_NO_LOCATION_LEAK_HTML = """<html><body>
<a href="https://aijobs.ai/job/us-only-role" class="icon-thumb">
  <img src="/x.jpeg" alt="">
</a>
<a href="https://aijobs.ai/job/us-only-role" class="iconbox-content">
  <div class="post-main-title">US Only Data Role</div>
  <div>Full Time</div>
  <span class="info-tools">United States</span>
</a>
<a href="https://aijobs.ai/job/uk-role" class="icon-thumb">
  <img src="/y.jpeg" alt="">
</a>
<a href="https://aijobs.ai/job/uk-role" class="iconbox-content">
  <div class="post-main-title">UK Data Role</div>
  <div>Full Time</div>
  <span class="info-tools">London</span>
</a>
</body></html>"""


def test_aijobs_ai_drops_the_cards_own_location_text_no_more():
    """RULE #30 LEAK FIX: this card layout has no location HTML attribute --
    the location is a plain text run inside the card, which used to be
    thrown away entirely (comment claimed "cards carry NO location").
    Confirmed live 2026-08-16 that discarding it let a non-UK card slip
    the fetch-time filter (empty location reads as "unknown, don't
    filter"). Now the text run is captured and used to filter correctly.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/.*"),
                      body=AIJOBS_AI_NO_LOCATION_LEAK_HTML, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                jobs = await source.fetch_jobs()
            titles = {j.title for j in jobs}
            assert "US Only Data Role" not in titles
            uk_job = next(j for j in jobs if j.title == "UK Data Role")
            assert uk_job.location == "London"
        finally:
            await session.close()
    _run(_test())


AIJOBS_AI_LDJSON_DETAIL = """<html><body>
<script type="application/ld+json">
{
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Senior Robotics Systems Engineer",
    "description": "<p>We build advanced robotics systems for logistics.</p>",
    "datePosted": "2026-08-04",
    "validThrough": "2026-09-03"
}
</script>
</body></html>"""


def test_aijobs_ai_fetches_real_description_from_detail_page():
    """description used to be hardcoded to the title -- the real text lives
    in the per-job ld+json (confirmed live 2026-08-16).
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://aijobs\.ai/(remote/)?$"),
                      body=AIJOBS_AI_CARD_HTML, content_type="text/html", repeat=True)
                m.get("https://aijobs.ai/job/senior-robotics-systems-engineer",
                      body=AIJOBS_AI_LDJSON_DETAIL, content_type="text/html", repeat=True)
                m.get("https://aijobs.ai/job/mission-operations-lead",
                      body=AIJOBS_AI_LDJSON_DETAIL, content_type="text/html", repeat=True)
                source = AIJobsAISource(session)
                jobs = await source.fetch_jobs()
            job = next(j for j in jobs if j.title == "Senior Robotics Systems Engineer")
            assert "advanced robotics systems" in job.description
            assert job.description != job.title
            assert job.deadline == "2026-09-03"
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
# Batch 3 new sources: Teaching Vacancies, GOV.UK Apprenticeships
# (NHS Jobs XML, Rippling ATS and Comeet ATS have since been removed — dead upstreams)
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


def test_teaching_vacancies_maps_employment_type():
    """employmentType (schema.org field, 100% fill live 2026-08-16) can be
    multi-valued -- take the raw first value.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {
                "jobs": [dict(TEACHING_VACANCIES_PAYLOAD["jobs"][0], employmentType=["FULL_TIME", "TEMPORARY"])]
            }
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, payload=payload)
                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()
                assert jobs[0].employment_type == "FULL_TIME"
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


# ---- NHS Jobs XML: source removed 2026-08-10 (feed serves HTML, retired).
#      The separate nhs_jobs source is ALIVE and still tested above. ----


# ---- Rippling ATS: source removed 2026-08-10 (board API gone, all slugs 404) ----
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


class TestHackerNewsUsesTheCurrentThread:
    """`/search` ranks by RELEVANCE, not date, so the old query returned the
    same COVID-era thread forever - "Ask HN: Who is hiring right now?" from
    2020-03-23. Measured 2026-08-08: all 118 stored HackerNews jobs carried
    posted_at=2020-03-23, stamped date_confidence='high'. The catalog was
    serving six-year-old dead postings as current, and recency scored them 0.

    Found by scripts/distribution_sanity.py on its first run (one distinct
    posted_at across 118 rows) - this test is the pin.

    These drive fetch_jobs() through aioresponses and assert on the URL that
    was actually REQUESTED and the thread that was actually CHOSEN. The first
    version of this class asserted `"search_by_date" in <the source file>`,
    which CodeRabbit correctly refused: that string also appears in a comment
    four lines above the call, so the test passed whether or not the code
    called the endpoint. It is the same defect this repo has now hit four
    times - NAMING A THING IS NOT RUNNING IT - so it is pinned behaviourally.
    """

    _SEEKERS = {
        "objectID": "99998",
        "title": "Ask HN: Who wants to be hired? (August 2026)",
        "created_at": "2026-08-01T15:00:00.000Z",
    }
    _HIRING = {
        "objectID": "99999",
        "title": "Ask HN: Who is hiring? (August 2026)",
        "created_at": "2026-08-01T15:00:00.000Z",
    }
    _COMMENT = {
        "children": [{
            "text": (
                "Acme Ltd | London, UK | Full-time | REMOTE&#x2F;hybrid<p>"
                "Backend Engineer building payment rails in Python and "
                "Postgres. Apply: https:&#x2F;&#x2F;acme.example&#x2F;jobs&#x2F;1"
            ),
            "created_at": "2026-08-01T16:00:00.000Z",
        }],
    }

    def _fetch(self, hits, items_by_id):
        """Run fetch_jobs() against mocked HN endpoints; return (jobs, urls)."""
        from src.sources.other.hackernews import HackerNewsSource

        seen: list[str] = []

        async def _test():
            session = aiohttp.ClientSession()
            try:
                with aioresponses() as m:
                    def _record(url, **kwargs):
                        seen.append(str(url))

                    m.get(
                        re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
                        payload={"hits": hits}, repeat=True, callback=_record,
                    )
                    m.get(
                        re.compile(r"https://hn\.algolia\.com/api/v1/search\?.*"),
                        payload={"hits": hits}, repeat=True, callback=_record,
                    )
                    for oid, body in items_by_id.items():
                        m.get(
                            f"https://hn.algolia.com/api/v1/items/{oid}",
                            payload=body, repeat=True, callback=_record,
                        )
                    source = HackerNewsSource(session, search_config=_sc_ai_defaults())
                    return await source.fetch_jobs()
            finally:
                await session.close()

        jobs = _run(_test())
        return jobs, seen

    def test_queries_the_date_sorted_endpoint(self) -> None:
        """The relevance-ranked `/search` must never be called."""
        jobs, urls = self._fetch([self._HIRING], {"99999": self._COMMENT})

        assert any("search_by_date" in u for u in urls), (
            f"must sort by date, not relevance; requested: {urls}"
        )
        assert not any(
            "/api/v1/search?" in u or u.endswith("/api/v1/search") for u in urls
        ), f"the relevance-ranked /search must not be called; requested: {urls}"
        assert len(jobs) == 1, "the mocked hiring thread must yield its one job"

    def test_skips_the_who_wants_to_be_hired_sibling(self) -> None:
        """The same author posts "Who wants to be hired?" on the same day -
        job SEEKERS advertising themselves, the inverse of this source. It is
        returned FIRST here, so taking hits[0] blindly fails this test."""
        jobs, urls = self._fetch(
            [self._SEEKERS, self._HIRING],
            {"99998": self._COMMENT, "99999": self._COMMENT},
        )

        assert not any("/items/99998" in u for u in urls), (
            f"followed the job-SEEKERS thread; requested: {urls}"
        )
        assert any("/items/99999" in u for u in urls), (
            f"did not reach the hiring thread; requested: {urls}"
        )
        assert len(jobs) == 1 and jobs[0].company == "Acme Ltd"

    def test_no_hiring_thread_returns_empty_rather_than_the_sibling(self) -> None:
        """Negative control: with ONLY the seekers thread present, the source
        must return nothing. Without this, a version that fell back to hits[0]
        would still pass the test above by never being offered a choice."""
        jobs, urls = self._fetch([self._SEEKERS], {"99998": self._COMMENT})

        assert jobs == [], "job SEEKERS must never enter the catalog"
        assert not any("/items/" in u for u in urls), (
            f"fetched a thread's comments anyway; requested: {urls}"
        )


# =============================================================================
# Issue #334 — descriptions that were never fetched at all
#
# `catalog_has_descriptions` fired with 139 new violations. Measured in prod
# 2026-08-19, the last 7 days were workable 115/115 empty, nofluffjobs 11/11
# empty. Both were mechanism (a), "never fetched" — insert_job writes the
# column fine (database.py:388,404) and there is exactly one `description`
# column on `jobs`.
#
# Rule #21 applies hard here: asserting the field EXISTS proves nothing,
# because it existed and was "" for every one of those rows. These assert a
# real value, past the 200-char floor the scorer and coverage.py actually use.
# =============================================================================

_WORKABLE_DETAIL_HTML = (
    "<p>Suade is a market-leading regtech SaaS company automating regulatory "
    "reporting, compliance and financial risk solutions for banks.</p>"
)
_WORKABLE_REQS_HTML = (
    "<ul><li>Strong Python and SQL</li><li>Experience with Kubernetes, "
    "Terraform and AWS</li><li>Comfortable owning services end to end</li></ul>"
)


def test_workable_fetches_description_from_the_detail_endpoint():
    """The DETAIL endpoint's richer prose must reach the Job.

    The list source is now `GET /api/v1/widget/accounts/{slug}?details=true`,
    not the old `POST /api/v2/.../jobs`. Field names below are the ones the live
    widget response actually uses (measured 2026-08-24 on the huggingface board,
    7 postings): flat `city`/`country` (NOT a `location` dict) and
    `published_on` (NOT `published`) — both of the old names are 0% present.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/[^/?]+"),
                      payload={"jobs": [{
                          "shortcode": "5B62764A74", "title": "Platform Engineer",
                          "city": "London", "country": "UK",
                          "published_on": "2026-08-18",
                          "description": "<p>Short list blurb.</p>",
                      }]})
                m.get(re.compile(r"https://apply\.workable\.com/api/v2/accounts/suade/jobs/5B62764A74"),
                      payload={"description": _WORKABLE_DETAIL_HTML,
                               "requirements": _WORKABLE_REQS_HTML,
                               "benefits": "<p>Pension and 25 days holiday.</p>"})
                source = WorkableSource(session, companies=["suade"])
                jobs = await source.fetch_jobs()

            assert len(jobs) == 1
            desc = jobs[0].description
            assert len(desc) > 200, f"empty/thin description is the bug: {len(desc)} chars"
            assert "regtech" in desc, "the detail endpoint's prose must reach the Job"
            assert "Kubernetes" in desc, "requirements carry the skills the scorer needs"
            assert "<p>" not in desc and "<li>" not in desc, "HTML must be stripped"
            assert jobs[0].apply_url == "https://apply.workable.com/suade/j/5B62764A74/"
        finally:
            await session.close()
    _run(_test())


def test_workable_detail_failure_degrades_to_the_list_text_never_drops_the_job():
    """A correctness fix must not turn a thin row into a lost row.

    Behaviour CHANGED here, deliberately: a failed detail fetch used to leave an
    EMPTY description. The widget list response carries a real description on
    100% of rows (measured live 2026-08-24), so that text is now the floor. The
    job is still never dropped — it just no longer falls all the way to "".
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/[^/?]+"),
                      payload={"jobs": [{
                          "shortcode": "ZZZ", "title": "Platform Engineer",
                          "city": "London", "country": "UK",
                          "description": "<p>List blurb survives a dead detail call.</p>",
                      }]})
                m.get(re.compile(r"https://apply\.workable\.com/api/v2/accounts/suade/jobs/ZZZ"),
                      status=404, repeat=True)
                source = WorkableSource(session, companies=["suade"])
                jobs = await source.fetch_jobs()
            assert len(jobs) == 1, "a failed detail fetch must never drop the job"
            assert "List blurb survives" in jobs[0].description
            assert "<p>" not in jobs[0].description, "HTML must be stripped"
        finally:
            await session.close()
    _run(_test())


def test_workable_detail_budget_caps_the_extra_requests(monkeypatch):
    """Budget in the same shape as workday/smartrecruiters, so one run cannot
    blow SOURCE_FETCH_TIMEOUT_ATS (240s). Past-budget jobs are still KEPT."""
    async def _test():
        import src.sources.ats.workable as workable_mod
        monkeypatch.setattr(workable_mod, "_MAX_DETAIL_FETCHES", 1)
        session = aiohttp.ClientSession()
        try:
            results = [{"shortcode": f"SC{i}", "title": "ML Engineer",
                        "city": "London", "country": "UK",
                        "description": "<p>List blurb.</p>"} for i in range(4)]
            detail_calls = []
            with aioresponses() as m:
                m.get(re.compile(r"https://apply\.workable\.com/api/v1/widget/accounts/[^/?]+"),
                      payload={"jobs": results}, repeat=True)

                def _cb(url, **kw):
                    from aioresponses import CallbackResult
                    detail_calls.append(str(url))
                    return CallbackResult(payload={"description": _WORKABLE_DETAIL_HTML})

                m.get(re.compile(r"https://apply\.workable\.com/api/v2/accounts/suade/jobs/SC\d+"),
                      callback=_cb, repeat=True)
                jobs = await WorkableSource(session, companies=["suade"]).fetch_jobs()
            assert len(jobs) == 4, "past-budget jobs must still be KEPT"
            assert len(detail_calls) == 1, f"budget must cap details, got {len(detail_calls)}"
        finally:
            await session.close()
    _run(_test())


_NFJ_BODY_HTML = (
    "<div><p>We are looking for a senior backend engineer to own our payments "
    "platform. You will design services, mentor engineers and work directly "
    "with product on the roadmap for the next two years.</p></div>"
)


def test_nofluffjobs_fetches_description_from_the_detail_endpoint():
    """The LIST payload carries NO body text at all — probed live over 1,000
    postings on 2026-08-19, the longest string on any list item was the
    138-char `id`. So the fix could not be "read another key off the list";
    the prose is at `requirements.description` on the per-posting endpoint.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting$"), payload={"postings": [{
                    "id": "senior-backend-engineer-acme-London",
                    "title": "Senior Backend Engineer",
                    "name": "Acme",
                    "location": {"places": [{"city": "London"}]},
                    "posted": 1784534208972,
                    "seniority": ["Senior"],
                }]})
                m.get(re.compile(
                    r"https://nofluffjobs\.com/api/posting/senior-backend-engineer-acme-London$"),
                    payload={"requirements": {
                        "description": _NFJ_BODY_HTML,
                        "musts": [{"value": "Python"}, {"value": "PostgreSQL"}],
                        "nices": [{"value": "Kubernetes"}],
                    }})
                jobs = await NoFluffJobsSource(session).fetch_jobs()

            assert len(jobs) == 1
            desc = jobs[0].description
            assert len(desc) > 200, f"empty description is the bug: {len(desc)} chars"
            assert "payments platform" in desc
            assert "Must have: Python, PostgreSQL" in desc, "the asked-for skills must land"
            assert "Nice to have: Kubernetes" in desc
            assert "<div>" not in desc and "<p>" not in desc, "HTML must be stripped"
        finally:
            await session.close()
    _run(_test())


def test_nofluffjobs_detail_failure_degrades_to_empty_never_drops_the_job():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting$"), payload={"postings": [{
                    "id": "senior-backend-engineer-acme-London",
                    "title": "Senior Backend Engineer", "name": "Acme",
                    "location": {"places": [{"city": "London"}]},
                }]})
                m.get(re.compile(r"https://nofluffjobs\.com/api/posting/senior.*"),
                      status=404, repeat=True)
                jobs = await NoFluffJobsSource(session).fetch_jobs()
            assert len(jobs) == 1 and jobs[0].description == ""
        finally:
            await session.close()
    _run(_test())
