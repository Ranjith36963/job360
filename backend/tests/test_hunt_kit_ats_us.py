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
from urllib.parse import urlsplit

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
TOKEN = {"bullhorn": "91:abc123def", "careerplug": "acme", "hireology": "acme", "hiringthing": "acme.hiringthing.com",
         "hrmdirect": "acme", "isolvedhire": "acme", "paycom": "notarealpaycomkeynotarealpaycom0",
         "paycor": "notarealpaycorclientidnotareal00", "jobscore": "acme", "recruiterflow": "radhires"}


def test_registry_and_citations():
    assert set(F) == {"bullhorn", "careerplug", "hireology", "hiringthing", "hrmdirect", "isolvedhire", "paycom", "paycor",
                      "jobscore", "recruiterflow"}
    assert set(TOKEN) == set(F)
    src = (KIT_DIR / "kit_ats_us.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)
    for n in F:  # every reader names its reference file
        assert f"sources/{n}.go" in src


def test_job_links_and_extra_boards_map_to_these_readers():
    k = "notarealpaycomkeynotarealpaycom0"
    links = {
        "https://golden-corral-careers.careerplug.com/jobs/123": ("careerplug", "golden-corral-careers"),
        "https://careers.hireology.com/Acme-Co/456/description": ("hireology", "acme-co"),
        "https://4mindsai.hiringthing.com/job/789/engineer": ("hiringthing", "4mindsai.hiringthing.com"),
        "https://1aautoinc.hrmdirect.com/employment/job-opening.php?req=1": ("hrmdirect", "1aautoinc"),
        "https://acme.isolvedhire.com/jobs/1": ("isolvedhire", "acme"),
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{k}/jobs/5": ("paycom", k),
        "https://recruitingbypaycor.com/career/CareerHome.action?clientId=notarealpaycorclientidnotareal00": (
            "paycor", "notarealpaycorclientidnotareal00"),
        "https://careers.jobscore.com/careers/ierustechnologiesinc/jobs/x": ("jobscore", "ierustechnologiesinc"),
        "https://recruiterflow.com/radhires/jobs/9": ("recruiterflow", "radhires"),
    }
    for url, (ats, tok) in links.items():
        info = hk.board_from_job_url(url)
        assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, tok), url
    # a link that does not show the board key gives no token rather than a guess
    assert hk.board_from_job_url("https://www.paycomonline.net/v4/ats/web.php/portal/short/jobs/5")["token"] == ""
    rows = hk.parse_extra("bullhorn:91:abc123def,bullhorn:x:y,careerplug:acme,hiringthing:acme.hiringthing.com")
    assert [r["token"] for r in rows] == ["91:abc123def", "acme", "acme.hiringthing.com"]


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
    assert {urlsplit(c[0]).hostname for c in http.calls} == {host}  # a link off the board's host is never followed


def test_hiringthing_failed_detail_keeps_listing_title_and_empty_board_is_ok():
    host = "skijapan.hiringthing.com"
    http = _stub({"/job/": (404, None), f"https://{host}/": (200, _ht_listing(f"https://{host}/job/1/x"))})
    jobs, _ = _run("hiringthing", {"token": host}, http)
    assert [(j["title"], j["description"]) for j in jobs] == [("A job", "")]
    assert _run("hiringthing", {"token": host}, _stub({"": (200, "<html><body></body></html>")}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
    assert _run("hiringthing", {"token": "nodot"}, _stub({}))[1] == {"tried": 1, "jobs": 0}  # a board is a host


# --------------------------------------------------------------------------- HRM Direct (hrmdirect_test.go:11-66)
HRM_LIST = ('<html><body><div class="reqResult"><table class="reqResultTable">'
            '<tr><td class="posTitle"><a href="job-opening.php?req=100&amp;req_loc=11&amp;&amp;#job">Senior Platform Engineer</a></td></tr>'
            '<tr><td class="posTitle"><a href="job-opening.php?req=100&amp;req_loc=12&amp;&amp;#job">Senior Platform Engineer</a></td></tr>'
            '<tr><td class="posTitle"><a href="job-opening.php?req=200&amp;req_loc=21&amp;&amp;#job">Support Lead</a></td></tr>'
            '<tr><td class="posTitle"><a href="job-opening.php?req=200&amp;req_loc=21&amp;&amp;#job">Support Lead</a></td></tr>'
            '<tr><td><a href="job-opening.php?req=300">filter form link, no req_loc</a></td></tr>'
            '</table><a href="job-openings.php?search=true&amp;dept=7">Engineering</a></div></body></html>')
HRM_A = ('<html><body><div class="hrmWelcomeContent"><h2>Welcome to Acme</h2></div><div class="reqResult"><h2>Senior Platform Engineer</h2>'
         '<table class="viewFields"><tr><td class="viewFieldName"><b>Department:</b></td><td class="viewFieldValue">Engineering</td></tr>'
         '<tr><td class="viewFieldName"><b>Location:</b></td><td class="viewFieldValue">Austin, TX<br></td></tr></table>'
         '<div class="jobDesc"><p>Own the platform’s roadmap.</p><script>x()</script></div></div></body></html>')
HRM_B = ('<html><body><div class="reqResult"><h2>Senior Platform Engineer</h2><table class="viewFields">'
         '<tr><td class="viewFieldName"><b>Department:</b></td><td class="viewFieldValue">Engineering</td></tr>'
         '<tr><td class="viewFieldName"><b>Office:</b></td><td class="viewFieldValue">HQ</td></tr>'
         '<tr><td class="viewFieldName"><b>Location:</b></td><td class="viewFieldValue">, Remote<br></td></tr></table>'
         '<div class="jobDesc"><p>Same role, second location.</p></div></div></body></html>')
HRM_C = ('<html><body><div class="reqResult"><h2>Support Lead</h2><table class="viewFields">'
         '<tr><td class="viewFieldName"><b>Location:</b></td><td class="viewFieldValue"><br></td></tr></table>'
         '<div class="jobDesc"><p>Lead the desk.</p></div></div></body></html>')


def test_hrmdirect_one_posting_per_req_and_location_with_fields_from_the_detail_page():
    http = _stub({"req=100&req_loc=11": (200, HRM_A), "req=100&req_loc=12": (200, HRM_B), "req=200&req_loc=21": (200, HRM_C),
                  "job-openings.php": (200, HRM_LIST)})
    jobs, stats = _run("hrmdirect", {"token": "acme", "name": "Acme"}, http)
    assert [(j["title"], j["location"]) for j in jobs] == [("Senior Platform Engineer", "Austin, TX"),
                                                           ("Senior Platform Engineer", "Remote"), ("Support Lead", "")]
    assert jobs[0]["url"] == "https://acme.hrmdirect.com/employment/job-opening.php?req=100&req_loc=11"
    assert jobs[0]["description"] == "Own the platform’s roadmap." and jobs[1]["remote"] is True and jobs[2]["remote"] is None
    assert jobs[0]["extra"] == {"req": "100", "req_loc": "11"} and stats["jobs"] == 3
    assert len(http.calls) == 4 and "search=true" in http.calls[0][0]


def test_hrmdirect_keeps_listing_title_when_detail_is_missing_and_empty_board_is_ok():
    http = _stub({"req=100": (200, "<html><body>no posting block</body></html>"), "job-openings.php": (200, HRM_LIST)})
    jobs, _ = _run("hrmdirect", {"token": "acme"}, http)
    assert [j["title"] for j in jobs] == ["Senior Platform Engineer", "Senior Platform Engineer", "Support Lead"]
    empty = '<html><body>Select options from the menus above and click Search</body></html>'
    assert _run("hrmdirect", {"token": "acme"}, _stub({"": (200, empty)}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
    # raw windows-1252 bytes were decoded by the kit's http; a lone \x92 character never breaks parsing
    assert _run("hrmdirect", {"token": "acme"}, _stub({"": (200, "<html>\x92</html>")}))[1]["ok"] == 1


# --------------------------------------------------------------------------- iSolved Hire (isolvedhire_test.go:12-27)
ISO_SM = ('<?xml version="1.0" encoding="UTF-8"?><urlset><url><loc>https://acme.isolvedhire.com/jobs/1792515</loc></url>'
          '<url><loc>https://acme.isolvedhire.com/jobs/1792515.html</loc></url>'
          '<url><loc>https://acme.isolvedhire.com/jobsandemployment/classifications/Finance/286423/</loc></url>'
          '<url><loc>https://acme.isolvedhire.com/jobs/</loc></url></urlset>')
ISO_D = _ld({"@type": "JobPosting", "title": "Post-Harvest Technician", "description": "<p>Trim plants.</p><script>x()</script>",
             "datePosted": "2026-06-11 00:00:00", "hiringOrganization": {"name": "Crisp Community LLC"},
             "jobLocation": {"address": {"addressLocality": "Norwich", "addressRegion": "CT", "addressCountry": "US"}}})


def test_isolvedhire_dedups_the_sitemap_and_reads_the_posting_ld_json():
    http = _stub({"/jobs/1792515": (200, ISO_D), "sitemap.xml": (200, ISO_SM)})
    jobs, stats = _run("isolvedhire", {"token": "acme", "name": "Crisp Community"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1} and len(http.calls) == 2
    j = jobs[0]
    assert (j["title"], j["company"], j["location"], j["posted"]) == ("Post-Harvest Technician", "Crisp Community", "Norwich, CT, US", "2026-06-11")
    assert j["url"] == "https://acme.isolvedhire.com/jobs/1792515" and j["description"] == "Trim plants."


@pytest.mark.parametrize("notice", ["<html>You typed the URL for this website incorrectly.</html>",
                                    "<html><p>This career site has been disabled</p></html>"])
def test_isolvedhire_a_platform_notice_is_a_dead_board_not_an_empty_one(notice):
    assert _run("isolvedhire", {"token": "acme"}, _stub({"sitemap.xml": (200, notice)}))[1] == {"tried": 1, "jobs": 0}
    # the same words deep inside a real sitemap are not a notice
    deep = "<urlset>" + " " * 600 + "<!-- career site has been disabled --></urlset>"
    assert _run("isolvedhire", {"token": "acme"}, _stub({"sitemap.xml": (200, deep)}))[1] == {"tried": 1, "ok": 1, "jobs": 0}


def test_isolvedhire_detail_cap_and_pages_without_a_posting_are_skipped():
    sm = "<urlset>" + "".join(f"<url><loc>https://acme.isolvedhire.com/jobs/{i}</loc></url>" for i in range(1, 200)) + "</urlset>"
    http = _stub({"/jobs/": (200, "<html>no ld+json</html>"), "sitemap.xml": (200, sm)})
    jobs, stats = _run("isolvedhire", {"token": "acme"}, http)
    assert jobs == [] and stats["ok"] == 1 and len(http.calls) == 1 + us.ID_ONLY_CAP


# --------------------------------------------------------------------------- Paycom (paycom_test.go:73-110)
PC_SHELL = ('<html><head><script>var configsFromHost = {"sessionJWT":"JWT123",'
            '"atsPortalMantleServiceUrl":"https:\\/\\/portal-applicant-tracking.us-cent.paycomonline.net\\/"};</script></head><body>Loading...</body></html>')
PC_KEY = "notarealpaycomkeynotarealpaycom0"


def _paycom_http(previews=None, count=2):
    previews = previews if previews is not None else [{"jobId": 546849}, {"jobId": 537487}]
    return _stub({
        "/jobs/1": (200, PC_SHELL),
        "/api/ats/company-name": (200, {"companyName": "City Electric Supply"}),
        "/api/ats/job-posting-previews/search": (200, {"jobPostingPreviewsCount": count, "jobPostingPreviews": previews}),
        "/api/ats/job-postings/546849": (200, {"jobPosting": {"jobId": 546849, "jobTitle": "Van Driver", "location": "Centennial, CO",
                                              "remoteType": "", "description": "&lt;p&gt;Drive a van.&lt;/p&gt;", "startDate": "2026-06-20T00:00:00+00:00"}}),
        "/api/ats/job-postings/537487": (200, {"jobPosting": {"jobId": 537487, "jobTitle": "Gear Specialist", "location": "Maitland, FL",
                                              "remoteType": "Remote", "description": "<p>Quote gears.</p><script>x()</script>", "startDate": "2026-06-19"}}),
    })


def test_paycom_bootstraps_the_session_jwt_lists_previews_and_maps_postings():
    http = _paycom_http()
    jobs, stats = _run("paycom", {"token": PC_KEY, "name": "fallback"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    a, b = jobs
    assert (a["title"], a["company"], a["location"], a["posted"], a["remote"]) == ("Van Driver", "City Electric Supply", "Centennial, CO", "2026-06-20", None)
    assert a["description"] == "Drive a van." and b["remote"] is True and b["description"] == "Quote gears."
    assert a["url"] == f"https://www.paycomonline.net/v4/ats/web.php/portal/{PC_KEY}/jobs/546849"
    api = [c for c in http.calls if "portal-applicant-tracking" in c[0]]
    assert api and all(c[3].get("Authorization") == "JWT123" and c[3].get("Locale") == "en-US" for c in api)
    assert all("paycomonline.net" in c[0] for c in http.calls)  # the JWT only goes to paycomonline.net hosts
    post = next(c for c in http.calls if c[1] == "POST")
    assert post[2]["take"] == 50 and post[2]["skip"] == 0 and post[2]["filtersForQuery"]["keywordSearchText"] == ""


def test_paycom_retired_key_missing_token_and_empty_board():
    assert _run("paycom", {"token": PC_KEY}, _stub({"/jobs/1": (200, "<p>Job board does not exist.</p>")}))[1] == {"tried": 1, "jobs": 0}
    assert _run("paycom", {"token": PC_KEY}, _stub({"/jobs/1": (200, "<html>no jwt</html>")}))[1] == {"tried": 1, "jobs": 0}
    jobs, stats = _run("paycom", {"token": PC_KEY}, _paycom_http(previews=[], count=0))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


def test_paycom_pages_by_skip_and_caps_detail_requests():
    pages: list[int] = []

    def search(url, method, body):
        pages.append(body["skip"])
        return 200, {"jobPostingPreviewsCount": 500, "jobPostingPreviews": [{"jobId": body["skip"] + i + 1} for i in range(50)]}
    http = _paycom_http()
    routes = {"/jobs/1": (200, PC_SHELL), "job-posting-previews/search": search,
              "/api/ats/job-postings/": (200, {"jobPosting": {"jobId": 5, "jobTitle": "T"}}), "company-name": (404, None)}
    http = _stub(routes)
    jobs, _ = _run("paycom", {"token": PC_KEY}, http)
    assert pages == [0] and len(jobs) == us.ID_ONLY_CAP and jobs[0]["company"] == "Acme"  # one page covers the cap; name falls back to the row


# --------------------------------------------------------------------------- Paycor (paycor_test.go:11-47)
CID = "notarealpaycorclientidnotareal00"
PCR_LIST = ('<html><body>'
            f'<a href="https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={CID}&amp;id=notarealpostingid1&amp;source=&amp;lang=en">Line Cook</a>'
            f'<a href="https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={CID}&amp;id=notarealpostingid1">Apply</a>'
            f'<a href="https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={CID}">Submit Resume</a>'
            '<a href="https://recruitingbypaycor.com/career/JobIntroduction.action?clientId=notarealotherclientidnotareal000&amp;id=notarealpostingid2">Other employer</a>'
            '<a href="https://example.com/about">About us</a></body></html>')
PCR_DETAIL = ('<html><body><div id="gnewtonCareerBody"><table><tr><td id="gnewtonJobPosition">\n\t<b>Position:</b>&nbsp;\n\tLine Cook, <b>Grill</b>\n</td></tr>'
              '<tr><td><table><tr><td id="gnewtonJobLocation"><b>Location:</b>&nbsp;</td><td id="gnewtonJobLocationInfo">\n\tRemote - Dublin, Ireland<br/>\n</td></tr></table></td></tr>'
              '<tr><td id="gnewtonJobID"><b>Job Id:</b>&nbsp; 2414</td></tr>'
              '<tr><td id="gnewtonJobDescriptionText"><div><p>Cook the food.</p><script>alert(1)</script></div></td></tr></table></div></body></html>')
PCR_GONE = '<html><body><div id="gnewtonConfirmation">Sorry, this job is no longer active.</div></body></html>'


def test_paycor_dedups_the_listing_skips_foreign_boards_and_reads_labelled_cells():
    http = _stub({"JobIntroduction.action?clientId=" + CID + "&id=": (200, PCR_DETAIL), "CareerHome.action": (200, PCR_LIST)})
    jobs, stats = _run("paycor", {"token": CID, "name": "Afiniti"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1} and len(http.calls) == 2
    j = jobs[0]
    assert (j["title"], j["location"], j["description"], j["remote"]) == ("Line Cook, Grill", "Remote - Dublin, Ireland", "Cook the food.", True)
    assert j["url"] == f"https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={CID}&id=notarealpostingid1"
    assert j["company"] == "Afiniti" and j["posted"] == ""


def test_paycor_inactive_posting_is_dropped_and_a_failed_listing_fails_the_board():
    http = _stub({"JobIntroduction.action?clientId=" + CID + "&id=": (200, PCR_GONE), "CareerHome.action": (200, PCR_LIST)})
    jobs, stats = _run("paycor", {"token": CID}, http)
    assert jobs == [] and stats["ok"] == 1
    assert _run("paycor", {"token": CID}, _stub({"CareerHome.action": (500, None)}))[1] == {"tried": 1, "jobs": 0}
    assert _run("paycor", {"token": CID.upper()}, _stub({"CareerHome.action": (200, PCR_LIST)}))[1]["ok"] == 1  # the client id is case-insensitive


# --------------------------------------------------------------------------- JobScore (jobscore_test.go:12-43)
JS_FEED = {"company_name": "IERUS Technologies, Inc.", "jobs": [
    {"id": "dgLTVq3fbbbkS61af48PpK", "title": "Systems and Network Administrator",
     "detail_url": "https://careers.jobscore.com/careers/ierustechnologiesinc/jobs/systems-and-network-administrator-dgLTVq3fbbbkS61af48PpK?ref=rss&sid=68",
     "description": "<p>IERUS specializes in RF.</p><script>alert(1)</script>", "city": "Huntsville", "state": "AL", "country": "US",
     "location": "Huntsville, AL, US", "remote": "No | Must be able to work onsite all of the time", "job_type": "Full Time",
     "experience_level": "Mid-Level", "opened_date": "2026-03-31T17:04:28.293Z"},
    {"id": "remote42", "title": "Senior Front-End Engineer", "detail_url": "https://careers.jobscore.com/careers/x/jobs/fe-remote42",
     "description": "<p>Build UI.</p>", "location": "Remote in Joinville, Santa Catarina, Brazil",
     "remote": "Yes | Can work remotely all of the time", "job_type": "Contractor", "experience_level": "Experienced (Non-Manager)", "opened_date": "2026-05-01"},
    {"id": "hyb", "title": "Hybrid Role", "detail_url": "https://careers.jobscore.com/careers/x/jobs/hyb", "remote": "Yes | Can work remotely some of the time"},
    {"id": "", "title": "No id", "detail_url": "https://careers.jobscore.com/careers/x/jobs/none"},
    {"id": "nolink", "title": "No link", "detail_url": "javascript:alert(1)"},
    {"id": "foreign", "title": "Foreign host", "detail_url": "https://other.example/x"}]}


def test_jobscore_maps_the_feed_and_the_remote_string():
    http = _stub({"/jobs/ierustechnologiesinc/feed.json": (200, JS_FEED)})
    jobs, stats = _run("jobscore", {"token": "ierustechnologiesinc", "name": "IERUS Technologies"}, http)
    assert [j["title"] for j in jobs] == ["Systems and Network Administrator", "Senior Front-End Engineer", "Hybrid Role"]
    a, b, c = jobs
    assert (a["location"], a["posted"], a["remote"], a["company"]) == ("Huntsville, AL", "2026-03-31", False, "IERUS Technologies")
    assert a["extra"] == {"work_mode": "onsite", "employment_type": "Full Time", "experience_level": "Mid-Level"}
    assert b["location"] == "Remote in Joinville, Santa Catarina, Brazil" and b["remote"] is True and b["posted"] == "2026-05-01"
    assert c["extra"]["work_mode"] == "hybrid" and c["remote"] is None
    assert a["url"].startswith("https://careers.jobscore.com/careers/ierustechnologiesinc/jobs/") and len(http.calls) == 1
    assert "limit=1000" in http.calls[0][0] and stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_jobscore_company_falls_back_to_the_feed_name_and_empty_feed_is_ok():
    jobs, _ = _run("jobscore", {"token": "x", "name": ""}, _stub({"feed.json": (200, JS_FEED)}))
    assert jobs[0]["company"] == "IERUS Technologies, Inc."
    assert _run("jobscore", {"token": "x"}, _stub({"feed.json": (200, {"company_name": "X", "jobs": []})}))[1] == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- RecruiterFlow (recruiterflow_test.go:14-35)
RF_LIST = ('<html><body><script>window.jobsList = {"department": [\n'
           '["Administration", [{"apply_link": "radhires/jobs/431", "details": "LATAM", "employment_type": "Full time", "job_id": 431, "job_name": "Executive Assistant", "last_opened": "2026-09-03T19:43:26+0000", "remote_type": "Remote"}]],\n'
           '["Engineering", [{"apply_link": "radhires/jobs/369", "details": "LATAM - Buenos Aires, LATAM - Sao Paulo", "employment_type": "Contract", "job_id": 369, "job_name": "AI Engineer - LATAM {x}", "last_opened": "2026-03-02T14:48:07+0000", "remote_type": null}]]\n'
           '], "group": [], "location": []};</script></body></html>')
RF_EMPTY = '<html><body><script>window.jobsList = {"department": [], "group": [], "location": []};</script></body></html>'


def _rf_detail(title: str, desc: str) -> str:
    return _ld({"@type": "JobPosting", "title": title, "description": desc, "hiringOrganization": {"name": "Rad Hires"}})


def test_recruiterflow_flattens_departments_and_reads_each_description():
    http = _stub({"radhires/jobs/431": (200, _rf_detail("Executive Assistant", "<p>Assist.</p><script>x()</script>")),
                  "radhires/jobs/369": (200, _rf_detail("AI Engineer", "<p>Build models.</p>")), "/radhires/jobs": (200, RF_LIST)})
    jobs, stats = _run("recruiterflow", {"token": "radhires", "name": "Rad Hires"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2} and len(http.calls) == 3
    a, b = jobs
    assert (a["title"], a["url"], a["location"], a["posted"], a["remote"], a["company"]) == (
        "Executive Assistant", "https://recruiterflow.com/radhires/jobs/431", "LATAM", "2026-09-03", True, "Rad Hires")
    assert a["description"] == "Assist." and a["extra"] == {"employment_type": "Full time", "job_id": 431}
    assert b["title"] == "AI Engineer - LATAM {x}" and b["remote"] is None and b["location"].startswith("LATAM - Buenos Aires")


def test_recruiterflow_empty_board_missing_list_and_failed_detail():
    assert _run("recruiterflow", {"token": "radhires"}, _stub({"": (200, RF_EMPTY)}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
    assert _run("recruiterflow", {"token": "radhires"}, _stub({"": (200, "<html>no list</html>")}))[1] == {"tried": 1, "jobs": 0}
    assert _run("recruiterflow", {"token": "radhires"}, _stub({"": (200, "<script>window.jobsList = {broken</script>")}))[1] == {"tried": 1, "jobs": 0}
    jobs, _ = _run("recruiterflow", {"token": "radhires"}, _stub({"jobs/431": (500, None), "jobs/369": (500, None), "/radhires/jobs": (200, RF_LIST)}))
    assert [j["description"] for j in jobs] == ["", ""]  # a failing detail only costs the description
    evil = RF_LIST.replace("radhires/jobs/431", "https://evil.example/x")
    jobs, _ = _run("recruiterflow", {"token": "radhires"}, _stub({"": lambda u, m, b: (200, evil) if u.endswith("/radhires/jobs") else (404, None)}))
    assert [j["title"] for j in jobs] == ["AI Engineer - LATAM {x}"]  # a link off recruiterflow.com is dropped


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
    # 5 s, not 1 s: a shared CI runner took 1.05 s on the linear code. Every input here is sized so the old
    # quadratic code takes well over 15 s, so the budget still tells linear from quadratic.
    import time
    t = time.perf_counter()
    out = fn()
    assert time.perf_counter() - t < 5.0
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


def test_hrmdirect_and_isolvedhire_vendor_hosts_are_not_boards():
    for u in ("https://www.hrmdirect.com/pricing", "https://apply.hrmdirect.com/employment/x.html", "https://www.isolvedhire.com/"):
        assert hk.board_from_job_url(u)["kind"] != "ats", u
    assert hk.board_from_job_url("https://acme.hrmdirect.com/employment/x.html")["token"] == "acme"


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


# --------------------------------------------------------------------------- ship-rule blockers
@pytest.mark.parametrize("bad", ["https://[::1/jobs/1", "http://[/x", "https://a]b/jobs/2"])
def test_one_malformed_href_does_not_lose_a_careerplug_or_hiringthing_board(bad):
    cp = f'<html><body><a href="{bad}">Broken</a><a href="/jobs/100">Cook</a></body></html>'
    jobs, stats = _run("careerplug", {"token": "acme"}, _stub({"/jobs/100": (200, CP_A), "/jobs": (200, cp)}))
    assert [j["url"] for j in jobs] == ["https://acme.careerplug.com/jobs/100"] and stats["ok"] == 1
    host = "acme.hiringthing.com"
    ht = f'<html><body><a href="{bad}">Broken</a><a href="/job/7/cook">Cook</a></body></html>'
    jobs, stats = _run("hiringthing", {"token": host}, _stub({"/job/7": (200, _ht_detail(HT_REC)), f"https://{host}/": (200, ht)}))
    assert [j["url"] for j in jobs] == [f"https://{host}/job/7/cook"] and stats["ok"] == 1


def test_descriptions_keep_a_literal_less_than_in_bullhorn_hireology_and_hiringthing():
    text = "<p>Requires &lt;5 years of experience, salary &gt; $50k</p><p>Benefits: health</p>"
    assert us._clean(text) == "Requires <5 years of experience, salary > $50k\nBenefits: health"
    assert us._clean("&lt;p&gt;Serve guests.&lt;/p&gt;", escaped=True) == "Serve guests."
    host = "acme.hiringthing.com"
    http = _stub({"/job/1": (200, _ht_detail(dict(HT_REC, html_description=text))), f"https://{host}/": (200, _ht_listing(f"https://{host}/job/1/x"))})
    jobs = F["hiringthing"]({"name": "Acme", "ats": "hiringthing", "token": host}, http, {})  # not _run: it forbids "<"
    assert "<5 years" in jobs[0]["description"] and "Benefits: health" in jobs[0]["description"]

@pytest.mark.parametrize("name", sorted(F))
def test_every_reader_survives_hostile_pages(name):
    for page in ('<a href="/jobs/1">x</a>' * 8000, "<script>" * 40000, '<td class="viewFieldName">' * 20000):
        _fast(lambda p=page: _run(name, {"token": TOKEN[name]}, _stub({"": (200, p)})))


def test_isolvedhire_sitemap_is_linear_on_whitespace_runs_and_big_lists():
    host = "acme.isolvedhire.com"
    hostile = "<loc>" + " " * 60000
    _fast(lambda: _run("isolvedhire", {"token": "acme"}, _stub({"sitemap.xml": (200, hostile)})))
    big = "<urlset>" + "".join(f"<loc>https://{host}/jobs/{i}</loc>" for i in range(20000)) + "</urlset>"
    _fast(lambda: _run("isolvedhire", {"token": "acme"}, _stub({"sitemap.xml": (200, big)})))
    padded = "<urlset><loc>\n https://acme.isolvedhire.com/jobs/7 \n</loc></urlset>"
    http = _stub({"sitemap.xml": (200, padded), "/jobs/7": (404, None)})
    _run("isolvedhire", {"token": "acme"}, http)
    assert any(c[0].endswith("/jobs/7") for c in http.calls)


def test_recruiterflow_vendor_pages_are_not_a_board():
    for path in ("pricing", "blog/post", "login", "jobs", "about", "contact", "demo", "privacy", "terms", ""):
        assert hk.board_from_job_url("https://recruiterflow.com/" + path)["kind"] != "ats", path
    assert hk.board_from_job_url("https://recruiterflow.com/acme/jobs/12")["token"] == "acme"


# --------------------------------------------------------------------------- malformed hrefs never lose a board
def test_hrmdirect_malformed_href_next_to_a_good_one_keeps_the_good_job():
    page = ('<html><body><div class="reqResult"><a href="http://[bad/x">broken</a>'
            '<a href="job-opening.php?req=100&amp;req_loc=11">Senior Platform Engineer</a></div></body></html>')
    http = _stub({"req=100&req_loc=11": (200, HRM_A), "job-openings.php": (200, page)})
    jobs, stats = _run("hrmdirect", {"token": "acme", "name": "Acme"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1} and len(jobs) == 1


def test_paycor_malformed_href_next_to_a_good_one_keeps_the_good_job():
    page = ('<html><body><a href="http://[bad/x">broken</a>'
            f'<a href="https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={CID}&amp;id=notarealpostingid1">Line Cook</a>'
            '</body></html>')
    http = _stub({"JobIntroduction.action?clientId=" + CID + "&id=": (200, PCR_DETAIL), "CareerHome.action": (200, page)})
    jobs, stats = _run("paycor", {"token": CID, "name": "Afiniti"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1} and len(jobs) == 1
