"""Regression tests for the Pillar 3 source-shelf audit fixes (2026-08-08).

Six bugs, each verified against a real live response before the fix landed
(see the docstrings/comments in the source files for the live evidence).
This is a NEW file — do not fold these into tests/test_sources.py, which
other parallel workers are also editing.

Covers:
  1. nhs_jobs.py       — total outage (<vacancyDetails> not <vacancy>) + 4
                          wrong tag names + unread postDate/deadline.
  2. weworkremotely.py — <expires_at> -> deadline.
  3. realworkfromanywhere.py — <author> preferred over brittle title split.
  4. uni_jobs.py       — <applyOnlineUrl> preferred over the listing <link>.
  5. teaching_vacancies.py — validThrough -> deadline, + bounded pagination.
  6. nofluffjobs.py    — seniority[] -> experience_level, + a currency guard
                          so PLN salaries are never stored as if they were GBP.
"""
import asyncio
import re

import aiohttp
from aioresponses import aioresponses

from src.services.profile.models import SearchConfig
from src.sources.apis_free.teaching_vacancies import TeachingVacanciesSource
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
from src.sources.other.nofluffjobs import NoFluffJobsSource


def _run(coro):
    return asyncio.run(coro)


def _sc(queries: list[str]) -> SearchConfig:
    return SearchConfig(search_queries=queries)


# =============================================================================
# 1. NHS Jobs — total outage + 4 wrong tags
# =============================================================================

# Shape copied from the LIVE https://www.jobs.nhs.uk/api/v1/search_xml
# response (verified 2026-08-08): root <nhsJobs>, records wrapped in
# <vacancyDetails> (not <vacancy>), closeDate/url/postDate (not
# closingDate/advertUrl), and a nested <locations><location> (not flat).
NHS_JOBS_LIVE_SHAPE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<nhsJobs>
  <vacancyDetails>
    <id>5086796</id>
    <title>Specialist Clinical Pharmacist Cancer and Aseptics</title>
    <employer>Buckinghamshire Healthcare NHS Trust</employer>
    <salary>£35,000 - £40,000</salary>
    <closeDate>2027-01-07</closeDate>
    <postDate>2026-01-07T16:22:37.450927</postDate>
    <url>https://beta.jobs.nhs.uk/candidate/jobadvert/M0042-86784</url>
    <locations>
      <location>Aylesbury, HP21 8AL</location>
      <location>High Wycombe, HP11 2TT</location>
    </locations>
  </vacancyDetails>
</nhsJobs>"""


def test_nhs_jobs_parses_live_vacancydetails_shape():
    """The exact bug: iterating <vacancy> found 0 of the live <vacancyDetails>
    records, so this source returned ZERO jobs. Also proves closeDate now
    fills the deadline shelf, url fills apply_url, postDate fills posted_at,
    and the nested locations are read."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.jobs\.nhs\.uk/api/v1/search_xml.*"),
                      body=NHS_JOBS_LIVE_SHAPE_XML, content_type="application/xml", repeat=True)
                source = NHSJobsSource(session, search_config=_sc(["pharmacist"]))
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1, "vacancyDetails records must be found — this was the ZERO-jobs bug"
                job = jobs[0]
                assert job.title == "Specialist Clinical Pharmacist Cancer and Aseptics"
                assert job.company == "Buckinghamshire Healthcare NHS Trust"

                # url -> apply_url (advertUrl fallback not used here)
                assert job.apply_url == "https://beta.jobs.nhs.uk/candidate/jobadvert/M0042-86784"

                # nested <locations><location> joined
                assert "Aylesbury" in job.location
                assert "High Wycombe" in job.location

                # closeDate -> deadline shelf, NOT date_posted_raw
                assert job.deadline == "2027-01-07"
                assert job.deadline_source == "listing"

                # postDate -> posted_at via normalize_posted_at, real confidence
                assert job.posted_at is not None
                assert job.posted_at.startswith("2026-01-07")
                assert job.date_confidence == "high"
                assert job.date_posted_raw == "2026-01-07T16:22:37.450927"

                # existing salary parsing untouched
                assert job.salary_min == 35000
                assert job.salary_max == 40000
        finally:
            await session.close()
    _run(_test())


NHS_JOBS_OLD_SHAPE_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
</vacancies>"""


def test_nhs_jobs_falls_back_to_old_vacancy_tag():
    """The <vacancy>/advertUrl fallback must keep working if upstream ever
    reverts — this is the safety net the fix explicitly kept."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r"https://www\.jobs\.nhs\.uk/api/v1/search_xml.*"),
                      body=NHS_JOBS_OLD_SHAPE_XML, content_type="application/xml", repeat=True)
                source = NHSJobsSource(session, search_config=_sc(["data scientist"]))
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                job = jobs[0]
                assert job.company == "NHS Digital"
                assert job.apply_url == "https://www.jobs.nhs.uk/candidate/jobadvert/12345"
                assert job.location == "Leeds"
                # Old shape has no postDate/closeDate at all — must never fabricate.
                assert job.posted_at is None
                assert job.date_confidence == "low"
                assert job.deadline is None
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# 2. WeWorkRemotely — expires_at -> deadline
# =============================================================================

WWR_XML_WITH_EXPIRES = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>Lattice: Engineering Manager, AI</title>
  <link>https://weworkremotely.com/remote-jobs/lattice-em-ai</link>
  <description>Remote AI engineering role. UK/EMEA timezone preferred.</description>
  <pubDate>Sat, 08 Aug 2026 07:30:54 +0000</pubDate>
  <region>Anywhere in the World</region>
  <expires_at>Mon, 07 Sep 2026 07:30:54 +0000</expires_at>
</item>
</channel>
</rss>"""


def test_weworkremotely_expires_at_fills_deadline():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://weworkremotely.com/remote-jobs.rss",
                      body=WWR_XML_WITH_EXPIRES, content_type="application/xml")
                source = WeWorkRemotelySource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].deadline == "2026-09-07"
                assert jobs[0].deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


WWR_XML_UNPARSEABLE_EXPIRES = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>DataCo: Senior AI Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/dataco</link>
  <description>UK/EMEA timezone.</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
  <expires_at>not-a-real-date</expires_at>
</item>
</channel>
</rss>"""


def test_weworkremotely_bad_expires_at_never_fabricates_deadline():
    """deadline must NEVER be guessed — an unparseable expires_at must yield
    None, not today's date (the trap _parse_rss_date's fallback would set)."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://weworkremotely.com/remote-jobs.rss",
                      body=WWR_XML_UNPARSEABLE_EXPIRES, content_type="application/xml")
                source = WeWorkRemotelySource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# 3. RealWorkFromAnywhere — <author> preferred over title split
# =============================================================================

RWFA_XML_WITH_AUTHOR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title><![CDATA[Data Scientist - Partner Firm XYZ]]></title>
  <description><![CDATA[Remote data science role, Europe/UK timezone.]]></description>
  <link>https://www.realworkfromanywhere.com/job/ds-globalai</link>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
  <author><![CDATA[GlobalAI Ltd]]></author>
</item>
</channel>
</rss>"""


def test_realworkfromanywhere_prefers_author_over_title_split():
    """title-splitting on " - " would give "Partner Firm XYZ" — a different,
    wrong value. <author> is the ground truth and must win."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://www.realworkfromanywhere.com/rss.xml",
                      body=RWFA_XML_WITH_AUTHOR, content_type="application/xml")
                source = RealWorkFromAnywhereSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].company == "GlobalAI Ltd"
        finally:
            await session.close()
    _run(_test())


RWFA_XML_NO_AUTHOR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>Data Scientist at GlobalAI</title>
  <description>Remote, Europe/UK timezone.</description>
  <link>https://www.realworkfromanywhere.com/job/ds-globalai-2</link>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_realworkfromanywhere_falls_back_to_title_split_without_author():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://www.realworkfromanywhere.com/rss.xml",
                      body=RWFA_XML_NO_AUTHOR, content_type="application/xml")
                source = RealWorkFromAnywhereSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].company == "GlobalAI"
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# 4. University Jobs — applyOnlineUrl preferred over <link>
# =============================================================================

UNI_JOBS_XML_WITH_APPLY_URL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>Research Associate x 2 (Fixed Term)</title>
  <link>https://www.jobs.cam.ac.uk/job/56185/</link>
  <applyOnlineUrl>https://hrsystems.admin.cam.ac.uk/recruit-ui/apply/SM50294</applyOnlineUrl>
  <description>Research role in the MRC Mitochondrial Biology Unit</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_uni_jobs_prefers_apply_online_url():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r".*jobs\.cam\.ac\.uk.*"),
                      body=UNI_JOBS_XML_WITH_APPLY_URL, content_type="application/xml")
                source = UniJobsSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://hrsystems.admin.cam.ac.uk/recruit-ui/apply/SM50294"
        finally:
            await session.close()
    _run(_test())


UNI_JOBS_XML_NO_APPLY_URL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>Research Associate in Machine Learning</title>
  <link>https://www.jobs.cam.ac.uk/job/12345/</link>
  <description>ML research role</description>
  <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
</item>
</channel>
</rss>"""


def test_uni_jobs_falls_back_to_link_without_apply_online_url():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(re.compile(r".*jobs\.cam\.ac\.uk.*"),
                      body=UNI_JOBS_XML_NO_APPLY_URL, content_type="application/xml")
                source = UniJobsSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://www.jobs.cam.ac.uk/job/12345/"
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# 5. Teaching Vacancies — validThrough -> deadline, + bounded pagination
# =============================================================================

TV_PAGE1 = {"data": [{
    "title": "Secondary Mathematics Teacher",
    "hiringOrganization": {"name": "Camden School for Girls"},
    "jobLocation": {"address": {"addressLocality": "London"}},
    "datePosted": "2026-08-01",
    "url": "https://teaching-vacancies.service.gov.uk/jobs/abc",
    "description": "Full-time mathematics teacher position",
    "validThrough": "2026-09-14T12:00:00+01:00",
}]}

TV_PAGE2 = {"data": [{
    "title": "Primary Teacher",
    "hiringOrganization": {"name": "St. Paul's Primary"},
    "jobLocation": {"address": {"addressLocality": "Manchester"}},
    "datePosted": "2026-08-02",
    "url": "https://teaching-vacancies.service.gov.uk/jobs/def",
    "description": "KS2 teacher",
    "validThrough": "2026-10-01T12:00:00+01:00",
}]}

TV_PAGE3_EMPTY = {"data": []}


def test_teaching_vacancies_paginates_and_fills_deadline():
    """(a) validThrough -> deadline shelf. (b) the API reports 3,893 jobs
    across ~39 pages but the old code sent no page param and only ever saw
    page 1 — this proves page 2 is actually fetched, and that the loop stops
    (bounded) once a page comes back empty rather than always hitting the
    MAX_PAGES=5 cap."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, payload=TV_PAGE1)
                m.get(re.compile(
                    r"https://teaching-vacancies\.service\.gov\.uk/api/v1/jobs\.json\?page=2.*"
                ), payload=TV_PAGE2)
                m.get(re.compile(
                    r"https://teaching-vacancies\.service\.gov\.uk/api/v1/jobs\.json\?page=3.*"
                ), payload=TV_PAGE3_EMPTY)

                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 2, "must aggregate page 1 AND page 2"
                by_title = {j.title: j for j in jobs}

                first = by_title["Secondary Mathematics Teacher"]
                assert first.deadline == "2026-09-14"
                assert first.deadline_source == "listing"

                second = by_title["Primary Teacher"]
                assert second.deadline == "2026-10-01"
                assert second.deadline_source == "listing"
        finally:
            await session.close()
    _run(_test())


def test_teaching_vacancies_missing_valid_through_leaves_deadline_none():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = {"data": [{
                "title": "Cleaner",
                "hiringOrganization": {"name": "Some School"},
                "jobLocation": {"address": {"addressLocality": "London"}},
                "datePosted": "2026-08-01",
                "url": "https://teaching-vacancies.service.gov.uk/jobs/xyz",
            }]}
            with aioresponses() as m:
                m.get(TeachingVacanciesSource.API_URL, payload=payload)
                m.get(re.compile(
                    r"https://teaching-vacancies\.service\.gov\.uk/api/v1/jobs\.json\?page=2.*"
                ), payload={"data": []})

                source = TeachingVacanciesSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                assert jobs[0].deadline is None
                assert jobs[0].deadline_source is None
        finally:
            await session.close()
    _run(_test())


# =============================================================================
# 6. NoFluffJobs — seniority[] -> experience_level, + currency guard
# =============================================================================

def test_nofluffjobs_experience_level_from_seniority():
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [{
                "id": "sre-role", "title": "Site Reliability Engineer",
                "company": "NoFluffCo",
                "location": {"places": [{"city": "London"}]},
                "remote": False,
                "posted": "2026-08-01",
                "seniority": ["Senior", "Expert"],
            }]
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/.*"),
                      payload=payload, repeat=True)
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 1
                # First element of the list wins.
                assert jobs[0].experience_level == "Senior"
        finally:
            await session.close()
    _run(_test())


def test_nofluffjobs_salary_currency_guard():
    """CURRENCY CORRECTNESS RISK (live: 19,229 of 20,631 postings are PLN).
    Only GBP, or an absent currency with a plausible GBP-sized figure, may
    populate salary_min/salary_max — anything else must come back None
    rather than silently mislabel a PLN figure as GBP."""
    async def _test():
        session = aiohttp.ClientSession()
        try:
            payload = [
                {
                    "id": "pln-job", "title": "PLN Priced Job", "company": "PolskaCo",
                    "location": {"places": [{"city": "London"}]}, "remote": False,
                    "posted": "2026-08-01",
                    "salary": {"from": 15000, "to": 20000, "currency": "PLN"},
                },
                {
                    "id": "gbp-job", "title": "GBP Priced Job", "company": "BritCo",
                    "location": {"places": [{"city": "Manchester"}]}, "remote": False,
                    "posted": "2026-08-01",
                    "salary": {"from": 60000, "to": 80000, "currency": "GBP"},
                },
                {
                    "id": "no-currency-plausible", "title": "No Currency Plausible Job",
                    "company": "AmbiguousCo",
                    "location": {"places": [{"city": "Leeds"}]}, "remote": False,
                    "posted": "2026-08-01",
                    "salary": {"from": 45000, "to": 55000},
                },
                {
                    "id": "no-currency-implausible", "title": "No Currency Implausible Job",
                    "company": "HourlyCo",
                    "location": {"places": [{"city": "Bristol"}]}, "remote": False,
                    "posted": "2026-08-01",
                    "salary": {"from": 15, "to": 20},
                },
            ]
            with aioresponses() as m:
                m.get(re.compile(r"https://nofluffjobs\.com/api/.*"),
                      payload=payload, repeat=True)
                source = NoFluffJobsSource(session)
                jobs = await source.fetch_jobs()

                assert len(jobs) == 4
                by_title = {j.title: j for j in jobs}

                pln = by_title["PLN Priced Job"]
                assert pln.salary_min is None and pln.salary_max is None, \
                    "PLN must never be stored as if it were GBP"

                gbp = by_title["GBP Priced Job"]
                assert gbp.salary_min == 60000.0
                assert gbp.salary_max == 80000.0

                plausible = by_title["No Currency Plausible Job"]
                assert plausible.salary_min == 45000.0
                assert plausible.salary_max == 55000.0

                implausible = by_title["No Currency Implausible Job"]
                assert implausible.salary_min is None and implausible.salary_max is None
        finally:
            await session.close()
    _run(_test())
