"""Offline tests for src/hunt_kit/kit_bigboards.py (LinkedIn, Glassdoor, Google Jobs; fixtures only, no network).

Owner decision 2026-10-19: the script reads these three sites at low volume (reverses the 2026-10-14 removal and
the 2026-10-16 "assistant only" rule). Indeed is not read.

Fixture origins: every fixture is built from what JobSpy's parsers read (commit 1988484): LinkedIn cards and job
pages from jobspy/linkedin/__init__.py:80-148, :224-248 and linkedin/util.py:9-23; Glassdoor answers from
jobspy/glassdoor/model.py:4-65 and glassdoor/__init__.py:70-86, :187-218, :250-271; Google ads from
jobspy/google/__init__.py:116-212 and google/util.py:6-33. The hosts are unreachable from the build container:
nothing here was checked against a live response.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_bigboards_under_test", _KIT / "kit_bigboards.py")
kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(kb, "PAUSE_S", 0)
    # the LinkedIn run budget has its own test below; the other tests check the search/page/detail limits
    monkeypatch.setattr(kb, "LI_MAX_REQUESTS", 0)
    monkeypatch.setattr(kb, "LI_PAUSE_S", 0)


def _http(handler):
    """Fake http: handler(url, data_obj) -> (status, body). Records every call (url, headers, kwargs)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=None):
        obj = json.loads(data) if data else None
        calls.append({"url": url, "method": method, "data": obj, "headers": headers or {},
                      "as_json": as_json, "once": once})
        return handler(url, obj)

    http.calls = calls
    return http


def _q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["url"].startswith("https://") and j["extra"]["attribution"]
        assert ("title_only" in j["extra"]) == (not j["description"])


def _never_credentials(calls):
    for c in calls:
        assert c["once"] is True  # the core must not re-send a refused request
        low = {k.lower() for k in c["headers"]}
        assert not low & {"cookie", "authorization", "proxy-authorization", "x-api-key"}


# ----------------------------------------------------------------------------------- LinkedIn
def li_card(i, title="AI Engineer", company="Acme", loc="London, England, United Kingdom", date="2026-10-15"):
    return (
        f'<li><div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:{i}">'
        f'<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{i}?trk=x"></a>'
        f'<div class="base-search-card__info"><h3 class="base-search-card__title">  {title} </h3>'
        f'<h4 class="base-search-card__subtitle"><a class="hidden-nested-link" '
        f'href="https://www.linkedin.com/company/acme?trk=public_jobs">{company}</a></h4>'
        f'<div class="base-search-card__metadata"><span class="job-search-card__location">{loc}</span>'
        f'<time class="job-search-card__listdate" datetime="{date}">1 day ago</time></div></div></div></li>')


def li_page(ids, **kw):
    return "".join(li_card(i, **kw) for i in ids)


LI_DETAIL = (
    '<html><body><section class="show-more-less-html"><div class="show-more-less-html__markup '
    'show-more-less-html__markup--clamp-after-5 relative overflow-hidden"><p>Build <strong>agents</strong> '
    'in Python.</p><div>Remote friendly</div><ul><li>Evals</li></ul></div></section>'
    '<ul class="description__job-criteria-list"><li><h3 class="description__job-criteria-subheader"> '
    'Seniority level </h3><span class="description__job-criteria-text description__job-criteria-text--criteria"> '
    'Mid-Senior level </span></li><li><h3 class="description__job-criteria-subheader">Employment type</h3>'
    '<span class="description__job-criteria-text">Full-time</span></li></ul></body></html>')
LI_AUTHWALL = '<html><head><title>Sign Up | LinkedIn</title></head><body class="authwall">Join now</body></html>'


def _li_handler(pages, detail=LI_DETAIL):
    """pages: {start: html}; every /jobs/view/ URL answers `detail`."""
    def h(url, _obj):
        if "/jobs/view/" in url:
            return 200, detail
        return 200, pages.get(int(_q(url)["start"]), kb.LI_EMPTY)
    return h


def _wrap200(h):
    def w(url, obj):
        r = h(url, obj)
        return r if isinstance(r, tuple) else (200, r)
    return w


def test_linkedin_happy_path_params_headers_and_details():
    pages = {0: li_page(range(100, 110)), 10: li_page(range(110, 113), title="Agent Engineer", company="Beta")}
    http = _http(_wrap200(_li_handler(pages)))
    stats: dict = {}
    jobs = kb.fetch_linkedin(["AI engineer"], http, stats, days=BIG, countries=["GB"])
    searches = [c for c in http.calls if "seeMoreJobPostings" in c["url"]]
    assert [_q(c["url"])["start"] for c in searches] == ["0", "10"]  # a page of 3 cards is the last
    q0 = _q(searches[0]["url"])
    assert q0["keywords"] == "AI engineer" and q0["location"] == "United Kingdom" and q0["distance"] == "50"
    assert q0["f_TPR"] == f"r{BIG * 24 * 3600}"
    assert searches[0]["url"].startswith("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?")
    h = searches[0]["headers"]
    assert "Chrome/120" in h["user-agent"] and h["accept-language"] == "en-US,en;q=0.9"
    _never_credentials(http.calls)
    assert len(jobs) == 13
    _check(jobs, "linkedin")
    first = jobs[0]
    assert first["company"] == "Acme" and first["title"] == "AI Engineer"  # the employer, never "LinkedIn"
    assert first["url"] == "https://www.linkedin.com/jobs/view/100"  # public URL, no tracking query
    assert first["location"] == "London, England, United Kingdom" and first["posted"] == "2026-10-15"
    assert first["extra"]["company_url"] == "https://www.linkedin.com/company/acme"
    assert first["extra"]["seniority"] == "Mid-Senior level" and first["extra"]["employment_type"] == "Full-time"
    assert "Build agents in Python." in first["description"] and "- Evals" in first["description"]
    assert first["remote"] is None and "title_only" not in first["extra"]  # the card says nothing of remote
    details = [c for c in http.calls if "/jobs/view/" in c["url"]]
    assert len(details) == kb.DETAIL_CAP == 10  # 13 ads, only the first 10 get their page read
    assert [j["description"] != "" for j in jobs] == [True] * 10 + [False] * 3
    assert jobs[12]["extra"]["title_only"] is True and jobs[12]["company"] == "Beta"
    assert stats["tried"] == len(http.calls) and stats["ok"] == len(http.calls) and stats["jobs"] == 13


def test_linkedin_pauses_between_requests_only(monkeypatch):
    monkeypatch.setattr(kb, "PAUSE_S", 5.0)
    slept: list = []
    monkeypatch.setattr(kb.time, "sleep", slept.append)
    http = _http(_wrap200(_li_handler({0: li_page([1, 2])})))
    kb.fetch_linkedin(["x"], http, {}, days=BIG, countries=["GB"])
    assert len(http.calls) == 3  # one search (a short page) + two job pages
    assert slept == [5.0, 5.0]  # none before the first request


def test_linkedin_limits_five_searches_three_pages_ten_details():
    counter = itertools.count(1000)

    def h(url, _obj):
        if "/jobs/view/" in url:
            return 200, LI_DETAIL
        return 200, li_page([next(counter) for _ in range(10)])  # always a full page of new ads

    http = _http(h)
    titles = [f"title{i}" for i in range(8)]
    jobs = kb.fetch_linkedin(titles, http, {}, days=BIG, countries=["GB", "DE", "FR"])
    searches = [c for c in http.calls if "seeMoreJobPostings" in c["url"]]
    pairs = {(_q(c["url"])["keywords"], _q(c["url"])["location"]) for c in searches}
    assert len(pairs) == kb.MAX_SEARCHES == 5
    assert len({k for k, _ in pairs}) > 1 and len({p for _, p in pairs}) > 1  # spread over titles AND countries
    assert len(searches) == 5 * kb.PAGES == 15
    assert max(int(_q(c["url"])["start"]) for c in searches) == 20
    assert len([c for c in http.calls if "/jobs/view/" in c["url"]]) == kb.DETAIL_CAP
    assert len(jobs) == 150


@pytest.mark.parametrize("status", [429, 999, 403, 0, 500])
def test_linkedin_stops_the_site_at_the_first_non_200(status):
    http = _http(lambda url, obj: (status, None))
    stats: dict = {}
    assert kb.fetch_linkedin(["a", "b"], http, stats, days=BIG, countries=["GB", "DE"]) == []
    assert len(http.calls) == 1 and stats["tried"] == 1 and stats["ok"] == 0
    assert http.calls[0]["once"] is True


def test_linkedin_keeps_what_it_read_and_reads_no_details_after_a_refusal():
    def h(url, _obj):
        s = int(_q(url)["start"])
        return (200, li_page(range(10))) if s == 0 else (429, None)

    http = _http(h)
    jobs = kb.fetch_linkedin(["a", "b"], http, {}, days=BIG, countries=["GB"])
    assert len(jobs) == 10 and all(not j["description"] for j in jobs)
    assert len(http.calls) == 2  # page 2 refused: no third page, no second search, no job pages


def test_linkedin_login_wall_instead_of_cards_stops():
    http = _http(lambda url, obj: (200, LI_AUTHWALL))
    assert kb.fetch_linkedin(["a", "b"], http, {}, days=BIG, countries=["GB", "DE"]) == []
    assert len(http.calls) == 1


def test_linkedin_login_wall_instead_of_a_job_page_stops_the_details():
    http = _http(_wrap200(_li_handler({0: li_page(range(5))}, detail=LI_AUTHWALL)))
    jobs = kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=["GB"])
    assert len(jobs) == 5 and all(j["extra"]["title_only"] for j in jobs)
    assert len([c for c in http.calls if "/jobs/view/" in c["url"]]) == 1  # the first wall ends the site
    # an ordinary page that merely has a "/login" link and no markup is a plain miss, not a stop
    plain = '<html><a href="https://www.linkedin.com/login">Sign in</a><p>gone</p></html>'
    http = _http(_wrap200(_li_handler({0: li_page(range(3))}, detail=plain)))
    assert len(kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=["GB"])) == 3
    assert len([c for c in http.calls if "/jobs/view/" in c["url"]]) == 3


def test_linkedin_empty_page_is_the_end_of_the_results_not_a_stop():
    http = _http(lambda url, obj: (200, kb.LI_EMPTY + "\n"))
    assert kb.fetch_linkedin(["a", "b"], http, {}, days=BIG, countries=["GB"]) == []
    assert len(http.calls) == 2  # both searches ran: an empty page only ends its own search


def test_linkedin_skips_cards_without_title_company_or_location_and_repeated_ids():
    broken = li_card(7).replace("base-search-card__subtitle", "x")  # no employer
    no_loc = li_card(8).replace("job-search-card__location", "x")
    page = broken + no_loc + li_card(9) + li_card(9)
    http = _http(_wrap200(_li_handler({0: page})))
    jobs = kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=["GB"])
    assert [j["extra"]["id"] for j in jobs] == ["9"]


def test_linkedin_star_searches_without_a_location_and_unknown_countries_send_nothing():
    http = _http(_wrap200(_li_handler({0: li_page([1])})))
    kb.fetch_linkedin(["a"], http, {}, days=0, countries=["*"])
    s = _q(http.calls[0]["url"])
    assert "location" not in s and "distance" not in s and "f_TPR" not in s  # no place, no window
    http = _http(_wrap200(_li_handler({0: li_page([1])})))
    kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=["GB", "*"])
    searches = [_q(c["url"]) for c in http.calls if "seeMoreJobPostings" in c["url"]]
    assert [s.get("location") for s in searches] == ["United Kingdom", None]
    for countries in ([], ["XX"], ["ZZ", "QQ"]):
        http = _http(lambda url, obj: (200, ""))
        assert kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=countries) == []
        assert http.calls == []
    http = _http(lambda url, obj: (200, ""))
    assert kb.fetch_linkedin([], http, {}, days=BIG, countries=["GB"]) == [] and http.calls == []


def test_linkedin_remote_word_and_no_window():
    page = li_card(1, title="Platform Engineer", loc="Remote") + li_card(2, title="Dev", loc="Paris, France")
    http = _http(_wrap200(_li_handler({0: page}, detail="<html></html>")))
    jobs = kb.fetch_linkedin(["a"], http, {}, days=0, countries=["FR"])
    assert [j["remote"] for j in jobs] == [True, None]


# ----------------------------------------------------------------------------------- Glassdoor
GD_PAGE_TOKEN = "PAGE-OWN-TOKEN-123:abc"
GD_PAGE = f'<html><script>window.gdGlobals = {{"x": 1, "token": "{GD_PAGE_TOKEN}", "y": 2}};</script></html>'
GD_PLACES = [{"locationId": 2671300, "locationType": "N", "label": "United Kingdom"}]


def gd_view(jid, title="ML Engineer", company="Initech", age=3, loc="London", kind="C", website="initech.com",
            remote=None, country="GB"):
    return {"jobview": {
        "header": {"adOrderSponsorshipLevel": "ORGANIC", "ageInDays": age, "employer": {"id": 42},
                   "employerNameFromSearch": company, "locationName": loc, "locationType": kind,
                   "remoteWorkTypes": remote or [], "jobTypeKeys": ["fulltime"]},
        "job": {"jobTitleText": title, "listingId": jid}, "map": {"country": country},
        "overview": {"website": website, "headquarters": "London"}}}


def gd_answer(views, cursors=None):
    return [{"data": {"jobListings": {"jobListings": views, "paginationCursors": cursors or []}}}]


def _gd_handler(pages, details=lambda o: [{"data": {}}], places=GD_PLACES, page=GD_PAGE):
    """pages: {pageNumber: answer}."""
    def h(url, obj):
        if "/autocomplete/location" in url:
            return 200, places(url) if callable(places) else places
        if url.endswith("/graph"):
            op = obj[0]["operationName"]
            if op == "JobSearchResultsQuery":
                return 200, pages.get(obj[0]["variables"]["pageNumber"], gd_answer([]))
            if op == "JobDetails":
                return 200, details(obj) if callable(details) else details
        if "/Job/" in url:
            return 200, page
        return 404, None
    return h


def test_glassdoor_happy_path_uses_the_pages_own_token_and_reads_details():
    views1 = [gd_view(111), gd_view(112, title="Data Scientist", remote=["WORK_FROM_HOME"], website="")]
    views2 = [gd_view(113, company="Hooli", loc="Remote", kind="N", age=0)]
    pages = {1: gd_answer(views1, [{"cursor": "CUR2", "pageNumber": 2}, {"cursor": "CUR3", "pageNumber": 3}]),
             2: gd_answer(views2)}  # no cursor for page 3: the search ends after page 2
    details = {"j111": {"job": {"description": "<p>Ship <b>models</b>.</p>"}},
               "j112": {"job": {"description": "<p>Analyse.</p>"}}, "j113": None}
    http = _http(_wrap200(_gd_handler(pages, details=lambda o: [{"data": details}])))
    stats: dict = {}
    jobs = kb.fetch_glassdoor(["ml engineer"], http, stats, days=30, countries=["GB"])
    _never_credentials(http.calls)
    urls = [c["url"] for c in http.calls]
    assert urls[0] == "https://www.glassdoor.co.uk/Job/computer-science-jobs.htm"
    assert urls[1].startswith("https://www.glassdoor.co.uk/autocomplete/location?locationTypeFilters=CITY,STATE,COUNTRY"
                              "&caller=jobs&term=United%20Kingdom")
    search = [c for c in http.calls if c["data"] and c["data"][0]["operationName"] == "JobSearchResultsQuery"]
    assert len(search) == 2
    v1 = search[0]["data"][0]["variables"]
    assert v1["keyword"] == "ml engineer" and v1["locationId"] == 2671300 and v1["locationType"] == "COUNTRY"
    assert v1["parameterUrlInput"] == "IL.0,12_ICOUNTRY2671300" and v1["numJobsToShow"] == 100
    assert {f["filterKey"]: f["values"] for f in v1["filterParams"]} == {"radius": "50", "fromAge": "30"}
    assert (v1["pageNumber"], v1["pageCursor"]) == (1, None)
    assert (search[1]["data"][0]["variables"]["pageNumber"], search[1]["data"][0]["variables"]["pageCursor"]) == (2, "CUR2")
    for c in search:  # JobSpy's one header, filled from the page; never the token JobSpy hard-codes
        assert c["headers"]["gd-csrf-token"] == GD_PAGE_TOKEN and c["url"] == "https://www.glassdoor.co.uk/graph"
    assert "Ft6oHEWl" not in (_KIT / "kit_bigboards.py").read_text(encoding="utf-8")
    assert len(jobs) == 3
    _check(jobs, "glassdoor")
    j = jobs[0]
    assert j["company"] == "Initech" and j["url"] == "https://www.glassdoor.co.uk/job-listing/j?jl=111"
    assert j["location"] == "London, United Kingdom" and j["extra"]["company_site"] == "https://initech.com"
    assert j["posted"] == (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    assert j["description"] == "Ship models." and j["remote"] is None
    assert jobs[1]["remote"] is True and "company_site" not in jobs[1]["extra"]
    assert jobs[2]["remote"] is True and jobs[2]["description"] == "" and jobs[2]["extra"]["title_only"] is True
    det = [c for c in http.calls if c["data"] and c["data"][0]["operationName"] == "JobDetails"]
    assert len(det) == 1  # three ads, one aliased request
    assert "j111: jobView(listingId: 111" in det[0]["data"][0]["query"] and "j113" in det[0]["data"][0]["query"]
    assert stats["jobs"] == 3 and stats["tried"] == len(http.calls)


def test_glassdoor_limits_and_pause(monkeypatch):
    monkeypatch.setattr(kb, "PAUSE_S", 5.0)
    slept: list = []
    monkeypatch.setattr(kb.time, "sleep", slept.append)
    counter = itertools.count(1)

    def pages_for(n):
        return gd_answer([gd_view(next(counter), company=f"Co{n}") for _ in range(100)],
                         [{"cursor": f"C{n}", "pageNumber": p} for p in (2, 3, 4)])

    def h(url, obj):
        if "/autocomplete/location" in url:
            term = _q(url)["term"]
            return 200, [{"locationId": sum(map(ord, term)), "locationType": "N"}]
        if url.endswith("/graph"):
            if obj[0]["operationName"] == "JobSearchResultsQuery":
                return 200, pages_for(obj[0]["variables"]["pageNumber"])
            return 200, [{"data": {}}]
        return 200, GD_PAGE

    http = _http(h)
    jobs = kb.fetch_glassdoor([f"t{i}" for i in range(9)], http, {}, days=BIG, countries=["GB", "DE", "FR"])
    search = [c for c in http.calls if c["data"] and c["data"][0]["operationName"] == "JobSearchResultsQuery"]
    assert len(search) == 5 * kb.PAGES == 15
    kw = {(c["data"][0]["variables"]["keyword"], c["data"][0]["variables"]["locationId"]) for c in search}
    assert len(kw) == 5
    det = [c for c in http.calls if c["data"] and c["data"][0]["operationName"] == "JobDetails"]
    assert sum(c["data"][0]["query"].count("jobView(") for c in det) == kb.DETAIL_CAP
    assert len(jobs) == 15 * 100
    assert len(slept) == len(http.calls) - 1 and set(slept) == {5.0}


def test_glassdoor_no_token_on_the_page_stops_before_any_search():
    http = _http(_wrap200(_gd_handler({}, page="<html>nothing here</html>")))
    assert kb.fetch_glassdoor(["a", "b"], http, {}, days=BIG, countries=["GB", "DE"]) == []
    assert len(http.calls) == 1
    http = _http(_wrap200(_gd_handler({}, page="<html><title>Security Check</title> captcha </html>")))
    assert kb.fetch_glassdoor(["a"], http, {}, days=BIG, countries=["GB"]) == [] and len(http.calls) == 1


@pytest.mark.parametrize("status", [403, 429, 999, 0])
def test_glassdoor_non_200_stops_the_site_at_any_step(status):
    def h(url, obj):
        return status, None
    http = _http(h)
    assert kb.fetch_glassdoor(["a", "b"], http, {}, days=BIG, countries=["GB"]) == []
    assert len(http.calls) == 1  # the token page was refused
    # refused at the location lookup / at the search / at the details: that request is the last one sent
    # (details come after both searches: token, 2 x (lookup + search), details)
    for refuse, total in (("/autocomplete/", 2), ("JobSearchResultsQuery", 3), ("JobDetails", 6)):
        inner = _gd_handler({1: gd_answer([gd_view(1)])})

        def h2(url, obj, refuse=refuse, inner=inner):
            hit = refuse in url or (obj and refuse in obj[0]["operationName"])
            return (status, None) if hit else _wrap200(inner)(url, obj)
        http = _http(h2)
        jobs = kb.fetch_glassdoor(["a", "b"], http, {}, days=BIG, countries=["GB"])
        assert len(http.calls) == total, refuse
        assert len(jobs) == (1 if refuse == "JobDetails" else 0)  # the ad was read before the details were refused


def test_glassdoor_api_error_answer_stops():
    bad = [{"data": None, "errors": [{"message": "bot"}]}]
    http = _http(_wrap200(_gd_handler({1: bad})))
    assert kb.fetch_glassdoor(["a", "b"], http, {}, days=BIG, countries=["GB"]) == []
    assert len([c for c in http.calls if c["url"].endswith("/graph")]) == 1


def test_glassdoor_unknown_place_skips_that_search_and_goes_on():
    seen_terms: list = []

    def h(url, obj):
        if "/autocomplete/location" in url:
            seen_terms.append(url)
            return 200, [] if len(seen_terms) == 1 else GD_PLACES
        return _wrap200(_gd_handler({1: gd_answer([gd_view(5)])}))(url, obj)

    http = _http(h)
    jobs = kb.fetch_glassdoor(["a"], http, {}, days=BIG, countries=["DE", "GB"])
    assert len(seen_terms) == 2 and [j["extra"]["id"] for j in jobs] == ["5"]
    hosts = [urlsplit(c["url"]).hostname for c in http.calls]
    assert hosts[0] == "www.glassdoor.de" and hosts[2] == "www.glassdoor.co.uk"


def test_glassdoor_star_has_no_location_lookup_and_countries_without_a_site_send_nothing():
    http = _http(_wrap200(_gd_handler({1: gd_answer([gd_view(9)])})))
    jobs = kb.fetch_glassdoor(["a"], http, {}, days=0, countries=["*"])
    assert len(jobs) == 1
    assert http.calls[0]["url"] == "https://www.glassdoor.com/Job/computer-science-jobs.htm"
    assert not any("autocomplete" in c["url"] for c in http.calls)
    v = [c for c in http.calls if c["data"] and c["data"][0]["operationName"] == "JobSearchResultsQuery"][0]
    assert "locationId" not in v["data"][0]["variables"] and v["data"][0]["variables"]["filterParams"] == []
    for countries in ([], ["JP"], ["NG", "XX"]):  # JP and NG have a place name but no Glassdoor site in the reference
        http = _http(lambda url, obj: (200, ""))
        assert kb.fetch_glassdoor(["a"], http, {}, days=BIG, countries=countries) == [] and http.calls == []


def test_glassdoor_drops_ads_without_an_employer():
    pages = {1: gd_answer([gd_view(1, company=""), gd_view(2, company="Real Co")])}
    http = _http(_wrap200(_gd_handler(pages)))
    jobs = kb.fetch_glassdoor(["a"], http, {}, days=BIG, countries=["GB"])
    assert [j["company"] for j in jobs] == ["Real Co"]


# --------------------------------------------------------------------------------- Google Jobs
def g_info(n, title="AI Engineer", company="Globex", place="London, UK", url=None, ago="3 days ago",
           desc="Build agents. Remote friendly."):
    info = [None] * 30
    info[0], info[1], info[2] = title, company, place
    info[3] = [[url or f"https://careers.globex.example/jobs/{n}", "Globex careers"]]
    info[12], info[19], info[28], info[29] = ago, desc, f"id{n}", []
    return info


def g_first(infos, cursor="CUR1"):
    body = "".join(f'<div class="x">"520084652":{json.dumps(i)}}}]]]]],</div>' for i in infos)
    fc = f'<div jsname="Yust4d" class="y" data-async-fc="{cursor}"></div>' if cursor else ""
    return f"<html>{fc}<script>{body}</script></html>"


def g_next(infos, cursor):
    pairs = [[f"k{i}", json.dumps([[[{"520084652": info}]]])] for i, info in enumerate(infos)]
    tail = f'\n<div data-async-fc="{cursor}"></div>' if cursor else "\n"
    return ")]}'\n" + json.dumps([[*pairs]]) + tail


def _g_handler(first, nxt):
    """first: page-1 html; nxt: {cursor: next-page text}."""
    def h(url, _obj):
        if "/async/callback:550" in url:
            return 200, nxt.get(_q(url)["fc"], "")
        return 200, first
    return h


def test_google_happy_path_three_pages_params_and_headers():
    infos2, infos3 = [g_info(3, title="Agent Engineer")], [g_info(4, company="Initech", ago="5 hours ago")]
    nxt = {"CUR1": g_next(infos2, "CUR2"), "CUR2": g_next(infos3, "CUR3")}
    http = _http(_g_handler(g_first([g_info(1), g_info(2, company="Umbrella")]), nxt))
    stats: dict = {}
    jobs = kb.fetch_googlejobs(["AI engineer"], http, stats, days=30, countries=["GB"])
    _never_credentials(http.calls)
    assert len(http.calls) == 3  # one search, three pages, no detail fetch (the text comes with the ad)
    first = http.calls[0]
    assert first["url"].startswith("https://www.google.com/search?") and not first["as_json"]
    p = _q(first["url"])
    assert p["q"] == "AI engineer jobs near United Kingdom in the last month" and p["udm"] == "8"
    assert first["headers"]["x-browser-channel"] == "stable" and "Chrome/130" in first["headers"]["user-agent"]
    p2 = _q(http.calls[1]["url"])
    assert http.calls[1]["url"].startswith("https://www.google.com/async/callback:550?")
    assert p2["fc"] == "CUR1" and p2["fcv"] == "3" and p2["async"].startswith("_basejs:/xjs/_/js/")
    assert http.calls[1]["headers"]["sec-fetch-dest"] == "empty"
    assert _q(http.calls[2]["url"])["fc"] == "CUR2"  # the third page ends the search; CUR3 is never used
    assert [j["extra"]["id"] for j in jobs] == ["id1", "id2", "id3", "id4"]
    _check(jobs, "googlejobs")
    j = jobs[0]
    assert j["company"] == "Globex" and j["url"] == "https://careers.globex.example/jobs/1"  # the employer's link
    assert j["location"] == "London, UK" and j["remote"] is True and "Build agents" in j["description"]
    assert j["posted"] == (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    assert jobs[3]["posted"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")  # "5 hours ago" is today
    assert stats["jobs"] == 4 and stats["tried"] == 3


def test_google_limits_five_searches_three_pages_and_the_pause(monkeypatch):
    monkeypatch.setattr(kb, "PAUSE_S", 5.0)
    slept: list = []
    monkeypatch.setattr(kb.time, "sleep", slept.append)
    counter = itertools.count(1)

    def h(url, _obj):
        if "callback:550" in url:
            return 200, g_next([g_info(next(counter))], "MORE")
        return 200, g_first([g_info(next(counter))], "START")

    http = _http(h)
    jobs = kb.fetch_googlejobs([f"t{i}" for i in range(9)], http, {}, days=0, countries=["GB", "DE", "FR"])
    first = [c for c in http.calls if "/search?" in c["url"]]
    assert len(first) == kb.MAX_SEARCHES == 5 and len(http.calls) == 15 and len(jobs) == 15
    assert len({_q(c["url"])["q"] for c in first}) == 5
    assert all("in the last" not in _q(c["url"])["q"] and "since yesterday" not in _q(c["url"])["q"] for c in first)
    assert len(slept) == 14 and set(slept) == {5.0}


@pytest.mark.parametrize("hours,days,phrase", [(24, 1, "since yesterday"), (72, 3, "in the last 3 days"),
                                              (168, 7, "in the last week"), (720, 30, "in the last month")])
def test_google_time_phrase(hours, days, phrase):
    assert kb._g_time_phrase(hours) == phrase
    http = _http(lambda url, obj: (200, g_first([g_info(1)], None)))
    kb.fetch_googlejobs(["x"], http, {}, days=days, countries=["GB"])
    assert _q(http.calls[0]["url"])["q"].endswith(phrase)


@pytest.mark.parametrize("status", [429, 403, 999, 503, 0])
def test_google_non_200_stops_the_site(status):
    http = _http(lambda url, obj: (status, None))
    assert kb.fetch_googlejobs(["a", "b"], http, {}, days=BIG, countries=["GB", "DE"]) == []
    assert len(http.calls) == 1
    # a refusal of page 2 keeps page 1 and ends everything
    def h(url, obj):
        return (status, None) if "callback" in url else (200, g_first([g_info(1)]))
    http = _http(h)
    jobs = kb.fetch_googlejobs(["a", "b"], http, {}, days=BIG, countries=["GB", "DE"])
    assert len(jobs) == 1 and len(http.calls) == 2


def test_google_consent_or_unusual_traffic_page_stops_but_an_empty_result_does_not():
    for page in ('<html><a href="https://consent.google.com/m?continue=x">Before you continue</a></html>',
                 "<html>Our systems have detected unusual traffic from your computer network.</html>",
                 '<html><form action="/sorry/index">captcha</form></html>'):
        http = _http(lambda url, obj, page=page: (200, page))
        assert kb.fetch_googlejobs(["a", "b"], http, {}, days=BIG, countries=["GB"]) == []
        assert len(http.calls) == 1
    http = _http(lambda url, obj: (200, "<html><body>No results</body></html>"))
    assert kb.fetch_googlejobs(["a", "b"], http, {}, days=BIG, countries=["GB"]) == []
    assert len(http.calls) == 2  # an empty search ends only itself


def test_google_stops_paging_on_an_empty_page_and_drops_bad_or_repeated_ads():
    bad = [g_info(1, company=""), g_info(2, url="not-a-link"), g_info(3), g_info(3)]
    http = _http(_g_handler(g_first(bad), {"CUR1": g_next([], "CUR2")}))
    jobs = kb.fetch_googlejobs(["a"], http, {}, days=BIG, countries=["GB"])
    assert [j["extra"]["id"] for j in jobs] == ["id3"] and len(http.calls) == 2
    assert kb.parse_google_ad(None) is None and kb.parse_google_ad([]) is None
    assert kb.parse_google_first_page("") == ([], None) and kb.parse_google_next_page("garbage") == ([], None)


def test_google_star_searches_without_near_and_unknown_countries_send_nothing():
    http = _http(lambda url, obj: (200, g_first([g_info(1)], None)))
    kb.fetch_googlejobs(["python dev"], http, {}, days=0, countries=["*"])
    assert _q(http.calls[0]["url"])["q"] == "python dev jobs"
    for countries in ([], ["XX"]):
        http = _http(lambda url, obj: (200, ""))
        assert kb.fetch_googlejobs(["a"], http, {}, days=BIG, countries=countries) == [] and http.calls == []


# ------------------------------------------------------------------------------ cross-cutting
def test_each_fetcher_never_raises_even_when_http_does():
    def boom(url, **kw):
        raise RuntimeError("network down")

    for name, fn in kb.FEEDS.items():
        stats: dict = {}
        assert fn(["a"], boom, stats, days=BIG, countries=["GB"]) == [], name
        assert stats["jobs"] == 0


def test_feeds_are_registered_with_the_kit_and_on_by_default():
    sys.modules.pop("hunt_kit_bigboards_check", None)
    spec = importlib.util.spec_from_file_location("hunt_kit_bigboards_check", _KIT / "hunt_kit.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_bigboards_check"] = hk
    spec.loader.exec_module(hk)  # type: ignore[union-attr]
    assert set(kb.FEEDS) == {"linkedin", "glassdoor", "googlejobs"}
    assert set(kb.FEEDS) <= set(hk.FEED_SOURCES) and "kit_bigboards" in hk.FEED_MODULES
    assert set(kb.FEEDS) <= set(hk.select_sources("default"))
    assert set(kb.FEEDS) <= set(hk._merged("FEEDS", hk.FEED_MODULES))
    assert "indeed" not in hk.ALL_SOURCES  # Indeed stays with the assistant
    hosts = dict(hk._FEED_HOSTS)
    for host, feed in (("www.linkedin.com", "linkedin"), ("linkedin.com", "linkedin"), ("www.glassdoor.com", "glassdoor"),
                       ("www.glassdoor.co.uk", "glassdoor"), ("de.glassdoor.ch", "glassdoor")):
        assert hosts[feed].search(host), host
    assert not any(rx.search("uk.indeed.com") for _, rx in hk._FEED_HOSTS)
    assert not any(rx.search("careers.globex.example") for _, rx in hk._FEED_HOSTS)


def test_core_http_once_sends_exactly_once(monkeypatch):
    import io
    import urllib.error

    from src.hunt_kit import hunt_kit as core

    calls: list = []

    def fake_open(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.com/x", code, "x", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(core, "_open", fake_open)
    monkeypatch.setattr(core, "check_url", lambda u: True)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    for code in (429, 503):
        calls.clear()
        assert core.http("https://example.com/x", once=True) == (code, None)
        assert len(calls) == 1
        calls.clear()
        core.http("https://example.com/x")  # the default is unchanged: 429 and 5xx are still retried
        assert len(calls) > 1


def test_module_text_keeps_the_rules():
    text = (_KIT / "kit_bigboards.py").read_text(encoding="utf-8")
    code = text.split('"""', 2)[2]  # everything after the module docstring
    assert not re.search(r"(?m)^\s*(import|from)\s+(jobspy|requests|curl_cffi|bs4)\b", text)
    assert not re.search(r"(?i)cookie|proxies|api[_-]?key", code)  # no cookies, no proxy, no copied key
    assert "indeed" not in code.lower()  # Indeed stays with the assistant
    for needle in ("jobspy/linkedin/__init__.py:", "jobspy/glassdoor/__init__.py:", "jobspy/google/__init__.py:",
                   "NOT IN REFERENCE"):
        assert needle in text



def test_linkedin_run_budget_is_eight_requests_eight_seconds_apart(monkeypatch):
    # Owner's PC smoke test 2026-10-20: 429 on the 10th request at 5 s apart.
    monkeypatch.setattr(kb, "LI_MAX_REQUESTS", 8)
    monkeypatch.setattr(kb, "LI_PAUSE_S", 8.0)
    monkeypatch.setattr(kb, "PAUSE_S", 5.0)
    slept: list = []
    monkeypatch.setattr(kb.time, "sleep", slept.append)
    counter = itertools.count(5000)
    http = _http(_wrap200(_li_handler({o: li_page([next(counter) for _ in range(10)]) for o in (0, 10, 20)})))
    kb.fetch_linkedin(["a", "b", "c"], http, {}, days=BIG, countries=["GB"])
    assert len(http.calls) == 8 and set(slept) == {8.0} and len(slept) == 7


def test_google_js_challenge_page_is_reported_blocked_not_empty(capsys):
    # Owner's PC, 2026-10-20: Google answered 200 with its JavaScript challenge (SG_REL, /httpservice/retry/enablejs).
    page = '<html><script>var SG_REL=1;</script><a href="/httpservice/retry/enablejs?sei=x">retry</a></html>'
    http = _http(lambda url, obj=None: (200, page))
    jobs = kb.fetch_googlejobs(["a", "b"], http, {}, days=BIG, countries=["GB"])
    assert jobs == [] and len(http.calls) == 1
    assert "googlejobs stopped" in capsys.readouterr().err


def test_a_429_says_try_again_tomorrow(capsys):
    http = _http(lambda url, obj=None: (429, ""))
    kb.fetch_linkedin(["a"], http, {}, days=BIG, countries=["GB"])
    assert "try again tomorrow" in capsys.readouterr().err


# --------------------------------------------------------------------------- regex scaling (hostile markup)
def _grows_linearly(fn, make, n=6000):
    """Time fn(make(n)) and fn(make(4n)): linear stays within 8x (+50 ms of noise room), quadratic takes ~16x."""
    import time

    def run(k):
        s, best = make(k), 9e9
        for _ in range(2):
            t0 = time.perf_counter()
            fn(s)
            best = min(best, time.perf_counter() - t0)
        return best

    t1, t4 = run(n), run(4 * n)
    assert t4 < 8 * t1 + 0.05, (t1, t4)


def test_div_and_google_scans_are_linear_on_hostile_markup():
    assert kb._div_inner('<div class="a x b"><div>in</div>tail</div>', "x") == "<div>in</div>tail"
    _grows_linearly(lambda p: kb._div_inner(p, "x"), lambda k: '<div class="x">' + "<div " * k)
    _grows_linearly(kb.parse_google_first_page, lambda k: '520084652":[' * k + "]] }]]]]]")
    _grows_linearly(kb.parse_google_first_page, lambda k: '520084652":[1] ' * k)
