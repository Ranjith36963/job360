"""Offline tests for fetch_icims (fixtures built from the documented iCIMS card / JSON-LD shape)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
HERE = Path(__file__).resolve().parent / "fixtures" / "hunt_kit"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

ROW = {"name": "Acme", "ats": "icims", "token": "careers-acme"}
HOST = "https://careers-acme.icims.com"


def _fx(name: str) -> str:
    return (HERE / "icims" / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def _routes():
    return {
        "pr=0": (200, _fx("icims_search_p0.html")),
        "pr=1": (200, _fx("icims_search_p1.html")),
        "pr=2": (200, "<html>no jobs</html>"),
        "/jobs/1001/": (200, _fx("icims_detail_1001.html")),
        "/jobs/1002/": (200, _fx("icims_detail_1002.html")),
        "/jobs/1004/": (200, _fx("icims_detail_1004.html")),
    }


def test_registered():
    assert kit_ats.ATS_FETCHERS["icims"] is kit_ats.fetch_icims


def test_parses_list_and_detail():
    http, stats = _stub(_routes()), {}
    jobs = kit_ats.fetch_icims(ROW, http, stats)
    assert [j["url"] for j in jobs] == [
        f"{HOST}/jobs/1001/data-engineer/job", f"{HOST}/jobs/1002/remote-analyst/job",
        f"{HOST}/jobs/1004/no-location/job"]  # off-host card dropped, query stripped
    a, b, c = jobs
    assert a["title"] == "Data & Platform Engineer"
    assert a["location"] == "US-NY-New York"  # list location kept
    assert a["posted"] == "2026-09-30"
    assert a["description"] == "Build & run pipelines.\nSQL"
    assert a["source"] == "icims" and a["board"] == "careers-acme" and a["company"] == "Acme"
    assert a["extra"] == {"employment_type": "FULL_TIME"}
    assert b["posted"] == "2026-10-01" and b["remote"] is True and b["location"] == "GB-London"
    assert c["location"] == "Austin, TX, United States"  # filled from JSON-LD, ISO country expanded
    assert c["posted"] == "2026-10-05"
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_pagination_stops_on_empty_and_repeat():
    http = _stub({"pr=": (200, _fx("icims_search_p0.html")), "/jobs/": (404, None)})  # same page forever
    jobs = kit_ats.fetch_icims(ROW, http, {})
    assert len(jobs) == 2
    assert len([u for u in http.calls if "/jobs/search" in u]) == 2  # page 0, then repeat detected
    assert all(j["posted"] == "" for j in jobs)  # detail 404: job kept, undated


def test_max_pages_cap():
    pages = {}

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        if "/jobs/search" in url:
            n = int(url.split("pr=")[1].split("&")[0])
            pages[n] = 1
            return 200, f'<li class="iCIMS_JobCardItem"><a href="/jobs/{n}/t/job"><h3>T{n}</h3></a></li>'
        return 404, None

    jobs = kit_ats.fetch_icims(ROW, http, {})
    assert len(jobs) == kit_ats.MAX_PAGES and max(pages) == kit_ats.MAX_PAGES - 1


def test_failures_never_raise():
    stats: dict = {}
    assert kit_ats.fetch_icims(ROW, _stub({}), stats) == []
    assert stats.get("tried") == 1 and not stats.get("ok") and not stats.get("jobs")

    def boom(*a, **k):
        raise RuntimeError("x")

    assert kit_ats.fetch_icims(ROW, boom, {}) == []
    assert kit_ats.fetch_icims({"token": "bad host/../x"}, _stub({}), {}) == []
    assert kit_ats.fetch_icims({}, _stub({}), {}) == []
    assert kit_ats.fetch_icims(ROW, _stub({"pr=0": (200, {"not": "text"})}), {}) == []


def test_token_with_suffix_and_empty_board():
    http = _stub({"pr=0": (200, "<html></html>")})
    stats: dict = {}
    assert kit_ats.fetch_icims({"token": "Careers-Acme.icims.com"}, http, stats) == []
    assert http.calls[0].startswith(HOST + "/jobs/search?ss=1&pr=0")
    assert stats["ok"] == 1 and stats["jobs"] == 0


def test_detail_only_for_titles_that_pass_the_gate():
    http = _stub(_routes())
    row = {**ROW, "title_ok": lambda t: "Analyst" in t}
    jobs = kit_ats.fetch_icims(row, http, {})
    by = {j["url"].split("/jobs/")[1].split("/")[0]: j for j in jobs}
    assert set(by) == {"1001", "1002", "1004"}  # non-matching cards are still returned
    assert not any("/jobs/1001/" in u or "/jobs/1004/" in u for u in http.calls)  # no detail request for them
    assert any("/jobs/1002/" in u for u in http.calls)
    assert by["1001"]["description"] == "" and by["1001"]["posted"] == ""
    assert by["1002"]["description"] and by["1002"]["posted"] == "2026-10-01"


def test_gate_does_not_spend_the_detail_cap_on_non_matching_cards(monkeypatch):
    monkeypatch.setattr(kit_ats, "ICIMS_DETAIL_CAP", 1)
    http = _stub(_routes())
    jobs = kit_ats.fetch_icims({**ROW, "title_ok": lambda t: "Analyst" in t or "Location" in t}, http, {})
    assert [bool(j["description"]) for j in jobs] == [False, True, False]  # cap of 1 goes to the first match
