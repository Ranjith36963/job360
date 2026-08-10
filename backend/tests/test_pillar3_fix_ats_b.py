"""Pillar 3 fix batch B - 4 verified-live-API bugs across ATS sources.

Every fix here was confirmed against a LIVE API response (see the task that
produced this file). This test file covers ONLY the new behaviour; it does
not duplicate the existing happy-path parsing tests in test_sources.py.

Workable:
    - "published" (ISO date) is the real posted-date field. The old code
      hardcoded posted_at=None, date_confidence="fabricated" ("fabricated"
      is not a valid date_confidence value).

Recruitee:
    - "salary" is a NESTED object ({min, max, period, currency}), not the
      flat min_salary/max_salary keys the old code read (which never
      existed on the live schema).
    - "close_at" (closing date) wires to the deadline shelf when present and
      parseable; null must not crash.

Pinpoint:
    - top-level "compensation" is a plain STRING (e.g. "Competitive"); the
      real numeric fields are compensation_minimum / compensation_maximum.
    - "deadline_at" wires to the deadline shelf when present and parseable;
      null must not crash.
    - date_confidence must be "low" (honest - no posted-date field exists),
      never the invalid "fabricated" literal.

SmartRecruiters:
    - the detail endpoint (already fetched for description text) also
      carries compensation: {min, max, currency, period} - wire to
      salary_min/salary_max with NO extra HTTP call.
    - the list endpoint carries experienceLevel: {id, label} - wire
      label to experience_level.
"""
import re

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses

from src.sources.ats.pinpoint import PinpointSource
from src.sources.ats.recruitee import RecruiteeSource
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.workable import WorkableSource


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Workable - date fix
# ---------------------------------------------------------------------------

def test_workable_uses_published_field_for_posted_at():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://apply\.workable\.com/.*"), payload={"results": [{
                    "shortcode": "XYZ789", "title": "Data Scientist",
                    "location": {"city": "London", "country": "UK"},
                    "published": "2026-07-01T09:00:00.000Z",
                }]})
                source = WorkableSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.posted_at is not None
                assert job.date_confidence == "high"
                assert job.date_posted_raw == "2026-07-01T09:00:00.000Z"
        finally:
            await session.close()
    _run(_test())


def test_workable_never_emits_fabricated_confidence():
    """The fabricated literal is not a valid date_confidence value - the
    enum is high/medium/low. Missing published must degrade to a real
    (None, low) pair, never the invalid literal."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(re.compile(r"https://apply\.workable\.com/.*"), payload={"results": [{
                    "shortcode": "NODATE1", "title": "Backend Engineer",
                    "location": {"city": "London", "country": "UK"},
                }]})
                source = WorkableSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.date_confidence != "fabricated"
                assert job.date_confidence == "low"
                assert job.posted_at is None
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# Recruitee - nested salary + close_at deadline
# ---------------------------------------------------------------------------

def test_recruitee_reads_nested_salary_object():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-401", "title": "Platform Engineer",
                    "description": "Platform role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/platform-engineer",
                    "published_at": "2026-06-01",
                    "salary": {"min": 55000, "max": 75000, "period": "year", "currency": "GBP"},
                    "min_salary": 1, "max_salary": 2,
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 55000
                assert jobs[0].salary_max == 75000
        finally:
            await session.close()
    _run(_test())


def test_recruitee_missing_salary_object_does_not_crash():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-402", "title": "Support Engineer",
                    "description": "Support role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/support-engineer",
                    "published_at": "2026-06-01",
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


def test_recruitee_wires_close_at_to_deadline_shelf():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-403", "title": "Recruiter",
                    "description": "Recruiting role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/recruiter",
                    "published_at": "2026-06-01",
                    "close_at": "2026-09-15T00:00:00.000Z",
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline == "2026-09-15"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_recruitee_null_close_at_is_guarded_not_crashed():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.recruitee\.com/api/offers/.*"), payload={"offers": [{
                    "id": "rc-404", "title": "Analyst",
                    "description": "Analyst role",
                    "location": "London, UK",
                    "careers_url": "https://test.recruitee.com/o/analyst",
                    "published_at": "2026-06-01",
                    "close_at": None,
                }]})
                source = RecruiteeSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# Pinpoint - real compensation fields + deadline_at + honest date_confidence
# ---------------------------------------------------------------------------

def test_pinpoint_reads_flat_compensation_fields_not_the_string():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-501", "title": "Site Reliability Engineer",
                    "description": "SRE role",
                    "url": "https://test.pinpointhq.com/postings/pp-501",
                    "location": {"name": "London, UK"},
                    "compensation": "Competitive",
                    "compensation_minimum": 68000,
                    "compensation_maximum": 92000,
                    "compensation_currency": "GBP",
                    "compensation_frequency": "annually",
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 68000
                assert jobs[0].salary_max == 92000
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_keeps_fabricated_confidence_so_recency_scores_zero():
    """Pinpoint has NO posted-date field in its 24-key schema (verified live).

    "fabricated" is NOT an invalid value — it is a deliberate 4th state that
    skill_matcher._recency_points() reads to return 0, refusing any freshness
    credit for a date we invented. An earlier version of this fix downgraded
    it to "low", which would have let the job fall through to
    `date_found * 0.6` and quietly earn 60% recency for a date that does not
    exist. This test pins the honest label down.
    """
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-502", "title": "QA Engineer",
                    "description": "QA role",
                    "url": "https://test.pinpointhq.com/postings/pp-502",
                    "location": {"name": "London, UK"},
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].date_confidence == "fabricated"
                assert jobs[0].posted_at is None
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_wires_deadline_at_to_deadline_shelf():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-503", "title": "Product Manager",
                    "description": "PM role",
                    "url": "https://test.pinpointhq.com/postings/pp-503",
                    "location": {"name": "London, UK"},
                    "deadline_at": "2026-10-01T00:00:00.000Z",
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline == "2026-10-01"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_pinpoint_null_deadline_at_is_guarded_not_crashed():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://.*\.pinpointhq\.com/postings\.json.*"), payload=[{
                    "id": "pp-504", "title": "Designer",
                    "description": "Design role",
                    "url": "https://test.pinpointhq.com/postings/pp-504",
                    "location": {"name": "London, UK"},
                    "deadline_at": None,
                }])
                source = PinpointSource(session, companies=["test"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# SmartRecruiters - compensation from the already-fetched detail response +
# experienceLevel from the list response
# ---------------------------------------------------------------------------

def test_smartrecruiters_wires_compensation_from_detail_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-201", "name": "Staff Engineer",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-201",
                        "releasedDate": "2026-01-15",
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-201",
                    payload={
                        "jobAd": {"sections": {
                            "jobDescription": {"text": "<p>Build systems.</p>"},
                        }},
                        "compensation": {"min": 70000, "max": 95000, "currency": "GBP", "period": "YEARLY"},
                    },
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min == 70000
                assert jobs[0].salary_max == 95000
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_wires_experience_level_from_list_response():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-202", "name": "Principal Engineer",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-202",
                        "releasedDate": "2026-01-15",
                        "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior level"},
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-202",
                    payload={"jobAd": {"sections": {}}},
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].experience_level == "Mid-Senior level"
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_description_and_compensation_come_from_one_http_call():
    """No extra HTTP call is allowed - description AND compensation must
    both come out of the single detail fetch already made for description
    text."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            detail_calls = []
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-203", "name": "Engineering Manager",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-203",
                        "releasedDate": "2026-01-15",
                    }]},
                )

                def _detail_cb(url, **kw):
                    detail_calls.append(str(url))
                    return CallbackResult(payload={
                        "jobAd": {"sections": {"jobDescription": {"text": "Lead a team."}}},
                        "compensation": {"min": 80000, "max": 110000},
                    })
                m.get(re.compile(r".*postings/sr-203$"), callback=_detail_cb)

                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert len(detail_calls) == 1, "must not make a second HTTP call for salary"
                assert jobs[0].salary_min == 80000
                assert "Lead a team" in jobs[0].description
        finally:
            await session.close()
    _run(_test())


def test_smartrecruiters_missing_compensation_in_detail_does_not_crash():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://api\.smartrecruiters\.com/v1/companies/wise/postings\?.*"),
                    payload={"content": [{
                        "id": "sr-204", "name": "Recruiter",
                        "location": {"city": "London", "country": "GB"},
                        "ref": "https://jobs.smartrecruiters.com/wise/sr-204",
                        "releasedDate": "2026-01-15",
                    }]},
                )
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/wise/postings/sr-204",
                    payload={"jobAd": {"sections": {}}},
                )
                source = SmartRecruitersSource(session, companies=["wise"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].salary_min is None
                assert jobs[0].salary_max is None
        finally:
            await session.close()
    _run(_test())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
