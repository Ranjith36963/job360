"""Offline tests for hunt_kit/kit_ats_misc4.py (ADP MyJobs, Eploy).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/adpmyjobs_test.go and eploy_test.go (MIT) and
written fresh. Nothing touches the network. Dayforce is not read (cookie session); Cornerstone and Jibe live in kit_ats_more.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m4 = _load("kit_ats_misc4_under_test", "kit_ats_misc4.py")
hk = _load("hunt_kit_misc4_under_test", "hunt_kit.py")
F = m4.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
ORGOID = "G2QKPTS5DSF914GQ"


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        calls.append((url, headers or {}, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name, row, http):
    stats: dict = {}
    return F[name](row, http, stats), stats


def _row(ats, token, **kw):
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http):
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_wiring():
    assert set(F) == {"adpmyjobs", "eploy"} and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_ats_misc4" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_ats_misc4.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_ats_misc4.py"' in served
    # the kit never reads Dayforce (cookie session): its link stays unsupported; Cornerstone and Jibe are the earlier readers
    assert hk.board_from_job_url("https://jobs.dayforcehcm.com/en-US/dcrusa/join-us")["kind"] == "unsupported"
    assert {"cornerstone", "jibe"} <= set(hk.BOARD_SOURCES)


def test_host_rules():
    info = hk.board_from_job_url("https://myjobs.adp.com/stellantisexternalcx/cx/job/5001")
    assert (info["kind"], info["ats"], info["token"]) == ("ats", "adpmyjobs", "stellantisexternalcx")
    assert hk.board_from_job_url("https://myjobs.adp.com/public/staffing/v1/career-site/x")["token"] == ""
    info = hk.board_from_job_url("https://peigroupweb.eploy.net/vacancies/442/senior-reporter--private-equity.html")
    assert (info["ats"], info["token"]) == ("eploy", "peigroupweb.eploy.net")
    # Workforce Now stays the other ADP product
    assert hk.board_from_job_url("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=a&ccId=b")["ats"] == "adp"


# --------------------------------------------------------------------------- ADP MyJobs
def _req(rid, title="Sr. SQE", posted="2026-09-03T17:28:52Z", desc=None, city="Auburn Hills"):
    r = {"reqId": rid, "jobTitle": title, "postingDate": posted,
         "requisitionLocations": [{"address": {"cityName": city, "country": {"longName": "United States"},
                                                "countrySubdivisionLevel1": {"longName": "Michigan"}}}]}
    if desc is not None:
        r["jobDescription"] = desc
    return r


def _adp_routes(reqs, *, details=None, total=None):
    """site record + list (paged by $skip) + per-requisition detail."""
    def listing(url, headers):
        skip = int(url.split("%24skip=")[1].split("&")[0])
        page = reqs[skip:skip + 50]
        return 200, {"count": len(reqs) if total is None else total, "jobRequisitions": page}
    routes = {}
    for rid, d in (details or {}).items():
        routes[f"job-requisitions/{rid}"] = d if isinstance(d, tuple) else (200, {"jobRequisitions": [d]})
    routes["job-requisitions?"] = listing
    routes["career-site/"] = (200, {"orgoid": ORGOID, "clientName": "Stellantis", "name": "Stellantis External CX"})
    return routes


def test_adp_happy_path_header_and_detail():
    detail = _req("5001", desc="&lt;p&gt;Do &lt;b&gt;quality&lt;/b&gt;.&lt;/p&gt;&lt;script&gt;steal()&lt;/script&gt;")
    http = _stub(_adp_routes([_req("5001")], details={"5001": detail}))
    jobs, stats = _run("adpmyjobs", {"name": "Stellantis", "ats": "adpmyjobs", "token": "stellantisexternalcx"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["source"] == "adpmyjobs" and j["company"] == "Stellantis"
    assert j["url"] == "https://myjobs.adp.com/stellantisexternalcx/cx/job/5001"
    assert j["title"] == "Sr. SQE" and j["posted"] == "2026-09-03"
    assert j["location"] == "Auburn Hills, Michigan, United States"
    assert "quality" in j["description"] and "steal" not in j["description"] and "<" not in j["description"]
    # the first call (the site record) learns the orgoid; every later call carries it
    assert "orgoid" not in http.calls[0][1]
    assert all(h.get("orgoid") == ORGOID for _u, h, _o in http.calls[1:])
    assert _hosts(http) == {"myjobs.adp.com", "my.adp.com"} and _job_hosts(jobs) == {"myjobs.adp.com"}


def test_adp_company_falls_back_to_client_name():
    jobs, _ = _run("adpmyjobs", {"ats": "adpmyjobs", "token": "acme"}, _stub(_adp_routes([_req("1")])))
    assert jobs[0]["company"] == "Stellantis"


def test_adp_paging():
    reqs = [_req(str(1000 + i), title=f"Role {i}") for i in range(120)]
    http = _stub(_adp_routes(reqs))
    jobs, _ = _run("adpmyjobs", _row("adpmyjobs", "acme", title_ok=lambda t: False), http)
    assert len(jobs) == 120 and len({j["url"] for j in jobs}) == 120
    skips = [u.split("%24skip=")[1] for u, *_ in http.calls if "%24skip=" in u]
    assert skips == ["0", "50", "100"]
    assert all("job-requisitions/" not in u for u, *_ in http.calls)  # no title passed: no detail request


def test_adp_site_without_orgoid_or_unknown_fails_the_board():
    http = _stub({"career-site/": (200, {"clientName": "Acme"}), "job-requisitions": (200, {"count": 0, "jobRequisitions": []})})
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme"), http)
    assert jobs == [] and stats.get("ok", 0) == 0
    assert all("job-requisitions" not in u for u, *_ in http.calls)  # never lists without the orgoid
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme"), _stub({}))
    assert jobs == [] and stats.get("ok", 0) == 0
    for bad in ("", "a/b", "a b", "../x", "a?b"):
        http = _stub({})
        assert _run("adpmyjobs", _row("adpmyjobs", bad), http)[0] == [] and http.calls == []


def test_adp_later_page_refusal_keeps_what_was_read():
    reqs = [_req(str(i)) for i in range(60)]
    routes = _adp_routes(reqs)
    first = routes["job-requisitions?"]
    routes["job-requisitions?"] = lambda u, h: first(u, h) if "%24skip=0" in u else (429, None)
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme"), _stub(routes))
    assert len(jobs) == 50 and stats["ok"] == 1


def test_adp_first_list_page_refused_fails_the_board():
    routes = _adp_routes([])
    routes["job-requisitions?"] = (403, None)
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme"), _stub(routes))
    assert jobs == [] and stats.get("ok", 0) == 0


def test_adp_detail_refusal_ends_detail_requests_but_keeps_jobs():
    reqs = [_req(str(i), title=f"Role {i}") for i in range(1, 6)]
    routes = _adp_routes(reqs)
    routes = {"job-requisitions/": (429, None), **routes}
    http = _stub(routes)
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme"), http)
    assert len(jobs) == 5 and stats["ok"] == 1
    details = [(u, o) for u, _h, o in http.calls if "job-requisitions/" in u]
    assert len(details) == 1 and details[0][1] is True  # sent once, no retry; the refusal ended the run


def test_adp_gone_detail_skips_only_that_posting():
    reqs = [_req("1", title="Gone"), _req("2", title="Kept")]
    routes = _adp_routes(reqs, details={"1": (410, None), "2": _req("2", title="Kept", desc="<p>Body here</p>")})
    jobs, _ = _run("adpmyjobs", _row("adpmyjobs", "acme"), _stub(routes))
    by = {j["title"]: j for j in jobs}
    assert by["Gone"]["description"] == "" and "Body here" in by["Kept"]["description"]


def test_adp_bad_posting_does_not_lose_the_board():
    reqs = [None, 7, {"reqId": "../etc", "jobTitle": "Evil id"}, {"reqId": "9", "jobTitle": ""}, {"reqId": True, "jobTitle": "Bool"},
            {"reqId": "10", "jobTitle": "Odd", "requisitionLocations": "nope", "postingDate": 5},
            _req("11", title="Fine")]
    jobs, stats = _run("adpmyjobs", _row("adpmyjobs", "acme", title_ok=lambda t: False), _stub(_adp_routes(reqs)))
    assert {j["title"] for j in jobs} == {"Odd", "Fine"} and stats["ok"] == 1
    assert _job_hosts(jobs) == {"myjobs.adp.com"}


def test_adp_title_filter_limits_detail_requests():
    reqs = [_req(str(i), title="Engineer" if i % 2 else "Cook") for i in range(1, 41)]
    http = _stub(_adp_routes(reqs))
    _run("adpmyjobs", _row("adpmyjobs", "acme", title_ok=lambda t: t == "Engineer"), http)
    assert sum("job-requisitions/" in u for u, *_ in http.calls) == 20
    http = _stub(_adp_routes(reqs))
    _run("adpmyjobs", _row("adpmyjobs", "acme"), http)
    assert sum("job-requisitions/" in u for u, *_ in http.calls) == m4.DETAIL_CAP


def test_adp_hostile_markup_is_linear_and_stripped():
    evil = "<p>" * 20000 + "<script>" * 5000 + "x" * 1000 + "<a href=javascript:alert(1)>" * 3000
    routes = _adp_routes([_req("5")], details={"5": _req("5", desc=evil)})
    t0 = time.time()
    jobs, _ = _run("adpmyjobs", _row("adpmyjobs", "acme"), _stub(routes))
    assert time.time() - t0 < 5 and len(jobs) == 1
    assert "<" not in jobs[0]["description"] and "javascript" not in jobs[0]["description"]


# --------------------------------------------------------------------------- Eploy
def _sitemap(*locs):
    body = "".join(f"<url><loc>{u}</loc><lastmod>2026-10-02T04:26:16+01:00</lastmod></url>" for u in locs)
    return ('<?xml version="1.0" encoding="utf-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")


PEI442 = "https://peigroupweb.eploy.net/vacancies/442/senior-reporter--private-equity.html"
PEI470 = "https://peigroupweb.eploy.net/vacancies/470/conference-producer.html"
PEI482 = "https://peigroupweb.eploy.net/vacancies/482/account-executive--new-business.html"

LD = json.dumps([{"@context": "http://schema.org", "@type": "JobPosting", "datePosted": "2026-09-14", "title": "Senior Reporter - Private Equity",
                  "description": "<p>About the role</p><script>evil()</script><ul><li>Break news</li></ul>",
                  "jobLocation": {"@type": "Place", "address": {"addressCountry": "United Kingdom", "addressLocality": "London ",
                                                                  "addressRegion": ""}}}])
LD = LD.replace("</", "<\\/")  # a JSON string cannot hold a literal </script> inside its own script element
PAGE_LD = f'<html><head><script type="application/ld+json">{LD}</script></head><body><h1 id="x_h1JobTitle">Other</h1></body></html>'
PAGE_PLAIN = ('<html><body><h1 id="ctl00_topContent_title_h1JobTitle">Plumbing Engineer - Croydon</h1>'
              '<div id="d" class="vac-details__description"><p>Fix <b>pipes</b>.</p><script>x()</script></div></body></html>')


def test_eploy_happy_path_titles_from_slug_without_pages():
    http = _stub({"live-jobs.xml": (200, _sitemap(PEI442, PEI470, PEI482))})
    jobs, stats = _run("eploy", _row("eploy", "careers.pei.group", title_ok=lambda t: False), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    assert [j["title"] for j in jobs] == ["Senior Reporter - Private Equity", "Conference Producer", "Account Executive - New Business"]
    assert all(set(j) == JOB_KEYS and j["source"] == "eploy" and j["posted"] == "" for j in jobs)  # lastmod is never the posted date
    assert [j["url"] for j in jobs] == [PEI442, PEI470, PEI482]
    assert _hosts(http) == {"careers.pei.group"}  # the title filter rejected every card: no page was opened


def test_eploy_opens_pages_for_passing_titles_only_and_reads_ld_json():
    http = _stub({"live-jobs.xml": (200, _sitemap(PEI442, PEI470)), "/vacancies/442/": (200, PAGE_LD), "/vacancies/470/": (200, PAGE_PLAIN)})
    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group", title_ok=lambda t: "Reporter" in t), http)
    by = {j["extra"]["job_id"]: j for j in jobs}
    assert by["442"]["title"] == "Senior Reporter - Private Equity" and by["442"]["posted"] == "2026-09-14"
    assert by["442"]["location"] == "London, United Kingdom" and "Break news" in by["442"]["description"]
    assert "evil" not in by["442"]["description"] and "<" not in by["442"]["description"]
    assert by["470"]["description"] == "" and by["470"]["title"] == "Conference Producer"
    assert _hosts(http) == {"careers.pei.group", "peigroupweb.eploy.net"}
    eploy_paths = [urlsplit(u).path for u, *_ in http.calls if urlsplit(u).hostname == "peigroupweb.eploy.net"]
    assert eploy_paths == ["/vacancies/442/senior-reporter--private-equity.html"]


def test_eploy_page_without_ld_json_reads_markup():
    url = "https://careers.example.com/vacancies/5348/plumbing-engineer.html"
    http = _stub({"live-jobs.xml": (200, _sitemap(url)), "/vacancies/5348/": (200, PAGE_PLAIN)})
    jobs, _ = _run("eploy", _row("eploy", "careers.example.com"), http)
    assert jobs[0]["title"] == "Plumbing Engineer - Croydon" and "pipes" in jobs[0]["description"]
    assert jobs[0]["location"] == "" and jobs[0]["posted"] == ""


def test_eploy_off_host_and_non_https_links_are_never_fetched_or_returned():
    locs = [
        "https://careers.pei.group/vacancies/500/x.html", "https://careers.pei.group/vacancies/500/x.html",  # repeated: one posting
        "https://evil.example/vacancies/1/x.html", "http://careers.pei.group/vacancies/2/x.html",
        "https://eploy.net/vacancies/3/x.html", "https://evil-eploy.net/vacancies/4/x.html",
        "https://eploy.net.evil.example/vacancies/5/x.html", "https://u:p@careers.pei.group/vacancies/6/x.html",
        "https://careers.pei.group:8443/vacancies/7/x.html", "https://careers.pei.group/vacancies/8/x.html?a=1",
        "https://careers.pei.group/vacancies/9/x.html#f", "https://careers.pei.group/about-us.html",
        "https://careers.pei.group/vacancies/abc/x.html", "javascript:alert(1)", "https://[::1/vacancies/1/x.html",
        "https://other.eploy.net/vacancies/10/ok.html",
    ]
    http = _stub({"live-jobs.xml": (200, _sitemap(*locs)), "/vacancies/": (200, PAGE_PLAIN)})
    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group"), http)
    assert sorted(j["extra"]["job_id"] for j in jobs) == ["10", "500"]
    assert _job_hosts(jobs) == {"careers.pei.group", "other.eploy.net"}
    assert all(urlsplit(j["url"]).scheme == "https" for j in jobs)
    assert _hosts(http) == {"careers.pei.group", "other.eploy.net"}


def test_eploy_first_refusal_ends_page_requests_gone_page_skips_one():
    urls = [f"https://careers.pei.group/vacancies/{i}/role-{i}.html" for i in range(1, 6)]
    http = _stub({"live-jobs.xml": (200, _sitemap(*urls)), "/vacancies/": (429, None)})
    jobs, stats = _run("eploy", _row("eploy", "careers.pei.group"), http)
    assert len(jobs) == 5 and stats["ok"] == 1
    pages = [(u, o) for u, _h, o in http.calls if "/vacancies/" in u]
    assert len(pages) == 1 and pages[0][1] is True
    http = _stub({"live-jobs.xml": (200, _sitemap(*urls[:2])), "/vacancies/1/": (410, None), "/vacancies/2/": (200, PAGE_PLAIN)})
    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group"), http)
    assert len(jobs) == 2 and jobs[1]["title"] == "Plumbing Engineer - Croydon"


def test_eploy_refusal_or_wrong_document_fails_the_board():
    index = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://careers.pei.group/a.xml</loc></sitemap></sitemapindex>'
    for resp in ((403, None), (0, None), (200, "<html><body>Sign in</body></html>"), (200, index), (200, "not xml <"),
                 (200, '<!DOCTYPE urlset [<!ENTITY a "b">]><urlset><url><loc>&a;</loc></url></urlset>')):
        jobs, stats = _run("eploy", _row("eploy", "careers.pei.group"), _stub({"live-jobs.xml": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0, resp
    jobs, stats = _run("eploy", _row("eploy", "careers.pei.group"), _stub({"live-jobs.xml": (200, "")}))
    assert jobs == [] and stats["ok"] == 1  # an empty body is an empty board
    jobs, stats = _run("eploy", _row("eploy", "careers.pei.group"), _stub({"live-jobs.xml": (200, _sitemap())}))
    assert jobs == [] and stats["ok"] == 1


def test_eploy_board_must_be_a_public_careers_host():
    for bad in ("", "careers", "https://careers.pei.group/", "a.b/c", "x.pei.group:8080", "localhost", "foo.local", "foo.internal",
                "127.0.0.1", "a b.example.com", "x@y.example.com"):
        http = _stub({})
        assert _run("eploy", _row("eploy", bad), http)[0] == [] and http.calls == [], bad


def test_eploy_bad_vacancy_does_not_lose_the_board():
    page = '<html><head><script type="application/ld+json">{broken</script></head><body></body></html>'
    urls = ["https://careers.pei.group/vacancies/1/%ff%fe-bad.html", "https://careers.pei.group/vacancies/2/fine-role.html"]
    http = _stub({"live-jobs.xml": (200, _sitemap(*urls)), "/vacancies/": (200, page)})
    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group"), http)
    assert "Fine Role" in {j["title"] for j in jobs}


def test_eploy_slug_titles():
    assert m4._eploy_title("senior-reporter--private-equity") == "Senior Reporter - Private Equity"
    assert m4._eploy_title("hr-it-manager--uk") == "HR IT Manager - UK"
    assert m4._eploy_title("--") == ""


def test_eploy_hostile_markup_is_linear():
    evil = "<div class=vac-details__description>" * 8000 + "<script>" * 5000 + "<p " * 20000
    http = _stub({"live-jobs.xml": (200, _sitemap("https://careers.pei.group/vacancies/1/x.html")), "/vacancies/1/": (200, evil)})
    t0 = time.time()
    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group"), http)
    assert time.time() - t0 < 5 and len(jobs) == 1 and "<" not in jobs[0]["description"]


def test_eploy_large_sitemap_is_asked_for_with_a_raised_cap():
    seen = {}

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        seen[url] = max_bytes
        return 200, _sitemap(*[f"https://careers.pei.group/vacancies/{i}/role-{i}.html" for i in range(1, 2001)])

    jobs, _ = _run("eploy", _row("eploy", "careers.pei.group", title_ok=lambda t: False), http)
    assert len(jobs) == 2000 and seen["https://careers.pei.group/live-jobs.xml"] == 10_000_000
