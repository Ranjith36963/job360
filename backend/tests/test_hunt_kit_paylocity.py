"""Offline tests for the Paylocity reader in hunt_kit/kit_ats_more.py.

Fixtures are shaped like freehire's internal/ingest/sources/paylocity_test.go (MIT): a listing page with
window.pageData + Jobs[], an empty Jobs board, a detail page with the Description block, and a legacy ld+json detail.
Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


more = _load("kit_ats_more_paylocity_under_test", "kit_ats_more.py")
GUID = "1a06dc72-45ee-4c90-a268-fe881bbeb577"
ROW = {"name": "Clover Basin Animal Hospital", "ats": "paylocity", "token": GUID}
LIST_URL = f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{GUID}"
DETAIL = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/"

LISTING = """<html><body>
<script>window.pageData = {"CookieBannerScriptSource":"x","Departments":["All Departments"],"IsLeadJoinEnabled":true,"Jobs":[
{"JobId":4026535,"JobTitle":"Technician Aide","LocationName":"CBAH - Airport Rd.","PublishedDate":"2026-06-29T11:55:56-05:00","IsRemote":false},
{"JobId":3836522,"JobTitle":"Associate Veterinarian","LocationName":"Remote","PublishedDate":"2026-03-19T16:53:34-05:00","IsRemote":true,"JobLocation":{"Country":"USA"}},
{"JobId":0,"JobTitle":"No id","LocationName":"x"}
]};</script></body></html>"""

DETAIL_HTML = """<html><body><div class="job-preview-details">
  <div class="vertical-padding"><div class="job-listing-header">Job Type</div><div>Full-time</div></div>
  <div class="job-listing-header">Description</div>
  <div><h2>About</h2><p>Care for animals.</p><div>Nested</div><script>alert(1)</script></div>
  <div class="job-listing-header">Other</div><div>Not this</div>
</div></body></html>"""

LD_DETAIL = ('<html><head><script type="application/ld+json">'
             + json.dumps({"@type": "JobPosting", "description": "&lt;p&gt;Legacy body&lt;/p&gt;"})
             + "</script></head></html>")


def _http(routes: dict):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append(url)
        for frag, resp in routes.items():
            if url.startswith(frag):
                return resp
        return 404, None

    http.calls = calls
    return http


def _run(http, row=ROW):
    stats: dict = {}
    jobs = more.ATS_FETCHERS["paylocity"](dict(row), http, stats)
    return jobs, stats


def test_registered():
    assert more.ATS_FETCHERS["paylocity"] is more.fetch_paylocity


def test_listing_and_detail():
    http = _http({LIST_URL: (200, LISTING), DETAIL: (200, DETAIL_HTML)})
    jobs, stats = _run(http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    by = {j["url"].rsplit("/", 1)[1]: j for j in jobs}
    tech = by["4026535"]
    assert tech["title"] == "Technician Aide" and tech["location"] == "CBAH - Airport Rd."
    assert tech["company"] == "Clover Basin Animal Hospital" and tech["source"] == "paylocity" and tech["board"] == GUID
    assert tech["url"] == DETAIL + "4026535" and tech["posted"] == "2026-06-29" and tech["remote"] is None
    assert "Care for animals." in tech["description"] and "Nested" in tech["description"]
    assert "alert" not in tech["description"] and "<" not in tech["description"]
    assert "Not this" not in tech["description"] and "Full-time" not in tech["description"]
    vet = by["3836522"]
    assert vet["remote"] is True and vet["extra"] == {"country": "USA"}


def test_empty_jobs_is_ok_with_no_jobs():
    page = '<html><script>window.pageData = {"Jobs":[]};</script></html>'
    jobs, stats = _run(_http({LIST_URL: (200, page)}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


def test_broken_or_missing_listing_skips_board():
    for resp in [(200, "<html>no data</html>"), (200, '{"Jobs": [ {"JobId": '), (404, None), (500, "x"), (0, None)]:
        http = _http({LIST_URL: resp})
        jobs, stats = _run(http)
        assert jobs == [] and stats == {"tried": 1, "jobs": 0}
        assert http.calls == [LIST_URL]


def test_bad_token_makes_no_request():
    http = _http({})
    jobs, stats = _run(http, {**ROW, "token": "../evil"})
    assert jobs == [] and stats.get("ok", 0) == 0 and http.calls == []


def test_failing_detail_keeps_job_without_description():
    http = _http({LIST_URL: (200, LISTING), DETAIL: (500, None)})
    jobs, stats = _run(http)
    assert [j["title"] for j in jobs] == ["Technician Aide", "Associate Veterinarian"]
    assert all(j["description"] == "" for j in jobs) and stats["ok"] == 1


def test_detail_that_raises_keeps_job():
    def http(url, **kw):
        if url == LIST_URL:
            return 200, LISTING
        raise RuntimeError("boom")

    jobs, stats = _run(http)
    assert len(jobs) == 2 and stats["ok"] == 1


def test_legacy_ld_json_description():
    jobs, _ = _run(_http({LIST_URL: (200, LISTING), DETAIL: (200, LD_DETAIL)}))
    assert jobs[0]["description"] == "Legacy body"


def test_brackets_inside_strings_do_not_break_the_slice():
    page = ('<script>window.pageData = {"Jobs":[{"JobId":7,"JobTitle":"Ops ] [ \\" lead","LocationName":"HQ"}],'
            '"After":[1]};</script>')
    jobs, _ = _run(_http({LIST_URL: (200, page), DETAIL: (404, None)}))
    assert [j["title"] for j in jobs] == ['Ops ] [ " lead']


def test_detail_requests_are_capped_per_board():
    n = more.PAYLOCITY_DETAIL_CAP + 15
    rows = ",".join(f'{{"JobId":{i + 1},"JobTitle":"Job {i}","LocationName":"X"}}' for i in range(n))
    page = '<script>window.pageData = {"Jobs":[' + rows + "]};</script>"
    http = _http({LIST_URL: (200, page), DETAIL: (200, DETAIL_HTML)})
    jobs, stats = _run(http)
    assert len(jobs) == n and stats["jobs"] == n
    assert len(http.calls) == 1 + more.PAYLOCITY_DETAIL_CAP
    assert sum(1 for j in jobs if j["description"]) == more.PAYLOCITY_DETAIL_CAP


def test_detail_only_for_titles_that_pass_the_gate():
    http = _http({LIST_URL: (200, LISTING), DETAIL: (200, DETAIL_HTML)})
    jobs, stats = _run(http, {**ROW, "title_ok": lambda t: "Veterinarian" in t})
    assert stats["jobs"] == 2  # the non-matching card is still returned
    assert http.calls == [LIST_URL, DETAIL + "3836522"]  # no detail request for "Technician Aide"
    by = {j["title"]: j for j in jobs}
    assert by["Technician Aide"]["description"] == ""
    assert "Care for animals." in by["Associate Veterinarian"]["description"]


def test_gate_keeps_the_cap_for_matching_titles():
    n = more.PAYLOCITY_DETAIL_CAP + 15
    rows = ",".join(f'{{"JobId":{i + 1},"JobTitle":"{"Keep" if i >= 10 else "Skip"} {i}","LocationName":"X"}}'
                    for i in range(n))
    page = '<script>window.pageData = {"Jobs":[' + rows + "]};</script>"
    http = _http({LIST_URL: (200, page), DETAIL: (200, DETAIL_HTML)})
    jobs, _ = _run(http, {**ROW, "title_ok": lambda t: t.startswith("Keep")})
    assert len(http.calls) == 1 + more.PAYLOCITY_DETAIL_CAP
    assert not any(j["description"] for j in jobs if j["title"].startswith("Skip"))
    assert sum(1 for j in jobs if j["description"]) == more.PAYLOCITY_DETAIL_CAP
