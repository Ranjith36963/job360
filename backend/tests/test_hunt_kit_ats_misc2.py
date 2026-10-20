"""Offline tests for hunt_kit/kit_ats_misc2.py.

Fixtures are shaped like the test data in the freehire adapters' tests (internal/ingest/sources/<name>_test.go, MIT):
every response below is written inline, nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
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


m2 = _load("kit_ats_misc2_under_test", "kit_ats_misc2.py")
F = m2.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}


def _stub(routes: dict):
    """http stub: the first route whose key is in the URL wins; a callable value gets (url, method, body, headers)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, method, body, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http, *, check: bool = True):
    stats: dict = {}
    jobs = F[name]({"name": "Acme", "ats": name, **row}, http, stats)
    if check:
        for j in jobs:
            assert set(j) == JOB_KEYS and j["source"] == name and j["url"].startswith("https://") and "<" not in j["description"]
    return jobs, stats


# --------------------------------------------------------------------------- shared
def test_registry_and_hunt_kit_wiring():
    assert set(F) == {"opencats", "odoo", "recruitingsolutions", "teamex"}
    src = (KIT_DIR / "kit_ats_misc2.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire internal/ingest/sources/") >= len(F)  # every reader names its reference file
    for fname in ("opencats", "odoo", "recruitingsolutions", "teamex"):
        assert f"sources/{fname}.go" in src


BAD_TOKENS = {
    "opencats": ["", "not a host", "https://x.com/y z"],
    "odoo": ["", "nodot", "a b.com"],
    "recruitingsolutions": ["", "bad token", "x/y"],
}


@pytest.mark.parametrize(("name", "tok"), [(n, t) for n, toks in BAD_TOKENS.items() for t in toks])
def test_bad_token_makes_no_request_and_counts_not_ok(name, tok):
    http = _stub({})
    jobs, stats = _run(name, {"token": tok}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0} and http.calls == []


GOOD_TOKEN = {"opencats": "careers.boomit.pt/careers", "odoo": "jobs.amewaregroup.com", "recruitingsolutions": "riv-prod",
              "teamex": "global"}


@pytest.mark.parametrize("name", sorted(F))
@pytest.mark.parametrize("status", [403, 404, 410, 429, 503, 0])
def test_non_200_on_the_first_request_fails_the_board_after_one_request(name, status):
    http = _stub({"": (status, None)})
    jobs, stats = _run(name, {"token": GOOD_TOKEN[name]}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    assert len(http.calls) == 1


GARBAGE = [(200, None), (200, ""), (200, "garbage <<<>>> {[}"), (200, {}), (200, []), (200, [1, None, "x", {}]),
           (200, {"value": "x", "data": {"data": 3}, "hits": [], "items": None}), (200, "<html><script type='application/ld+json'>{</script>")]


@pytest.mark.parametrize("name", sorted(F))
@pytest.mark.parametrize("resp", GARBAGE)
def test_never_raises_on_garbage(name, resp):
    jobs, stats = _run(name, {"token": GOOD_TOKEN[name]}, _stub({"": resp}))
    assert isinstance(jobs, list) and stats["tried"] == 1 and stats["jobs"] == len(jobs)


@pytest.mark.parametrize("name", sorted(F))
def test_a_raising_http_never_escapes(name):
    def boom(url, **kw):
        raise RuntimeError("network down")

    jobs, stats = _run(name, {"token": GOOD_TOKEN[name]}, boom)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}


# --------------------------------------------------------------------------- OpenCATS
def _oc_stock(*rows):
    body = "".join(f'<tr class="careerPortalListItem"><td>Engineering</td><td><a href="index.php?m=careers&amp;p=showJob&amp;ID={i}">{t}</a></td><td>{loc}</td></tr>'
                   for i, t, loc in rows)
    return f'<html><body><table class="careerPortalListing"><tr><th>Department</th><th>Job Title</th><th>Location</th></tr>{body}</table></body></html>'


OC_DETAIL = """<html><body><div id="careerContent"><h1>Position Details: MuleSoft Platform Support Engineer (L2)</h1>
<table id="detailsTable"><tr><td class="detailsHeader"><strong>Location:</strong></td><td>Lisboa, Lisboa</td></tr>
<tr><td class="detailsHeader"><strong>Openings:</strong></td><td>1</td></tr></table>
<div id="descriptive"><p><strong>Description:</strong></p><p>A equipa est&aacute; a crescer.</p><ul><li>Suporte L2.</li></ul><script>alert(1)</script></div>
</div></body></html>"""


def test_opencats_happy_path_nested_mount():
    http = _stub({"p=showJob&ID=51": (200, OC_DETAIL),
                  "https://careers.boomit.pt/careers/index.php?m=careers&p=showAll": (200, _oc_stock(("51", "MuleSoft Platform Support Engineer (L2)", "Lisbon")))})
    jobs, stats = _run("opencats", {"name": "Boomit", "token": "careers.boomit.pt/careers"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert j["url"] == "https://careers.boomit.pt/careers/index.php?m=careers&p=showJob&ID=51"
    assert j["title"] == "MuleSoft Platform Support Engineer (L2)" and j["company"] == "Boomit"
    assert j["location"] == "Lisboa, Lisboa" and j["extra"]["job_id"] == "51"
    assert "Suporte L2" in j["description"] and "está" in j["description"] and "alert" not in j["description"]


def test_opencats_root_mounted_rewritten_template_with_apply_twin_and_clickable_rows():
    rewritten = ('<html><body><div class="job-card"><h3><a class="ttl" href="/index.php?m=careers&amp;p=showJob&amp;ID=30156">Security Analyst</a></h3>'
                 '<a class="btn" href="/index.php?m=careers&amp;p=showJob&amp;ID=30156">APPLY NOW</a></div>'
                 '<table><tr onclick="document.location.href=\'/index.php?m=careers&p=showJob&ID=7\'"><td>Clerk</td><td>Leeds</td></tr></table>'
                 '<a href="/index.php?m=careers&amp;p=showJob&amp;ID=99">Can\'t find what you want? Apply here</a>'
                 '<a href="/index.php?m=careers&amp;p=candidateRegistration&amp;ID=5">Register</a></div></body></html>')
    http = _stub({"p=showJob": (200, OC_DETAIL), "https://atscareers.g4s.com/index.php?m=careers&p=showAll": (200, rewritten)})
    jobs, _ = _run("opencats", {"token": "atscareers.g4s.com"}, http)
    assert [(j["extra"]["job_id"], j["title"]) for j in jobs] == [("30156", "Security Analyst"), ("7", "Clerk")]


def test_opencats_localised_label_falls_back_to_first_details_row_and_renamed_body():
    det = ('<html><body><table id="detailsTable"><tr><td><strong>Місцезнаходження:</strong></td><td>Київ, Україна</td></tr></table>'
           '<div class="job-decription"><p>Робота в магазині.</p></div></body></html>')
    http = _stub({"p=showJob&ID=406": (200, det), "p=showAll": (200, _oc_stock(("406", "Адміністратор", "Київ")))})
    jobs, _ = _run("opencats", {"token": "opencats.gorgany.com/careers"}, http)
    assert jobs[0]["location"] == "Київ, Україна" and "Робота" in jobs[0]["description"]


def test_opencats_detail_cap_title_gate_and_failed_detail_keep_listing_fields():
    rows = [(str(i), f"Engineer {i}" if i % 2 == 0 else f"Cook {i}", "X") for i in range(1, 61)]
    http = _stub({"p=showJob": (200, OC_DETAIL), "p=showAll": (200, _oc_stock(*rows))})
    jobs, _ = _run("opencats", {"token": "a.example.com", "title_ok": lambda t: t.startswith("Engineer")}, http)
    assert len(jobs) == 60  # every card is kept
    assert len(http.calls) - 1 == 25  # detail requests: the cap, and only for titles the gate accepts
    assert all(j["description"] == "" for j in jobs if j["title"].startswith("Cook"))
    http = _stub({"p=showJob": (500, None), "p=showAll": (200, _oc_stock(("1", "Dev", "X")))})
    jobs, _ = _run("opencats", {"token": "a.example.com"}, http)
    assert [j["title"] for j in jobs] == ["Dev"] and jobs[0]["description"] == ""


def test_opencats_empty_portal_and_foreign_host_links():
    jobs, stats = _run("opencats", {"token": "a.example.com"}, _stub({"p=showAll": (200, "<html><body>No openings</body></html>")}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}
    evil = '<a href="https://evil.example.org/index.php?m=careers&amp;p=showJob&amp;ID=3">Dev</a>'
    jobs, _ = _run("opencats", {"token": "a.example.com"}, _stub({"p=showAll": (200, evil)}))
    assert jobs == []


# --------------------------------------------------------------------------- Odoo
def _odoo_list(*links):
    return '<html><body><a href="/jobs">All positions</a>' + "".join(f'<a href="/jobs/detail/{x}">A Role</a>' for x in links) + "</body></html>"


def _odoo_detail(title):
    return (f'<html><body itemscope itemtype="https://schema.org/JobPosting"><h1 itemprop="title">{title}</h1>'
            '<span itemprop="datePosted" content="2026-07-10">Jul 10</span><span itemprop="addressLocality">Kyiv</span>'
            '<span itemprop="addressCountry">Ukraine</span><div itemprop="description"><p>Build things.</p><script>alert(1)</script></div></body></html>')


def test_odoo_happy_path_and_page_without_jobposting_is_dropped():
    http = _stub({"/jobs/detail/recruiter-157": (200, _odoo_detail("Recruiter")), "/jobs/detail/bare-2": (200, "<html>none</html>"),
                  "https://jobs.amewaregroup.com/jobs": (200, _odoo_list("recruiter-157", "bare-2"))})
    jobs, stats = _run("odoo", {"name": "Ameware", "token": "jobs.amewaregroup.com"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert (j["title"], j["company"], j["location"], j["posted"]) == ("Recruiter", "Ameware", "Kyiv, Ukraine", "2026-07-10")
    assert j["url"] == "https://jobs.amewaregroup.com/jobs/detail/recruiter-157" and j["extra"]["job_id"] == "157"
    assert j["description"] == "Build things."


def test_odoo_title_gate_skips_detail_requests():
    http = _stub({"/jobs/detail/": (200, _odoo_detail("Dev")), "https://a.example.com/jobs": (200, _odoo_list("dev-1", "cook-2"))})
    _run("odoo", {"token": "a.example.com", "title_ok": lambda t: False}, http)
    assert len(http.calls) == 1


def test_odoo_empty_listing():
    jobs, stats = _run("odoo", {"token": "a.example.com"}, _stub({"/jobs": (200, "<html><body>Nothing</body></html>")}))
    assert jobs == [] and stats["ok"] == 1


# --------------------------------------------------------------------------- recruiting-solutions.org
def _rs_rec(base, lang, title, company, tele="telecommute2", active=True):
    return {"jobId": f"{base}-{lang}", "title": title, "company": company, "description": "<p>Body</p><script>x()</script>",
            "teleComuteId": tele, "isActive": active, "datePosted": "2026-05-29T07:58:01Z", "country": "Germany",
            "location": {"city": "Berlin", "countryProvince": "DE-BE"},
            "link": f"https://jobsearch.createyourowncareer.com/job-invite/{base}/?locale={lang}"}


def test_recruitingsolutions_dedups_locales_drops_inactive_and_sends_customer_header():
    page = {"@odata.count": 3, "value": [_rs_rec("100", "de_DE", "Ingenieur", "Riverty Group GmbH"), _rs_rec("100", "en_GB", "Engineer", "Riverty Group GmbH"),
                                          _rs_rec("100", "en_US", "Engineer US", "Riverty Group GmbH"),
                                          _rs_rec("200", "de_DE", "Buchhalter", "BFS Health Finance GmbH", tele="telecommute3"),
                                          _rs_rec("300", "en_GB", "Closed", "Riverty Norway AS", active=False)]}
    http = _stub({"recruiting-solutions.org/search": (200, page)})
    jobs, stats = _run("recruitingsolutions", {"token": "riv-prod"}, http)
    assert stats["jobs"] == 2 and len(http.calls) == 1
    url, method, body, headers = http.calls[0]
    assert method == "POST" and headers["customerId"] == "riv-prod" and body == {"count": True, "facets": [], "filter": "", "search": "*", "skip": 0}
    by = {j["extra"]["job_id"]: j for j in jobs}
    assert by["100"]["title"] == "Engineer" and by["100"]["company"] == "Riverty Group GmbH" and by["100"]["remote"] is True
    assert by["100"]["url"] == "https://jobsearch.createyourowncareer.com/job-invite/100/" and by["100"]["location"] == "Berlin, Germany"
    assert by["200"]["title"] == "Buchhalter" and by["200"]["company"] == "BFS Health Finance GmbH" and by["200"]["extra"]["work_mode"] == "onsite"
    assert by["100"]["description"] == "Body"


def test_recruitingsolutions_pages_by_skip_until_count_and_keeps_page_one_on_later_failure():
    def pages(url, method, body, headers):
        skip = body["skip"]
        if skip == 0:
            return 200, {"@odata.count": 120, "value": [_rs_rec("1", "en_GB", "A", "X")]}
        if skip == 50:
            return 200, {"@odata.count": 120, "value": [_rs_rec("2", "en_GB", "B", "X")]}
        return 503, None

    http = _stub({"search": pages})
    jobs, _ = _run("recruitingsolutions", {"token": "t"}, http)
    assert [j["title"] for j in jobs] == ["A", "B"] and [c[2]["skip"] for c in http.calls] == [0, 50, 100]


def test_recruitingsolutions_empty_result_is_ok():
    jobs, stats = _run("recruitingsolutions", {"token": "t"}, _stub({"search": (200, {"@odata.count": 0, "value": []})}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- TeamEx
TEAMEX_PAGE = {"data": {"paging": {"totalCount": 3}, "data": [
    {"sequenceId": 1506, "title": "Senior Full-Stack Engineer", "isActive": True, "isAnonymous": False, "publishedDate": "2026-07-06T13:52:49.765Z",
     "yearsOfExperienceMin": 6, "descriptions": [{"title": "Overview", "content": "<p>Build systems.</p><script>x()</script>"}],
     "countries": [{"name": "Argentina"}, {"name": "Costa Rica"}], "requiredSkills": [{"skill": {"displayName": "Docker"}}, {"skill": {"displayName": "React"}}],
     "company": {"name": "Acme Health"}},
    {"sequenceId": 1507, "title": "QA Engineer", "isActive": True, "isAnonymous": True, "company": {"name": "Secret Corp"}, "yearsOfExperienceMin": 0,
     "descriptions": [], "countries": []},
    {"sequenceId": 1508, "title": "Closed", "isActive": False}]}}


def test_teamex_maps_posting_anonymises_and_skips_inactive():
    http = _stub({"api.teamex.io/api/jts/global/filter?pageSize=50&pageIndex=1": (200, TEAMEX_PAGE)})
    jobs, stats = _run("teamex", {"name": "TeamEx", "token": "global"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2} and len(http.calls) == 1  # totalCount reached: no second page
    a, b = jobs
    assert a["url"] == "https://teamex.io/job/1506" and a["company"] == "Acme Health" and a["location"] == "Argentina, Costa Rica"
    assert a["posted"] == "2026-07-06" and a["extra"]["years_min"] == 6 and a["extra"]["skills"] == ["Docker", "React"]
    assert "Overview" in a["description"] and "Build systems." in a["description"] and "x()" not in a["description"]
    assert b["company"] == "TeamEx" and "years_min" not in b["extra"]  # anonymous employer stays hidden; 0 is not a stated floor
    assert http.calls[0][1] == "POST" and http.calls[0][2] == {}


def test_teamex_walks_pages_and_later_failure_keeps_what_it_has():
    def page(n):
        return {"data": {"paging": {"totalCount": 500}, "data": [{"sequenceId": n, "title": f"T{n}", "isActive": True}]}}

    http = _stub({"pageIndex=1": (200, page(1)), "pageIndex=2": (200, page(2)), "pageIndex=3": (503, None)})
    jobs, _ = _run("teamex", {"token": "x"}, http)
    assert [j["title"] for j in jobs] == ["T1", "T2"] and len(http.calls) == 3


def test_teamex_empty():
    jobs, stats = _run("teamex", {"token": "x"}, _stub({"": (200, {"data": {"paging": {"totalCount": 0}, "data": []}})}))
    assert jobs == [] and stats["ok"] == 1


# --------------------------------------------------------------------------- wiring into hunt_kit.py and the served files
def test_registered_in_hunt_kit_and_served():
    hk = _load("hunt_kit_misc2_under_test", "hunt_kit.py")
    from src.api.routes.hunt_kit import KIT_FILES

    assert "kit_ats_misc2" in hk.ATS_MODULES and "kit_ats_misc2.py" in KIT_FILES
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES)) and set(F) <= set(hk.BOARD_SOURCES)
    rows = hk.parse_extra("opencats:careers.boomit.pt/careers,odoo:jobs.amewaregroup.com,evil:x")
    assert [(r["ats"], r["token"], r["name"]) for r in rows] == [
        ("opencats", "careers.boomit.pt/careers", "careers.boomit.pt"), ("odoo", "jobs.amewaregroup.com", "jobs.amewaregroup.com")]
    assert set(F) <= {r["ats"] for r in hk.load_companies(KIT_DIR / "companies.json")}  # every type has a shipped row


# --------------------------------------------------------------------------- review findings: linear scans, finite salary, path case
def _fast(fn, limit=1.0):
    t0 = time.perf_counter()
    out = fn()
    assert time.perf_counter() - t0 < limit
    return out


def test_body_strips_script_and_style_in_linear_time():
    assert m2._body("<p>a</p><SCRIPT type=x>x()</Script ><style>p{}</style >b<scripture>c</scripture>") == "a\nbc"
    assert "x()" not in m2._body("ok<script>x()") and m2._body("ok<script>x()").strip() == "ok"  # unclosed: runs to the end
    assert m2._body("ok<script") == "ok"
    for bomb in ("<script>" * 25000, "<style>" * 25000, "<script" * 40000, "<scripts>" * 25000, "<script>" + "</script" * 30000):
        _fast(lambda b=bomb: m2._body(b))


def test_opencats_nested_unclosed_anchors_stay_linear():
    page = "<html><body>" + '<a href="x">' * 8000
    http = _stub({"p=showAll": (200, page)})
    jobs, stats = _fast(lambda: _run("opencats", {"token": "a.example.com"}, http))
    assert jobs == [] and stats["ok"] == 1


def test_opencats_keeps_the_path_case_and_lowercases_only_the_host():
    http = _stub({"p=showAll": (200, _oc_stock(("5", "Engineer", "X")))})
    jobs, _ = _run("opencats", {"token": "Careers.Boomit.PT/Careers"}, http)
    assert http.calls[0][0] == "https://careers.boomit.pt/Careers/index.php?m=careers&p=showAll"
    assert jobs[0]["url"] == "https://careers.boomit.pt/Careers/index.php?m=careers&p=showJob&ID=5"
