"""Offline tests for fetch_successfactors (fixtures built from career-ops' documented shapes)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

ROW = {"name": "ZF", "ats": "successfactors", "token": "https://jobs.zf.com/search/"}


def _stub(routes):
    calls = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append((url, method, data))
        for frag, resp in routes.items():
            if frag in url:
                r = resp(url, data) if callable(resp) else resp
                return r
        return 404, None

    http.calls = calls
    return http


def _tile(i, title, path, city=None):
    c = f'<div id="x-{i}-section-city-value">{city}</div>' if city else ""
    return (f'<li class="job-tile job-id-{i} x"><a class="jobTitle-link x" href="{path}">{title}</a>'
            f'<div data-url="{path}"></div>{c}</li>')


PAGE1 = _tile(1, "Software &amp; Data Engineer (m/w/d)", "/job/Friedrichshafen-Software-Data-Engineer-m-w-d/1001/")  \
    + _tile(2, "Buyer", "/job/Buyer/1002/", city="Passau") + _tile(2, "Buyer", "/job/Buyer/1002/")


def test_tiles_paginate_and_dedup():
    def tile(url, data):
        return (200, PAGE1) if "startrow=0" in url else (200, "<html></html>")
    http = _stub({"tile-search-results": tile})
    stats = {}
    jobs = kit_ats.fetch_successfactors(ROW, http, stats)
    assert [j["title"] for j in jobs] == ["Software & Data Engineer (m/w/d)", "Buyer"]
    assert jobs[0]["location"] == "Friedrichshafen"
    assert jobs[1]["location"] == "Passau"
    assert jobs[0]["url"] == "https://jobs.zf.com/job/Friedrichshafen-Software-Data-Engineer-m-w-d/1001/"
    assert jobs[0]["source"] == "successfactors" and jobs[0]["company"] == "ZF" and jobs[0]["posted"] == ""
    assert http.calls[0][0] == "https://jobs.zf.com/tile-search-results/?startrow=0"
    assert http.calls[1][0].endswith("startrow=3")  # advances by tiles returned (incl. dup copy)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_tiles_stop_when_server_ignores_offset():
    http = _stub({"tile-search-results": (200, PAGE1)})
    jobs = kit_ats.fetch_successfactors(ROW, http, {})
    assert len(jobs) == 2 and len(http.calls) == 2


def test_csb_fallback_with_locale_discovery_and_dates():
    search = '<a href="/search/?q=&amp;startrow=0&amp;locale=en_US">EN</a><a href="?locale=de_DE">DE</a>'

    def api(url, data):
        body = json.loads(data)
        assert body["pageNumber"] == 0 and body["sortBy"] == "date"
        if body["locale"] == "de_DE":
            return 200, {"totalJobs": 1, "jobSearchResult": [{"response": {
                "id": 77, "unifiedStandardTitle": "Ingenieur", "unifiedUrlTitle": "Ingenieur-&amp;-Co",
                "jobLocationShort": ["Ulm, DEU, 89073<br/>", "Ulm, DEU, 89073<br/>"],
                "unifiedStandardStart": "20.11.23"}}]}
        return 200, {"totalJobs": 0, "jobSearchResult": []}
    http = _stub({"tile-search-results": (200, "<!DOCTYPE html>"), "/search/": (200, search),
                  "services/recruiting/v1/jobs": api})
    jobs = kit_ats.fetch_successfactors(ROW, http, {})
    assert len(jobs) == 1
    j = jobs[0]
    assert j["url"] == "https://jobs.zf.com/job/Ingenieur-Co/77-de_DE"
    assert j["location"] == "Ulm, DEU, 89073" and j["posted"] == "2023-11-20"
    posts = [c for c in http.calls if c[1] == "POST"]
    assert json.loads(posts[0][2])["locale"] == "de_DE"  # priority order


def test_csb_variant_skips_tiles_and_paginates():
    def api(url, data):
        p = json.loads(data)["pageNumber"]
        n = 10 if p == 0 else 2
        return 200, {"totalJobs": 12, "jobSearchResult": [{"response": {
            "id": p * 100 + i + 1, "unifiedStandardTitle": f"T{p}-{i}", "unifiedStandardStart": "6/18/26"}}
            for i in range(n)]}
    row = dict(ROW, variant="csb", locales=["en_US"], token="https://jobs.zf.com/Brand/go/All/123/25/")
    http = _stub({"services/recruiting/v1/jobs": api})
    jobs = kit_ats.fetch_successfactors(row, http, {})
    assert len(jobs) == 12 and jobs[0]["posted"] == "2026-06-18"
    assert not any("tile-search" in c[0] for c in http.calls)
    assert http.calls[0][0] == "https://jobs.zf.com/Brand/services/recruiting/v1/jobs"


def test_csb_date_variants():
    d = kit_ats._sf_csb_date
    assert d("6/18/26", "en_US") == "2026-06-18"
    assert d("05/10/2026", "fr_FR") == "2026-10-05"
    assert d("2025. 07. 03.", "hu_HU") == "2025-07-03"
    assert d("31.02.24") == "" and d("soon") == "" and d(None) == ""


def test_failures_never_raise():
    stats = {}
    assert kit_ats.fetch_successfactors(ROW, _stub({}), stats) == []
    assert stats["tried"] == 1 and stats.get("ok", 0) == 0
    assert kit_ats.fetch_successfactors(dict(ROW, token=""), _stub({}), {}) == []

    def boom(*a, **k):
        raise RuntimeError("x")
    assert kit_ats.fetch_successfactors(ROW, boom, {}) == []


def test_registered():
    assert kit_ats.ATS_FETCHERS["successfactors"] is kit_ats.fetch_successfactors


def test_rejects_unsafe_token_and_locale():
    for t in ["https://evil.com@jobs.zf.com", "https://a b.com", "https://jobs.zf.com/../x", "ftp://x.com"]:
        assert kit_ats._sf_config({"token": t}) is None
    calls = []

    def http(url, **kw):
        calls.append((url, kw.get("data")))
        return 200, {"totalJobs": 0, "jobSearchResult": []}
    row = {"name": "x", "token": "https://a.com", "variant": "csb", "locales": ["x/../y?", "en_US"]}
    kit_ats.fetch_successfactors(row, http, {})
    assert len(calls) == 1 and b'en_US' in calls[0][1]
