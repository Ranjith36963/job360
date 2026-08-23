"""Pillar 3 fix batch (worker C) — successfactors / workday / personio.

Each fix here was verified against a LIVE upstream response before this
file was written (see the worker brief); these tests pin the fixes down
with mocked HTTP so they cannot silently regress.

successfactors.py: 0-job outage. The vendor sitemap namespace was hardcoded
to sitemaps.org, but QinetiQ actually serves the google.com namespace, and
BAE/Thales serve a sitemap INDEX (one level of indirection) that the old
code never followed. Both are covered below.

workday.py: the detail endpoint already fetched for the job description
also carries a real ISO startDate/endDate. Prefer it over the relative-text
guess for a "high" confidence posted_at, and wire endDate to the deadline
shelf.

personio.py: the old code hardcoded date_confidence="fabricated", which is
not a valid enum value (high|medium|low). The feed carries a real ISO
createdAt and a seniority field — both get wired up here.
"""
import asyncio
import re

import aiohttp
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.sources.ats.personio import PersonioSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.ats.workday import WorkdaySource


def _run(coro):
    return asyncio.run(coro)


def _sc_ai_defaults() -> SearchConfig:
    """Minimal SearchConfig so job_titles/search_queries aren't empty
    (core/keywords.py defaults are emptied — see test_sources.py's helper
    of the same name, duplicated here to keep this file self-contained)."""
    return SearchConfig(
        job_titles=["AI Engineer", "ML Engineer"],
        search_queries=["AI engineer"],
        relevance_keywords=["python", "machine learning", "ai", "ml"],
        primary_skills=["Python"],
        secondary_skills=["Docker"],
    )


# ---------------------------------------------------------------------------
# successfactors.py
# ---------------------------------------------------------------------------

GOOGLE_NS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.google.com/schemas/sitemap/0.9">
  <url><loc>https://careers.qinetiq.com/job/software-engineer-uk</loc></url>
  <url><loc>https://careers.qinetiq.com/job/data-scientist-london</loc></url>
</urlset>
"""

SITEMAPS_ORG_NS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/job/backend-engineer-london</loc></url>
</urlset>
"""

NO_NS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://careers.example.com/job/platform-engineer-remote</loc></url>
</urlset>
"""


def test_successfactors_google_namespace_extracts_urls():
    """The live QinetiQ bug: the code hardcoded the sitemaps.org namespace,
    but the vendor serves google.com/schemas/sitemap — 0/162 <loc> URLs
    matched. Namespace-agnostic local-name matching must extract them."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://careers.qinetiq.com/sitemap.xml", body=GOOGLE_NS_SITEMAP)
                source = SuccessFactorsSource(
                    session,
                    companies=[{"name": "QinetiQ", "sitemap_url": "https://careers.qinetiq.com/sitemap.xml"}],
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) == 2
                titles = {j.title for j in jobs}
                assert "Software Engineer Uk" in titles
                assert "Data Scientist London" in titles
                assert all(j.company == "QinetiQ" for j in jobs)
                assert all(j.source == "successfactors" for j in jobs)
        finally:
            await session.close()
    _run(_test())


def test_successfactors_sitemaps_org_namespace_still_works():
    """Regression guard: the original sitemaps.org namespace must keep
    working after the fix (not just the google.com one)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://careers.example.com/sitemap.xml", body=SITEMAPS_ORG_NS_SITEMAP)
                source = SuccessFactorsSource(
                    session,
                    companies=[{"name": "Example Co", "sitemap_url": "https://careers.example.com/sitemap.xml"}],
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].title == "Backend Engineer London"
        finally:
            await session.close()
    _run(_test())


def test_successfactors_no_namespace_still_works():
    """Regression guard: a sitemap with no namespace at all must also parse
    via the same local-name matching path."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://careers.example.com/sitemap.xml", body=NO_NS_SITEMAP)
                source = SuccessFactorsSource(
                    session,
                    companies=[{"name": "Example Co", "sitemap_url": "https://careers.example.com/sitemap.xml"}],
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].title == "Platform Engineer Remote"
        finally:
            await session.close()
    _run(_test())


def _child_sitemap(job_slug: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://jobs.baesystems.com/job/{job_slug}</loc></url>
</urlset>
"""


def test_successfactors_follows_sitemap_index():
    """BAE and Thales serve a sitemap INDEX (nested sitemapindex -> child
    sitemap entries), which the old code never followed, so they yielded 0
    jobs even after the namespace fix. One level of index-following must
    fetch each child sitemap and extract job URLs from it."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            index_xml = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://jobs.baesystems.com/sitemap-jobs-1.xml</loc></sitemap>
  <sitemap><loc>https://jobs.baesystems.com/sitemap-jobs-2.xml</loc></sitemap>
</sitemapindex>
"""
            with aioresponses() as m:
                m.get("https://jobs.baesystems.com/sitemap.xml", body=index_xml)
                m.get("https://jobs.baesystems.com/sitemap-jobs-1.xml", body=_child_sitemap("cyber-security-engineer"))
                m.get("https://jobs.baesystems.com/sitemap-jobs-2.xml", body=_child_sitemap("systems-engineer-london"))
                source = SuccessFactorsSource(
                    session,
                    companies=[{"name": "BAE Systems", "sitemap_url": "https://jobs.baesystems.com/sitemap.xml"}],
                )
                jobs = await source.fetch_jobs()
                assert len(jobs) == 2
                titles = {j.title for j in jobs}
                assert "Cyber Security Engineer" in titles
                assert "Systems Engineer London" in titles
                assert all(j.company == "BAE Systems" for j in jobs)
        finally:
            await session.close()
    _run(_test())


def test_successfactors_sitemap_index_is_bounded():
    """Index-following must be bounded (e.g. max 5 child sitemaps) so a
    vendor whose index lists many shards cannot trigger runaway fetching."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            n_children = 7
            sitemap_entries = "\n".join(
                f"  <sitemap><loc>https://jobs.baesystems.com/sitemap-jobs-{i}.xml</loc></sitemap>"
                for i in range(1, n_children + 1)
            )
            index_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{sitemap_entries}
</sitemapindex>
"""
            with aioresponses() as m:
                m.get("https://jobs.baesystems.com/sitemap.xml", body=index_xml)
                for i in range(1, n_children + 1):
                    m.get(
                        f"https://jobs.baesystems.com/sitemap-jobs-{i}.xml",
                        body=_child_sitemap(f"engineer-{i}"),
                    )
                source = SuccessFactorsSource(
                    session,
                    companies=[{"name": "BAE Systems", "sitemap_url": "https://jobs.baesystems.com/sitemap.xml"}],
                )
                jobs = await source.fetch_jobs()
                # Only the first 5 child sitemaps should have been followed.
                assert len(jobs) == 5
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# workday.py
# ---------------------------------------------------------------------------

def test_workday_prefers_detail_startdate_over_relative_text():
    """The detail endpoint the code already calls for the description also
    returns a real ISO startDate (verified live 2/2). It must win over the
    cheap list call's relative-text guess ("Posted 3 Days Ago") and land as
    a "high" confidence posted_at via normalize_posted_at()."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/ext/jobs"),
                    payload={"jobPostings": [{
                        "title": "AI Engineer",
                        "locationsText": "London, United Kingdom",
                        "externalPath": "/job/London/AI-Engineer_R123",
                        "postedOn": "Posted 3 Days Ago",
                    }]},
                    repeat=True,
                )
                m.get(
                    "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/London/AI-Engineer_R123",
                    payload={"jobPostingInfo": {
                        "jobDescription": "<p>Build AI systems.</p>",
                        "startDate": "2026-07-20T00:00:00.000Z",
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
                job = jobs[0]
                assert job.date_confidence == "high"
                assert job.posted_at == "2026-07-20T00:00:00.000Z"
                # Relative text is still preserved as the audit-only raw value.
                assert job.date_posted_raw == "Posted 3 Days Ago"
        finally:
            await session.close()
    _run(_test())


def test_workday_wires_enddate_to_deadline_shelf():
    """The detail endpoint's endDate (verified live 1/2, a closing date)
    must land on Job.deadline with deadline_source="listing"."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/ext/jobs"),
                    payload={"jobPostings": [{
                        "title": "AI Engineer",
                        "locationsText": "London, United Kingdom",
                        "externalPath": "/job/London/AI-Engineer_R123",
                        "postedOn": "Posted Today",
                    }]},
                    repeat=True,
                )
                m.get(
                    "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/London/AI-Engineer_R123",
                    payload={"jobPostingInfo": {
                        "jobDescription": "Build AI systems.",
                        "startDate": "2026-07-20T00:00:00.000Z",
                        "endDate": "2026-08-31T00:00:00.000Z",
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
                job = jobs[0]
                assert job.deadline == "2026-08-31"
                assert job.deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_workday_falls_back_to_relative_text_when_no_detail_dates():
    """When the detail response has no startDate (or no detail was fetched
    at all), the relative-text parse must remain the fallback — medium
    confidence, no deadline fabricated."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.post(
                    re.compile(r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/ext/jobs"),
                    payload={"jobPostings": [{
                        "title": "AI Engineer",
                        "locationsText": "London, United Kingdom",
                        "externalPath": "/job/London/AI-Engineer_R123",
                        "postedOn": "Posted 3 Days Ago",
                    }]},
                    repeat=True,
                )
                m.get(
                    "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/London/AI-Engineer_R123",
                    payload={"jobPostingInfo": {
                        "jobDescription": "Build AI systems.",
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
                job = jobs[0]
                assert job.date_confidence == "medium"
                assert job.deadline is None
                assert job.deadline_source is None
        finally:
            await session.close()
    _run(_test())


# ---------------------------------------------------------------------------
# personio.py
# ---------------------------------------------------------------------------

PERSONIO_XML_WITH_DATE_AND_SENIORITY = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>12345</id>
    <name>Senior Software Engineer</name>
    <office>London</office>
    <seniority>Senior</seniority>
    <createdAt>2026-06-15T09:00:00Z</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>Description</name>
        <value>Build distributed systems.</value>
      </jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>
"""

PERSONIO_XML_NO_DATE = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>67890</id>
    <name>Backend Engineer</name>
    <office>Remote</office>
    <seniority>Mid</seniority>
    <jobDescriptions>
      <jobDescription>
        <name>Description</name>
        <value>Build APIs.</value>
      </jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>
"""

def test_personio_wires_created_at_to_posted_at():
    """The old code hardcoded date_confidence="fabricated" - not a valid
    enum value (schema is high|medium|low). The live XML carries a real
    ISO createdAt; it must land as a high-confidence posted_at via
    normalize_posted_at()."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://testco\.jobs\.personio\.de/xml.*"),
                    body=PERSONIO_XML_WITH_DATE_AND_SENIORITY,
                )
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.date_confidence != "fabricated"
                assert job.date_confidence == "high"
                assert job.posted_at == "2026-06-15T09:00:00Z"
                assert job.date_posted_raw == "2026-06-15T09:00:00Z"
        finally:
            await session.close()
    _run(_test())


def test_personio_wires_seniority_to_experience_level():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://testco\.jobs\.personio\.de/xml.*"),
                    body=PERSONIO_XML_WITH_DATE_AND_SENIORITY,
                )
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                assert jobs[0].experience_level == "Senior"
        finally:
            await session.close()
    _run(_test())


def test_personio_missing_created_at_degrades_gracefully():
    """No createdAt in the feed must never raise and must never produce the
    invalid "fabricated" confidence - it degrades to no posted_at / low
    confidence like any other source with a missing date field."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://testco\.jobs\.personio\.de/xml.*"),
                    body=PERSONIO_XML_NO_DATE,
                )
                source = PersonioSource(session, companies=["testco"])
                jobs = await source.fetch_jobs()
                assert len(jobs) == 1
                job = jobs[0]
                assert job.date_confidence != "fabricated"
                assert job.posted_at is None
                assert job.date_confidence == "low"
                assert job.experience_level == "Mid"
        finally:
            await session.close()
    _run(_test())


def test_workday_fetch_job_description_backcompat_for_description_backfill():
    """Regression guard: description_backfill.py reconstructs a
    WorkdaySource and calls source._fetch_job_description(...) by name on
    a stored apply_url (the enrichment_sweep cron BACKFILL phase), expecting
    a plain str back. The date-upgrade fix renamed the real fetch method to
    _fetch_job_detail() (it now also returns startDate/endDate), so
    _fetch_job_description must keep existing as a thin str-returning
    wrapper for this external caller."""
    from src.core.companies import WORKDAY_COMPANIES
    from src.services.description_backfill import _refetch_workday

    assert WORKDAY_COMPANIES, "expected at least one configured Workday tenant"

    async def _test():
        session = aiohttp.ClientSession()
        try:
            entry = WORKDAY_COMPANIES[0]
            tenant = entry["tenant"]
            wd = entry["wd"]
            site = entry["site"]
            # Mirrors how workday.py builds apply_url at ingestion
            # (f"{base_url}/en-US{ext_path}") — _refetch_workday reverses
            # this exact encoding.
            apply_url = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/job/London/X_R1"

            with aioresponses() as m:
                m.get(
                    re.compile(r".*myworkdayjobs\.com/wday/cxs/.*"),
                    payload={
                        "jobPostingInfo": {
                            "jobDescription": "<p>Build things in London.</p>",
                            "startDate": "2026-08-07",
                            "endDate": "2026-09-30",
                        }
                    },
                )
                # This is the EXACT call description_backfill.py:162 makes
                # against a stored apply_url, in production.
                result = await _refetch_workday(apply_url, session)

            # The external caller expects a plain string, never a dict.
            assert isinstance(result, str)
            assert "London" in result
        finally:
            await session.close()

    _run(_test())
