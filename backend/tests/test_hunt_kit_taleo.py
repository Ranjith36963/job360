"""Offline tests for fetch_taleo (fixtures built from career-ops providers/taleo.mjs's documented shapes)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import quote

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

ROW = {"name": "Acme", "ats": "taleo", "tenant": "acme", "section": "2"}

SHELL = """<html><script>var x={"portalId":"101430233"};</script>
<table id="jobs"><thead><tr><th>Icons</th><th>Requisition Title</th><th>Location</th><th>Posting Date</th></tr></thead></table></html>"""
SHELL_URL_FRAG = "/careersection/2/jobsearch.ftl"
API_FRAG = "/careersection/rest/jobboard/searchjobs"


def _stub(routes: dict):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append((url, method, data))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, data) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _page(n):
    return {"requisitionList": [
        {"contestNo": f"R{n}1", "column": ["Engineer", '["AB-Calgary","AB-Edmonton"]', "Oct 17, 2026"]},
        {"jobId": f"J{n}2", "column": ["Analyst", "London", "2026-10-01"]},
    ], "pagingData": {"totalCount": "4"}}


def test_parses_list_pages_and_stops_at_total():
    def api(url, data):
        n = json.loads(data)["pageNo"]
        return 200, _page(n)

    http = _stub({SHELL_URL_FRAG: (200, SHELL), API_FRAG: api})
    stats: dict = {}
    jobs = kit_ats.fetch_taleo(ROW, http, stats)
    assert len(jobs) == 4  # 2 pages x 2, stopped at totalCount=4
    assert sum(1 for c in http.calls if API_FRAG in c[0]) == 2
    j = jobs[0]
    assert j["title"] == "Engineer" and j["source"] == "taleo" and j["board"] == "acme"
    assert j["location"] == "AB-Calgary; AB-Edmonton"
    assert j["posted"] == "2026-10-17"
    assert j["url"] == "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=R11&lang=en"
    assert jobs[1]["url"].endswith("job=J12&lang=en") and jobs[1]["posted"] == "2026-10-01"
    assert "portal=101430233" in [c for c in http.calls if API_FRAG in c[0]][0][0]
    assert stats == {"tried": 1, "ok": 1, "jobs": 4}


def test_page_cap():
    http = _stub({SHELL_URL_FRAG: (200, SHELL),
                  API_FRAG: lambda u, d: (200, {"requisitionList": [{"contestNo": str(json.loads(d)["pageNo"]),
                                                                      "column": ["T", "L", ""]}]})})
    jobs = kit_ats.fetch_taleo(ROW, http, {})
    assert len(jobs) == kit_ats.MAX_PAGES


def test_positional_columns_without_headings():
    http = _stub({SHELL_URL_FRAG: (200, "<a href=\"x.ftl?lang=en&portal=77\">"),
                  API_FRAG: (200, {"requisitionList": [{"contestNo": "9", "column": ["Dev", "Leeds", "17-Oct-2026"]}]})})
    (j,) = kit_ats.fetch_taleo(ROW, http, {})
    assert (j["title"], j["location"], j["posted"]) == ("Dev", "Leeds", "2026-10-17")


def test_details_opt_in_json_ld():
    ld = {"@graph": [{"@type": "JobPosting", "description": quote("<p>Build &amp; ship</p><p>Daily</p>")}]}
    detail = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    http = _stub({SHELL_URL_FRAG: (200, SHELL), API_FRAG: (200, _page(1) | {"pagingData": {"totalCount": 2}}),
                  "jobdetail.ftl": (200, detail)})
    jobs = kit_ats.fetch_taleo(ROW, http, {})
    assert all(j["description"] == "" for j in jobs)
    assert not any("jobdetail" in c[0] for c in http.calls)
    jobs = kit_ats.fetch_taleo({**ROW, "fetch_details": True, "detail_limit": 1}, http, {})
    assert jobs[0]["description"] == "Build & ship\nDaily"
    assert jobs[1]["description"] == ""
    assert "<" not in jobs[0]["description"]


def test_private_section_and_bad_rows_fail_soft():
    stats: dict = {}
    assert kit_ats.fetch_taleo(ROW, _stub({SHELL_URL_FRAG: (200, "<html>no portal</html>")}), stats) == []
    assert stats == {"tried": 1, "jobs": 0}
    for bad in ({"tenant": "evil.com/x", "section": "2"}, {"tenant": "acme", "section": "../x"},
                {"tenant": "tre", "section": "2"}, {"tenant": "", "section": ""}):
        s: dict = {}
        assert kit_ats.fetch_taleo({"name": "X", **bad}, _stub({}), s) == []
        assert s == {"tried": 1, "jobs": 0}


def test_never_raises_and_unexpected_shape():
    def boom(url, **kw):
        raise RuntimeError("x")

    s: dict = {}
    assert kit_ats.fetch_taleo(ROW, boom, s) == [] and s["tried"] == 1 and s.get("ok", 0) == 0
    http = _stub({SHELL_URL_FRAG: (200, SHELL), API_FRAG: (200, {"foo": 1})})
    s = {}
    assert kit_ats.fetch_taleo(ROW, http, s) == [] and s.get("ok", 0) == 0
    http = _stub({SHELL_URL_FRAG: (200, SHELL), API_FRAG: (200, {})})
    s = {}
    assert kit_ats.fetch_taleo(ROW, http, s) == [] and s["ok"] == 1


def test_registered():
    assert kit_ats.ATS_FETCHERS["taleo"] is kit_ats.fetch_taleo


def _api_ok():
    return {"requisitionList": [{"contestNo": "1", "column": ["T", "L", "2026-10-01"]}], "pagingData": {"totalCount": 1}}


def test_url_injection_rejected():
    for bad in ({"lang": "en&portal=9"}, {"lang": "en\r\nX: 1"}, {"section": ".."}, {"tenant": "ſ"}):
        http = _stub({SHELL_URL_FRAG: (200, SHELL), "jobsearch": (200, SHELL), API_FRAG: (200, _api_ok())})
        s: dict = {}
        assert kit_ats.fetch_taleo({**ROW, **bad}, http, s) == [] and not http.calls and s.get("ok", 0) == 0


def test_hostile_detail_never_drops_listing():
    deep = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    http = _stub({SHELL_URL_FRAG: (200, SHELL), API_FRAG: (200, _api_ok()), "jobdetail.ftl": (200, deep)})
    jobs = kit_ats.fetch_taleo({**ROW, "fetch_details": True}, http, {})
    assert len(jobs) == 1
    jobs = kit_ats.fetch_taleo({**ROW, "fetch_details": True, "detail_limit": float("inf")}, http, {})
    assert len(jobs) == 1


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


def test_taleo_head_and_ld_scans_are_linear_on_unclosed_markup():
    page = '<table id="jobs"><thead><tr><th>Title</th><th>Location</th></tr></thead></table>'
    assert kit_ats._taleo_headings(page) == ["Title", "Location"]
    _grows_linearly(kit_ats._taleo_headings, lambda k: "<table id='jobs'>" * k + "</thead>")
    _grows_linearly(kit_ats._taleo_headings, lambda k: "<label " * k + "</th>")
    ld = '<script type="application/ld+json">{"@type": "JobPosting", "description": "<p>Hi</p>"}</script>'
    assert kit_ats._taleo_description(ld) == "Hi"
    _grows_linearly(kit_ats._taleo_description, lambda k: '<script type="application/ld+json">' * k)
