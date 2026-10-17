"""Offline tests for hunt_kit/kit_ats.py (fixtures are trimmed real public-board responses)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
KIT = ROOT.parent / "src" / "hunt_kit" / "kit_ats.py"
FIX = ROOT / "fixtures" / "hunt_kit"

_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board",
            "remote", "visa_flag", "extra"}


def _json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    """http stub: substring-of-url -> (status, payload); unknown -> (404, None). Records calls."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append((url, method, data))
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def _check_shape(jobs, source):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS
        assert j["source"] == source
        assert j["title"] and j["url"].startswith("https://")
        assert "<" not in j["description"]
        assert j["posted"] == "" or len(j["posted"]) == 10


def test_registry_has_all_six():
    assert set(kit_ats.ATS_FETCHERS) == {"workday", "workable", "recruitee", "personio", "teamtailor", "bamboohr"}


def test_workday_parses_list_and_detail():
    lst = _json("workday_list.json")
    path2 = lst["jobPostings"][1]["externalPath"]
    http = _stub({
        "/jobs": (200, lst),
        path2: (200, _json("workday_detail.json")),
    })
    stats = {}
    row = {"name": "NVIDIA", "ats": "workday", "token": "nvidia", "tenant": "nvidia", "wd": "wd5",
           "site": "NVIDIAExternalCareerSite"}
    jobs = kit_ats.fetch_workday(row, http, stats)
    _check_shape(jobs, "workday")
    assert len(jobs) == 2
    assert jobs[1]["url"] == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite" + path2
    assert jobs[1]["location"].startswith("US, CA, Santa Clara")
    assert jobs[1]["posted"] == "2026-10-13"
    assert "NVIDIA" in jobs[1]["description"]
    assert jobs[0]["description"] == ""  # detail 404 -> still listed
    assert jobs[0]["company"] == "NVIDIA" and jobs[0]["board"] == "nvidia"
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    post = http.calls[0]
    assert post[1] == "POST"
    assert json.loads(post[2]) == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    assert post[0] == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"


def test_workday_caps_pages():
    full = {"total": 5000, "jobPostings": [{"title": f"t{i}", "externalPath": f"/job/x/t{i}"} for i in range(20)]}
    http = _stub({"/jobs": (200, full)})
    row = {"name": "X", "token": "x", "wd": 1, "site": "s"}
    kit_ats.fetch_workday(row, http, {})
    posts = [c for c in http.calls if c[1] == "POST"]
    assert len(posts) == kit_ats.MAX_PAGES == 10


def test_workday_posted_labels():
    from datetime import date

    t = date(2026, 10, 13)
    assert kit_ats._workday_posted("Posted Today", t) == "2026-10-13"
    assert kit_ats._workday_posted("Posted 5 Days Ago", t) == "2026-10-08"
    assert kit_ats._workday_posted("Posted 30+ Days Ago", t) == ""


def test_workable_parses():
    http = _stub({"widget/accounts/samacoaching": (200, _json("workable.json"))})
    stats = {}
    jobs = kit_ats.fetch_workable({"name": "Sama", "ats": "workable", "token": "samacoaching"}, http, stats)
    _check_shape(jobs, "workable")
    assert jobs[0]["title"] == "Enterprise Account Executive"
    assert jobs[0]["url"] == "https://apply.workable.com/j/A5C5685457"
    assert jobs[0]["posted"] == "2026-09-15"
    assert jobs[0]["remote"] is True  # telecommuting flag
    assert "Canada" in jobs[0]["location"]
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_recruitee_parses():
    http = _stub({"bunq.recruitee.com/api/offers": (200, _json("recruitee.json"))})
    stats = {}
    jobs = kit_ats.fetch_recruitee({"name": "bunq", "ats": "recruitee", "token": "bunq"}, http, stats)
    _check_shape(jobs, "recruitee")
    assert jobs[0]["title"] == "(Senior) Product Owner"
    assert jobs[0]["url"] == "https://careers.bunq.com/o/senior-product-owner-1"
    assert jobs[0]["location"] == "Amsterdam, Noord-Holland, Netherlands"
    assert jobs[0]["posted"] == "2026-10-13"
    assert jobs[0]["remote"] is False
    assert stats["jobs"] == 2


def test_personio_parses_xml():
    http = _stub({"cardmarket.jobs.personio.de/xml": (200, _text("personio.xml"))})
    stats = {}
    jobs = kit_ats.fetch_personio({"name": "Cardmarket", "ats": "personio", "token": "cardmarket"}, http, stats)
    _check_shape(jobs, "personio")
    assert jobs[0]["title"].startswith("Customer Support Agent")
    assert jobs[0]["url"] == "https://cardmarket.jobs.personio.de/job/2834894"
    assert "Berlin" in jobs[0]["location"]
    assert "At Cardmarket, people come first." in jobs[0]["description"]
    assert jobs[0]["extra"]["department"] == "Support"
    assert stats["ok"] == 1 and stats["jobs"] == len(jobs)


def test_teamtailor_parses_rss():
    http = _stub({"career.teamtailor.com/jobs.rss": (200, _text("teamtailor.xml"))})
    stats = {}
    jobs = kit_ats.fetch_teamtailor({"name": "Teamtailor", "ats": "teamtailor", "token": "career"}, http, stats)
    _check_shape(jobs, "teamtailor")
    assert jobs[0]["title"] == "UK Account Executive - SMB"
    assert jobs[0]["url"].startswith("https://career.teamtailor.com/jobs/8118064")
    assert jobs[0]["location"] == "London, United Kingdom"
    assert jobs[0]["posted"] == "2026-07-24"
    assert jobs[0]["remote"] is False  # remoteStatus=hybrid
    assert jobs[0]["extra"]["department"] == "Sales"
    assert "Teamtailor" in jobs[0]["description"]


def test_bamboohr_parses_list_and_detail():
    lst = _json("bamboohr_list.json")
    det = _json("bamboohr_detail.json")
    http = _stub({"/careers/list": (200, lst), "/detail": (200, det)})
    stats = {}
    row = {"name": "Co", "ats": "bamboohr", "token": "co"}
    jobs = kit_ats.fetch_bamboohr(row, http, stats)
    _check_shape(jobs, "bamboohr")
    assert jobs[0]["title"] == lst["result"][0]["jobOpeningName"]
    assert jobs[0]["url"] == f"https://co.bamboohr.com/careers/{lst['result'][0]['id']}"
    assert jobs[0]["description"]
    assert stats["jobs"] == len(jobs)


@pytest.mark.parametrize("name", sorted(kit_ats.ATS_FETCHERS))
def test_network_error_returns_empty(name):
    http = _stub({"": (0, None)})
    stats = {}
    row = {"name": "X", "ats": name, "token": "x", "tenant": "x", "wd": "wd1", "site": "s"}
    assert kit_ats.ATS_FETCHERS[name](row, http, stats) == []
    assert stats["tried"] == 1 and stats.get("ok", 0) == 0 and stats["jobs"] == 0


@pytest.mark.parametrize("name", sorted(kit_ats.ATS_FETCHERS))
def test_garbage_and_raising_http_return_empty(name):
    row = {"name": "X", "ats": name, "token": "x", "tenant": "x", "wd": "wd1", "site": "s"}
    assert kit_ats.ATS_FETCHERS[name](row, _stub({"": (200, "<<not valid>>")}), {}) == []

    def boom(*a, **k):
        raise RuntimeError("net down")

    assert kit_ats.ATS_FETCHERS[name](row, boom, {}) == []


def test_import_has_no_side_effects():
    src = KIT.read_text(encoding="utf-8")
    assert "import urllib" not in src and "import requests" not in src


def test_workday_searches_each_title_and_dedupes():
    lst = _json("workday_list.json")
    http = _stub({"/jobs": (200, lst)})
    row = {"name": "NVIDIA", "ats": "workday", "token": "nvidia", "tenant": "nvidia", "wd": "wd5",
           "site": "NVIDIAExternalCareerSite", "search_texts": ["ai engineer", "llm engineer"]}
    jobs = kit_ats.fetch_workday(row, http, {})
    posts = [json.loads(c[2]) for c in http.calls if c[1] == "POST"]
    assert [p["searchText"] for p in posts] == ["ai engineer", "llm engineer"]
    # the same postings came back for both titles: each is kept (and detail-fetched) once
    assert len(jobs) == len(lst["jobPostings"])
    gets = [c for c in http.calls if c[1] == "GET"]
    assert len(gets) == len(lst["jobPostings"])


def test_workday_search_pages_are_capped_per_title():
    full = {"total": 5000, "jobPostings": [{"title": f"t{i}", "externalPath": f"/job/x/t{i}"} for i in range(20)]}
    http = _stub({"/jobs": (200, full)})
    row = {"name": "X", "token": "x", "wd": 1, "site": "s", "search_texts": ["a"]}
    kit_ats.fetch_workday(row, http, {})
    assert len([c for c in http.calls if c[1] == "POST"]) == kit_ats.SEARCH_PAGES
