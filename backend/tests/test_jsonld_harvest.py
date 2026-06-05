"""Tests for the generic Schema.org JSON-LD JobPosting harvester.

copy_roadmap.md Tier-2 #3 (core). Extracts JobPosting structured data embedded
in arbitrary career-page HTML (``<script type="application/ld+json">``) and maps
it to Job objects. Pure / offline — no HTTP, no live network (CLAUDE.md rule #4).

This is the unblocked *extraction* core; wiring it into SOURCE_REGISTRY as a
BaseJobSource is deferred (changes the source count → rules #8/#13).
"""
import asyncio

import aiohttp
from aioresponses import aioresponses

from src.models import Job
from src.services.jsonld_harvest import (
    extract_jsonld_blocks,
    harvest_jobs,
    harvest_url,
    parse_jobposting,
)

_DATE = "2026-06-05T00:00:00+00:00"


def _run(coro):
    return asyncio.run(coro)


def _wrap(json_text: str) -> str:
    return f'<html><head><script type="application/ld+json">{json_text}</script></head><body>x</body></html>'


# ----------------------------- extraction ----------------------------------

def test_extract_single_block():
    html = _wrap('{"@type": "JobPosting", "title": "Engineer"}')
    blocks = extract_jsonld_blocks(html)
    assert len(blocks) == 1
    assert blocks[0]["title"] == "Engineer"


def test_extract_multiple_blocks():
    html = (
        _wrap('{"@type": "Organization", "name": "Acme"}')
        + _wrap('{"@type": "JobPosting", "title": "Dev"}')
    )
    blocks = extract_jsonld_blocks(html)
    assert len(blocks) == 2


def test_extract_flattens_graph_and_top_level_list():
    graph = _wrap('{"@graph": [{"@type": "JobPosting", "title": "A"}, {"@type": "WebPage"}]}')
    listed = _wrap('[{"@type": "JobPosting", "title": "B"}, {"@type": "JobPosting", "title": "C"}]')
    blocks = extract_jsonld_blocks(graph + listed)
    titles = [b.get("title") for b in blocks if b.get("@type") == "JobPosting"]
    assert titles == ["A", "B", "C"]


def test_extract_skips_malformed_json():
    html = _wrap("{not valid json,,,}") + _wrap('{"@type": "JobPosting", "title": "OK"}')
    blocks = extract_jsonld_blocks(html)
    assert len(blocks) == 1
    assert blocks[0]["title"] == "OK"


def test_extract_case_insensitive_script_tag():
    html = '<SCRIPT TYPE="application/ld+json">{"@type":"JobPosting","title":"Caps"}</SCRIPT>'
    blocks = extract_jsonld_blocks(html)
    assert len(blocks) == 1


def test_extract_empty_html_returns_empty():
    assert extract_jsonld_blocks("") == []
    assert extract_jsonld_blocks(None) == []  # type: ignore[arg-type]


# ----------------------------- parse_jobposting ----------------------------

def test_parse_basic_fields():
    node = {
        "@type": "JobPosting",
        "title": "Senior Engineer",
        "hiringOrganization": {"name": "Acme Ltd"},
        "url": "https://acme.example/jobs/1",
        "description": "Build things.",
        "datePosted": "2026-06-01",
        "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}},
    }
    job = parse_jobposting(node, source="jsonld", date_found=_DATE)
    assert isinstance(job, Job)
    assert job.title == "Senior Engineer"
    assert job.company == "Acme Ltd"
    assert job.apply_url == "https://acme.example/jobs/1"
    assert job.location == "London"
    assert job.source == "jsonld"
    assert job.posted_at == "2026-06-01"
    assert job.date_confidence == "high"


def test_parse_hiring_org_as_string():
    node = {"@type": "JobPosting", "title": "Dev", "hiringOrganization": "PlainCo"}
    job = parse_jobposting(node, source="jsonld", date_found=_DATE)
    assert job.company == "PlainCo"


def test_parse_type_as_list():
    node = {"@type": ["JobPosting", "Thing"], "title": "Listed"}
    job = parse_jobposting(node, source="jsonld", date_found=_DATE)
    assert job is not None
    assert job.title == "Listed"


def test_parse_salary_band_min_max():
    node = {
        "@type": "JobPosting",
        "title": "Paid",
        "baseSalary": {"currency": "GBP", "value": {"minValue": 50000, "maxValue": 70000, "unitText": "YEAR"}},
    }
    job = parse_jobposting(node, source="jsonld", date_found=_DATE)
    assert job.salary_min == 50000
    assert job.salary_max == 70000


def test_parse_salary_single_value():
    node = {
        "@type": "JobPosting",
        "title": "Flat",
        "baseSalary": {"value": {"value": 60000}},
    }
    job = parse_jobposting(node, source="jsonld", date_found=_DATE)
    assert job.salary_min == 60000
    assert job.salary_max == 60000


def test_parse_missing_title_returns_none():
    assert parse_jobposting({"@type": "JobPosting"}, source="jsonld", date_found=_DATE) is None
    assert parse_jobposting({"@type": "JobPosting", "title": "   "}, source="jsonld", date_found=_DATE) is None


def test_parse_non_jobposting_returns_none():
    assert parse_jobposting({"@type": "Organization", "name": "x"}, source="jsonld", date_found=_DATE) is None


def test_parse_no_date_low_confidence():
    job = parse_jobposting({"@type": "JobPosting", "title": "T"}, source="jsonld", date_found=_DATE)
    assert job.posted_at is None
    assert job.date_confidence == "low"


# ----------------------------- harvest_jobs (end to end) -------------------

def test_harvest_end_to_end_filters_to_jobpostings():
    html = (
        _wrap('{"@type": "BreadcrumbList"}')
        + _wrap('{"@type": "JobPosting", "title": "One", "hiringOrganization": {"name": "A"}}')
        + _wrap('[{"@type": "JobPosting", "title": "Two"}, {"@type": "Organization", "name": "z"}]')
    )
    jobs = harvest_jobs(html, source="jsonld", date_found=_DATE)
    assert [j.title for j in jobs] == ["One", "Two"]
    assert all(j.source == "jsonld" for j in jobs)
    assert all(j.date_found == _DATE for j in jobs)


def test_harvest_skips_jobposting_without_title():
    html = _wrap('{"@type": "JobPosting", "description": "no title here"}')
    assert harvest_jobs(html, source="jsonld", date_found=_DATE) == []


def test_harvest_default_date_found_is_set():
    html = _wrap('{"@type": "JobPosting", "title": "T"}')
    jobs = harvest_jobs(html)  # date_found defaults to now
    assert len(jobs) == 1
    assert jobs[0].date_found  # non-empty ISO string


# ----------------------------- harvest_url (fetch + extract) ---------------

def test_harvest_url_fetches_and_extracts():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://acme.example/careers",
                    body=_wrap('{"@type": "JobPosting", "title": "Remote Dev", "url": "https://acme.example/j/1"}'),
                    content_type="text/html",
                )
                jobs = await harvest_url(session, "https://acme.example/careers", source="acme")
                assert len(jobs) == 1
                assert jobs[0].title == "Remote Dev"
                assert jobs[0].source == "acme"
        finally:
            await session.close()
    _run(_t())


def test_harvest_url_404_returns_empty():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://acme.example/careers", status=404)
                assert await harvest_url(session, "https://acme.example/careers") == []
        finally:
            await session.close()
    _run(_t())


def test_harvest_url_network_error_returns_empty():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://acme.example/careers", exception=aiohttp.ClientError("boom"))
                assert await harvest_url(session, "https://acme.example/careers") == []
        finally:
            await session.close()
    _run(_t())
