"""Offline tests for src/hunt_kit/kit_startups.py (Wellfound, Work at a Startup, Getro, Built In, WTTJ).

Fixtures are shaped like the references' own test data and nothing was checked against a live response (the build
container reaches none of these hosts): Wellfound from freehire wellfound_test.go:55-106 (Apollo cache) and the
role page shape in wellfound.go; Work at a Startup from freehire workatastartup.go:52-68 (Algolia hits); Getro from
freehire getro_test.go:60-70 (listing JSON) and career-ops getro.test.mjs (__NEXT_DATA__ network.id); Built In from
career-ops tests/providers/builtin.test.mjs:16-18 and :274-293 (ItemList + cards); WTTJ from career-ops wttj.mjs:78-
160 (the /api/env payload and Algolia hits).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_startups_under_test", _KIT / "kit_startups.py")
ks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ks)  # type: ignore[union-attr]

JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}
BIG = 36500
NOW = int(time.time())


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(ks, "PAUSE_S", 0)


def _http(handler):
    """Fake http: handler(url, data_obj) -> (status, body). Records every call."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=None):
        obj = json.loads(data) if data else None
        calls.append({"url": url, "method": method, "data": obj, "headers": headers or {},
                      "as_json": as_json, "once": once})
        return handler(url, obj)

    http.calls = calls
    return http


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["url"].startswith("https://") and j["extra"]["attribution"]
        assert j["company"].lower() not in ("wellfound", "getro", "built in", "welcome to the jungle")
        assert ("title_only" in j["extra"]) == (not j["description"])


def _run(fn, http, queries=("engineer",), countries=("US",), days=BIG):
    stats: dict = {}
    jobs = fn(list(queries), http, stats, days=days, countries=list(countries))
    return jobs, stats


# ------------------------------------------------------------------------------------------ Wellfound
def _wf_page(data: dict | str, raw_script=None) -> str:
    body = raw_script if raw_script is not None else json.dumps(
        {"props": {"pageProps": {"apolloState": {"data": data}}}})
    return f'<!DOCTYPE html><html><body><script id="__NEXT_DATA__" type="application/json">{body}</script></body></html>'


def _wf_data(page_count=1):
    return {
        "ROOT_QUERY": {"talent": {'seoLandingPageJobSearchResults({"page":1,"role":"software-engineer"})': {
            "pageCount": page_count, "totalJobCount": 4}}},
        "StartupResult:1": {"name": "Acme Inc", "highlightedJobListings": [{"__ref": "JobListingSearchResult:100"}]},
        "StartupResult:2": {"name": "Widget Co", "highlightedJobListings": [{"__ref": "JobListingSearchResult:101"}]},
        "JobListingSearchResult:100": {
            "id": "100", "slug": "senior-backend-engineer", "title": "Senior Backend Engineer",
            "description": "**Build** things. See [docs](https://x.example).", "compensation": "$150k - $180k",
            "remote": True, "locationNames": ["United States", "Canada"]},
        "JobListingSearchResult:101": {
            "id": "101", "slug": "product-designer", "title": "Product Designer", "description": "Design.",
            "compensation": "", "remote": False, "locationNames": ["Berlin"]},
        "JobListingSearchResult:103": {  # no startup references it: the employer is unknown, so it is dropped
            "id": "103", "slug": "orphan", "title": "Orphan Role", "description": "x", "locationNames": []},
        "JobListingSearchResult:104": "not an object",
    }


def test_wellfound_happy_path():
    http = _http(lambda url, d: (200, _wf_page(_wf_data(1))))
    jobs, stats = _run(ks.fetch_wellfound, http, queries=["Software Engineer"])
    _check(jobs, "wellfound")
    by = {j["extra"]["id"]: j for j in jobs}
    assert set(by) == {"100", "101"}
    assert by["100"]["company"] == "Acme Inc" and by["101"]["company"] == "Widget Co"
    assert by["100"]["url"] == "https://wellfound.com/jobs/100-senior-backend-engineer"
    assert by["100"]["location"] == "United States; Canada" and by["100"]["remote"] is True
    assert by["100"]["description"] == "Build things. See docs." and by["100"]["extra"]["compensation"] == "$150k - $180k"
    assert http.calls[0]["url"] == "https://wellfound.com/role/r/software-engineer?page=1"
    assert http.calls[0]["once"] is True and stats["jobs"] == 2


def test_wellfound_pages_follow_stated_page_count_and_cap():
    http = _http(lambda url, d: (200, _wf_page(_wf_data(9))))
    _run(ks.fetch_wellfound, http, queries=["a", "b", "c", "d"])
    assert len(http.calls) == ks.MAX_SEARCHES * ks.PAGES  # 3 slugs x 2 pages, never the stated 9
    assert [c["url"].rsplit("page=", 1)[1] for c in http.calls] == ["1", "2"] * 3


def test_wellfound_single_page_when_count_is_one():
    http = _http(lambda url, d: (200, _wf_page(_wf_data(1))))
    _run(ks.fetch_wellfound, http, queries=["a", "b"])
    assert len(http.calls) == 2  # one page per slug


def test_wellfound_empty_and_non_200_and_challenge_stop_the_site():
    http = _http(lambda url, d: (200, _wf_page({})))
    jobs, _ = _run(ks.fetch_wellfound, http, queries=["a", "b"])
    assert jobs == [] and len(http.calls) == 1  # no apollo data: the site stops
    http = _http(lambda url, d: (403, None))
    jobs, _ = _run(ks.fetch_wellfound, http, queries=["a", "b"])
    assert jobs == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, "<html><title>Just a moment...</title></html>"))
    jobs, _ = _run(ks.fetch_wellfound, http, queries=["a", "b"])
    assert jobs == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, _wf_page(None, raw_script="{not json")))
    assert _run(ks.fetch_wellfound, http)[0] == [] and len(http.calls) == 1


def test_wellfound_no_total_stops_the_site():
    data = _wf_data()
    data["ROOT_QUERY"] = {"talent": {}}
    http = _http(lambda url, d: (200, _wf_page(data)))
    assert _run(ks.fetch_wellfound, http)[0] == [] and len(http.calls) == 1


def test_wellfound_no_countries_or_queries_send_nothing():
    http = _http(lambda url, d: (200, _wf_page(_wf_data())))
    assert _run(ks.fetch_wellfound, http, countries=[])[0] == []
    assert _run(ks.fetch_wellfound, http, queries=[])[0] == []
    assert _run(ks.fetch_wellfound, http, queries=["!!!"])[0] == []
    assert http.calls == []


# ------------------------------------------------------------------------------- Work at a Startup
def _waas_hit(i, **kw):
    h = {"id": i, "title": f"Engineer {i}", "description": "## Role\n**Do** things", "remote": "yes",
         "created_at": "2026-10-01T10:00:00Z", "company_name": f"Startup {i}",
         "locations_for_search": [[[["Remote"]]], "San Francisco, CA, US"],
         "search_path": f"https://www.workatastartup.com/jobs/{i}"}
    h.update(kw)
    return h


def test_waas_without_key_sends_no_request(monkeypatch):
    monkeypatch.delenv("WAAS_ALGOLIA_KEY", raising=False)
    http = _http(lambda url, d: (200, {"hits": [_waas_hit(1)]}))
    assert _run(ks.fetch_workatastartup, http)[0] == [] and http.calls == []


def test_waas_happy_path(monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "user-supplied-key")
    http = _http(lambda url, d: (200, {"hits": [
        _waas_hit(11), _waas_hit(12, remote="no", search_path=""), _waas_hit(0), _waas_hit(13, company_name=""),
        "junk"]}))
    jobs, stats = _run(ks.fetch_workatastartup, http, queries=["engineer"])
    _check(jobs, "workatastartup")
    assert [j["extra"]["id"] for j in jobs] == ["11", "12"]
    assert jobs[0]["company"] == "Startup 11" and jobs[0]["remote"] is True and jobs[1]["remote"] is False
    assert jobs[1]["url"] == "https://www.workatastartup.com/jobs/12"  # search_path missing: the reference's fallback
    assert jobs[0]["location"] == "San Francisco, CA, US" and jobs[0]["posted"] == "2026-10-01"
    assert jobs[0]["description"] == "Role Do things"
    c = http.calls[0]
    assert c["url"] == "https://45BWZJ1SGC-dsn.algolia.net/1/indexes/WaaSPublicCompanyJob_production/query"
    assert c["method"] == "POST" and c["headers"]["X-Algolia-API-Key"] == "user-supplied-key"
    assert c["headers"]["X-Algolia-Application-Id"] == "45BWZJ1SGC"
    p = parse_qs(c["data"]["params"])
    assert p["distinct"] == ["false"] and p["query"] == ["engineer"] and stats["jobs"] == 2


def test_waas_second_page_only_when_first_is_full(monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    full = [_waas_hit(i) for i in range(1, ks.WAAS_PER_PAGE + 1)]
    http = _http(lambda url, d: (200, {"hits": full}))
    _run(ks.fetch_workatastartup, http, queries=["a", "b", "c", "d"])
    assert len(http.calls) == ks.MAX_SEARCHES * ks.PAGES


def test_waas_empty_non_200_and_no_countries(monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    http = _http(lambda url, d: (200, {"hits": []}))
    assert _run(ks.fetch_workatastartup, http)[0] == [] and len(http.calls) == 1
    http = _http(lambda url, d: (403, None))
    assert _run(ks.fetch_workatastartup, http, queries=["a", "b"])[0] == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, {"hits": [_waas_hit(1)]}))
    assert _run(ks.fetch_workatastartup, http, countries=[])[0] == [] and http.calls == []


# ------------------------------------------------------------------------------------------- Getro
def _getro_job(i, **kw):
    j = {"id": i, "slug": f"{i}-role", "title": f"Backend Engineer {i}", "url": f"https://boards.greenhouse.io/acme/jobs/{i}",
         "work_mode": "remote", "locations": ["Berlin, Germany"], "created_at": NOW - 3600,
         "organization": {"name": f"Portfolio {i}", "slug": f"p-{i}"},
         "compensation_amount_min_cents": 6000000, "compensation_amount_max_cents": 8000000,
         "compensation_currency": "EUR", "compensation_period": "year"}
    j.update(kw)
    return j


def _getro_page(path_jobs, count=None):
    return {"results": {"jobs": path_jobs, "count": len(path_jobs) if count is None else count}}


def _board_page(nid=9911):
    return ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"network": {"id": nid}}}}) + "</script></html>")


def test_getro_happy_path_known_id_sends_referer_and_query():
    http = _http(lambda url, d: (200, _getro_page([
        _getro_job(1), _getro_job(2, work_mode="on_site", locations=[], searchable_locations=["Zurich"]),
        _getro_job(3, url=""), _getro_job(4, organization={"name": ""}), "junk"])))
    jobs, stats = _run(ks.fetch_getro, http, queries=["backend"])
    _check(jobs, "getro")
    assert [j["company"] for j in jobs[:2]] == ["Portfolio 1", "Portfolio 2"]  # the portfolio company, not the fund
    assert jobs[0]["location"] == "Berlin, Germany, Remote" and jobs[0]["remote"] is True
    assert jobs[1]["location"] == "Zurich" and jobs[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert jobs[0]["extra"]["salary_min"] == 60000 and jobs[0]["extra"]["salary_currency"] == "EUR"
    first = http.calls[0]
    assert first["url"] == "https://api.getro.com/api/v2/collections/4283/search/jobs"  # b2venture, no id lookup
    assert first["headers"]["referer"] == "https://jobs.b2venture.vc/"
    assert first["data"] == {"hitsPerPage": 20, "page": 0, "filters": {"page": 0}, "query": "backend"}
    assert stats["jobs"] == len(jobs)


def test_getro_resolves_id_from_the_board_page(monkeypatch):
    monkeypatch.setattr(ks, "GETRO_NETWORKS", [("Atomico", None, "https://careers.atomico.com/jobs")])

    def handler(url, d):
        if url == "https://careers.atomico.com/jobs":
            return 200, _board_page(9911)
        return 200, _getro_page([_getro_job(7)])

    http = _http(handler)
    jobs, _ = _run(ks.fetch_getro, http, queries=["x"])
    assert len(jobs) == 1
    assert http.calls[0]["url"] == "https://careers.atomico.com/jobs" and http.calls[0]["as_json"] is False
    assert http.calls[1]["url"].endswith("/collections/9911/search/jobs")
    assert http.calls[1]["headers"]["referer"] == "https://careers.atomico.com/"


def test_getro_page_without_network_id_skips_that_network(monkeypatch):
    monkeypatch.setattr(ks, "GETRO_NETWORKS", [("Atomico", None, "https://careers.atomico.com/jobs")])
    http = _http(lambda url, d: (200, "<html>nothing</html>"))
    assert _run(ks.fetch_getro, http)[0] == [] and len(http.calls) == 1


def test_getro_challenge_page_without_id_stops_the_site(monkeypatch):
    monkeypatch.setattr(ks, "GETRO_NETWORKS", [("Atomico", None, "https://careers.atomico.com/jobs"),
                                               ("Other", None, "https://careers.other.com/jobs")])
    http = _http(lambda url, d: (200, "<html><title>Just a moment...</title></html>"))
    assert _run(ks.fetch_getro, http, queries=["a", "b"])[0] == [] and len(http.calls) == 1


def test_getro_extract_collection_id():
    assert ks.extract_collection_id(_board_page(9911)) == "9911"
    assert ks.extract_collection_id(_board_page("9911")) == "9911"
    for bad in (_board_page(0), _board_page("abc"), _board_page(True), "<html></html>", None, 5,
                '<script id="__NEXT_DATA__">{bad</script>'):
        assert ks.extract_collection_id(bad) is None


def test_getro_stops_at_the_age_bound_and_the_count(monkeypatch):
    monkeypatch.setattr(ks, "GETRO_NETWORKS", [("b2venture", 4283, "https://jobs.b2venture.vc/jobs")])
    old = NOW - 100 * 86400
    http = _http(lambda url, d: (200, _getro_page([_getro_job(1), _getro_job(2, created_at=old)], count=40)))
    jobs, _ = _run(ks.fetch_getro, http, queries=["x"], days=30)
    assert [j["extra"]["id"] for j in jobs] == ["1"]  # the old posting is filtered; page 2 never fetched
    assert len(http.calls) == 1
    http = _http(lambda url, d: (200, _getro_page([_getro_job(1)], count=1)))
    _run(ks.fetch_getro, http, queries=["x"])
    assert len(http.calls) == 1  # count reached after page 1


def test_getro_reads_two_pages_and_caps_searches():
    http = _http(lambda url, d: (200, _getro_page([_getro_job(d["page"] + 1)], count=999)))
    jobs, _ = _run(ks.fetch_getro, http, queries=["a", "b", "c", "d"])
    assert len(http.calls) <= ks.GETRO_MAX_REQUESTS
    posts = [c for c in http.calls if c["method"] == "POST"]
    assert len(posts) <= ks.MAX_SEARCHES * ks.PAGES and {c["data"]["page"] for c in posts} == {0, 1}
    assert len({c["url"] for c in http.calls if c["method"] == "GET"}) <= ks.MAX_SEARCHES


def test_getro_empty_non_200_and_no_countries():
    http = _http(lambda url, d: (200, _getro_page([])))
    assert _run(ks.fetch_getro, http, queries=["a"])[0] == []
    assert [c["method"] for c in http.calls] == ["POST", "POST", "GET"]  # b2venture, Cherry, Atomico's id lookup
    http = _http(lambda url, d: (406, None))
    assert _run(ks.fetch_getro, http, queries=["a", "b"])[0] == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, _getro_page([_getro_job(1)])))
    assert _run(ks.fetch_getro, http, countries=[])[0] == [] and http.calls == []


# ----------------------------------------------------------------------------------------- Built In
def _bi_item(name, url, desc=None):
    s = f'{{"@type":"ListItem","position":1,"name":{json.dumps(name)},"url":{json.dumps(url)}'
    if desc is not None:
        s += f',"description":{json.dumps(desc)}'
    return s + "}"


def _bi_card(id, title, company, posted, mode, location, tooltip=None, pay=None):
    slug = "/company/" + company.lower().replace(" ", "-")
    tip = f'aria-label="Job locations" data-bs-title="{tooltip}"' if tooltip else 'class="font-barlow"'
    sal = (f'<div><div><i class="fa-regular fa-sack-dollar fs-xs"></i></div> <span>{pay}</span></div>'
           if pay else "")
    return f"""
    <div><a href="{slug}" class="z-1"><span>{company}</span></a></div>
    <h2><a href="/job/x/{id}" data-id="job-card-title" data-builtin-track-job-id="{id}">{title}</a></h2>
    <div><i class="fa-regular fa-clock fs-xs"></i>{posted}</span></div>
    <div><div><i class="fa-regular fa-house-building fs-xs"></i></div> <span>{mode}</span></div>
    <div><div><i class="fa-regular fa-location-dot fs-xs"></i></div> <div><span {tip}>{location}</span></div></div>
    {sal}"""


def _bi_html(ids=(101, 102, 103, 104)):
    cards = {
        101: _bi_card(101, "Backend Engineer", "Acme Corp", "Yesterday", "Hybrid", "Seattle, WA, USA",
                      pay="170K-230K Annually"),
        102: _bi_card(102, "Platform Engineer", "Globex", "Reposted 3 Days Ago", "Remote", "United States",
                      pay="115K-130K Hourly"),
        103: _bi_card(103, "Staff Engineer", "Initech", "2 Hours Ago", "In-Office", "2 Locations",
                      tooltip="&lt;div&gt;Denver, CO, USA&lt;/div&gt;&lt;div&gt;Long Beach, CA, USA&lt;/div&gt;"),
        104: _bi_card(104, "Data Engineer", "Umbrella", "5 Days Ago", "Hybrid", "3 Locations"),
    }
    items = {
        101: _bi_item("Backend Engineer", "https://builtin.com/job/backend-engineer/101", "Build the API."),
        102: _bi_item("Platform Engineer", "/job/platform-engineer/102", "Run the platform."),
        103: _bi_item("Staff Engineer", "https://builtin.com/job/staff-engineer/103"),
        104: _bi_item("Data Engineer", "https://builtin.com/job/data-engineer/104", "Data."),
    }
    return ("<html><body>" + "".join(cards[i] for i in ids) + "<script>{\"itemListElement\":["
            + ",".join(items[i] for i in ids) + "]}</script></body></html>")


def test_builtin_happy_path():
    http = _http(lambda url, d: (200, _bi_html()))
    jobs, stats = _run(ks.fetch_builtin, http, queries=["platform engineer"])
    _check([j for j in jobs if j["description"]] or jobs, "builtin")
    by = {j["extra"]["id"]: j for j in jobs}
    assert set(by) == {"101", "102", "103", "104"}
    assert by["101"]["company"] == "Acme Corp" and by["101"]["location"] == "Hybrid · Seattle, WA, USA"
    assert by["101"]["extra"]["salary_min"] == 170000 and by["101"]["extra"]["salary_max"] == 230000
    assert by["102"]["url"] == "https://builtin.com/job/platform-engineer/102"  # relative url made absolute
    assert "salary_min" not in by["102"]["extra"] and by["102"]["remote"] is True  # Hourly band ignored
    assert by["103"]["location"] == "In-Office · Denver, CO, USA · Long Beach, CA, USA"
    assert by["104"]["location"] == "" and by["103"]["extra"]["title_only"] is True  # no excerpt in the ItemList
    assert by["101"]["posted"] == (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert by["101"]["description"] == "Build the API."
    c = http.calls[0]
    assert c["url"] == "https://builtin.com/jobs?search=platform%20engineer&page=1" and c["once"] is True
    assert stats["jobs"] == 4


def test_builtin_row_without_card_company_is_dropped():
    html = _bi_html((101,)).replace("</script></body>", ',' + _bi_item("Orphan", "https://builtin.com/job/o/999")
                                    + "</script></body>")
    http = _http(lambda url, d: (200, html))
    jobs, _ = _run(ks.fetch_builtin, http)
    assert [j["extra"]["id"] for j in jobs] == ["101"]


def test_builtin_stops_when_nothing_new_and_caps_pages():
    http = _http(lambda url, d: (200, _bi_html()))
    _run(ks.fetch_builtin, http, queries=["a", "b", "c", "d"])
    # query a: page 1 new, page 2 repeats it (stop); b and c: page 1 repeats it (stop); a 4th query is never sent
    assert len(http.calls) == 4


def test_builtin_second_page_when_new_rows():
    pages = {1: _bi_html((101, 102)), 2: _bi_html((103, 104))}
    http = _http(lambda url, d: (200, pages[int(url.rsplit("page=", 1)[1])]))
    jobs, _ = _run(ks.fetch_builtin, http, queries=["a"])
    assert len(jobs) == 4 and len(http.calls) == ks.PAGES


def test_builtin_countries_gate():
    http = _http(lambda url, d: (200, _bi_html()))
    for c in ([], ["GB"], ["DE", "IN"]):
        assert _run(ks.fetch_builtin, http, countries=c)[0] == []
    assert http.calls == []
    assert _run(ks.fetch_builtin, http, countries=["us"])[0] and _run(ks.fetch_builtin, http, countries=["*"])[0]


def test_builtin_empty_non_200_and_challenge():
    http = _http(lambda url, d: (200, "<html><body>no results</body></html>"))
    assert _run(ks.fetch_builtin, http, queries=["a", "b"])[0] == [] and len(http.calls) == 2
    http = _http(lambda url, d: (429, None))
    assert _run(ks.fetch_builtin, http, queries=["a", "b"])[0] == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, "<html><title>Just a moment...</title></html>"))
    assert _run(ks.fetch_builtin, http, queries=["a", "b"])[0] == [] and len(http.calls) == 1


# ------------------------------------------------------------------------------------------- WTTJ
ENV_TEXT = 'window.env = ' + json.dumps({"PUBLIC_ALGOLIA_APPLICATION_ID": "AB12CD34",
                                         "PUBLIC_ALGOLIA_API_KEY_CLIENT": "0123456789abcdef0123456789abcdef"}) + ";"


def _wttj_hit(slug, org="acme", **kw):
    h = {"name": f"Data Engineer {slug}", "slug": slug, "organization": {"name": "Acme SAS", "slug": org},
         "offices": [{"city": "Paris", "country": "France"}], "remote": "fulltime",
         "published_at_timestamp": NOW - 86400, "salary_yearly_minimum": 55000, "salary_maximum": 70000,
         "salary_period": "yearly", "salary_currency": "eur"}
    h.update(kw)
    return h


def _wttj_handler(hits):
    def handler(url, d):
        if url == "https://www.welcometothejungle.com/api/env":
            return 200, ENV_TEXT
        return 200, {"hits": hits}
    return handler


def test_wttj_happy_path_filters_by_country():
    http = _http(_wttj_handler([_wttj_hit("de-1"), _wttj_hit("de-2", remote="no", salary_period="monthly"),
                                _wttj_hit("bad slug"), _wttj_hit("x", org=""), "junk",
                                _wttj_hit("y", organization={"slug": "z"})]))
    jobs, stats = _run(ks.fetch_wttj, http, queries=["data engineer"], countries=["FR", "gb"])
    _check(jobs, "wttj")
    assert [j["extra"]["id"] for j in jobs] == ["de-1", "de-2"]
    j = jobs[0]
    assert j["company"] == "Acme SAS" and j["url"] == "https://www.welcometothejungle.com/en/companies/acme/jobs/de-1"
    assert j["location"] == "Paris, France, Remote" and j["remote"] is True
    assert j["extra"]["salary_min"] == 55000 and j["extra"]["salary_max"] == 70000
    assert j["extra"]["salary_currency"] == "EUR" and jobs[1]["extra"]["salary_max"] == 55000
    assert j["posted"] == (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert http.calls[0]["url"] == "https://www.welcometothejungle.com/api/env"
    c = http.calls[1]
    assert c["url"] == "https://AB12CD34-dsn.algolia.net/1/indexes/wttj_jobs_production_en/query"
    assert c["headers"]["x-algolia-api-key"].startswith("0123") and c["headers"]["x-algolia-application-id"] == "AB12CD34"
    assert c["headers"]["referer"] == "https://www.welcometothejungle.com/"
    p = parse_qs(c["data"]["params"])
    assert p["filters"] == ["(offices.country_code:FR OR offices.country_code:GB)"]
    assert p["hitsPerPage"] == ["100"] and p["query"] == ["data engineer"] and stats["jobs"] == 2


def test_wttj_one_request_per_query_after_env_and_capped():
    http = _http(_wttj_handler([_wttj_hit("a-1")]))
    jobs, _ = _run(ks.fetch_wttj, http, queries=["a", "b", "c", "d"], countries=["FR"])
    assert len(http.calls) == 1 + ks.MAX_SEARCHES and len(jobs) == 1  # same url across queries: deduplicated
    p = parse_qs(http.calls[1]["data"]["params"])
    assert p["filters"] == ["offices.country_code:FR"]


def test_wttj_star_has_no_country_filter_and_needs_a_query():
    http = _http(_wttj_handler([_wttj_hit("a-1")]))
    assert _run(ks.fetch_wttj, http, queries=["dev"], countries=["*"])[0]
    assert "filters" not in parse_qs(http.calls[1]["data"]["params"])
    http = _http(_wttj_handler([_wttj_hit("a-1")]))
    assert _run(ks.fetch_wttj, http, queries=[], countries=["*"])[0] == [] and http.calls == []


def test_wttj_bad_countries_send_nothing_and_no_query_still_filters():
    http = _http(_wttj_handler([_wttj_hit("a-1")]))
    assert _run(ks.fetch_wttj, http, countries=[])[0] == []
    assert _run(ks.fetch_wttj, http, countries=["USA", "1"])[0] == [] and http.calls == []
    jobs, _ = _run(ks.fetch_wttj, http, queries=[], countries=["FR"])
    assert jobs and parse_qs(http.calls[1]["data"]["params"], keep_blank_values=True)["query"] == [""]


def test_wttj_env_failures_and_empty_and_bad_answer():
    http = _http(lambda url, d: (403, None))
    assert _run(ks.fetch_wttj, http, countries=["FR"])[0] == [] and len(http.calls) == 1
    http = _http(lambda url, d: (200, "window.env = {}"))
    assert _run(ks.fetch_wttj, http, countries=["FR"])[0] == [] and len(http.calls) == 1
    http = _http(_wttj_handler([]))
    assert _run(ks.fetch_wttj, http, queries=["a", "b"], countries=["FR"])[0] == []
    assert len(http.calls) == 3
    http = _http(lambda url, d: (200, ENV_TEXT) if url.endswith("/api/env") else (200, {"error": "x"}))
    assert _run(ks.fetch_wttj, http, queries=["a", "b"], countries=["FR"])[0] == [] and len(http.calls) == 2
    http = _http(lambda url, d: (200, ENV_TEXT) if url.endswith("/api/env") else (500, None))
    assert _run(ks.fetch_wttj, http, queries=["a", "b"], countries=["FR"])[0] == [] and len(http.calls) == 2


def test_parse_wttj_env_rejects_odd_payloads():
    assert ks.parse_wttj_env(ENV_TEXT) == ("AB12CD34", "0123456789abcdef0123456789abcdef")
    bad_app = ENV_TEXT.replace("AB12CD34", "x/../evil")
    for bad in (bad_app, "no json", "{bad}", None, 5, "[1]", ENV_TEXT.replace("0123456789abcdef0123456789abcdef", "s")):
        assert ks.parse_wttj_env(bad) is None


# ------------------------------------------------------------------------------- shared behaviour
def test_feeds_registry():
    assert set(ks.FEEDS) == {"wellfound", "workatastartup", "getro", "builtin", "wttj"}


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro", "builtin", "wttj"])
@pytest.mark.parametrize("garbage", [None, "", "<html>", "{}", [], 42, {"hits": "x"}, {"results": 5}, b"\xff"])
def test_never_raises_on_garbage(name, garbage, monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    http = _http(lambda url, d: (200, garbage))
    jobs, stats = _run(ks.FEEDS[name], http, queries=["x"], countries=["*"])
    assert isinstance(jobs, list) and stats["jobs"] == len(jobs)


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro", "builtin", "wttj"])
def test_never_raises_when_http_raises(name, monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")

    def boom(url, **kw):
        raise OSError("down")

    assert ks.FEEDS[name](["x"], boom, {}, days=30, countries=["US", "FR"]) == []


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro", "builtin", "wttj"])
def test_empty_countries_never_send_a_request(name, monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    http = _http(lambda url, d: (200, {}))
    assert _run(ks.FEEDS[name], http, countries=[])[0] == [] and http.calls == []


def test_pause_between_requests(monkeypatch):
    slept: list = []
    monkeypatch.setattr(ks, "PAUSE_S", 2.5)
    monkeypatch.setattr(ks.time, "sleep", lambda s: slept.append(s))
    http = _http(lambda url, d: (200, _wf_page(_wf_data(9))))
    _run(ks.fetch_wellfound, http, queries=["a"])
    assert len(http.calls) == 2 and slept == [2.5]  # none before the first request, one pause before the second


def test_getro_org_dict_without_name_falls_back_or_drops():
    base = {"url": "https://x.example/1", "title": "Dev"}
    jobs, _, _ = ks.parse_getro_jobs({"results": {"jobs": [
        {**base, "organization": {"name": None}, "organization_name": "Acme"},
        {**base, "organization": {}, "organization_name": "Beta"},
        {**base, "organization": {}},
        {**base, "organization": {"name": None}}]}})
    assert [j["company"] for j in jobs] == ["Acme", "Beta"]


def test_waas_full_raw_page_with_a_dropped_hit_still_reads_page_two(monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    full = [_waas_hit(i) for i in range(1, ks.WAAS_PER_PAGE)] + [_waas_hit(999, company_name="")]
    http = _http(lambda url, d: (200, {"hits": full}))
    _run(ks.fetch_workatastartup, http, queries=["a"])
    assert len(http.calls) == ks.PAGES  # 50 raw hits (one kept row fewer) is still a full page


def test_every_feed_is_a_selectable_default_source():
    sys.modules.pop("hunt_kit_startups_check", None)
    spec = importlib.util.spec_from_file_location("hunt_kit_startups_check", _KIT / "hunt_kit.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_startups_check"] = hk
    spec.loader.exec_module(hk)  # type: ignore[union-attr]
    assert set(ks.FEEDS) <= set(hk.FEED_SOURCES) and "kit_startups" in hk.FEED_MODULES
    assert set(ks.FEEDS) <= set(hk.select_sources("default"))  # workatastartup sends nothing without a key


def test_wttj_visa_star_next_to_real_countries_keeps_the_country_filter():
    http = _http(_wttj_handler([_wttj_hit("a-1")]))
    _run(ks.fetch_wttj, http, queries=["dev"], countries=["GB", "*"])  # the core's default (--visa-onsite)
    assert parse_qs(http.calls[1]["data"]["params"])["filters"] == ["offices.country_code:GB"]


def test_builtin_location_without_a_workplace_mode_has_no_leading_separator():
    page = ("<html><body>" + _bi_card(555, "Dev", "Acme", "2 Days Ago", "", "Seattle, WA, USA")
            + '<script>{"itemListElement":[' + _bi_item("Dev", "https://builtin.com/job/dev/555", "Dev.")
            + "]}</script></body></html>")
    jobs = ks.parse_builtin_page(page)
    assert jobs and jobs[0]["location"] == "Seattle, WA, USA"
