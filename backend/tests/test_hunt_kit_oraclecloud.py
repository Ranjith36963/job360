"""Offline tests for fetch_oraclecloud (fixtures built from the documented ORC response shape)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

ROW = {"name": "Acme", "ats": "oraclecloud", "token": "acme", "host": "acme.fa.us2.oraclecloud.com",
       "siteNumber": "CX_1001", "lang": "en"}


def _stub(routes):
    calls = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def _req(i, **kw):
    d = {"Id": str(i), "Title": f"Role {i}", "PostedDate": "2026-09-30", "PrimaryLocation": "London, UK",
         "WorkplaceTypeCode": "ORA_ONSITE", "ShortDescriptionStr": "<p>Build &amp; ship</p>"}
    d.update(kw)
    return d


def _page(reqs, total):
    return 200, {"items": [{"TotalJobsCount": total, "requisitionList": reqs}], "hasMore": False}


def test_parses_fields_and_url():
    http = _stub({"recruitingCEJobRequisitions": _page([
        _req(1), _req(2, WorkplaceTypeCode="ORA_REMOTE", ExternalURL="https://careers.acme.com/j/2"),
        _req(3, PrimaryLocation="", workLocation=[{"TownOrCity": "Leeds", "Country": "UK"}], PostedDate="bad")], 3)})
    st = {}
    jobs = kit_ats.fetch_oraclecloud(ROW, http, st)
    assert len(jobs) == 3
    j = jobs[0]
    assert j["url"] == "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1"
    assert j["posted"] == "2026-09-30" and j["location"] == "London, UK" and j["description"] == "Build & ship"
    assert j["source"] == "oraclecloud" and j["remote"] is None
    assert jobs[1]["url"] == "https://careers.acme.com/j/2" and jobs[1]["remote"] is True
    assert jobs[1]["location"] == "London, UK · Remote"
    assert jobs[2]["location"] == "Leeds, UK" and jobs[2]["posted"] == ""
    assert st == {"tried": 1, "ok": 1, "jobs": 3}
    u = http.calls[0]
    assert "expand=requisitionList.workLocation,requisitionList.secondaryLocations" in u
    assert "finder=findReqs;siteNumber=CX_1001," in u and "offset=0" in u


def test_paginates_past_short_page_until_total():
    n = kit_ats.ORACLE_PAGE
    p0 = _page([_req(i) for i in range(n - 1)], n + 5)  # short page, total says more
    p1 = _page([_req(1000 + i) for i in range(5)], n + 5)

    def http(url, **kw):
        http.calls.append(url)
        return p1 if "offset=200" in url else p0

    http.calls = []
    jobs = kit_ats.fetch_oraclecloud(ROW, http, {})
    assert len(jobs) == n - 1 + 5 and len(http.calls) == 2


def test_page_cap_and_empty_page_stop():
    def http(url, **kw):
        http.calls.append(url)
        return _page([_req(len(http.calls) * 1000 + i) for i in range(kit_ats.ORACLE_PAGE)], 10**9)

    http.calls = []
    kit_ats.fetch_oraclecloud(ROW, http, {})
    assert len(http.calls) == kit_ats.MAX_PAGES
    http2 = _stub({"recruitingCEJobRequisitions": _page([], 0)})
    st = {}
    assert kit_ats.fetch_oraclecloud(ROW, http2, st) == [] and st["ok"] == 1 and len(http2.calls) == 1


def test_bad_host_row_and_failures_never_raise():
    st = {}
    for bad in ({"host": "evil.example.com"}, {"host": "x.fa.oraclecloud.com.evil.com"}, {}, {"host": "a.fa.oraclecloud.com", "siteNumber": "CX/../x"}):
        http = _stub({})
        assert kit_ats.fetch_oraclecloud({**ROW, **bad} if bad else {"name": "x"}, http, st) == []
        assert http.calls == []
    assert st["tried"] == 4 and st.get("ok", 0) == 0
    assert kit_ats.fetch_oraclecloud(ROW, _stub({}), {}) == []

    def boom(url, **kw):
        raise RuntimeError("net")

    assert kit_ats.fetch_oraclecloud(ROW, boom, {}) == []
    assert kit_ats.fetch_oraclecloud(ROW, _stub({"oraclecloud": (200, ["x"])}), {}) == []


def test_numbered_apex_and_location_id_and_dedup():
    row = {**ROW, "host": "t.fa.ocs.oraclecloud26.com", "locationId": "300000"}
    http = _stub({"recruitingCEJobRequisitions": _page([_req(1), _req(1)], 2)})
    jobs = kit_ats.fetch_oraclecloud(row, http, {})
    assert len(jobs) == 1 and "locationId=300000" in http.calls[0]


def test_hardening_newline_unicode_and_bad_id():
    for bad in ({"siteNumber": "CX_1\nX"}, {"locationId": "\u0663"}, {"lang": "en\n"}):
        http = _stub({})
        st = {}
        r = {**ROW, **bad}
        if bad.get("lang") == "en\n":
            continue  # stripped to a valid value
        assert kit_ats.fetch_oraclecloud(r, http, st) == [] and http.calls == []
    http = _stub({"recruitingCEJobRequisitions": _page([_req("../x"), _req(5)], 2)})
    jobs = kit_ats.fetch_oraclecloud(ROW, http, {})
    assert [j["url"].rsplit("/", 1)[1] for j in jobs] == ["5"]
