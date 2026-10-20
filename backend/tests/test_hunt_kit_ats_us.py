"""Offline tests for hunt_kit/kit_ats_us.py.

Fixtures are shaped like the sample data in the freehire adapter tests (internal/ingest/sources/<name>_test.go, MIT) for every
ATS here. Nothing touches the network.
"""
from __future__ import annotations

import html as _html
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


us = _load("kit_ats_us_under_test", "kit_ats_us.py")
hk = _load("hunt_kit_ats_us_under_test", "hunt_kit.py")
F = us.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, method, body) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name]({"name": "Acme", "ats": name, **row}, http, stats)
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == name and j["url"].startswith("https://") and "<" not in j["description"]
        assert "alert" not in j["description"] and "x()" not in j["description"]  # script bodies never reach a description
    return jobs, stats


# A good token per ATS (what the reader accepts), and a board response that is empty but valid.
TOKEN = {"bullhorn": "91:abc123def", "careerplug": "acme", "hireology": "acme", "hiringthing": "acme.hiringthing.com"}


def test_registry_and_citations():
    assert set(F) == {"bullhorn", "careerplug", "hireology", "hiringthing"}
    assert set(TOKEN) == set(F)
    src = (KIT_DIR / "kit_ats_us.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)
    for n in F:  # every reader names its reference file
        assert f"sources/{n}.go" in src


def test_wiring_into_hunt_kit():
    assert "kit_ats_us" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk.ALL_SOURCES)


def test_companies_file_rows_are_wellformed():
    allrows = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    rows = [r for r in allrows if r["ats"] in F]  # the freehire rows added with this kit file
    assert rows and all(set(r) == {"name", "ats", "board", "source"} and r["ats"] in F and r["name"] and r["board"] for r in rows)
    assert len({(r["ats"], r["board"]) for r in rows}) == len(rows)
    for r in rows:  # a shipped board must be a token its own reader accepts
        jobs, stats = _run(r["ats"], {"token": r["board"]}, _stub({}))
        assert stats == {"tried": 1, "jobs": 0}


@pytest.mark.parametrize("name", sorted(F))
def test_bad_token_dead_board_and_garbage_never_raise(name):
    jobs, stats = _run(name, {"token": ""}, _stub({}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    jobs, stats = _run(name, {"token": "bad token/../x"}, _stub({}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    # a good token on a board that answers 404 (the stub's default) is not ok
    http = _stub({})
    jobs, stats = _run(name, {"token": TOKEN[name]}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0} and 1 <= len(http.calls) <= 3
    # garbage bodies of every shape: no raise, no jobs
    for junk in ((200, "<<<not html or json{{"), (200, None), (200, 12345), (200, ["x"]), (200, {"data": "x", "jobs": 3}), (500, "boom")):
        j2, s2 = _run(name, {"token": TOKEN[name]}, _stub({"": junk}))
        assert j2 == [] and s2["tried"] == 1
    def boom(url, **kw):
        raise RuntimeError("network down")
    j3, s3 = _run(name, {"token": TOKEN[name]}, boom)
    assert j3 == [] and s3 == {"tried": 1, "jobs": 0}


# --------------------------------------------------------------------------- Bullhorn (bullhorn_test.go:13-30)
BH1 = {"total": 1000, "start": 0, "count": 500, "data": [
    {"id": 4231, "title": "Senior Go Engineer", "publicDescription": "<p>Build backends.</p><script>alert(1)</script>",
     "address": {"city": "Austin", "state": "TX", "countryName": "United States"}, "dateAdded": 1750617799000,
     "employmentType": "Contract To Hire"},
    {"id": 0, "title": "No id", "publicDescription": "x"},
    {"id": 7, "title": "Remote Analyst", "address": {"countryName": "United States"}, "dateAdded": None}]}
BH2 = {"total": 1000, "start": 500, "count": 500, "data": []}


def test_bullhorn_pages_until_an_empty_window_and_maps():
    http = _stub({"start=0": (200, BH1), "start=500": (200, BH2)})
    jobs, stats = _run("bullhorn", {"token": "91:abc123def", "name": "Acme Staffing"}, http)
    assert [j["title"] for j in jobs] == ["Senior Go Engineer", "Remote Analyst"] and stats == {"tried": 1, "ok": 1, "jobs": 2}
    a = jobs[0]
    assert a["url"] == "https://public-rest91.bullhornstaffing.com/rest-services/abc123def/entity/JobOrder/4231"
    assert a["company"] == "Acme Staffing" and a["location"] == "Austin, TX" and a["posted"] == "2025-06-22"
    assert a["description"] == "Build backends." and a["extra"]["employment_type"] == "Contract To Hire"
    assert jobs[1]["location"] == "United States" and jobs[1]["posted"] == ""
    assert len(http.calls) == 2 and "query=%28isOpen%3A1%29" in http.calls[0][0] and "count=500" in http.calls[0][0]


def test_bullhorn_stops_when_total_reached_and_keeps_pages_after_a_hiccup():
    full = {"total": 1, "data": [{"id": 1, "title": "T"}]}
    http = _stub({"start=0": (200, full)})
    assert len(_run("bullhorn", {"token": "1:tok"}, http)[0]) == 1 and len(http.calls) == 1
    more = {"total": 9999, "data": [{"id": 1, "title": "T"}]}
    http = _stub({"start=0": (200, more), "start=500": (503, None)})
    jobs, stats = _run("bullhorn", {"token": "1:tok"}, http)
    assert len(jobs) == 1 and stats["ok"] == 1  # non-200 after page 0 stops, page 0 is kept
    for bad in ("nocolon", ":tok", "91:", "x:tok", "91:to k"):
        assert _run("bullhorn", {"token": bad}, _stub({}))[1] == {"tried": 1, "jobs": 0}


# --------------------------------------------------------------------------- CareerPlug (careerplug_test.go:13-42)
def _ld(obj: dict) -> str:
    body = json.dumps(obj).replace("</", "<\\/")  # a real page escapes the slash so the script block survives
    return '<html><head><script type="application/ld+json">' + body + "</script></head><body>Apply</body></html>"


CP_A = _ld({"@type": "JobPosting", "title": "Restaurant Team Member", "description": "<div>Serve guests.</div><script>x()</script>",
            "datePosted": "2026-06-20T12:00:00+00:00", "hiringOrganization": {"@type": "Organization", "name": "Golden Corral - Concord"},
            "jobLocation": {"@type": "Place", "address": {"addressLocality": "Concord", "addressRegion": "CA", "addressCountry": "US"}}})
CP_B = _ld({"@type": "JobPosting", "title": "Shift Lead", "description": "<p>Lead a shift.</p>", "datePosted": "2026-06-19T09:00:00+00:00",
            "hiringOrganization": {"name": "Golden Corral - Reno"},
            "jobLocation": {"address": {"addressLocality": "Reno", "addressRegion": "NV", "addressCountry": "US"}}})
CP_L1 = ('<html><body><div id="job_table"><a href="/jobs/100"><span class="name">Restaurant Team Member</span></a>'
         '<a href="/jobs/100">Apply</a><a href="/about">About</a><a href="/jobs?page=2">Next</a><a href="https://evil.example/jobs/9">x</a></div></body></html>')
CP_L2 = '<html><body><div id="job_table"><a href="/jobs/200"><span class="name">Shift Lead</span></a></div></body></html>'


def test_careerplug_pages_then_reads_each_posting_and_prefers_the_posting_employer():
    def listing(url, method, body):
        return (200, CP_L2) if "page=2" in url else (200, CP_L1 if "page=3" not in url else "<html></html>")
    http = _stub({"/jobs/100": (200, CP_A), "/jobs/200": (200, CP_B), "/jobs": listing})
    jobs, stats = _run("careerplug", {"token": "golden-corral-careers", "name": "Golden Corral"}, http)
    assert [(j["title"], j["company"], j["location"], j["posted"]) for j in jobs] == [
        ("Restaurant Team Member", "Golden Corral - Concord", "Concord, CA, US", "2026-06-20"),
        ("Shift Lead", "Golden Corral - Reno", "Reno, NV, US", "2026-06-19")]
    assert jobs[0]["url"] == "https://golden-corral-careers.careerplug.com/jobs/100" and jobs[0]["description"] == "Serve guests."
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_careerplug_a_board_that_repeats_its_last_page_ends_and_detail_failure_keeps_the_listing_title():
    http = _stub({"/jobs/100": (500, None), "/jobs": (200, CP_L1)})  # every ?page=N serves the same links
    jobs, _ = _run("careerplug", {"token": "acme"}, http)
    assert [(j["title"], j["company"], j["description"]) for j in jobs] == [("Restaurant Team Member", "Acme", "")]
    assert len([c for c in http.calls if "/jobs?" in c[0] or c[0].endswith("/jobs")]) == 2  # page 1, page 2 (no new link) -> stop
    # the title gate: a posting the user does not search spends no detail request
    http = _stub({"/jobs": (200, CP_L1)})
    jobs, _ = _run("careerplug", {"token": "acme", "title_ok": lambda t: False}, http)
    assert len(jobs) == 1 and not any("/jobs/100" in c[0] for c in http.calls)


def test_careerplug_listing_failure_fails_the_board_and_detail_cap_holds():
    assert _run("careerplug", {"token": "acme"}, _stub({"/jobs": (503, None)}))[1] == {"tried": 1, "jobs": 0}
    many = "".join(f'<a href="/jobs/{i}">Role {i}</a>' for i in range(1, 60))
    http = _stub({"/jobs?page=2": (200, "<html></html>"), "/jobs/": (200, CP_B), "/jobs": (200, f"<html>{many}</html>")})
    jobs, _ = _run("careerplug", {"token": "acme"}, http)
    assert len(jobs) == 59 and len([c for c in http.calls if "/jobs/" in c[0]]) == us.DETAIL_CAP


# --------------------------------------------------------------------------- Hireology (hireology_test.go:10-16)
HIRE = {"data": [
    {"type": "careers", "id": "25816", "attributes": {"id": 25816, "name": "Pet Sitter", "remote": False,
     "job-description": "<p>Sit pets.</p><script>x()</script>", "locations": ["Highlands Ranch, CO"], "status": "Open",
     "career-site-url": "https://careers.hireology.com/acme/25816/description"}},
    {"type": "careers", "id": "999", "attributes": {"id": 999, "name": "Closed Role", "status": "Filled", "locations": []}},
    {"type": "careers", "id": "7", "attributes": {"name": "Remote Dev", "remote": True, "status": "open", "locations": []}}]}


def test_hireology_keeps_open_postings_only_and_builds_a_missing_url():
    jobs, stats = _run("hireology", {"token": "acme"}, _stub({"/v1/careers/acme": (200, HIRE)}))
    assert [j["title"] for j in jobs] == ["Pet Sitter", "Remote Dev"] and stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert jobs[0]["url"] == "https://careers.hireology.com/acme/25816/description" and jobs[0]["location"] == "Highlands Ranch, CO"
    assert jobs[0]["description"] == "Sit pets." and jobs[0]["company"] == "Acme" and jobs[0]["remote"] is None
    assert jobs[1]["url"] == "https://careers.hireology.com/acme/7/description" and jobs[1]["remote"] is True
    assert _run("hireology", {"token": "acme"}, _stub({"": (200, {"data": []})}))[1] == {"tried": 1, "ok": 1, "jobs": 0}  # empty board is ok


# --------------------------------------------------------------------------- HiringThing (hiringthing_test.go:15-50)
def _ht_listing(*urls: str) -> str:
    cards = "".join(f'<div class="job-container"><a href="{u}"><h2>A job</h2></a><a href="{u}">Learn more</a></div>' for u in urls)
    return f'<html><body><div class="jobs-list-container">{cards}<a href="/privacy">Privacy</a></div></body></html>'


def _ht_detail(record: dict) -> str:
    props = _html.escape(json.dumps({"formID": "application-form-container", "jobObj": {"table": record}}))
    return ('<html><body><div data-react-class="HiringThing.Components.JobSalary" data-react-props="{&quot;payFrequency&quot;:&quot;hourly&quot;}"></div>'
            f'<div data-react-class="HiringThing.Components.ApplyButtonGroup" data-react-props="{props}"></div>'
            '<div data-react-class="HiringThing.Components.CookieBanner" data-react-props="{}"></div></body></html>')


HT_REC = {"id": 1052705, "title": "Guest Services Admin", "html_description": "<p>Welcome guests.</p><script>x()</script>",
          "posted_at": "2026-06-18T09:30:00Z", "location": "Remote - Windsor Locks, CT", "remote": True,
          "location_info": {"country": "US", "city": "Windsor Locks", "state": "CT"}, "min_salary": {"amount": "13.00"}}


def test_hiringthing_reads_the_record_found_by_component_name_and_dedups_by_id():
    host = "crown-shredding-llc.prismhr-hire.com"
    listing = _ht_listing(f"https://{host}/job/111/guest-services", f"https://{host}/job/111/other-slug",
                          "/job/222/second", "https://other.hiringthing.com/job/333/foreign")
    http = _stub({"/job/111": (200, _ht_detail(HT_REC)), "/job/222": (200, _ht_detail(dict(HT_REC, title="Cook", remote=False,
                  location="Austin, TX"))), f"https://{host}/": (200, listing)})
    jobs, stats = _run("hiringthing", {"token": host, "name": "Crown Shredding"}, http)
    assert [j["title"] for j in jobs] == ["Guest Services Admin", "Cook"] and stats["jobs"] == 2
    a, b = jobs
    assert a["url"] == f"https://{host}/job/111/guest-services" and a["location"] == "Remote - Windsor Locks, CT"
    assert a["posted"] == "2026-06-18" and a["description"] == "Welcome guests." and a["company"] == "Crown Shredding"
    assert a["remote"] is True and a["extra"]["country"] == "US" and b["remote"] is None  # the flag decides, not the location text
    assert not any("other.hiringthing.com" in c[0] for c in http.calls)  # a link off the board's host is never followed


def test_hiringthing_failed_detail_keeps_listing_title_and_empty_board_is_ok():
    host = "skijapan.hiringthing.com"
    http = _stub({"/job/": (404, None), f"https://{host}/": (200, _ht_listing(f"https://{host}/job/1/x"))})
    jobs, _ = _run("hiringthing", {"token": host}, http)
    assert [(j["title"], j["description"]) for j in jobs] == [("A job", "")]
    assert _run("hiringthing", {"token": host}, _stub({"": (200, "<html><body></body></html>")}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
    assert _run("hiringthing", {"token": "nodot"}, _stub({}))[1] == {"tried": 1, "jobs": 0}  # a board is a host


# --------------------------------------------------------------------------- shared
@pytest.mark.parametrize("name", sorted(F))
def test_every_reader_is_wired_through_wrap_and_counts_once(name):
    stats: dict = {}
    F[name]({"name": "Acme", "ats": name, "token": TOKEN[name]}, _stub({}), stats)
    assert stats["tried"] == 1


def test_deeply_nested_markup_does_not_blow_the_stack():
    page = "<div>" * 5000 + '<a href="/jobs/1">Deep</a>'
    http = _stub({"/jobs/1": (404, None), "/jobs": (200, page)})
    jobs, _ = _run("careerplug", {"token": "acme"}, http)
    assert [j["title"] for j in jobs] == ["Deep"]


# --------------------------------------------------------------------------- hostile input (review fixes)
def _fast(fn):
    import time
    t = time.perf_counter()
    out = fn()
    assert time.perf_counter() - t < 1.0
    return out


def test_clean_is_linear_on_unclosed_script_repeats_and_still_drops_blocks():
    assert _fast(lambda: us._clean("<script>" * 60000)) == ""
    assert _fast(lambda: us._clean("<style " * 40000)) == ""
    assert us._clean("a<script>x()</script>b<STYLE type=x>p{}</Style >c") == "abc"
    assert us._clean("a<script>never closed b") == "a"
    assert "x" in us._clean("a<scripts>x</scripts> b")  # <scripts> is not a script tag
    assert len(us._clean("word " * 100000)) <= 200_000


def test_nested_unclosed_anchors_stay_linear_and_still_read():
    page = '<a href="/j/1">Job</a>' + '<a href="/j/1">' * 8000
    anchors = _fast(lambda: list(us._anchors(us._parse(page).root)))
    assert anchors[0] == ("/j/1", "Job") and len(anchors) <= 5000
    t = us._parse('<a href="/a">One<a href="/b">Two</a>')
    assert list(us._anchors(t.root)) == [("/a", "One"), ("/b", "Two")]


def test_stray_end_tags_after_deep_nesting_stay_linear():
    page = "<div>" * 9000 + "</p>" * 12000
    _fast(lambda: us._parse(page))
    t = us._parse("<div><p>a</p></span></p><b>x</b></div>")
    assert [n.tag for n in us._nodes(t.root)] == ["#root", "div", "p", "b"]


def test_deeply_nested_anchors_read_in_linear_time_and_keep_order():
    page = ('<a href=/jobs/1>x' + "<b>" * 65) * 1500
    anchors = _fast(lambda: list(us._anchors(us._parse(page).root)))
    assert anchors[0][0] == "/jobs/1" and anchors[0][1].startswith("x") and len(anchors[0][1]) <= 2000 and len(anchors) == 1500
    t = us._parse('<a href="/a">One <b>and</b> <a href="/b">Two</a></a>')
    assert list(us._anchors(t.root)) == [("/a", "One and"), ("/b", "Two")]


def test_hireology_url_on_a_foreign_host_falls_back_to_the_board_host():
    data = {"data": [{"id": "5", "attributes": {"name": "Dev", "status": "Open", "locations": [],
                                                "career-site-url": "https://evil.example/phish"}}]}
    jobs, _ = _run("hireology", {"token": "acme"}, _stub({"/v1/careers/acme": (200, data)}))
    assert [j["url"] for j in jobs] == ["https://careers.hireology.com/acme/5/description"]


def test_vendor_own_subdomains_are_not_attributed_to_a_board():
    for u in ("https://www.careerplug.com/pricing", "https://help.careerplug.com/x", "https://app.careerplug.com/",
              "https://www.hiringthing.com/", "https://support.hiringthing.com/a"):
        assert hk.board_from_job_url(u)["kind"] != "ats", u
    assert hk.board_from_job_url("https://acme.careerplug.com/jobs/1")["token"] == "acme"
    assert hk.board_from_job_url("https://acme.hiringthing.com/job/1")["token"] == "acme.hiringthing.com"


def test_every_part_one_reader_survives_hostile_pages():
    page = '<a href="/jobs/1">x</a>' * 8000
    for name in ("careerplug", "hiringthing"):
        _fast(lambda n=name: _run(n, {"token": TOKEN[n]}, _stub({"": (200, page)})))
