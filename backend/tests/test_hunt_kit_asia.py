"""Offline tests for src/hunt_kit/kit_asia.py (Glints and Kalibrr; fixtures only, no network).

Fixture origins: Kalibrr rows follow freehire's kalibrr.go:71-106 field names and testdata/kalibrr/indonesia_it.json
(first row's values); Glints answers follow career-ops providers/glints.mjs:38-67 and the searchJobsV3 response
shape in career-ops tests/providers/glints.test.mjs (:119-135 a full data.searchJobsV3 { jobsInPage [...],
hasMore: false } answer, :150 and :163 more; company name/brandName at :29 and :49). The hosts are unreachable from
the build container: nothing here was checked against a live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_asia_under_test", _KIT / "kit_asia.py")
ka = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ka)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(ka, "PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets (url, body_obj)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=None):
        obj = json.loads(data) if data else None
        calls.append({"url": url, "method": method, "body": obj, "headers": headers or {}, "once": once})
        for needle, resp in routes.items():
            if needle in url:
                return resp(url, obj) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["url"].startswith("https://") and j["extra"]["attribution"]
        assert ("title_only" in j["extra"]) == (not j["description"])


def _run(fn, http, countries, queries=("engineer",), days=BIG):
    stats: dict = {}
    return fn(list(queries), http, stats, days=days, countries=countries), stats


# ----------------------------------------------------------------------------------- Glints
def _glints_row(i, title="Backend Engineer", company="Gojek", **kw):
    row = {"id": f"id-{i}", "title": title, "company": {"name": company, "brandName": "Brand"},
           "city": {"name": "Jakarta Selatan"}, "country": {"code": "ID", "name": "Indonesia"},
           "createdAt": "2026-10-10T08:00:00.000Z"}
    row.update(kw)
    return row


def _glints_page(rows, more=False):
    return {"data": {"searchJobsV3": {"jobsInPage": rows, "expInfo": "x", "hasMore": more}}}


def test_glints_happy_path_request_and_job():
    http = _http({"glints.com/api/v2-alc/graphql": (200, _glints_page([
        _glints_row(1), {**_glints_row(2), "company": {"brandName": "Brand"}},
        {**_glints_row(3), "company": {}}, {"title": "no id", "company": {"name": "X"}}, "junk"]))})
    jobs, stats = _run(ka.fetch_glints, http, ["ID"])
    _check(jobs, "glints")
    assert [j["company"] for j in jobs] == ["Gojek", "Brand"]  # no employer at all (row 3) is dropped
    j = jobs[0]
    assert j["company"] == "Gojek" and j["url"] == "https://glints.com/id/opportunities/jobs/id-1"
    assert j["location"] == "Jakarta Selatan, Indonesia" and j["posted"] == "2026-10-10"
    call = http.calls[0]
    assert call["method"] == "POST" and call["once"] is True
    assert call["headers"]["origin"] == "https://glints.com" and "Chrome" in call["headers"]["user-agent"]
    v = call["body"]["variables"]["data"]
    assert call["body"]["operationName"] == "searchJobsV3"
    assert v["SearchTerm"] == "engineer" and v["CountryCode"] == "ID" and v["page"] == 1
    assert v["includeExternalJobs"] is True and stats["jobs"] == 2


def test_glints_brand_name_fallback_and_paging_stops_when_no_more():
    rows = [_glints_row(i, company="") for i in range(ka.GLINTS_PAGE)]
    for r in rows:
        r["company"] = {"brandName": "Acme"}
    http = _http({"graphql": (200, _glints_page(rows, more=True))})
    jobs, _ = _run(ka.fetch_glints, http, ["SG"])
    assert len(http.calls) == ka.GLINTS_PAGES and {j["company"] for j in jobs} == {"Acme"}
    assert http.calls[0]["body"]["variables"]["data"]["CountryCode"] == "SG"
    http2 = _http({"graphql": (200, _glints_page(rows, more=False))})
    _run(ka.fetch_glints, http2, ["SG"])
    assert len(http2.calls) == 1


def test_glints_empty_result():
    jobs, stats = _run(ka.fetch_glints, _http({"graphql": (200, _glints_page([]))}), ["ID"])
    assert jobs == [] and stats["ok"] == 1


def test_glints_non_200_stops_the_site():
    http = _http({"graphql": (403, None)})
    jobs, stats = _run(ka.fetch_glints, http, ["ID", "SG", "MY"], queries=("a", "b"))
    assert jobs == [] and len(http.calls) == 1 and stats["tried"] == 1


def test_glints_countries_gating():
    http = _http({"graphql": (200, _glints_page([_glints_row(1)]))})
    for cs in ([], ["GB"], ["TW"], ["us", "de"]):  # TW is NOT IN REFERENCE
        assert _run(ka.fetch_glints, http, cs)[0] == []
    assert http.calls == []
    _run(ka.fetch_glints, http, ["id", "vn"])
    assert {c["body"]["variables"]["data"]["CountryCode"] for c in http.calls} == {"ID", "VN"}
    http.calls.clear()
    _run(ka.fetch_glints, http, ["*"])
    assert {c["body"]["variables"]["data"]["CountryCode"] for c in http.calls} == set(ka.GLINTS_MARKETS)


def test_glints_pages_on_the_raw_page_size_not_the_parsed_count():
    rows = [_glints_row(i) for i in range(ka.GLINTS_PAGE - 5)] + [{**_glints_row(i), "company": {}}
                                                                  for i in range(100, 105)]
    http = _http({"graphql": (200, _glints_page(rows, more=True))})
    _run(ka.fetch_glints, http, ["ID"])
    assert len(http.calls) == ka.GLINTS_PAGES  # 30 rows came back (5 without an employer): there is a page 2


def test_wanted_puts_the_users_own_markets_first():
    assert ka._wanted(["VN", "*"], *ka.GLINTS_MARKETS) == ["VN", "ID", "SG", "MY"]
    assert ka._wanted(["my", "vn"], *ka.GLINTS_MARKETS) == ["MY", "VN"]
    assert ka._wanted(["VN", "GB"], "ID", "SG") == []


def test_glints_star_spreads_the_budget_so_the_users_market_is_read():
    http = _http({"graphql": (200, _glints_page([_glints_row(i) for i in range(ka.GLINTS_PAGE)], more=True))})
    _run(ka.fetch_glints, http, ["VN", "*"], queries=("a", "b", "c", "d", "e"))
    seen = [(c["body"]["variables"]["data"]["CountryCode"], c["body"]["variables"]["data"]["page"])
            for c in http.calls]
    assert seen[0] == ("VN", 1) and len(seen) == ka.ASIA_MAX_REQUESTS
    first = [i for i, (_, p) in enumerate(seen) if p == 1]
    assert {cc for cc, p in seen if p == 1} == set(ka.GLINTS_MARKETS)  # every market read before any page 2
    assert first == list(range(len(first))) and len(first) == 5 * len(ka.GLINTS_MARKETS)


def test_glints_days_window_applies_locally():
    old = _glints_row(1, createdAt="2020-01-01T00:00:00Z")
    new = _glints_row(2, createdAt=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    jobs, _ = _run(ka.fetch_glints, _http({"graphql": (200, _glints_page([old, new]))}), ["ID"], days=7)
    assert [j["extra"]["id"] for j in jobs] == ["id-2"]


# ----------------------------------------------------------------------------------- Kalibrr
def _kal_row(i=23001, country="Indonesia", **kw):
    row = {"id": i, "name": "IT  Engineer", "slug": "it-engineer", "function": "IT and Software",
           "company_name": "PT Bank Central Asia Tbk", "company": {"code": "bank-central-asia-1", "name": "BCA"},
           "description": "<p>Join the IT team</p>", "qualifications": "<p>5 years</p>",
           "activation_date": "2026-09-17T08:27:25.188443+00:00", "is_work_from_home": False,
           "apply_redirect_url": "https://karir.bca.co.id/candidate/id/career/it-engineer",
           "google_location": {"address_components": {"city": "Jakarta", "region": "DKI Jakarta",
                                                       "country": country}}}
    row.update(kw)
    return row


def test_kalibrr_happy_path():
    http = _http({"kalibrr.com/kjs/job_board/search": (200, {"count": 2, "jobs": [
        _kal_row(1), _kal_row(2, is_work_from_home=True, apply_redirect_url="https://bit.ly/x")]})})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["ID"])
    _check(jobs, "kalibrr")
    j = jobs[0]
    assert j["title"] == "IT Engineer" and j["company"] == "PT Bank Central Asia Tbk"
    assert j["url"] == "https://www.kalibrr.com/c/bank-central-asia-1/jobs/1/it-engineer"
    assert j["location"] == "Jakarta, DKI Jakarta, Indonesia" and j["posted"] == "2026-09-17"
    assert "Join the IT team" in j["description"] and "5 years" in j["description"]
    assert j["extra"]["apply_url"].startswith("https://karir.bca.co.id/") and j["remote"] is None
    assert "apply_url" not in jobs[1]["extra"] and jobs[1]["remote"] is True
    q = parse_qs(urlsplit(http.calls[0]["url"]).query)
    assert q["country"] == ["Indonesia"] and q["text"] == ["engineer"] and q["offset"] == ["0"]
    assert "function" not in q  # NOT IN REFERENCE to combine; the kit does not pin a job function


def test_kalibrr_drops_other_countries_unsafe_segments_and_missing_employer():
    rows = [_kal_row(1, country="Thailand"), _kal_row(2, slug="bad/slug"), _kal_row(3, company_name="",
            company={"code": "c", "name": ""}), _kal_row(4), "junk", {"id": "x"}]
    jobs, _ = _run(ka.fetch_kalibrr, _http({"job_board/search": (200, {"count": 6, "jobs": rows})}), ["ID"])
    assert [j["extra"]["id"] for j in jobs] == [4]


def test_kalibrr_pages_by_offset_and_dedups_repeated_rows():
    full = {"count": 150, "jobs": [_kal_row(i) for i in range(1, ka.KALIBRR_LIMIT + 1)]}
    http = _http({"job_board/search": (200, full)})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["ID"])
    assert len(http.calls) == 2 and parse_qs(urlsplit(http.calls[1]["url"]).query)["offset"] == ["100"]
    assert len(jobs) == ka.KALIBRR_LIMIT  # the repeated second page is deduplicated


def test_kalibrr_empty_non_200_and_gating():
    assert _run(ka.fetch_kalibrr, _http({"search": (200, {"count": 0, "jobs": []})}), ["PH"])[0] == []
    http = _http({"search": (429, None)})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["PH", "ID"], queries=("a", "b"))
    assert jobs == [] and len(http.calls) == 1
    http = _http({"search": (200, {"count": 0, "jobs": []})})
    for cs in ([], ["SG"], ["VN"]):
        assert _run(ka.fetch_kalibrr, http, cs)[0] == []
    assert http.calls == []
    _run(ka.fetch_kalibrr, http, ["*"])
    assert {parse_qs(urlsplit(c["url"]).query)["country"][0] for c in http.calls} == {"Philippines", "Indonesia"}


# ----------------------------------------------------------------------------- shared contract
GARBAGE = [None, 123, "<<<not json>>>", [], {}, {"hits": "x"}, {"jobs": [None, 5, "x"]}, {"data": []},
           {"data": {"searchJobsV3": {"jobsInPage": [None, 1]}}}]


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
@pytest.mark.parametrize("body", GARBAGE)
def test_feeds_never_raise_on_garbage(name, body):
    http = _http({"": (200, body)})
    stats: dict = {}
    out = ka.FEEDS[name](["engineer"], http, stats, days=30, countries=["*"])
    assert out == [] and stats["jobs"] == 0


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
def test_feeds_never_raise_when_http_raises_or_status_zero(name):
    def boom(url, **kw):
        raise OSError("down")

    assert ka.FEEDS[name](["engineer"], boom, {}, days=30, countries=["*"]) == []
    http = _http({"": (0, None)})
    assert ka.FEEDS[name](["engineer"], http, {}, days=30, countries=["*"]) == [] and len(http.calls) == 1


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
def test_feeds_respect_request_cap_and_empty_queries(name, monkeypatch):
    monkeypatch.setattr(ka, "ASIA_MAX_REQUESTS", 3)
    http = _http({"glints": (200, _glints_page([_glints_row(1)], more=True)),
                  "kalibrr": (200, {"count": 9999, "jobs": [_kal_row(i) for i in range(1, 101)]})})
    ka.FEEDS[name](["a", "b", "c", "d"], http, {}, days=30, countries=["*"])
    assert len(http.calls) <= 3


def test_feed_names_are_the_dict_keys():
    assert set(ka.FEEDS) == {"glints", "kalibrr"}
