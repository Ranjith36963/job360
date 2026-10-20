"""Offline tests for hunt_kit/kit_ats_misc1.py (Betterteam, CATS, ClinchTalent, Crelate).

Fixtures are shaped like the test data in the MIT freehire adapters (internal/ingest/sources/<name>_test.go and
testdata/): same selectors, ids, and field names. Nothing touches the network.
"""
from __future__ import annotations

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


m1 = _load("kit_ats_misc1_under_test", "kit_ats_misc1.py")
F = m1.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
ALL = ["betterteam", "catsone", "clinch", "crelate"]


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                st, payload = resp(url, method, body) if callable(resp) else resp
                if st != 200:
                    return st, None
                if as_json and isinstance(payload, str):
                    return st, json.loads(payload)
                if not as_json and not isinstance(payload, str):
                    return st, json.dumps(payload)
                return st, payload
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name](dict(row, ats=name), http, stats)
    return jobs, stats


def _check_shape(jobs):
    for j in jobs:
        assert set(j) == JOB_KEYS
        assert j["title"] and j["url"].startswith("https://")


# ----------------------------------------------------------------------------- Betterteam
def _bt_detail(title, desc, posted, city, country):
    ld = {"@context": "https://schema.org/", "@type": "JobPosting", "title": title, "description": desc, "datePosted": posted,
          "hiringOrganization": {"@type": "Organization", "name": "110 Grill"},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": city, "addressCountry": country}}}
    return '<html><head><script type="application/ld+json">%s</script></head></html>' % json.dumps(ld)


def test_betterteam_happy_path():
    listing = '<ul><li><a href="/bartender-21">A job</a></li><li><a href="/bartender-21">dup</a></li><li><a href="/about-us">About</a></li></ul>'
    http = _stub({"/bartender-21": (200, _bt_detail("Bartender", "&lt;div&gt;Pour drinks.&lt;/div&gt;", "2026-06-09T15:07:41.914471Z", "North Conway", "US")),
                  "110grill.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "110 Grill", "token": "110grill"}, http)
    assert len(jobs) == 1 and stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    _check_shape(jobs)
    assert j["url"] == "https://110grill.betterteam.com/bartender-21" and j["company"] == "110 Grill"
    assert j["location"] == "North Conway, US" and j["posted"] == "2026-06-09" and "Pour drinks" in j["description"]
    assert j["extra"]["id"] == "bartender-21" and j["remote"] is None
    assert len(http.calls) == 2  # listing + one detail (duplicate and /about-us never fetched)


def test_betterteam_empty_nonok_garbage():
    jobs, stats = _run("betterteam", {"name": "x", "token": "x"}, _stub({"betterteam.com": (200, "<ul></ul>")}))
    assert jobs == [] and stats["ok"] == 1
    http = _stub({"betterteam.com": (500, None)})
    jobs, stats = _run("betterteam", {"name": "x", "token": "x"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1
    jobs, _ = _run("betterteam", {"name": "x", "token": "../bad host"}, _stub({}))
    assert jobs == []


# ----------------------------------------------------------------------------- CATS (catsone)
def _cats_listing(*rows):
    out = '<div class="jobs-table"><div class="table-header"><div class="header-cell">Job Title</div></div>'
    for slug, title, loc in rows:
        out += ('<a class="table-row" href="/careers/90438/jobs/%s"><div class="data-cell title-cell">%s</div>'
                '<div class="data-cell" data-label="Category">General</div><div class="data-cell" data-label="Location">%s</div></a>') % (slug, title, loc)
    return out + "</div>"


CATS_DETAIL = ('<div class="job-description-container"><a class="view-all-jobs" href="/careers/90438">View all jobs</a>'
               '<div><div class="job-header"><h2>Ignored Header</h2></div><div class="job-description"><p><strong>About</strong></p>'
               '<p>Sell things.</p><script>alert(1)</script></div></div></div>')


def test_catsone_matches_freehire_listing_then_detail():
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "/jobs/16823910": (200, CATS_DETAIL),
                  "sphereinc.catsone.com/careers": (200, _cats_listing(("16831828-sdr", "Sales Development Representative (SDR)", "Brazil, Remote"),
                                                                        ("16823910-ai", "AI Engineer", "Kyiv"),
                                                                        ("16831828-sdr", "dup row", "x")))})
    jobs, stats = _run("catsone", {"name": "Sphere", "token": "sphereinc.catsone.com"}, http)
    _check_shape(jobs)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    by = {j["extra"]["id"]: j for j in jobs}
    j = by["16831828"]
    assert j["url"] == "https://sphereinc.catsone.com/careers/90438/jobs/16831828-sdr"
    assert j["title"] == "Sales Development Representative (SDR)" and j["company"] == "Sphere"
    assert j["location"] == "Brazil, Remote" and j["remote"] is True
    assert "Sell things" in j["description"] and "alert" not in j["description"]
    assert "View all jobs" not in j["description"] and "Ignored Header" not in j["description"]
    assert http.calls[0][0] == "https://sphereinc.catsone.com/careers"  # GET /careers, nothing else first


def test_catsone_skips_rows_without_numeric_id_and_drops_failed_detail():
    listing = _cats_listing(("16831828-sdr", "Keep", "Kyiv")).replace("</div>", "", 0) + '<a class="table-row" href="/careers/90438"><div class="title-cell">No id</div></a>'
    http = _stub({"/jobs/16831828": (500, None), "acme.catsone.com/careers": (200, listing)})
    jobs, _ = _run("catsone", {"name": "A", "token": "acme.catsone.com"}, http)
    assert jobs == []  # catsone.go:55-57: a failed detail drops the posting; the id-less row never counts


def test_catsone_title_gate_keeps_listing_row_without_detail_request():
    http = _stub({"acme.catsone.com/careers": (200, _cats_listing(("1-a", "Cook", "Leeds")))})
    jobs, _ = _run("catsone", {"name": "A", "token": "acme.catsone.com", "title_ok": lambda t: False}, http)
    assert len(jobs) == 1 and jobs[0]["description"] == "" and len(http.calls) == 1


def test_catsone_empty_nonok_garbage():
    assert _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (200, "<html></html>")}))[0] == []
    jobs, stats = _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    assert _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (200, "\x00<<a class='table-row' href=")}))[0] == []


# ----------------------------------------------------------------------------- ClinchTalent
def _sitemap(*locs):
    return '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % "".join(
        "<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (u, "2026-07-01T10:00:00Z") for u in locs)


def test_clinch_happy_path_and_waf_latch():
    base = "https://careers.withwaymo.com"
    detail = '<div class="job-description"><p>Drive safely.</p><script>x()</script></div>'
    http = _stub({"/jobs/one-": (200, detail), "/jobs/two-": (202, None), "/jobs/three-": (200, detail),
                  "sitemap.xml": (200, _sitemap(base + "/", base + "/why-waymo", base + "/jobs/one-london-england-united-kingdom",
                                                base + "/jobs/two-warsaw-poland-0a1b2c3d-0000-1111-2222-333344445555",
                                                base + "/jobs/three-remote-germany"))})
    jobs, stats = _run("clinch", {"name": "Waymo", "token": "careers.withwaymo.com"}, http)
    _check_shape(jobs)
    assert [j["title"] for j in jobs] == ["One London England", "Two Warsaw", "Three Remote"]
    assert jobs[0]["location"] == "United Kingdom" and jobs[1]["location"] == "Poland" and jobs[0]["posted"] == "2026-07-01"
    assert jobs[1]["extra"]["id"] == "0a1b2c3d-0000-1111-2222-333344445555"
    assert "Drive safely" in jobs[0]["description"] and "x()" not in jobs[0]["description"]
    assert jobs[1]["description"] == "" and jobs[2]["description"] == ""  # a challenge latches detail fetching off
    assert len(http.calls) == 3 and jobs[2]["company"] == "Waymo"


def test_clinch_empty_nonok_garbage():
    assert _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (200, _sitemap("https://c.example.com/about"))}))[0] == []
    jobs, stats = _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (200, "<<not xml")}))
    assert jobs == [] and stats.get("ok", 0) == 0


# ----------------------------------------------------------------------------- Crelate
CRELATE_BOARD = "careertree:ec546cba-84d5-4d8a-97e5-52e8ef47db08"


def test_crelate_happy_path_company_is_the_employer():
    feed = {"Jobs": [{"Id": "a1", "Title": "Home Health RN", "CompanyName": "Bayview Home Care", "Description": "<p>Home Health role.</p><script>alert(1)</script>",
                      "City": "Tampa", "State": "FL", "Country": "United States", "Url": "/not-a-real-job-code",
                      "JobCode": "not-a-real-job-code", "LastPostedOnDate": "2026-07-17T17:17:12.81Z"},
                     {"Id": "no-company-1", "Title": "Recruiter", "Description": "<p>Join us.</p>", "Country": "United States", "Url": "/abc123", "JobCode": "",
                      "LastPostedOnDate": "2026-07-01T00:00:00Z"},
                     {"Id": "", "Title": "No id", "JobCode": "zzz"}], "IsError": False, "ErrorMessage": None}
    http = _stub({"candidateportal/GetAllJobs": (200, feed)})
    jobs, stats = _run("crelate", {"name": "Career Tree Network", "token": CRELATE_BOARD}, http)
    _check_shape(jobs)
    assert stats["jobs"] == 2
    a, b = jobs
    assert a["company"] == "Bayview Home Care" and a["location"] == "Tampa, FL" and a["posted"] == "2026-07-17"
    assert a["url"] == "https://jobs.crelate.com/portal/careertree/job/not-a-real-job-code" and "alert" not in a["description"]
    assert b["company"] == "Career Tree Network" and b["location"] == "United States" and b["url"].endswith("/job/abc123")
    url = http.calls[0][0]
    assert "requestEnvelope=" in url and "ec546cba-84d5-4d8a-97e5-52e8ef47db08" in url and len(http.calls) == 1


def test_crelate_error_bad_board_garbage():
    assert _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": [], "IsError": False})}))[0] == []
    jobs, stats = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": [], "IsError": True, "ErrorMessage": "no"})}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    for bad in ("", "nocolon", ":org", "slug:"):
        http = _stub({})
        assert _run("crelate", {"name": "x", "token": bad}, http)[0] == [] and http.calls == []
    assert _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": ["junk", 3, None]})}))[0] == []


# ----------------------------------------------------------------------------- all readers
@pytest.mark.parametrize("name", ALL)
def test_every_reader_never_raises_on_garbage_and_http_exceptions(name):
    row = {"name": "X", "token": {"crelate": CRELATE_BOARD}.get(name, "x")}
    for payload in (None, 5, [], {}, "<<>>", "\x00�", {"Jobs": 1}):
        jobs, stats = _run(name, row, lambda url, p=payload, **kw: (200, p))
        assert isinstance(jobs, list) and stats["tried"] == 1

    def boom(url, **kw):
        raise OSError("down")

    jobs, stats = _run(name, row, boom)
    assert jobs == [] and stats["tried"] == 1 and stats.get("ok", 0) == 0


@pytest.mark.parametrize("name", ALL)
def test_registered_and_first_non200_stops_after_one_request(name):
    assert name in F
    row = {"name": "X", "token": {"crelate": CRELATE_BOARD}.get(name, "x")}
    http = _stub({"": (503, None)})
    assert _run(name, row, http)[0] == [] and len(http.calls) == 1


# ----------------------------------------------------------------------------- readers stay on the board host
OFF_HOST_HREFS = ("https://evil.example/x-123", "//evil2.example/y-9", "http://good.betterteam.com/ok-5")


def test_betterteam_never_fetches_or_emits_off_host_links():
    listing = "".join('<a href="%s">x</a>' % h for h in OFF_HOST_HREFS) + '<a href="/bartender-21">ok</a>'
    http = _stub({"good.betterteam.com/bartender-21": (200, _bt_detail("Bartender", "d", "2026-06-09T15:07:41Z", "Leeds", "GB")),
                  "good.betterteam.com": (200, listing)})
    jobs, _ = _run("betterteam", {"name": "G", "token": "good"}, http)
    assert [j["url"] for j in jobs] == ["https://good.betterteam.com/bartender-21"]
    assert not any("evil" in c[0] or c[0].startswith("http://") for c in http.calls)


def test_catsone_never_fetches_or_emits_off_host_links():
    row = '<a class="table-row" href="%s"><div class="title-cell">Evil</div></a>'
    listing = (row % "https://evil.example/careers/1/jobs/77-x") + (row % "//evil2.example/careers/1/jobs/78-y") \
        + (row % "http://good.catsone.com/careers/1/jobs/79-z") + _cats_listing(("16831828-sdr", "Ok", "Kyiv"))
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "good.catsone.com/careers": (200, listing)})
    jobs, _ = _run("catsone", {"name": "G", "token": "good.catsone.com"}, http)
    assert [j["url"] for j in jobs] == ["https://good.catsone.com/careers/90438/jobs/16831828-sdr"]
    assert not any("evil" in c[0] or c[0].startswith("http://") for c in http.calls)


def test_clinch_never_fetches_or_emits_off_host_or_non_https_locs():
    base = "https://c.example.com"
    detail = '<div class="job-description"><p>Hi.</p></div>'
    http = _stub({"c.example.com/jobs/good-london": (200, detail),
                  "sitemap.xml": (200, _sitemap("https://evil.example/jobs/evil-one", "javascript:void(0)//jobs/x",
                                                "http://c.example.com/jobs/plain-http", "https://c.example.com.evil.example/jobs/lookalike",
                                                "//evil2.example/jobs/y", base + "/jobs/good-london"))})
    jobs, _ = _run("clinch", {"name": "C", "token": "c.example.com"}, http)
    assert [j["url"] for j in jobs] == [base + "/jobs/good-london"]
    assert [c[0] for c in http.calls] == [base + "/sitemap.xml", base + "/jobs/good-london"]


def test_clinch_sitemap_with_doctype_or_entity_returns_no_rows():
    bomb = ('<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY x "https://c.example.com/jobs/boom-1">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&x;</loc></url>'
            '<url><loc>https://c.example.com/jobs/real-1</loc></url></urlset>')
    http = _stub({"sitemap.xml": (200, bomb)})
    jobs, stats = _run("clinch", {"name": "C", "token": "c.example.com"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1


# ----------------------------------------------------------------------------- wiring
def test_misc1_is_wired_into_the_kit():
    hk = _load("hunt_kit_misc1_wiring", "hunt_kit.py")
    assert hk.ATS_MODULES == ("kit_ats", "kit_ats_more", "kit_ats_extra", "kit_ats_misc1", "kit_ats_misc2")
    assert set(ALL) <= set(F) and set(ALL) <= set(hk.BOARD_SOURCES)
    from src.api.routes.hunt_kit import KIT_FILES
    assert "kit_ats_misc1.py" in KIT_FILES


def test_deeply_nested_html_keeps_the_board_and_does_not_stall():
    import time

    deep = "<p>line" * 3000
    jobs_in = [
        {"Id": "1", "JobCode": "a", "Title": "Nurse", "Description": "<p>ok"},
        {"Id": "2", "JobCode": "b", "Title": "Cook", "Description": deep},
    ]
    jobs, _ = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"IsError": False, "Jobs": jobs_in})}))
    assert [j["title"] for j in jobs] == ["Nurse", "Cook"]
    assert "line" in jobs[1]["description"]
    t0 = time.monotonic()
    m1._parse("<b>" * 12500 + "</i>" * 12500)
    assert time.monotonic() - t0 < 3
    assert m1._own_text(m1._parse("<div>" * 3000 + "hi")) == "hi"


# ----------------------------------------------------------------------------- review round 2
def test_betterteam_deeply_nested_ldjson_drops_only_that_posting():
    bomb = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    listing = '<a href="/bad-1">x</a><a href="/good-2">y</a>'
    http = _stub({"/bad-1": (200, "<html>" + bomb + "</html>"),
                  "/good-2": (200, _bt_detail("Cook", "Fry.", "2026-06-09", "Leeds", "GB")),
                  "bt.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1


def test_betterteam_cats_clinch_detail_parse_failure_drops_only_that_posting(monkeypatch):
    real = m1._parse

    def boom(text, *a, **k):
        if "BOOM" in str(text):
            raise RuntimeError("parser blew up")
        return real(text, *a, **k)

    monkeypatch.setattr(m1, "_parse", boom)
    http = _stub({"/bad-1": (200, "BOOM"), "/good-2": (200, _bt_detail("Cook", "Fry.", "2026-06-09", "Leeds", "GB")),
                  "bt.betterteam.com": (200, '<a href="/bad-1">x</a><a href="/good-2">y</a>')})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1

    http = _stub({"/jobs/1": (200, "BOOM"), "/jobs/2": (200, '<div class="job-description">Fine</div>'),
                  "c.example.com/careers": (200, _cats_listing(("1", "Nurse", "Leeds"), ("2", "Cook", "York")))})
    jobs, stats = _run("catsone", {"name": "C", "token": "c.example.com"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1

    http = _stub({"sitemap.xml": (200, _sitemap("https://k.example.com/jobs/nurse-canada", "https://k.example.com/jobs/cook-canada")),
                  "/jobs/nurse-canada": (200, "BOOM"), "/jobs/cook-canada": (200, '<div class="job-description">Fine</div>')})
    jobs, stats = _run("clinch", {"name": "K", "token": "k.example.com"}, http)
    assert sorted(j["title"] for j in jobs) == ["Cook", "Nurse"] and stats["ok"] == 1


def test_ld_posting_is_linear_on_a_huge_array_and_keeps_document_order():
    import time

    n = 200000
    blob = json.dumps([{"@type": "Thing"}] * n + [{"@type": "JobPosting", "title": "Last"}])
    root = m1._parse('<script type="application/ld+json">' + blob + "</script>")
    t0 = time.monotonic()
    assert m1._ld_posting(root)["title"] == "Last"
    assert time.monotonic() - t0 < 1
    first = m1._parse('<script type="application/ld+json">' + json.dumps(
        [{"@graph": [{"@type": "JobPosting", "title": "A"}]}, {"@type": "JobPosting", "title": "B"}]) + "</script>")
    assert m1._ld_posting(first)["title"] == "A"


# ----------------------------------------------------------------------------- one malformed href never loses the board
def test_betterteam_malformed_href_keeps_the_good_job():
    listing = '<a href="/eng-1">ok</a><a href="http://[bad/x-2">bad</a>'
    http = _stub({"/eng-1": (200, _bt_detail("Engineer", "d", "2026-06-09T15:07:41Z", "Leeds", "GB")),
                  "bt.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["url"] for j in jobs] == ["https://bt.betterteam.com/eng-1"] and stats["ok"] == 1


def test_catsone_malformed_href_keeps_the_good_job():
    bad = '<a class="table-row" href="https://[x/careers/1/jobs/6"><div class="title-cell">Bad</div></a>'
    listing = _cats_listing(("16831828-sdr", "Ok", "Kyiv")) + bad
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "good.catsone.com/careers": (200, listing)})
    jobs, stats = _run("catsone", {"name": "G", "token": "good.catsone.com"}, http)
    assert [j["url"] for j in jobs] == ["https://good.catsone.com/careers/90438/jobs/16831828-sdr"] and stats["ok"] == 1
