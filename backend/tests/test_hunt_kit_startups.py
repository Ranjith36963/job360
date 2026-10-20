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


# ------------------------------------------------------------------------------- shared behaviour
def test_feeds_registry():
    assert set(ks.FEEDS) == {"wellfound", "workatastartup", "getro"}


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro"])
@pytest.mark.parametrize("garbage", [None, "", "<html>", "{}", [], 42, {"hits": "x"}, {"results": 5}, b"\xff"])
def test_never_raises_on_garbage(name, garbage, monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")
    http = _http(lambda url, d: (200, garbage))
    jobs, stats = _run(ks.FEEDS[name], http, queries=["x"], countries=["*"])
    assert isinstance(jobs, list) and stats["jobs"] == len(jobs)


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro"])
def test_never_raises_when_http_raises(name, monkeypatch):
    monkeypatch.setenv("WAAS_ALGOLIA_KEY", "k")

    def boom(url, **kw):
        raise OSError("down")

    assert ks.FEEDS[name](["x"], boom, {}, days=30, countries=["US", "FR"]) == []


@pytest.mark.parametrize("name", ["wellfound", "workatastartup", "getro"])
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
