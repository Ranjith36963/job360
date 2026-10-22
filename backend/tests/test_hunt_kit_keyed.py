"""Offline tests for src/hunt_kit/kit_keyed.py (Reed, USAJOBS, WhatJobs). No network.

Fixture shapes come from the freehire reference (MIT, internal/ingest/sources/): Reed from
testdata/reed_search.json, USAJOBS from testdata/usajobs_search.json, WhatJobs from the
``whatjobsPage`` envelope in whatjobs_test.go. Not run against the live APIs (egress blocked).
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_HERE = Path(__file__).resolve().parent
_KIT = _HERE.parent / "src" / "hunt_kit"
_spec = importlib.util.spec_from_file_location("kit_keyed_under_test", _KIT / "kit_keyed.py")
kk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kk)  # type: ignore[union-attr]

BIG_DAYS = 36500
SECRETS = ("REED_API_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL", "WHATJOBS_PUBLISHER_IDS")


class FakeHttp:
    """Routes URL substrings to canned bodies (a list is consumed one page per call) and records calls."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes, self.calls = routes, []

    def __call__(self, url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        self.calls.append({"url": url, "method": method, "headers": headers or {}})
        for needle, body in self.routes.items():
            if needle in url:
                if isinstance(body, list):
                    return 200, (body.pop(0) if body else {})
                return 200, body
        return 404, None


def _stats() -> dict:
    return {"tried": 0, "ok": 0, "jobs": 0}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in SECRETS:
        monkeypatch.delenv(k, raising=False)


def _q(call) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(call["url"]).query).items()}


# ------------------------------------------------------------------------------ no key
@pytest.mark.parametrize("name,countries", [("reed", ["GB"]), ("usajobs", ["US"]), ("whatjobs", ["GB", "US"])])
def test_no_key_returns_empty_and_sends_nothing(name, countries):
    http = FakeHttp({})
    assert kk.FEEDS[name](["python"], http, _stats(), days=7, countries=countries) == []
    assert http.calls == []


def test_usajobs_needs_the_email_too(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "K")
    http = FakeHttp({})
    assert kk.fetch_usajobs(["python"], http, _stats(), days=7, countries=["US"]) == []
    assert http.calls == []


# ------------------------------------------------------------------------------ Reed
# Shape of freehire testdata/reed_search.json (search list: "date", jobUrl, truncated jobDescription).
REED_PAGE = {"results": [
    {"jobId": 56785467, "employerId": 671786, "employerName": "Moorepay", "jobTitle": "Software Engineer",
     "locationName": "Manchester", "minimumSalary": None, "maximumSalary": None, "currency": None,
     "expirationDate": "23/07/2026", "date": "15/04/2026",
     "jobDescription": "As a Software Engineer you will be a key contributor ... ", "applications": 145,
     "jobUrl": "https://www.reed.co.uk/jobs/software-engineer/56785467"},
    {"jobId": 57002409, "employerName": "Matchtech ", "jobTitle": "Software Developer",
     "locationName": "Dorking", "minimumSalary": 45000.0, "maximumSalary": 65000.0, "currency": "GBP",
     "date": "09/06/2026", "jobDescription": "Defence &amp; Security sector ... ",
     "jobUrl": "https://www.reed.co.uk/jobs/software-developer/57002409"},
    {"jobId": 1, "employerName": "", "jobTitle": "No employer", "jobUrl": "https://www.reed.co.uk/jobs/x/1"},
], "ambiguousLocations": [], "totalResults": 7434}


def test_parse_reed_fixture():
    jobs = kk.parse_reed(REED_PAGE)
    assert [j["company"] for j in jobs] == ["Moorepay", "Matchtech"]  # no-employer row dropped (reed.go:98)
    j = jobs[0]
    assert j["source"] == "feed:reed" and j["posted"] == "2026-04-15"
    assert j["url"] == "https://www.reed.co.uk/jobs/software-engineer/56785467"
    assert j["extra"]["truncated"] is True and set(j) >= {"title", "location", "description", "extra"}
    assert jobs[1]["extra"]["salary_min"] == 45000.0 and "&amp;" not in jobs[1]["description"]
    detail = dict(REED_PAGE["results"][0], datePosted="17/06/2026", externalUrl="https://careers.example/1")
    d = kk.parse_reed({"results": [detail]})[0]  # the detail shape: employer url wins (reed.go:103)
    assert d["url"] == "https://careers.example/1" and d["posted"] == "2026-06-17"


def test_reed_sends_basic_auth_and_params(monkeypatch):
    monkeypatch.setenv("REED_API_KEY", "REEDKEY")
    http = FakeHttp({"reed.co.uk/api/1.0/search": [dict(REED_PAGE, totalResults=2)]})
    jobs = kk.fetch_reed(["software engineer"], http, _stats(), days=BIG_DAYS, countries=["gb"])
    assert len(jobs) == 2
    call = http.calls[0]
    token = base64.b64encode(b"REEDKEY:").decode()  # key as username, blank password (reed.go:114)
    assert call["headers"]["Authorization"] == "Basic " + token
    assert "REEDKEY" not in call["url"]
    q = _q(call)
    assert q["keywords"] == "software engineer" and q["resultsToTake"] == "100" and q["resultsToSkip"] == "0"


def test_reed_country_gate(monkeypatch):
    monkeypatch.setenv("REED_API_KEY", "REEDKEY")
    http = FakeHttp({"reed.co.uk": REED_PAGE})
    assert kk.fetch_reed(["python"], http, _stats(), days=7, countries=["US", "DE"]) == []
    assert kk.fetch_reed(["python"], http, _stats(), days=7, countries=[]) == []
    assert http.calls == []


def test_reed_paging_stops_on_total_then_empty(monkeypatch):
    monkeypatch.setenv("REED_API_KEY", "REEDKEY")
    big = dict(REED_PAGE, totalResults=250)
    http = FakeHttp({"reed.co.uk": [big, {"results": [dict(REED_PAGE["results"][0], jobId=9)],
                                         "totalResults": 250}, {"results": [], "totalResults": 250}]})
    jobs = kk.fetch_reed(["dev"], http, _stats(), days=BIG_DAYS, countries=["GB"])
    assert [_q(c)["resultsToSkip"] for c in http.calls] == ["0", "100", "200"]
    assert {j["extra"]["id"] for j in jobs} == {"56785467", "57002409", "9"}
    # totalResults reached: no further page requested
    http2 = FakeHttp({"reed.co.uk": [dict(REED_PAGE, totalResults=100), dict(REED_PAGE)]})
    kk.fetch_reed(["dev"], http2, _stats(), days=BIG_DAYS, countries=["GB"])
    assert len(http2.calls) == 1


def test_reed_http_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("REED_API_KEY", "REEDKEY")
    assert kk.fetch_reed(["dev"], FakeHttp({}), _stats(), days=7, countries=["GB"]) == []


# ---------------------------------------------------------------------------- USAJOBS
# Shape of freehire testdata/usajobs_search.json (first item, trimmed).
USA_PAGE = {"SearchResult": {"SearchResultCount": 1, "SearchResultCountAll": 1621, "SearchResultItems": [
    {"MatchedObjectId": "871922900", "MatchedObjectDescriptor": {
        "PositionID": "26-12971621B-ITS-2210-12",
        "PositionTitle": "Information Technology Specialist (Applications Software)",
        "PositionURI": "https://www.usajobs.gov:443/job/871922900",
        "PositionLocationDisplay": "Multiple Locations",
        "PositionLocation": [{"LocationName": "Anchorage, Alaska", "CountryCode": "United States"},
                             {"LocationName": "Birmingham, Alabama"}],
        "OrganizationName": "Internal Revenue Service", "DepartmentName": "Department of the Treasury",
        "PositionStartDate": "2026-06-05T00:00:00.0000", "PositionEndDate": "2026-06-18T23:59:59.9970",
        "UserArea": {"Details": {
            "JobSummary": "Additional jobs may be filled from this announcement.",
            "MajorDuties": ["Provides technical guidance in designing, coding & testing programs."],
            "Requirements": "U.S Citizenship - Must be a U.S. Citizen or National.",
            "TeleworkEligible": True, "RemoteIndicator": False}}}},
    {"MatchedObjectId": "", "MatchedObjectDescriptor": {"PositionTitle": "x", "OrganizationName": "Y"}},
]}}


def test_parse_usajobs_fixture():
    jobs = kk.parse_usajobs(USA_PAGE)
    assert len(jobs) == 1  # the row without MatchedObjectId is dropped (usajobs.go:116)
    j = jobs[0]
    assert j["source"] == "feed:usajobs" and j["company"] == "Internal Revenue Service"
    assert j["url"] == "https://www.usajobs.gov/job/871922900"  # ":443" removed (usajobs.go:176)
    assert j["location"] == "Anchorage, Alaska" and j["posted"] == "2026-06-05"
    assert j["extra"]["work_mode"] == "hybrid" and j["remote"] is None
    assert "coding & testing" in j["description"] and "U.S Citizenship" in j["description"]


def test_usajobs_sends_key_and_email_headers(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "USAKEY")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    http = FakeHttp({"data.usajobs.gov/api/search": [USA_PAGE, {}]})
    jobs = kk.fetch_usajobs(["software"], http, _stats(), days=BIG_DAYS, countries=["US"])
    assert len(jobs) == 1
    h = http.calls[0]["headers"]
    assert h["Authorization-Key"] == "USAKEY" and h["User-Agent"] == "me@example.com"
    assert "USAKEY" not in http.calls[0]["url"]
    q = _q(http.calls[0])
    assert q["ResultsPerPage"] == "500" and q["Page"] == "1" and q["Keyword"] == "software"
    assert _q(http.calls[1])["Page"] == "2"  # paged until an empty page


def test_usajobs_country_gate(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "USAKEY")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    http = FakeHttp({"usajobs.gov": USA_PAGE})
    assert kk.fetch_usajobs(["x"], http, _stats(), days=7, countries=["GB", "CA"]) == []
    assert http.calls == []


def test_usajobs_paging_caps_at_max_pages(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "USAKEY")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    pages = [json.loads(json.dumps(USA_PAGE)) for _ in range(20)]
    for n, p in enumerate(pages):  # distinct ids so none is a repeat
        p["SearchResult"]["SearchResultItems"][0]["MatchedObjectId"] = str(n + 1)
    http = FakeHttp({"usajobs.gov": pages})
    jobs = kk.fetch_usajobs(["x"], http, _stats(), days=BIG_DAYS, countries=["US"])
    assert len(http.calls) == kk.MAX_PAGES and len(jobs) == kk.MAX_PAGES


# --------------------------------------------------------------------------- WhatJobs
# Shape of the whatjobsPage envelope in freehire whatjobs_test.go: data[], snippet is the full body.
WJ_PAGE = {"data": [
    {"title": "Senior Rust Engineer", "company": "Acme", "location": "Austin", "postcode": "78701",
     "snippet": "<p>Own the Rust scoring platform.</p>\n#J-18808-Ljbffr", "age": "July 28th, 2026", "age_days": 1,
     "url": "https://www.whatjobs.com/pub_api__cpl__2737655843__7065?utm_campaign=publisher&utm_source=7065"},
    {"title": "Rust Developer", "company": "Other", "location": "", "snippet": "<p>Should be skipped.</p>",
     "age_days": 3, "url": "https://www.whatjobs.com/some-other-page"},
]}


def test_parse_whatjobs_ids_and_page():
    assert kk.parse_whatjobs_ids("gb:123, us:456,bad,:7,uk:") == {"gb": "123", "us": "456"}
    jobs = kk.parse_whatjobs(WJ_PAGE, "United States")
    assert len(jobs) == 1  # a url without pub_api__cpl__ is dropped (whatjobs.go:337)
    j = jobs[0]
    assert j["extra"]["id"] == "2737655843" and j["location"] == "Austin, United States"
    assert j["description"] == "Own the Rust scoring platform." and "Ljbffr" not in j["description"]
    assert j["url"].startswith("https://www.whatjobs.com/pub_api__cpl__2737655843__7065")
    assert j["posted"] == "" and j["source"] == "feed:whatjobs"


def test_whatjobs_sends_publisher_and_country_gate(monkeypatch):
    monkeypatch.setenv("WHATJOBS_PUBLISHER_IDS", "gb:111,us:7065")
    http = FakeHttp({"api.whatjobs.com": [WJ_PAGE, {"data": []}]})
    jobs = kk.fetch_whatjobs(["rust engineer"], http, _stats(), days=BIG_DAYS, countries=["US", "JP", "DE"])
    assert len(jobs) == 1 and jobs[0]["location"] == "Austin, United States"
    q = _q(http.calls[0])  # only US has an id AND is a user country; JP/DE are skipped
    assert q["publisher"] == "7065" and q["keyword"] == "rust engineer"
    assert q["limit"] == "50" and q["page"] == "1" and q["user_ip"] == kk.WHATJOBS_CRAWL_IP
    assert all(_q(c)["publisher"] == "7065" for c in http.calls)


def test_whatjobs_gb_uses_the_gb_id_and_uk_market(monkeypatch):
    monkeypatch.setenv("WHATJOBS_PUBLISHER_IDS", "gb:111")
    http = FakeHttp({"api.whatjobs.com": {"data": []}})
    kk.fetch_whatjobs(["rust"], http, _stats(), days=7, countries=["GB"])
    assert _q(http.calls[0])["publisher"] == "111"
    monkeypatch.setenv("WHATJOBS_PUBLISHER_IDS", "uk:222")  # freehire's own code for the UK
    http2 = FakeHttp({"api.whatjobs.com": {"data": []}})
    kk.fetch_whatjobs(["rust"], http2, _stats(), days=7, countries=["GB"])
    assert _q(http2.calls[0])["publisher"] == "222"


def test_whatjobs_no_id_for_country_or_blank_query_sends_nothing(monkeypatch):
    monkeypatch.setenv("WHATJOBS_PUBLISHER_IDS", "us:7065")
    http = FakeHttp({"api.whatjobs.com": WJ_PAGE})
    assert kk.fetch_whatjobs(["rust"], http, _stats(), days=7, countries=["GB", "FR"]) == []
    assert kk.fetch_whatjobs([], http, _stats(), days=7, countries=["US"]) == []  # blank keyword refused
    assert http.calls == []


def test_whatjobs_paging_stops_on_empty_and_on_relevance_collapse(monkeypatch):
    monkeypatch.setenv("WHATJOBS_PUBLISHER_IDS", "us:7065")
    # empty page ends it
    http = FakeHttp({"api.whatjobs.com": [WJ_PAGE, {"data": []}, WJ_PAGE]})
    kk.fetch_whatjobs(["rust"], http, _stats(), days=BIG_DAYS, countries=["US"])
    assert len(http.calls) == 2
    # a page where under half name the keyword stops the crawl after that page
    pad = {"data": [{"title": "Barista", "company": "Cafe", "location": "Austin", "snippet": "<p>Coffee</p>",
                     "url": "https://www.whatjobs.com/pub_api__cpl__5__7065"}]}
    http2 = FakeHttp({"api.whatjobs.com": [pad, WJ_PAGE]})
    assert kk.fetch_whatjobs(["rust"], http2, _stats(), days=BIG_DAYS, countries=["US"]) == []
    assert len(http2.calls) == 1


def test_keyed_registry_and_stats(monkeypatch):
    assert set(kk.FEEDS) == {"reed", "usajobs", "whatjobs"}
    assert all(callable(f) for f in kk.FEEDS.values())
    monkeypatch.setenv("REED_API_KEY", "REEDKEY")
    st = _stats()
    kk.fetch_reed(["dev"], FakeHttp({"reed.co.uk": dict(REED_PAGE, totalResults=2)}), st,
                  days=BIG_DAYS, countries=["GB"])
    assert st["tried"] == 1 and st["ok"] == 1 and st["jobs"] == 2
    import sys
    sys.path.insert(0, str(_KIT))
    try:
        hk = importlib.import_module("hunt_kit")
        assert {"reed", "usajobs", "whatjobs"} <= set(hk.FEED_SOURCES) and "kit_keyed" in hk.FEED_MODULES
    finally:
        sys.path.remove(str(_KIT))
