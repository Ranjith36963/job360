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

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
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
    assert set(F) == {"opencats", "odoo", "recruitingsolutions", "teamex", "wantapply", "werecruit", "workstream", "wpyoast",
                      "careerspage", "jobboardly", "jobappnetwork"}
    src = (KIT_DIR / "kit_ats_misc2.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire internal/ingest/sources/") >= len(F)  # every reader names its reference file
    for fname in ("opencats", "odoo", "recruitingsolutions", "teamex", "wantapply", "werecruit", "workstream", "wpyoast",
                  "careerspage", "jobboardly", "jobappnetwork"):
        assert f"sources/{fname}.go" in src


BAD_TOKENS = {
    "opencats": ["", "not a host", "https://x.com/y z"],
    "odoo": ["", "nodot", "a b.com"],
    "recruitingsolutions": ["", "bad token", "x/y"],
    "werecruit": ["", "idiap", "en/", "/idiap", "en/a/b"],
    "workstream": ["", "zzzzzzzz", "965a796", "965A796B0"],
    "wpyoast": ["", "nodot", "a b.com"],
    "careerspage": ["", "-x", "a.b", "x_y"],
    "jobboardly": ["", "a.b", "https://x.jobboardly.com", "-x", "x_y"],
    "jobappnetwork": ["", "abc", "0", "-5", "1.5"],
}


@pytest.mark.parametrize(("name", "tok"), [(n, t) for n, toks in BAD_TOKENS.items() for t in toks])
def test_bad_token_makes_no_request_and_counts_not_ok(name, tok):
    http = _stub({})
    jobs, stats = _run(name, {"token": tok}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0} and http.calls == []


GOOD_TOKEN = {"opencats": "careers.boomit.pt/careers", "odoo": "jobs.amewaregroup.com", "recruitingsolutions": "riv-prod",
              "teamex": "global", "wantapply": "global", "werecruit": "en/idiap", "workstream": "965a796b",
              "wpyoast": "careers.acme.com", "careerspage": "davidjoseph-co", "jobboardly": "kubernetes-jobs",
              "jobappnetwork": "20448"}


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


# --------------------------------------------------------------------------- Wantapply
WA_SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://wantapply.cy/</loc></url><url><loc>https://wantapply.cy/android-team-lead-at-tradingview-3</loc></url>
<url><loc>https://wantapply.cy/company/tradingview</loc></url><url><loc>https://wantapply.cy/sign-in</loc></url>
<url><loc>https://example.com/some-role-at-acme</loc></url><url><loc>https://wantapply.cy/head-of-qa-remote-only-at-cloudlinux</loc></url>
<url><loc>https://wantapply.cy/no-employer-at-nobody</loc></url></urlset>"""


def _wa_detail(title, company, jlt="", desc="<p>Flat text.</p>"):
    org = f'"hiringOrganization":{{"@type":"Organization","name":"{company}"}},' if company else ""
    jl = '"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Limassol","addressCountry":"Cyprus"}},{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"Cyprus"}}]'
    return ('<html><body><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"' + title
            + '","description":"' + desc.replace('"', '\\"') + '","datePosted":"2026-09-01T10:00:00Z","employmentType":["FULL_TIME"],'
            + (f'"jobLocationType":"{jlt}",' if jlt else "") + org + jl + '}</script><div class="Description"><h2>Role</h2><p>Rich body.</p></div></body></html>')


def test_wantapply_reads_vacancy_slugs_only_and_maps_jobposting():
    http = _stub({"sitemap.xml": (200, WA_SITEMAP),
                  "android-team-lead-at-tradingview-3": (200, _wa_detail("Android Team Lead", "TradingView")),
                  "head-of-qa-remote-only-at-cloudlinux": (200, _wa_detail("Head of QA", "CloudLinux", jlt="TELECOMMUTE")),
                  "no-employer-at-nobody": (200, _wa_detail("Ghost", ""))})
    jobs, stats = _run("wantapply", {"token": "global"}, http)
    assert stats["jobs"] == 2
    assert [c[0] for c in http.calls] == ["https://wantapply.cy/sitemap.xml", "https://wantapply.cy/android-team-lead-at-tradingview-3",
                                         "https://wantapply.cy/head-of-qa-remote-only-at-cloudlinux", "https://wantapply.cy/no-employer-at-nobody"]
    a, b = jobs
    assert (a["title"], a["company"], a["location"], a["posted"]) == ("Android Team Lead", "TradingView", "Limassol, Cyprus; Cyprus", "2026-09-01")
    assert "Rich body." in a["description"] and a["url"] == "https://wantapply.cy/android-team-lead-at-tradingview-3" and a["remote"] is None
    assert b["remote"] is True and b["company"] == "CloudLinux"


def test_wantapply_stops_on_first_throttle_and_respects_title_gate():
    http = _stub({"sitemap.xml": (200, WA_SITEMAP), "android": (429, None), "head-of-qa": (200, _wa_detail("QA", "X"))})
    jobs, _ = _run("wantapply", {"token": "g"}, http)
    assert jobs == [] and len(http.calls) == 2  # sitemap + the throttled page; nothing after
    http = _stub({"sitemap.xml": (200, WA_SITEMAP)})
    _run("wantapply", {"token": "g", "title_ok": lambda t: False}, http)
    assert len(http.calls) == 1


def test_wantapply_empty_sitemap():
    jobs, stats = _run("wantapply", {"token": "g"}, _stub({"sitemap.xml": (200, "<urlset></urlset>")}))
    assert jobs == [] and stats["ok"] == 1


# --------------------------------------------------------------------------- werecruit
WR_LISTING = ('<html><script>window.allOffers = [{"Id":"49895d7f-fe57","TitleTranslated":"Postdoctoral Researcher",'
              '"Url":"https://careers.werecruit.io/en/idiap/offers/postdoctoral-researcher-49895d","Address_City":"Martigny","Address_Region":"Valais",'
              '"Address_State":"CH","TimeTranslated":"Full time","PublicationStartDate":"2026-08-20T15:07:34.2606458+00:00"},'
              '{"Id":"","TitleTranslated":"No id","Url":"https://careers.werecruit.io/en/idiap/offers/x"},'
              '{"Id":"b","TitleTranslated":"Off host","Url":"https://evil.example.org/x"}];</script></html>')
WR_DETAIL = '<html><body><div class="description rich-text"><p>Study things.</p><script>x()</script></div><div class="description-blocks">not me</div></body></html>'


def test_werecruit_happy_path():
    http = _stub({"/offers/postdoctoral-researcher-49895d": (200, WR_DETAIL), "https://careers.werecruit.io/en/idiap": (200, WR_LISTING)})
    jobs, stats = _run("werecruit", {"name": "IDIAP", "token": "en/idiap"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert (j["title"], j["company"], j["location"], j["posted"]) == ("Postdoctoral Researcher", "IDIAP", "Martigny, Valais", "2026-08-20")
    assert j["description"] == "Study things." and j["extra"] == {"job_id": "49895d7f-fe57", "country_code": "CH", "employment_type": "full_time"}


def test_werecruit_unserved_locale_is_empty_not_an_error_and_broken_json_is_an_error():
    jobs, stats = _run("werecruit", {"token": "fr/idiap"}, _stub({"/fr/idiap": (200, "<html><body>no offers here</body></html>")}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}
    jobs, stats = _run("werecruit", {"token": "fr/idiap"}, _stub({"/fr/idiap": (200, "<script>window.allOffers = [{broken</script>")}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}


def test_werecruit_detail_cap_keeps_listing_fields_past_it():
    offers = [{"Id": str(i), "TitleTranslated": f"Role {i}", "Url": f"https://careers.werecruit.io/en/t/offers/r{i}"} for i in range(40)]
    http = _stub({"/offers/": (200, WR_DETAIL), "/en/t": (200, "window.allOffers = " + json.dumps(offers) + ";")})
    jobs, _ = _run("werecruit", {"token": "en/t"}, http)
    assert len(jobs) == 40 and len(http.calls) == 1 + 25
    assert jobs[0]["description"] and jobs[30]["description"] == ""


# --------------------------------------------------------------------------- Workstream
WS_BOARD = "965a796b"
WS_POST = "/j/965a796b/moxies/pickering-79247/line-cook-09eacfc8"


def _ws_card(href, title, address, pay="", *tags):
    tag_html = "".join(f'<span class="tag tag-small">{t}</span>' for t in tags)
    pay_html = f'<div><img data-icon="rate-of-pay" src="/x.svg"/><span class="mute">{pay}</span></div>' if pay else ""
    return (f'<div class="position-card pointer" onclick="location.href=\'{href}?locale=en\'"><span><a class="b" href="{href}?locale=en">{title}</a>{tag_html}</span>'
            f'<div class="position-address mute">{address}</div><div class="position-short-desc">blurb…</div>{pay_html}'
            f'<a class="view-position-btn" href="{href}?locale=en"><img data-icon="right-arrow"/></a></div>')


def _ws_list(base, pages, *cards):
    return (f"<html><head><title>FineCasual Careers and Jobs</title></head><body>{''.join(cards)}</body>"
            f"<script>var query = {{}};var totalPages = {pages};var searchBaseUrl = '{base}';</script></html>")


WS_POSTING = '<html><body><h1>Line Cook</h1><div class="position-rich-text-content"><p>You will cook.</p><script>x()</script></div></body></html>'


def test_workstream_multi_brand_listing_then_posting():
    base = f"https://www.workstream.us/j/{WS_BOARD}/positions"
    card = _ws_card(WS_POST, "Line Cook", "1355 Kingston Rd, Pickering, ON L1V 1B8, Canada", "$17.00 - 20.00 per hour", "Full-time")
    http = _stub({WS_POST: (200, WS_POSTING), "/positions": (200, _ws_list(base, 1, card))})
    jobs, stats = _run("workstream", {"name": "FineCasual", "token": WS_BOARD}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    assert len(http.calls) == 2  # listing (page 1 was already in hand), one posting
    j = jobs[0]
    assert j["url"] == "https://www.workstream.us" + WS_POST and j["company"] == "FineCasual"  # canonical: no ?locale
    assert (j["title"], j["location"]) == ("Line Cook", "1355 Kingston Rd, Pickering, ON L1V 1B8, Canada")
    assert j["description"] == "You will cook.\n\nPay: $17.00 - 20.00 per hour" and j["posted"] == ""
    assert j["extra"] == {"posting_id": "09eacfc8", "employment_type": "full_time", "pay": "$17.00 - 20.00 per hour"}


def test_workstream_single_brand_follows_the_stated_listing_and_pages_by_total():
    brand = "https://www.workstream.us/j/a8f9405b/sarku-japan/positions"
    p1 = "/j/a8f9405b/sarku-japan/clearwater-217806/crew-member-3ae49676"
    p2 = "/j/a8f9405b/sarku-japan/tampa-1/crew-member-11111111"
    http = _stub({p1: (200, WS_POSTING), p2: (200, WS_POSTING),
                  "sarku-japan/positions?page=2": (200, _ws_list(brand, 2, _ws_card(p2, "Crew Member", "Tampa", "", "Full-time", "Part-time"))),
                  "sarku-japan/positions": (200, _ws_list(brand, 2, _ws_card(p1, "Crew Member", "Clearwater"))),
                  "/j/a8f9405b/positions": (200, _ws_list(brand, 2))})  # the redirected locations page: no cards
    jobs, _ = _run("workstream", {"token": "a8f9405b"}, http)
    assert [j["extra"]["posting_id"] for j in jobs] == ["3ae49676", "11111111"]
    assert jobs[1]["extra"]["employment_type"] == ""  # two schedules state no single type
    assert not any("page=3" in c[0] for c in http.calls)  # totalPages is exact


def test_workstream_page_failure_keeps_earlier_pages_and_bodyless_posting_has_no_description():
    base = f"https://www.workstream.us/j/{WS_BOARD}/positions"
    http = _stub({WS_POST: (200, "<html><body>no body</body></html>"), "page=2": (503, None),
                  "/positions": (200, _ws_list(base, 3, _ws_card(WS_POST, "Line Cook", "Pickering")))})
    jobs, _ = _run("workstream", {"token": WS_BOARD}, http)
    assert len(jobs) == 1 and jobs[0]["description"] == ""


def test_workstream_throttle_stops_posting_requests_and_card_cap():
    base = f"https://www.workstream.us/j/{WS_BOARD}/positions"
    cards = [_ws_card(f"/j/965a796b/b/s/job-{i:08x}", f"Cook {i}", "X") for i in range(1, 11)]
    http = _stub({"/job-": (429, None), "/positions": (200, _ws_list(base, 1, *cards))})
    jobs, _ = _run("workstream", {"token": WS_BOARD}, http)
    assert len(jobs) == 10 and len(http.calls) == 2  # one throttled posting request, then none


def test_workstream_page_that_is_not_a_career_site_fails():
    jobs, stats = _run("workstream", {"token": WS_BOARD}, _stub({"/positions": (200, "<html><body>marketing</body></html>")}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}


def test_workstream_live_career_site_with_nothing_open_is_ok_and_empty():
    base = f"https://www.workstream.us/j/{WS_BOARD}/positions"
    jobs, stats = _run("workstream", {"token": WS_BOARD}, _stub({"/positions": (200, _ws_list(base, 1))}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- WordPress / Yoast
WP_INDEX = ('<sitemapindex><sitemap><loc>https://careers.acme.com/page-sitemap.xml</loc></sitemap>'
            '<sitemap><loc>https://careers.acme.com/job_listing-sitemap.xml</loc></sitemap></sitemapindex>')
WP_JOB = "https://careers.acme.com/job/640127-electrical-engineer-corona-california/"
WP_DETAIL = ('<html><script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting","title":"Electrical Engineer III &#8211; Design",'
             '"description":"<p>Design things.</p><script>alert(1)<\\/script>","datePosted":"2026-05-19T00:00:00Z",'
             '"hiringOrganization":{"@type":"Organization","name":"Acme Group"},'
             '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Corona","addressRegion":"CA","addressCountry":"US"}}}</script></html>')


def test_wpyoast_index_to_job_sitemap_to_detail():
    sm = f'<urlset><url><loc>{WP_JOB}</loc></url><url><loc>https://careers.acme.com/page/about/</loc></url><url><loc>https://evil.example.org/job/1-x/</loc></url></urlset>'
    http = _stub({"/job/640127-": (200, WP_DETAIL), "job_listing-sitemap.xml": (200, sm), "https://careers.acme.com/sitemap.xml": (200, WP_INDEX)})
    jobs, stats = _run("wpyoast", {"name": "Fallback", "token": "careers.acme.com"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert j["title"] == "Electrical Engineer III – Design" and j["company"] == "Acme Group" and j["url"] == WP_JOB
    assert j["location"] == "Corona, CA, US" and j["posted"] == "2026-05-19" and j["description"] == "Design things." and j["extra"]["job_id"] == "640127"


def test_wpyoast_no_job_listing_sitemap_is_empty_not_an_error():
    only_pages = "<sitemapindex><sitemap><loc>https://careers.acme.com/page-sitemap.xml</loc></sitemap></sitemapindex>"
    jobs, stats = _run("wpyoast", {"token": "careers.acme.com"}, _stub({"/sitemap.xml": (200, only_pages)}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


def test_wpyoast_job_sitemap_failure_fails_board_and_detail_without_jobposting_is_dropped():
    http = _stub({"job_listing-sitemap.xml": (503, None), "/sitemap.xml": (200, WP_INDEX)})
    assert _run("wpyoast", {"token": "careers.acme.com"}, http)[1] == {"tried": 1, "jobs": 0}
    sm = f"<urlset><url><loc>{WP_JOB}</loc></url></urlset>"
    http = _stub({"/job/640127-": (200, "<html>no ld</html>"), "job_listing-sitemap.xml": (200, sm), "/sitemap.xml": (200, WP_INDEX)})
    assert _run("wpyoast", {"token": "careers.acme.com"}, http)[0] == []


# --------------------------------------------------------------------------- careers-page.com
U1, U2, U3 = "f2222f51-835a-4d0a-9435-291cd2bdaa06", "a593c5a5-855d-4660-ad73-f68ababc77fd", "b1763eee-15ef-4dc5-963d-d783ff466124"


def _cp_list(*ids):
    cards = "".join(f'<div class="job-card"><a href="/jobs/{u}">A Role</a><a href="jobs/{u}/refer">Refer</a><a href="jobs/{u}/apply">Apply</a></div>' for u in ids)
    return f'<html><body>{cards}<div class="pagination-wrapper"><a href="?page=2">Next</a></div></body></html>'


def _cp_detail(title):
    return ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"' + title + '","description":"<h2>About</h2><p>Build things.</p><script>alert(1)<\\/script>",'
            '"datePosted":"2026-06-29T19:22:10.912789+00:00","hiringOrganization":{"@type":"Organization","name":"David Joseph & Company"},'
            '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"New York","addressCountry":"United States"}}}</script></html>')


def test_careerspage_paginates_ignores_refer_apply_links_and_maps_detail():
    http = _stub({"?page=1": (200, _cp_list(U1, U2)), "?page=2": (200, _cp_list(U3)), "?page=3": (200, "<html><body></body></html>"),
                  f"/jobs/{U1}": (200, _cp_detail("Founding Software Engineer")), f"/jobs/{U2}": (200, _cp_detail("Designer")),
                  f"/jobs/{U3}": (200, _cp_detail("Analyst"))})
    jobs, stats = _run("careerspage", {"name": "Fallback Co", "token": "davidjoseph-co"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    j = jobs[0]
    assert j["url"] == f"https://davidjoseph-co.careers-page.com/jobs/{U1}" and j["title"] == "Founding Software Engineer"
    assert j["company"] == "David Joseph & Company" and j["location"] == "New York, United States" and j["posted"] == "2026-06-29"
    assert "Build things." in j["description"] and "alert" not in j["description"] and j["extra"]["job_id"] == U1
    assert [c[0] for c in http.calls[:3]] == [f"https://davidjoseph-co.careers-page.com/?page={n}" for n in (1, 2, 3)]


def test_careerspage_later_listing_failure_keeps_earlier_links_and_throttle_stops_details():
    http = _stub({"?page=1": (200, _cp_list(U1)), "?page=2": (503, None), f"/jobs/{U1}": (429, None)})
    jobs, _ = _run("careerspage", {"token": "acme"}, http)
    assert jobs == [] and len(http.calls) == 3
    http = _stub({"?page=1": (200, _cp_list(U1)), "?page=2": (503, None), f"/jobs/{U1}": (200, _cp_detail("Dev"))})
    assert len(_run("careerspage", {"token": "acme"}, http)[0]) == 1


def test_careerspage_empty_board():
    jobs, stats = _run("careerspage", {"token": "acme"}, _stub({"?page=1": (200, "<html><body></body></html>")}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- Jobboardly
JB = [
    {"title": "Kubernetes Platform Engineer", "arrangement": "fulltime", "location": "onsite", "location_limits": [], "published_at": "2026-10-16T08:00:00Z",
     "description": {"html": "<p><b>The Opportunity:</b> Run clusters.</p><script>x()</script>"},
     "company": {"name": "Booz Allen Hamilton", "location": "Fort Bragg, North Carolina, United States"},
     "salary": {"schedule": "yearly", "minimum": {"cents": 6940000, "currency_iso": "USD"}, "maximum": {"cents": 15800000, "currency_iso": "USD"}},
     "links": {"self": "https://kubernetesjobs.com/jobs/kubernetes-platform-engineer-1da9413c"}},
    {"title": "SRE", "arrangement": "parttime", "location": "remote", "location_limits": ["US"], "published_at": "2026-10-15",
     "description": {"html": "<p>Remote.</p>"}, "company": {"name": "Acme Cloud", "location": None}, "salary": None,
     "links": {"self": "https://kubernetes-jobs.jobboardly.com/jobs/sre-aaaaaaaa"}},
    {"title": "Worldwide Contractor", "arrangement": "contract", "location": "remote", "location_limits": ["Worldwide"],
     "description": {"html": ""}, "company": {"name": "Globex", "location": ""},
     "salary": {"schedule": "hourly", "minimum": {"cents": 8000, "currency_iso": "USD"}, "maximum": None},
     "links": {"self": "https://kubernetes-jobs.jobboardly.com/jobs/worldwide-contractor-bbbbbbbb"}},
    {"title": "Podcast Sponsorship Coordinator", "arrangement": None, "location": "hybrid", "application_link": "someone@example.com",
     "description": {"html": "<p>Coordinate.</p>"}, "company": {"name": "Mamas Inc", "location": "Austin, Texas"},
     "links": {"self": "https://hire-mamas.jobboardly.com/jobs/business-development-fe01dc06"}},
    {"title": "No company", "company": {"name": ""}, "links": {"self": "https://x.jobboardly.com/jobs/a-cccccccc"}},
    {"title": "Not a posting page", "company": {"name": "Z"}, "links": {"self": "https://x.jobboardly.com/other/a-dddddddd"}},
    {"title": "Mixed currencies", "company": {"name": "Z"}, "links": {"self": "https://x.jobboardly.com/jobs/mixed-eeeeeeee"},
     "salary": {"schedule": "yearly", "minimum": {"cents": 100, "currency_iso": "USD"}, "maximum": {"cents": 200, "currency_iso": "EUR"}}},
]


def test_jobboardly_maps_postings_to_their_own_employers():
    http = _stub({"https://kubernetes-jobs.jobboardly.com/jobs.json": (200, JB)})
    jobs, stats = _run("jobboardly", {"name": "Kubernetes Jobs", "token": "Kubernetes-Jobs"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 5} and len(http.calls) == 1
    a, b, c, d, e = jobs
    assert a["company"] == "Booz Allen Hamilton" and a["url"] == "https://kubernetesjobs.com/jobs/kubernetes-platform-engineer-1da9413c"
    assert a["location"] == "Fort Bragg, North Carolina, United States" and a["extra"]["work_mode"] == "onsite" and a["remote"] is None
    assert a["extra"]["employment_type"] == "full_time" and a["posted"] == "2026-10-16"
    assert (a["extra"]["salary_min"], a["extra"]["salary_max"], a["extra"]["salary_currency"], a["extra"]["salary_period"]) == (69400, 158000, "USD", "year")
    assert "The Opportunity:" in a["description"] and "x()" not in a["description"]
    assert b["remote"] is True and b["location"] == "US" and "salary_min" not in b["extra"]
    assert c["location"] == "Worldwide" and c["extra"]["salary_min"] == 80 and c["extra"]["salary_period"] == "hour" and "salary_max" not in c["extra"]
    assert d["url"] == "https://hire-mamas.jobboardly.com/jobs/business-development-fe01dc06" and d["extra"]["work_mode"] == "hybrid"
    assert "salary_min" not in e["extra"]  # two currencies state no salary


def test_jobboardly_empty_board():
    jobs, stats = _run("jobboardly", {"token": "b"}, _stub({"jobs.json": (200, [])}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- JobAppNetwork
def _jan_hit(jid, title="Shift Leader"):
    return {"_source": {"jobId": jid, "title": title, "description": "<p>Lead the shift.</p><script>x()</script>",
                        "address": {"city": "Houston", "stateOrProvince": "TX", "country": "US"}, "clientId": 20448,
                        "internalOrExternal": "externalOnly", "createdDate": "2022-02-16"}}


def test_jobappnetwork_scopes_the_search_to_one_client_and_external_postings():
    http = _stub({"proxy-es/search-en-us/posting/_search": (200, {"hits": {"total": 1, "hits": [_jan_hit(7216553)]}})})
    jobs, stats = _run("jobappnetwork", {"name": "HZ Coffee Group LLC", "token": "20448"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    url, method, body, _h = http.calls[0]
    assert url == "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search" and method == "POST"
    assert body == {"query": {"bool": {"filter": [{"term": {"clientId": 20448}}, {"term": {"internalOrExternal": "externalOnly"}}]}}, "from": 0, "size": 100}
    j = jobs[0]
    assert j["url"] == "https://apply.jobappnetwork.com/clients/20448/posting/7216553/" and j["company"] == "HZ Coffee Group LLC"
    assert (j["title"], j["location"], j["posted"], j["description"]) == ("Shift Leader", "Houston, TX, US", "2022-02-16", "Lead the shift.")


def test_jobappnetwork_pages_until_total_and_keeps_page_one_when_a_later_page_fails():
    def pager(url, method, body, headers):
        frm = body["from"]
        if frm == 0:
            return 200, {"hits": {"total": 250, "hits": [_jan_hit(i) for i in range(100)]}}
        if frm == 100:
            return 200, {"hits": {"total": {"value": 250}, "hits": [_jan_hit(i) for i in range(100, 200)]}}
        return 503, None

    http = _stub({"_search": pager})
    jobs, _ = _run("jobappnetwork", {"token": "1"}, http)
    assert len(jobs) == 200 and [c[2]["from"] for c in http.calls] == [0, 100, 200]
    http = _stub({"_search": lambda *a: (200, {"hits": {"total": 5, "hits": [_jan_hit(1)]}}) if a[2]["from"] == 0 else (200, {"hits": {"total": 5, "hits": []}})})
    assert len(_run("jobappnetwork", {"token": "1"}, http)[0]) == 1 and len(http.calls) == 2  # an empty page ends the walk


def test_jobappnetwork_empty_and_postings_without_id_are_skipped():
    jobs, stats = _run("jobappnetwork", {"token": "1"}, _stub({"_search": (200, {"hits": {"total": 0, "hits": []}})}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}
    bad = {"hits": {"total": 2, "hits": [{"_source": {"title": "No id"}}, _jan_hit(5, title="")]}}
    assert _run("jobappnetwork", {"token": "1"}, _stub({"_search": (200, bad)}))[0] == []


# --------------------------------------------------------------------------- wiring into hunt_kit.py and the served files
def test_registered_in_hunt_kit_and_served():
    hk = _load("hunt_kit_misc2_under_test", "hunt_kit.py")
    from src.api.routes.hunt_kit import KIT_FILES

    assert "kit_ats_misc2" in hk.ATS_MODULES and "kit_ats_misc2.py" in KIT_FILES
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES)) and set(F) <= set(hk.BOARD_SOURCES)
    rows = hk.parse_extra("opencats:careers.boomit.pt/careers,werecruit:en/idiap,odoo:jobs.amewaregroup.com,evil:x")
    assert [(r["ats"], r["token"], r["name"]) for r in rows] == [
        ("opencats", "careers.boomit.pt/careers", "careers.boomit.pt"), ("werecruit", "en/idiap", "idiap"), ("odoo", "jobs.amewaregroup.com", "jobs.amewaregroup.com")]
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


def test_sitemap_scans_are_linear_and_keep_index_vs_urlset():
    xml = "<sitemapindex><sitemap><loc> https://a.example/x </loc></sitemap></sitemapindex><urlset><url><loc>https://a.example/u</loc></url></urlset>"
    assert m2._locs(xml, "sitemap") == ["https://a.example/x"] and m2._locs(xml, "url") == ["https://a.example/u"]
    assert m2._locs("<url><loc>a&amp;b</loc><loc>second</loc></url><url><url><loc>c</loc>", "url") == ["a&b", "c"]
    for bomb in ("<url>" * 20000, "<url><loc>" * 20000, "<url><loc>" + " " * 400000, "<loc>" + " " * 400000 + "x"):
        _fast(lambda b=bomb: m2._locs(b, "url"))
    http = _stub({"sitemap.xml": (200, "<url>" * 20000)})
    jobs, stats = _fast(lambda: _run("wantapply", {"token": "g"}, http))
    assert jobs == [] and stats["ok"] == 1


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


def test_hostile_pages_stay_fast_for_dom_build_odoo_listing_and_detail_reads():
    # an end tag with no open element must not rescan the stack
    _fast(lambda: m2._dom("<b>" * 20000 + "</i>" * 20000), 2.0)
    # Odoo listing: the same href repeated inside nested unclosed anchors
    http = _stub({"/jobs": (200, "<html>" + '<a href="/jobs/detail/x-1">t' * 8000)})
    _fast(lambda: _run("odoo", {"token": "a.example.com"}, http, check=False), 2.0)
    # OpenCATS detail: nested unclosed td labels; Odoo detail: nested description elements
    _fast(lambda: m2._oc_location(m2._dom("<table>" + "<td>location" * 8000)), 2.0)
    page = "<html>" + '<div itemprop="description">x' * 8000
    http = _stub({"/jobs/detail/y-2": (200, page), "/jobs": (200, '<a href="/jobs/detail/y-2">Engineer</a>')})
    _fast(lambda: _run("odoo", {"token": "a.example.com"}, http, check=False), 2.0)


@pytest.mark.parametrize("bad", ["Infinity", "-Infinity", "NaN", "1e999", "1" + "0" * 400])
def test_jobboardly_non_finite_salary_keeps_the_board(bad):
    raw = ('[{"title":"SRE","company":{"name":"Acme"},"links":{"self":"https://x.jobboardly.com/jobs/sre-aaaaaaaa"},'
           '"salary":{"schedule":"yearly","minimum":{"cents":%s,"currency_iso":"USD"},"maximum":{"cents":200000,"currency_iso":"USD"}}}]' % bad)
    jobs, stats = _run("jobboardly", {"token": "b"}, _stub({"jobs.json": (200, json.loads(raw))}))
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    assert jobs[0]["extra"]["salary_max"] == 2000 and "salary_min" not in jobs[0]["extra"]
