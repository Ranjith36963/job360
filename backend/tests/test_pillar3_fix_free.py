"""Regression tests for the 6 free-API source fixes (himalayas, jobicy,
remoteok, remotive, devitjobs, landingjobs).

Every fixed field mapping was verified against a LIVE upstream response
before this batch was written. These tests pin the mapping so it cannot
regress silently. New file, separate from tests/test_sources.py (shared,
owned by other parallel workers).
"""
import asyncio
import re

import aiohttp
from aioresponses import aioresponses

from src.sources.apis_free.devitjobs import DevITJobsSource
from src.sources.apis_free.himalayas import HimalayasSource
from src.sources.apis_free.jobicy import JobicySource
from src.sources.apis_free.landingjobs import LandingJobsSource
from src.sources.apis_free.remoteok import RemoteOKSource
from src.sources.apis_free.remotive import RemotiveSource, _parse_remotive_salary


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Himalayas - apply_url / deadline / experience_level
# ---------------------------------------------------------------------------

def test_himalayas_apply_url_uses_application_link():
    """applicationLink (the real, 100%-fill key) must win over url/applicationUrl."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://himalayas\.app/jobs/api.*"), payload={"jobs": [{
                    "id": "1", "title": "Backend Engineer", "companyName": "Acme",
                    "locationRestrictions": ["UK"],
                    "excerpt": "Backend role",
                    "applicationLink": "https://himalayas.app/jobs/1/apply",
                    "url": "https://himalayas.app/jobs/1",
                    "minSalary": 60000, "maxSalary": 80000,
                    "expiryDate": 1798761600,
                    "seniority": ["Senior"],
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://himalayas.app/jobs/1/apply"
        finally:
            await session.close()
    _run(_test())


def test_himalayas_apply_url_falls_back_when_application_link_absent():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://himalayas\.app/jobs/api.*"), payload={"jobs": [{
                    "id": "2", "title": "Frontend Engineer", "companyName": "Acme",
                    "locationRestrictions": ["UK"],
                    "excerpt": "Frontend role",
                    "applicationUrl": "https://himalayas.app/jobs/2/apply-fallback",
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://himalayas.app/jobs/2/apply-fallback"
        finally:
            await session.close()
    _run(_test())


def test_himalayas_deadline_and_experience_level():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://himalayas\.app/jobs/api.*"), payload={"jobs": [{
                    "id": "3", "title": "Data Engineer", "companyName": "Acme",
                    "locationRestrictions": ["UK"],
                    "excerpt": "Data role",
                    "applicationLink": "https://himalayas.app/jobs/3/apply",
                    "expiryDate": 1798761600,
                    "seniority": ["Senior"],
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.deadline == "2027-01-01"
                assert job.deadline_source == "listing"
                assert job.experience_level == "Senior"
        finally:
            await session.close()
    _run(_test())


def test_himalayas_deadline_absent_when_expiry_missing():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://himalayas\.app/jobs/api.*"), payload={"jobs": [{
                    "id": "4", "title": "QA Engineer", "companyName": "Acme",
                    "locationRestrictions": ["UK"],
                    "excerpt": "QA role",
                    "applicationLink": "https://himalayas.app/jobs/4/apply",
                }]})
                source = HimalayasSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
                assert jobs[0].experience_level == ""
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# 2. Jobicy - salary keys / jobLevel / description preference
# ---------------------------------------------------------------------------

def test_jobicy_salary_uses_real_keys():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jobicy\.com/api/v2/remote-jobs.*"), payload={"jobs": [{
                    "id": 1, "jobTitle": "Data Scientist",
                    "companyName": "DataCo", "jobGeo": "UK",
                    "url": "https://jobicy.com/jobs/1",
                    "salaryMin": 50000, "salaryMax": 70000,
                    "annualSalaryMin": 999999, "annualSalaryMax": 999999,
                    "jobLevel": "Senior",
                    "jobExcerpt": "short teaser",
                    "jobDescription": "full prose description of the role",
                }]})
                source = JobicySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min == 50000
                assert job.salary_max == 70000
                assert job.experience_level == "Senior"
                assert job.description == "full prose description of the role"
        finally:
            await session.close()
    _run(_test())


def test_jobicy_description_falls_back_to_excerpt():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://jobicy\.com/api/v2/remote-jobs.*"), payload={"jobs": [{
                    "id": 2, "jobTitle": "Backend Dev",
                    "companyName": "DataCo", "jobGeo": "UK",
                    "url": "https://jobicy.com/jobs/2",
                    "jobExcerpt": "short teaser only",
                }]})
                source = JobicySource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].description == "short teaser only"
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# 3. RemoteOK - salary-0-sentinel + real location
# ---------------------------------------------------------------------------

def test_remoteok_zero_salary_becomes_none():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remoteok\.com/api.*"), payload=[
                    {"legal": "notice"},
                    {"id": "1", "position": "ML Engineer", "company": "RemoteCo",
                     "location": "", "description": "role",
                     "url": "https://remoteok.com/jobs/1",
                     "salary_min": 0, "salary_max": 0},
                ])
                source = RemoteOKSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


def test_remoteok_nonzero_salary_kept():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remoteok\.com/api.*"), payload=[
                    {"legal": "notice"},
                    {"id": "2", "position": "ML Engineer", "company": "RemoteCo",
                     "location": "", "description": "role",
                     "url": "https://remoteok.com/jobs/2",
                     "salary_min": 55000, "salary_max": 75000},
                ])
                source = RemoteOKSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 55000
                assert jobs[0].salary_max == 75000
        finally:
            await session.close()
    _run(_test())


def test_remoteok_uses_real_location():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remoteok\.com/api.*"), payload=[
                    {"legal": "notice"},
                    {"id": "3", "position": "Backend Engineer", "company": "RemoteCo",
                     "location": "Leeds, ", "description": "role",
                     "url": "https://remoteok.com/jobs/3"},
                ])
                source = RemoteOKSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].location == "Leeds"
        finally:
            await session.close()
    _run(_test())


def test_remoteok_falls_back_to_remote_when_location_empty():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remoteok\.com/api.*"), payload=[
                    {"legal": "notice"},
                    {"id": "4", "position": "Backend Engineer", "company": "RemoteCo",
                     "location": "", "description": "role",
                     "url": "https://remoteok.com/jobs/4"},
                ])
                source = RemoteOKSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].location == "Remote"
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# 4. Remotive - broadened salary parser (unit tests on the parser directly,
#    plus one end-to-end fetch_jobs test)
# ---------------------------------------------------------------------------

def test_parse_remotive_salary_classic_range():
    assert _parse_remotive_salary("$45,000 - $50,000") == (45000.0, 50000.0)


def test_parse_remotive_salary_k_suffix():
    assert _parse_remotive_salary("$20k -$35k") == (20000.0, 35000.0)


def test_parse_remotive_salary_decimal_comma_k_suffix():
    assert _parse_remotive_salary("$31,2k- $52k") == (31200.0, 52000.0)


def test_parse_remotive_salary_hourly_is_skipped():
    assert _parse_remotive_salary("$90 - $150 /hour") == (None, None)
    assert _parse_remotive_salary("$20 per hour") == (None, None)


def test_parse_remotive_salary_ote_prefix():
    assert _parse_remotive_salary("OTE $25k - $35k") == (25000.0, 35000.0)


def test_parse_remotive_salary_empty_or_none_is_silent():
    assert _parse_remotive_salary("") == (None, None)
    assert _parse_remotive_salary(None) == (None, None)
    assert _parse_remotive_salary("negotiable") == (None, None)


def test_remotive_fetch_jobs_wires_broadened_salary():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://remotive\.com/api/remote-jobs.*"), payload={"jobs": [{
                    "id": 1, "title": "AI Engineer",
                    "company_name": "RemotiveAI", "candidate_required_location": "Worldwide",
                    "description": "AI and ML role with Python",
                    "url": "https://remotive.com/jobs/1",
                    "publication_date": "2024-01-15",
                    "salary": "$20k -$35k",
                }]})
                source = RemotiveSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 20000.0
                assert jobs[0].salary_max == 35000.0
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# 5. DevITjobs - activeFrom date + contract-rate salary handling
# ---------------------------------------------------------------------------

def test_devitjobs_uses_active_from_for_date():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=[{
                    "name": "ML Engineer", "company": "Revolut", "actualCity": "London",
                    "annualSalaryFrom": 65000, "annualSalaryTo": 95000,
                    "hasVisaSponsorship": "No", "expLevel": "Senior",
                    "jobUrl": "https://devitjobs.uk/jobs/revolut-ml-engineer",
                    "publishedAt": "2020-01-01T00:00:00.000+00:00",
                    "activeFrom": "2026-08-05T14:27:12.425+00:00",
                }])
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.date_confidence == "high"
                assert job.posted_at is not None
                assert job.posted_at.startswith("2026-08-05")
                assert job.date_posted_raw == "2026-08-05T14:27:12.425+00:00"
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_contract_rate_used_when_annual_absent_and_type_is_year():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=[{
                    "name": "Contract Engineer", "company": "Acme", "actualCity": "London",
                    "hasVisaSponsorship": "No", "expLevel": "Senior",
                    "jobUrl": "https://devitjobs.uk/jobs/acme-contract-engineer",
                    "activeFrom": "2026-08-05T14:27:12.425+00:00",
                    "contractRateFrom": 80000,
                    "contractRateTo": 100000,
                    "contractRateType": "YEAR",
                }])
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min == 80000.0
                assert job.salary_max == 100000.0
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_day_rate_is_not_annualised():
    """A DAY contractRateType must never be turned into a fabricated annual
    salary - leave salary_min/max as None rather than guess a working-days
    multiplier."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=[{
                    "name": "Contract Engineer", "company": "Acme", "actualCity": "London",
                    "hasVisaSponsorship": "No", "expLevel": "Senior",
                    "jobUrl": "https://devitjobs.uk/jobs/acme-contract-engineer",
                    "activeFrom": "2026-08-05T14:27:12.425+00:00",
                    "contractRateFrom": 400,
                    "contractRateTo": 600,
                    "contractRateType": "DAY",
                }])
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min is None
                assert job.salary_max is None
        finally:
            await session.close()
    _run(_test())


def test_devitjobs_contract_rate_ignored_when_annual_present():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://devitjobs\.uk/api/jobsLight.*"), payload=[{
                    "name": "ML Engineer", "company": "Revolut", "actualCity": "London",
                    "annualSalaryFrom": 65000, "annualSalaryTo": 95000,
                    "hasVisaSponsorship": "No", "expLevel": "Senior",
                    "jobUrl": "https://devitjobs.uk/jobs/revolut-ml-engineer",
                    "activeFrom": "2026-08-05T14:27:12.425+00:00",
                    "contractRateFrom": 400,
                    "contractRateTo": 600,
                    "contractRateType": "YEAR",
                }])
                source = DevITJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.salary_min == 65000.0
                assert job.salary_max == 95000.0
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# 6. LandingJobs - role_description / expires_at deadline / gross salary
# ---------------------------------------------------------------------------

def test_landingjobs_uses_role_description_and_strips_html():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"), payload=[{
                    "title": "NLP Engineer",
                    "locations": [{"city": "London", "country_code": "GB"}],
                    "remote": False,
                    "tags": ["python", "nlp"],
                    "role_description": "<p>We build <b>NLP</b> systems.</p>",
                    "url": "https://landing.jobs/job/nlp-engineer",
                    "published_at": "2024-01-12",
                }])
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                # Tag-strip replaces each tag with a space (matches the
                # convention in ats/greenhouse.py and ats/smartrecruiters.py),
                # so inline tags like <b> leave double spaces behind.
                assert jobs[0].description == "We build  NLP  systems."
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_description_falls_back_to_tags():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"), payload=[{
                    "title": "NLP Engineer",
                    "locations": [{"city": "London", "country_code": "GB"}],
                    "remote": False,
                    "tags": ["python", "nlp"],
                    "url": "https://landing.jobs/job/nlp-engineer",
                    "published_at": "2024-01-12",
                }])
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].description == "python nlp"
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_deadline_from_expires_at():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"), payload=[{
                    "title": "NLP Engineer",
                    "locations": [{"city": "London", "country_code": "GB"}],
                    "remote": False,
                    "tags": ["python"],
                    "url": "https://landing.jobs/job/nlp-engineer",
                    "published_at": "2024-01-12",
                    "expires_at": "2026-09-02",
                }])
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline == "2026-09-02"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_gross_salary_wired():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"), payload=[{
                    "title": "NLP Engineer",
                    "locations": [{"city": "London", "country_code": "GB"}],
                    "remote": False,
                    "tags": ["python"],
                    "url": "https://landing.jobs/job/nlp-engineer",
                    "published_at": "2024-01-12",
                    "gross_salary_low": 55000,
                    "gross_salary_high": 75000,
                }])
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 55000.0
                assert jobs[0].salary_max == 75000.0
        finally:
            await session.close()
    _run(_test())


def test_landingjobs_no_deadline_when_expires_at_absent():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://landing\.jobs/api/v1/jobs\.json.*"), payload=[{
                    "title": "NLP Engineer",
                    "locations": [{"city": "London", "country_code": "GB"}],
                    "remote": False,
                    "tags": ["python"],
                    "url": "https://landing.jobs/job/nlp-engineer",
                    "published_at": "2024-01-12",
                }])
                source = LandingJobsSource(session)
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())
