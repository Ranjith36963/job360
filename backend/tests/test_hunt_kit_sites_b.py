"""Offline tests for src/hunt_kit/kit_sites_b.py (fixtures only, no network).

Fixture origins (freehire, MIT): Airswift sitemap and ad page follow airswift_test.go:9-33; Brief follows
briefhq_test.go:12-39; emagine listing and detail follow emagine_test.go:60-70 and :163-170; Functional Works follows
functionalworks_test.go:45-52; Golang Jobs follows the row shape of testdata/golangjobs/jobs.json and the client
pattern at golangjobs.go:65-70; Joppy follows joppy_test.go:36-56, :170-200 and :205-221; Rapyd follows
rapyd_test.go:14-36; StartupAndVC follows startupandvc_test.go:17-48; TechTree follows techtree_test.go:22-40 and
testdata/techtree_sitemap.xml. The hosts are unreachable from the build container: nothing here was checked
against a live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_sites_b_under_test", _KIT / "kit_sites_b.py")
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(sb, "PAUSE_S", 0)
    monkeypatch.setattr(sb, "AIRSWIFT_PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets the url."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append({"url": url, "method": method, "data": data, "headers": headers or {}, "as_json": as_json,
                      "once": once})
        for needle, resp in routes.items():
            if needle in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["url"].startswith("https://") and j["extra"]["attribution"]
        assert ("title_only" in j["extra"]) == (not j["description"])


def _ld(**kw):
    base = {"@context": "https://schema.org", "@type": "JobPosting"}
    return '<script type="application/ld+json">' + json.dumps({**base, **kw}) + "</script>"


def _urlset(*locs):
    return "<?xml version='1.0'?><urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in locs) + "</urlset>"


# ------------------------------------------------------------------------------------- Airswift
AS_LIVE = "https://www.airswift.com/jobs/snr-metering-engineer-1279815"
AS_EXPIRED = "https://www.airswift.com/jobs/lead-engineer-1279900"
AS_OTHER = "https://www.airswift.com/jobs/accountant-1279000"


def _as_page(title="Snr Metering Engineer"):
    return ("<html><head>" + _ld(title=title, description="&lt;p&gt;Lead metering.&lt;/p&gt;",
                                 datePosted="2026-10-01", hiringOrganization={"name": "Airswift"},
                                 jobLocation=[{"@type": "Place", "address": {"addressLocality": "Doha",
                                                                              "addressCountry": "Qatar"}}])
            + "</head><body></body></html>")


AS_EXPIRED_PAGE = ('<html><body><div class="c-jobs-article-expired o-content-editor"><p>no longer accepting '
                   "applicants.</p></div></body></html>")


def test_airswift_happy_path_expired_dropped_and_slug_prefilter():
    http = _http({"sitemap.xml": (200, _urlset(AS_LIVE, AS_EXPIRED, AS_OTHER, "https://www.airswift.com/",
                                               "https://www.airswift.com/about-us")),
                  "1279815": (200, _as_page()), "1279900": (200, AS_EXPIRED_PAGE)})
    jobs = sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=["QA"])
    _check(jobs, "airswift")
    j = jobs[0]
    assert len(jobs) == 1 and j["url"] == AS_LIVE and j["company"] == "Airswift"
    assert j["location"] == "Doha, Qatar" and j["posted"] == "2026-10-01" and "Lead metering." in j["description"]
    urls = [c["url"] for c in http.calls]
    assert urls[0] == sb.AIRSWIFT_SITEMAP and AS_OTHER not in urls and len(urls) == 3  # "accountant" never read
    assert urls[1] == AS_EXPIRED  # highest id first


def test_airswift_country_filter():
    routes = {"sitemap.xml": (200, _urlset(AS_LIVE)), "1279815": (200, _as_page())}
    assert sb.fetch_airswift(["engineer"], _http(routes), {}, days=BIG, countries=["GB"]) == []
    assert len(sb.fetch_airswift(["engineer"], _http(routes), {}, days=BIG, countries=["*"])) == 1
    assert len(sb.fetch_airswift(["engineer"], _http(routes), {}, days=BIG, countries=[])) == 1


def test_airswift_empty_sitemap_and_refusal_stops():
    http = _http({"sitemap.xml": (200, _urlset("https://www.airswift.com/"))})
    assert sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[]) == []
    assert len(http.calls) == 1
    ids = [f"https://www.airswift.com/jobs/engineer-{n}" for n in (101, 102, 103)]
    http = _http({"sitemap.xml": (200, _urlset(*ids)), "engineer-": (429, None)})
    assert sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[]) == []
    assert len(http.calls) == 2  # the sitemap and ONE refused page


# ----------------------------------------------------------------------------------------- Brief
BRIEF = ("<html><head>" + _ld(**{"@type": "Organization", "name": "Brief"}) + _ld(**{"@type": "BreadcrumbList"})
         + _ld(title="Product Builder (Forward Deployed)", url="https://briefhq.ai/careers/#product-builder",
               description="<p>Ship product with customers.</p>", datePosted="2026-07-06", jobLocationType="ON_SITE",
               hiringOrganization={"name": "Brief"},
               jobLocation={"@type": "Place", "address": {"addressLocality": "San Francisco",
                                                          "addressRegion": "CA", "addressCountry": "US"}},
               identifier={"value": "product-builder"})
         + _ld(title="Senior Product Builder (Engineering Focus)", url="https://briefhq.ai/careers/#senior-builder",
               description="<p>Own the codebase.</p>", datePosted="2026-07-06", jobLocationType="TELECOMMUTE",
               hiringOrganization={"name": "Brief"}, identifier={"value": "senior-builder"})
         + "</head><body></body></html>")


def test_briefhq_happy_path_and_country_filter():
    http = _http({"briefhq.ai/careers": (200, BRIEF)})
    jobs = sb.fetch_briefhq(["builder"], http, {}, days=BIG, countries=[])
    _check(jobs, "briefhq")
    assert [j["title"] for j in jobs] == ["Product Builder (Forward Deployed)",
                                          "Senior Product Builder (Engineering Focus)"]
    assert jobs[0]["location"] == "San Francisco, CA, US" and jobs[0]["remote"] is None
    assert jobs[1]["remote"] is True and jobs[1]["location"] == ""
    assert jobs[0]["url"] == "https://briefhq.ai/careers/#product-builder" and jobs[0]["company"] == "Brief"
    assert len(http.calls) == 1
    only = sb.fetch_briefhq(["builder"], _http({"briefhq.ai": (200, BRIEF)}), {}, days=BIG, countries=["GB"])
    assert [j["remote"] for j in only] == [True]  # the San Francisco ad is not for a UK user
    us = sb.fetch_briefhq(["builder"], _http({"briefhq.ai": (200, BRIEF)}), {}, days=BIG, countries=["US"])
    assert len(us) == 2  # "CA" in "San Francisco, CA, US" is a state, not Canada


def test_briefhq_urlless_and_refused_url_postings_all_kept():
    page = ("<html>" + _ld(title="Backend Engineer", identifier={"value": "be"})
            + _ld(title="Frontend Engineer", identifier={"value": "fe"})
            + _ld(title="Data Engineer", url="http://briefhq.ai/careers/#data", identifier={"value": "de"})
            + _ld(title="Ops Engineer") + _ld(title="Ops Engineer") + "</html>")
    jobs = sb.fetch_briefhq(["engineer"], _http({"briefhq.ai": (200, page)}), {}, days=BIG, countries=[])
    got = sorted((j["title"], j["url"]) for j in jobs)
    assert got == [("Backend Engineer", "https://briefhq.ai/careers/#be"),
                   ("Data Engineer", "https://briefhq.ai/careers/#de"),
                   ("Frontend Engineer", "https://briefhq.ai/careers/#fe"),
                   ("Ops Engineer", "https://briefhq.ai/careers/")]


def test_briefhq_empty_nonjob_blocks_and_non_200():
    http = _http({"briefhq.ai": (200, "<html>" + _ld(**{"@type": "Organization"}) + "</html>")})
    assert sb.fetch_briefhq([], http, {}, days=BIG, countries=[]) == []
    http = _http({"briefhq.ai": (503, None)})
    assert sb.fetch_briefhq([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1


# --------------------------------------------------------------------------------------- emagine
EM_LIST = {"items": [
    {"id": 178956, "title": "Senior AI Platform Engineer", "isPartTime": False,
     "jobAdWorkLocation": {"workLocationType": "Remote", "city": None, "region": None, "country": "Portugal"},
     "area": {"id": 1, "name": "Data & Analytics"}},
    {"id": 178953, "title": "Junior Business Analyst", "isPartTime": False,
     "jobAdWorkLocation": {"workLocationType": "Hybrid", "city": "Warsaw", "region": "Mazovia", "country": "Poland"},
     "area": {"id": 2, "name": "Project Delivery"}},
    {"id": 178950, "title": "Part-time Tester", "isPartTime": True, "jobAdWorkLocation": None,
     "area": {"id": 3, "name": "Test & QA"}},
    {"id": 0, "title": "No id"}, {"id": 5, "title": " "}, "garbage"], "totalCount": 6}


def test_emagine_happy_path_request_shape_filter_and_detail():
    detail = {"id": 178953, "status": "Open", "seniority": "Entry", "description": "<p>Analyse things.</p>"}
    http = _http({"/api/JobAds/Search": (200, EM_LIST), "details/178953/EN": (200, detail)})
    jobs = sb.fetch_emagine(["analyst"], http, {}, days=BIG, countries=["PL"])
    _check(jobs, "emagine")
    j = jobs[0]
    assert len(jobs) == 1 and j["title"] == "Junior Business Analyst" and j["company"] == "emagine"
    assert j["url"] == "https://portal.emagine.org/jobs/178953/junior-business-analyst"
    assert j["location"] == "Warsaw, Mazovia, Poland" and "Analyse things." in j["description"]
    assert j["posted"] == "" and j["extra"]["contract"] is True
    post = http.calls[0]
    body = json.loads(post["data"])
    assert post["method"] == "POST" and post["headers"]["Content-Type"] == "application/json"
    assert body["skipCount"] == 0 and body["maxResultCount"] == 500 and body["sorting"] == "Id asc"
    assert body["supportedLanguageId"] == "EN" and body["filter"]["textFilters"] == []
    assert all(v == [] for v in body["filter"].values()) and len(body["filter"]) == 8


def test_emagine_country_gate_filter_and_unknown_place():
    http = _http({"/api/JobAds/Search": (200, EM_LIST)})
    assert sb.fetch_emagine([], http, {}, days=BIG, countries=["US"]) == [] and http.calls == []
    http = _http({"/api/JobAds/Search": (200, EM_LIST), "details": (404, None)})
    titles = [j["title"] for j in sb.fetch_emagine([], http, {}, days=BIG, countries=["PL"])]
    # Portugal is another country; the ad with no place states none and stays
    assert sorted(titles) == ["Junior Business Analyst", "Part-time Tester"]
    part = [j for j in sb.fetch_emagine(["tester"], http, {}, days=BIG, countries=["*"])][0]
    assert part["extra"]["part_time"] is True and part["extra"]["title_only"] is True


def test_emagine_closed_detail_dropped_and_paging():
    first = {"items": [{"id": 1, "title": "One", "jobAdWorkLocation": None}], "totalCount": 2}
    second = {"items": [{"id": 2, "title": "Two", "jobAdWorkLocation": None}], "totalCount": 2}
    pages = iter([first, second])
    http = _http({"/Search": lambda url: (200, next(pages)),
                  "details/1/": (200, {"status": "Closed", "description": "x"}),
                  "details/2/": (200, {"status": "Open", "description": "<p>Open</p>"})})
    jobs = sb.fetch_emagine([], http, {}, days=BIG, countries=["DE"])
    assert [j["title"] for j in jobs] == ["Two"]
    skips = [json.loads(c["data"])["skipCount"] for c in http.calls if c["method"] == "POST"]
    assert skips == [0, 1]


def test_emagine_pages_on_raw_items_not_kept_rows():
    # 6 raw items, 3 usable: the next skipCount is 6 and the totalCount test counts raw items (emagine.go:186, :193)
    first = {**EM_LIST, "totalCount": 8}
    second = {"items": [{"id": 7, "title": "Seven", "jobAdWorkLocation": None}, {"id": 8, "title": "Eight"}],
              "totalCount": 8}
    pages = iter([first, second])
    http = _http({"/Search": lambda url: (200, next(pages)), "details": (404, None)})
    jobs = sb.fetch_emagine([], http, {}, days=BIG, countries=["*"])
    skips = [json.loads(c["data"])["skipCount"] for c in http.calls if c["method"] == "POST"]
    assert skips == [0, 6] and len(jobs) == 5


def test_emagine_empty_and_non_200():
    http = _http({"/Search": (200, {"items": [], "totalCount": 99})})
    assert sb.fetch_emagine([], http, {}, days=BIG, countries=["DE"]) == [] and len(http.calls) == 1
    http = _http({"/Search": (403, None)})
    assert sb.fetch_emagine([], http, {}, days=BIG, countries=["DE"]) == [] and len(http.calls) == 1


# ------------------------------------------------------------------------------ Functional Works
FW = {"data": {"jobs": [
    {"title": "Senior Scala Engineer", "slug": "remote-senior-scala-engineer-abc", "company": {"name": "Acme"},
     "location": {"city": "Berlin", "country": "Germany"}, "remote": True, "firstPublished": "2026-07-15T21:18:56.516Z",
     "descriptionHtml": "<p>Build &amp; ship with Scala.</p>", "tags": [{"label": "Scala"}, {"label": "Kafka"}]},
    {"title": "On-site Clojure Dev", "slug": "clojure-dev-2", "company": {"name": "Globex"},
     "location": {"city": None, "country": "France"}, "remote": False, "firstPublished": "2026-07-10T00:00:00Z",
     "descriptionHtml": "<p>Onsite role.</p>", "tags": []},
    {"title": "No slug", "slug": "", "company": {"name": "NoSlug"}},
    {"title": "No company", "slug": "has-slug-3", "company": {"name": ""}}]}}


def test_functionalworks_happy_path_request_and_country_filter():
    http = _http({"works-hub.com/api/graphql": (200, FW)})
    jobs = sb.fetch_functionalworks([], http, {}, days=BIG, countries=[])
    _check(jobs, "functionalworks")
    assert len(jobs) == 2 and len(http.calls) == 1
    j = jobs[0]
    assert j["url"] == "https://functional.works-hub.com/jobs/remote-senior-scala-engineer-abc"
    assert j["company"] == "Acme" and j["location"] == "Berlin, Germany" and j["remote"] is True
    assert j["posted"] == "2026-07-15" and "Build & ship with Scala." in j["description"]
    assert j["extra"]["tags"] == ["Scala", "Kafka"] and jobs[1]["location"] == "France"
    call = http.calls[0]
    assert call["method"] == "POST" and 'vertical:"functional"' in json.loads(call["data"])["query"]
    gb = sb.fetch_functionalworks([], _http({"graphql": (200, FW)}), {}, days=BIG, countries=["GB"])
    assert [x["company"] for x in gb] == ["Acme"]  # remote stays, the French on-site ad goes
    q = sb.fetch_functionalworks(["clojure"], _http({"graphql": (200, FW)}), {}, days=BIG, countries=["FR"])
    assert [x["company"] for x in q] == ["Globex"]


def test_functionalworks_empty_and_non_200():
    assert sb.fetch_functionalworks([], _http({"graphql": (200, {"data": {"jobs": []}})}), {}, days=BIG,
                                    countries=[]) == []
    http = _http({"graphql": (500, None)})
    assert sb.fetch_functionalworks([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1


# ---------------------------------------------------------------------------------- Golang Jobs
GO_HOME = '<html><script type="module" crossorigin src="/assets/index-AbC123_x.js"></script></html>'
GO_BUNDLE = ('var a=1;const c=new Zq("https://abcdefghij0123456789.supabase.co",'
             '"eyJhbGciOi.eyJyb2xl.c2lnbmF0dXJl");')


def _go_rows():
    now = datetime.now(timezone.utc)
    new, old = (now - timedelta(days=3)).isoformat(), (now - timedelta(days=100)).isoformat()
    return [
        {"id": "1", "title": "Senior Go Engineer", "company": "Rubix Network", "type": "full-time",
         "description": "<p>We are looking for a Senior Go Engineer.</p>", "posted_at": new, "slug": "senior-go-1",
         "application_url": "https://boards.greenhouse.io/rubix/jobs/1", "cities": {"name": "Remote", "country": "Global"}},
        {"id": "2", "title": "Go Developer", "company": "Happl", "description": "<p>x</p>", "posted_at": new,
         "slug": "go-dev-2", "application_url": "https://jobs.example.com/shared",
         "cities": {"name": "Berlin", "country": "Germany"}},
        {"id": "3", "title": "Go Developer (repost)", "company": "Happl", "description": "<p>x</p>", "posted_at": new,
         "slug": "go-dev-3", "application_url": "https://jobs.example.com/shared",
         "cities": {"name": "Berlin", "country": "Germany"}},
        {"id": "4", "title": "Ancient", "company": "Old Co", "posted_at": old, "slug": "old-4", "cities": None},
        {"id": "5", "title": "No slug", "company": "X", "posted_at": new, "slug": ""},
        {"id": "6", "title": "Remote Germany Go", "company": "Gopher GmbH", "posted_at": new, "slug": "rg-6",
         "cities": {"name": "Remote", "country": "Germany"}}]


def _go_routes(rows):
    return {"/rest/v1/jobs": (200, rows), "assets/index-": (200, GO_BUNDLE), "golangjobs.tech/": (200, GO_HOME)}


def test_golangjobs_happy_path_request_shape_urls_age_and_scope():
    http = _http(_go_routes(_go_rows()))
    jobs = sb.fetch_golangjobs(["go"], http, {}, days=BIG, countries=[])
    _check(jobs, "golangjobs")
    by = {j["company"]: j for j in jobs}
    assert set(by) == {"Rubix Network", "Happl", "Gopher GmbH"} and len(jobs) == 4  # old and slug-less are dropped
    assert by["Rubix Network"]["url"] == "https://boards.greenhouse.io/rubix/jobs/1"  # the original posting
    assert by["Rubix Network"]["remote"] is True and by["Rubix Network"]["location"] == "Remote"
    happl = [j for j in jobs if j["company"] == "Happl"]
    assert {j["url"] for j in happl} == {"https://www.golangjobs.tech/golang-jobs/go-dev-2",
                                         "https://www.golangjobs.tech/golang-jobs/go-dev-3"}  # shared link: own page
    assert happl[0]["location"] == "Berlin, Germany"
    urls = [c["url"] for c in http.calls]
    assert urls[0] == "https://www.golangjobs.tech/" and urls[1].endswith("/assets/index-AbC123_x.js")
    rest = http.calls[2]
    assert rest["url"].startswith("https://abcdefghij0123456789.supabase.co/rest/v1/jobs?")
    for part in ("is_archived=eq.false", "order=id", "limit=1000", "offset=0", "select=id,title,company"):
        assert part in rest["url"]
    assert rest["headers"]["apikey"] == "eyJhbGciOi.eyJyb2xl.c2lnbmF0dXJl"
    assert rest["headers"]["Authorization"] == "Bearer eyJhbGciOi.eyJyb2xl.c2lnbmF0dXJl"
    assert len(http.calls) == 3


def test_golangjobs_only_plain_https_links_are_emitted():
    now = datetime.now(timezone.utc).isoformat()
    bad = ["http://jobs.example.com/x", "http://user:pw@evil.example:8080/x?a=1",
           "https://user:pw@evil.example/x", "https://jobs.example.com:8443/x", "ftp://jobs.example.com/x"]
    rows = [{"id": str(i), "title": "Go Dev", "company": "Co", "posted_at": now, "slug": f"s-{i}",
             "application_url": u, "cities": None} for i, u in enumerate(bad)]
    rows.append({"id": "9", "title": "Go Dev", "company": "Co", "posted_at": now, "slug": "s-9",
                 "application_url": "https://jobs.example.com/ok?gh_jid=7", "cities": None})
    got = {j["title"] + j["url"] for j in sb.parse_golangjobs(rows)}
    assert got == {"Go Dev" + f"https://www.golangjobs.tech/golang-jobs/s-{i}" for i in range(5)} | {
        "Go Dev" + "https://jobs.example.com/ok?gh_jid=7"}


def test_golangjobs_country_filter():
    gb = sb.fetch_golangjobs([], _http(_go_routes(_go_rows())), {}, days=BIG, countries=["GB"])
    assert [j["company"] for j in gb] == ["Rubix Network"]  # Berlin and "Remote, Germany" are other countries
    de = sb.fetch_golangjobs([], _http(_go_routes(_go_rows())), {}, days=BIG, countries=["DE"])
    assert {j["company"] for j in de} == {"Rubix Network", "Happl", "Gopher GmbH"}


def test_golangjobs_empty_and_failures_stop_early():
    http = _http(_go_routes([]))
    assert sb.fetch_golangjobs([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 3
    http = _http({"golangjobs.tech/": (403, None)})
    assert sb.fetch_golangjobs([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1
    http = _http({"golangjobs.tech/": (200, "<html>no bundle</html>")})
    assert sb.fetch_golangjobs([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1
    http = _http({"assets/index-": (200, "var nothing=1"), "golangjobs.tech/": (200, GO_HOME)})
    assert sb.fetch_golangjobs([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 2
    routes = _go_routes([])
    routes["/rest/v1/jobs"] = (401, None)
    assert sb.fetch_golangjobs([], _http(routes), {}, days=BIG, countries=[]) == []


def test_golangjobs_bundle_naming_captcha_is_not_a_security_page():
    routes = _go_routes(_go_rows())
    routes["assets/index-"] = (200, GO_BUNDLE + "gotrue_meta_security:{captcha_token:e.options?.captchaToken}")
    stats: dict = {}
    jobs = sb.fetch_golangjobs([], _http(routes), stats, days=BIG, countries=[])
    assert len(jobs) == 4 and stats["ok"] == 3  # supabase-js names captchaToken; a JS bundle is not a page


def test_golangjobs_pages_until_a_short_page(monkeypatch):
    monkeypatch.setattr(sb, "GOLANG_PAGE", 2)
    rows = _go_rows()
    pages = iter([rows[:2], rows[2:3]])
    routes = _go_routes([])
    routes["/rest/v1/jobs"] = lambda url: (200, next(pages))
    http = _http(routes)
    jobs = sb.fetch_golangjobs([], http, {}, days=BIG, countries=[])
    assert len(jobs) == 3
    offsets = [c["url"].split("offset=")[1] for c in http.calls if "/rest/v1/" in c["url"]]
    assert offsets == ["0", "2"]


# ----------------------------------------------------------------------------------------- Joppy
def _joppy_sitemap():
    rows = [("https://www.joppy.me/companies/seidor", "2026-09-10T08:00:00.000Z"),
            ("https://www.joppy.me/companies/seidor/7941c018-b7c5-42f2-a92a-bced77649fc4", "2026-09-10T08:00:00.000Z"),
            ("https://www.joppy.me/companies/empty-co", "2026-09-12T08:00:00.000Z"),
            ("https://www.joppy.me/companies/broken-co", "2026-01-01T08:00:00.000Z"),
            ("https://www.joppy.me/about", "2026-09-12T08:00:00.000Z")]
    return ("<?xml version='1.0'?><urlset>"
            + "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in rows) + "</urlset>")


JOB1 = {"uid": "7941c018-b7c5-42f2-a92a-bced77649fc4", "title": "Consultor/a Senior SAP FI-CO",
        "description": "<p>Great role.</p>", "isSalaryPublic": True, "onlyEuCandidates": True, "sponsorVisa": False,
        "relocationPack": False, "skills": [{"name": "SAP", "isMandatory": True},
                                           {"name": "SAP Implementation", "isMandatory": False}],
        "place": {"isRemote": True, "isHybrid": False, "isOffice": False, "located": "Madrid, Barcelona", "cities": []},
        "languages": [{"name": "spanish", "level": 5}], "salaryMin": 40000, "salaryMax": 50000}
JOB2 = {"uid": "14d16b99-0cdb-4813-8644-dd6f3ccf0d64", "title": "Consultor/a SAP Logistica",
        "description": "<p>Second role.</p>", "isSalaryPublic": False, "sponsorVisa": True, "relocationPack": True,
        "skills": [], "place": {"isRemote": True, "isHybrid": True, "isOffice": False, "located": "Espana",
                                "cities": ["Valencia, Spain", "Barcelona, Spain"]}, "languages": [],
        "salaryMin": 45000, "salaryMax": 60000}


def _joppy_page(name, slug, *jobs):
    data = {"props": {"pageProps": {"company": {"name": name, "slug": slug, "jobs": list(jobs)}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def test_joppy_happy_path_order_gate_and_fields():
    routes = {"sitemap.directory.xml": (200, _joppy_sitemap()),
              "/companies/seidor": (200, _joppy_page("SEIDOR", "seidor", JOB1, JOB2, {"uid": "", "title": "No id"})),
              "/companies/empty-co": (200, _joppy_page("Empty Co", "empty-co")),
              "/companies/broken-co": (500, None)}
    http = _http(routes)
    jobs = sb.fetch_joppy(["sap"], http, {}, days=BIG, countries=["ES"])
    _check(jobs, "joppy")
    assert [c["url"].rsplit("/", 1)[-1] for c in http.calls] == ["sitemap.directory.xml", "empty-co", "seidor", "broken-co"]
    by = {j["extra"]["visa_sponsor"]: j for j in jobs}
    assert len(jobs) == 2 and by[False]["company"] == "SEIDOR"
    assert by[False]["url"] == "https://www.joppy.me/companies/seidor/7941c018-b7c5-42f2-a92a-bced77649fc4"
    assert by[False]["remote"] is True and by[False]["location"] == "Madrid, Barcelona, Spain"
    assert "Must-have skills: SAP." in by[False]["description"] and "Salary: 40000 - 50000 EUR" in by[False]["description"]
    assert by[True]["remote"] is None and by[True]["location"] == "Valencia, Spain; Barcelona, Spain"
    assert "work visa" in by[True]["description"] and "Salary" not in by[True]["description"]
    for cc in (["GB"], [], ["US"]):
        h = _http(routes)
        assert sb.fetch_joppy([], h, {}, days=BIG, countries=cc) == [] and h.calls == []
    assert sb.fetch_joppy([], _http(routes), {}, days=BIG, countries=["*"])


def test_joppy_empty_non_200_refusal_and_bad_page():
    http = _http({"sitemap.directory.xml": (200, "<urlset></urlset>")})
    assert sb.fetch_joppy([], http, {}, days=BIG, countries=["ES"]) == [] and len(http.calls) == 1
    http = _http({"sitemap.directory.xml": (404, None)})
    assert sb.fetch_joppy([], http, {}, days=BIG, countries=["ES"]) == [] and len(http.calls) == 1
    http = _http({"sitemap.directory.xml": (200, _joppy_sitemap()), "/companies/": (429, None)})
    assert sb.fetch_joppy([], http, {}, days=BIG, countries=["ES"]) == [] and len(http.calls) == 2
    assert sb.parse_joppy_company("<html>no data</html>") == []
    assert sb.parse_joppy_company('<script id="__NEXT_DATA__">{not json</script>') == []
    assert sb.parse_joppy_company('<script id="__NEXT_DATA__">{"props":{}}</script>') == []


# ----------------------------------------------------------------------------------------- Rapyd
RP_POS1 = "https://www.rapyd.net/company/careers/positions/finance-data-developer-bogota-colombia/"
RP_LIST = ('<html><body><a href="/company/careers/positions/finance-data-developer-bogota-colombia/">Finance</a>'
           f'<a href="{RP_POS1}">Apply</a>'
           '<a href="/company/careers/positions/sales-manager-bogota-colombia/">Sales</a>'
           '<a href="/company/careers/">All</a><a href="/company/about/">About</a></body></html>')


def _rp_page(title="Finance Data Analyst", where="Bogota, Colombia"):
    return (f'<html><body><div class="country-term"><h4>{where}</h4></div>'
            f'<h1 class="vcex-heading"><span>{title}</span></h1>'
            '<div class="single-career-position__main"><div class="job-details"><h3>Description</h3>'
            "<p>Build <b>pipelines</b>.</p><h3>Requirements</h3><ul><li>Python</li></ul></div></div>"
            '<div class="footer"><a href="/company/partners">Partners</a></div></body></html>')


def test_rapyd_happy_path_prefilter_and_country():
    routes = {"careers-search": (200, RP_LIST), "finance-data-developer": (200, _rp_page())}
    http = _http(routes)
    jobs = sb.fetch_rapyd(["finance"], http, {}, days=BIG, countries=["CO"])
    _check(jobs, "rapyd")
    j = jobs[0]
    assert len(jobs) == 1 and j["url"] == RP_POS1 and j["title"] == "Finance Data Analyst" and j["company"] == "Rapyd"
    assert j["location"] == "Bogota, Colombia" and "pipelines" in j["description"] and "Python" in j["description"]
    assert "Partners" not in j["description"]
    assert [c["url"] for c in http.calls] == [sb.RAPYD_LIST, RP_POS1]  # the sales ad was never read
    assert sb.fetch_rapyd(["finance"], _http(routes), {}, days=BIG, countries=["GB"]) == []
    assert len(sb.fetch_rapyd(["finance"], _http(routes), {}, days=BIG, countries=[])) == 1


def test_rapyd_failed_detail_skips_only_that_one_and_refusal_stops():
    routes = {"careers-search": (200, RP_LIST), "sales-manager": (404, None),
              "finance-data-developer": (200, _rp_page())}
    http = _http(routes)
    jobs = sb.fetch_rapyd([], http, {}, days=BIG, countries=[])
    assert len(jobs) == 1 and len(http.calls) == 3
    routes["finance-data-developer"] = (429, None)
    http = _http(routes)
    assert sb.fetch_rapyd([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 2


def test_rapyd_empty_and_non_200_and_no_heading():
    assert sb.fetch_rapyd([], _http({"careers-search": (200, "<html></html>")}), {}, days=BIG, countries=[]) == []
    http = _http({"careers-search": (503, None)})
    assert sb.fetch_rapyd([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1
    assert sb.parse_rapyd_ad("<html><body><p>layout page</p></body></html>", RP_POS1) is None


# ------------------------------------------------------------------------------- StartupAndVC
SV_LIST = ('<html><body><a href="/venture-capital-jobs/associate-9a2fb">A Role</a>'
           '<a href="/venture-capital-jobs/associate-9a2fb">A Company</a>'
           '<a href="/venture-capital-jobs/analyst-39b3f">Analyst</a>'
           '<a href="/venture-capital-jobs">All jobs</a><a href="/about-us">About</a></body></html>')
SV_APPLY = "https://www.linkedin.com/jobs/view/4417182287/?eBP=xyz&trk=abc?utm_source=startupandvc"


def _sv_page(title="Associate", company="Acme Ventures", apply=SV_APPLY, locality="London", country="UK"):
    btn = f'<a href="{apply}" target="_blank" class="button-large fw w-button">Apply now</a>' if apply else ""
    return ("<html><head></head><body><h1>" + title + "</h1>"
            + _ld(title=title, description=f"<p>{company} is looking for a {title}.</p>", datePosted="Jul 07, 2026",
                  hiringOrganization={"name": company},
                  jobLocation={"@type": "Place", "address": {"addressLocality": locality, "addressCountry": country}})
            + '<div class="w-richtext"><h2>POSITION SUMMARY</h2><p>Real body with detail.</p>'
              "<script>alert(1)</script></div>" + btn + "</body></html>")


def test_startupandvc_happy_path_original_link_and_prefilter():
    http = _http({"venture-capital-jobs/associate-9a2fb": (200, _sv_page()),
                  "venture-capital-jobs": (200, SV_LIST)})
    jobs = sb.fetch_startupandvc(["associate"], http, {}, days=BIG, countries=["GB"])
    _check(jobs, "startupandvc")
    j = jobs[0]
    assert len(jobs) == 1 and j["url"] == "https://www.linkedin.com/jobs/view/4417182287/"  # tracking cut off
    assert j["company"] == "Acme Ventures" and j["posted"] == "2026-07-07" and j["location"] == "London, UK"
    assert "Real body with detail." in j["description"] and "alert" not in j["description"]
    assert [c["url"] for c in http.calls][0] == sb.SVC_LIST and len(http.calls) == 2  # "analyst" never read


def test_startupandvc_links_stay_on_the_boards_own_host():
    page = ('<a href="https://evil.example/venture-capital-jobs/x-1">x</a>'
            '<a href="https://www.startupandvc.com/venture-capital-jobs/ok-1">ok</a>'
            '<a href="//evil.example/venture-capital-jobs/y-2">y</a>')
    assert sb.svc_links(page) == ["https://www.startupandvc.com/venture-capital-jobs/ok-1"]


def test_startupandvc_no_apply_button_falls_back_and_drops_unusable():
    page = _sv_page(apply="")
    http = _http({"associate-9a2fb": (200, page), "analyst-39b3f": (200, "<html>no posting</html>"),
                  "venture-capital-jobs": (200, SV_LIST)})
    jobs = sb.fetch_startupandvc([], http, {}, days=BIG, countries=[])
    assert [j["url"] for j in jobs] == ["https://www.startupandvc.com/venture-capital-jobs/associate-9a2fb"]
    assert sb.parse_startupandvc_ad(_sv_page(company=""), "https://x.example/a") is None
    assert sb.fetch_startupandvc([], _http({"associate": (200, _sv_page(locality="Paris", country="France")),
                                            "venture-capital-jobs": (200, SV_LIST)}), {}, days=BIG,
                                 countries=["GB"]) == []


def test_startupandvc_empty_non_200_and_refusal():
    assert sb.fetch_startupandvc([], _http({"venture-capital-jobs": (200, "<html></html>")}), {}, days=BIG,
                                 countries=[]) == []
    http = _http({"venture-capital-jobs": (500, None)})
    assert sb.fetch_startupandvc([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1
    http = _http({"associate-9a2fb": (403, None), "venture-capital-jobs": (200, SV_LIST)})
    assert sb.fetch_startupandvc([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 2


# --------------------------------------------------------------------------------------- TechTree
TT1 = "https://jobs.techtree.dev/job/8773660e-c2c3-4b49-9d03-6f9f76859bc8"
TT2 = "https://jobs.techtree.dev/job/0515f6f5-8f30-4cb6-abd2-16bf87137a4d"
TT3 = "https://jobs.techtree.dev/job/e00478de-1575-4da7-bbba-ea2505c9545b"


def _tt_page(company="Telepatia", where="Latin America (remote)", prose=True):
    ldp = dict(title="Mid/Senior AI Engineer, New Products", description="short summary",
               datePosted="2026-09-11T07:46:06.765320", employmentType="FULL_TIME",
               jobLocation={"@type": "Place", "address": where})
    if company:
        ldp["hiringOrganization"] = {"@type": "Organization", "name": company}
    body = '<div class="prose prose-base"><p>Full body text.</p><ul><li>Python</li></ul></div>' if prose else ""
    return "<html><head>" + _ld(**ldp) + "</head><body>" + body + "</body></html>"


def test_techtree_happy_path_company_from_ad_and_drops():
    http = _http({"sitemap.xml": (200, _urlset("https://jobs.techtree.dev/", "https://jobs.techtree.dev/talent-scout",
                                               TT1 + "?tp=abc", TT2, TT3)),
                  "8773660e": (200, _tt_page()), "0515f6f5": (200, _tt_page(company="")),
                  "e00478de": (200, _tt_page(prose=False))})
    jobs = sb.fetch_techtree([], http, {}, days=BIG, countries=["GB"])
    _check(jobs, "techtree")
    j = jobs[0]
    assert len(jobs) == 1 and j["url"] == TT1 and j["company"] == "Telepatia" and j["remote"] is True
    assert j["location"] == "Latin America (remote)" and j["posted"] == "2026-09-11"
    assert "Full body text." in j["description"] and "short summary" not in j["description"]
    assert len(http.calls) == 4


def test_techtree_empty_non_200_refusal_and_query():
    assert sb.fetch_techtree([], _http({"sitemap.xml": (200, _urlset("https://jobs.techtree.dev/"))}), {}, days=BIG,
                             countries=[]) == []
    http = _http({"sitemap.xml": (502, None)})
    assert sb.fetch_techtree([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 1
    http = _http({"sitemap.xml": (200, _urlset(TT1, TT2)), "job/": (429, None)})
    assert sb.fetch_techtree([], http, {}, days=BIG, countries=[]) == [] and len(http.calls) == 2
    ok = _http({"sitemap.xml": (200, _urlset(TT1)), "8773660e": (200, _tt_page())})
    assert sb.fetch_techtree(["plumber"], ok, {}, days=BIG, countries=[]) == []  # the page is read, then matched


# ------------------------------------------------------------------------------- all readers
RUN_ARGS = {"airswift": [], "briefhq": [], "emagine": ["DE"], "functionalworks": [], "golangjobs": [],
            "joppy": ["ES"], "rapyd": [], "startupandvc": [], "techtree": []}


def test_feeds_dict_complete():
    assert set(sb.FEEDS) == set(RUN_ARGS) and all(callable(f) for f in sb.FEEDS.values())


@pytest.mark.parametrize("name", sorted(RUN_ARGS))
@pytest.mark.parametrize("status", [0, 403, 404, 429, 500, 503])
def test_first_request_not_200_stops_the_run(name, status):
    http = _http({"": (status, None)})
    stats: dict = {}
    assert sb.FEEDS[name](["engineer"], http, stats, days=BIG, countries=RUN_ARGS[name]) == []
    assert len(http.calls) == 1 and stats["jobs"] == 0 and stats["tried"] == 1 and stats["ok"] == 0


@pytest.mark.parametrize("name", sorted(RUN_ARGS))
@pytest.mark.parametrize("body", ["garbage <<< {[", 12345, b"\x00\xff", [], {}, None, "<html></html>", '{"a": 1}'])
def test_garbage_never_raises_and_requests_stay_capped(name, body):
    http = _http({"": (200, body)})
    out = sb.FEEDS[name](["engineer"], http, {}, days=BIG, countries=RUN_ARGS[name])
    assert out == [] and len(http.calls) <= sb.SITES_MAX_REQUESTS


@pytest.mark.parametrize("name", sorted(RUN_ARGS))
def test_http_that_raises_is_contained(name):
    def boom(url, **kw):
        raise RuntimeError("network down")

    assert sb.FEEDS[name](["engineer"], boom, {}, days=BIG, countries=RUN_ARGS[name]) == []


def test_gated_feeds_send_no_request_for_other_countries():
    for name, cc in (("joppy", ["GB", "US", "DE"]), ("emagine", ["US", "ES", "BR"])):
        http = _http({"": (200, {})})
        assert sb.FEEDS[name](["x"], http, {}, days=BIG, countries=cc) == [] and http.calls == []
        assert sb.FEEDS[name](["x"], http, {}, days=BIG, countries=[]) == [] and http.calls == []


# ----------------------------------------------------------------------------------- helpers
def test_place_ok_rules():
    ok = sb._place_ok
    assert ok("San Francisco, CA, US", ["US"]) and not ok("San Francisco, CA, US", ["GB", "CA"])
    assert ok("Atlanta, US", ["US"]) and not ok("Atlanta, US", ["DE"])
    assert ok("Doha, Qatar", ["QA"]) and not ok("Doha, Qatar", ["GB"])
    assert ok("London, UK", ["GB"]) and ok("London, United Kingdom", ["UK"])
    assert ok("Somewhere", ["GB"]) and ok("", ["GB"]) and ok("Latin America (remote)", ["GB"])
    assert ok("Paris, France", ["GB"], remote=True) and ok("Paris, France", ["*"]) and ok("Paris, France", [])
    assert ok("Indianapolis, Indiana", ["GB"])  # "Indiana" is not India: no country is named, so it stays
    assert sb._iso_hits("Indianapolis, Indiana") == set()


def test_slug_hit_and_remote_helpers():
    assert sb._slug_hit("snr-metering-engineer", ["Senior Metering Engineer"])
    assert not sb._slug_hit("accountant", ["data engineer"]) and sb._slug_hit("anything", [])
    assert sb._slug_hit("anything", ["to"])  # no significant word: nothing to prefilter on
    assert sb._remote("Remote, Europe") is True and sb._remote("Berlin") is None


def test_parsers_survive_garbage_inputs():
    for fn, args in ((sb.parse_airswift_ad, (None, "https://x")), (sb.parse_briefhq, (123,)),
                     (sb.parse_emagine, ("x",)), (sb.parse_functionalworks, ({"data": []},)),
                     (sb.parse_golangjobs, ({"a": 1},)), (sb.parse_joppy_company, (None,)),
                     (sb.parse_rapyd_ad, (None, "https://x")), (sb.parse_startupandvc_ad, (None, "https://x")),
                     (sb.parse_techtree_ad, (None, "https://x")), (sb.rapyd_links, (None,)), (sb.svc_links, (5,))):
        assert fn(*args) in ([], None)


# ------------------------------------------------------------- stop on non-200 / bot check (mid-run)
_AS_IDS = [f"https://www.airswift.com/jobs/engineer-{n}" for n in (101, 102, 103, 104, 105)]
_BOT_PAGE = "<html><head><title>Just a moment...</title></head><body>Verify you are human</body></html>"


@pytest.mark.parametrize("status", [0, 500, 503])
def test_mid_run_server_error_stops_the_run(status):
    served = {"n": 0}

    def page(url):
        served["n"] += 1
        return (200, _as_page("Engineer")) if served["n"] == 1 else (status, None)

    http = _http({"sitemap.xml": (200, _urlset(*_AS_IDS)), "engineer-": page})
    jobs = sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[])
    assert len(jobs) == 1
    assert len(http.calls) == 3  # sitemap, the good ad, ONE failing ad; the other three never sent


def test_all_ads_503_sends_sitemap_plus_one_page():
    http = _http({"sitemap.xml": (200, _urlset(*_AS_IDS)), "engineer-": (503, None)})
    assert sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[]) == []
    assert len(http.calls) == 2


def test_captcha_page_on_first_ad_stops_the_run():
    http = _http({"sitemap.xml": (200, _urlset(*_AS_IDS)), "engineer-": (200, _BOT_PAGE)})
    assert sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[]) == []
    assert len(http.calls) == 2


def test_gone_ad_is_skipped_but_the_run_goes_on():
    http = _http({"sitemap.xml": (200, _urlset(*_AS_IDS[:3])), "engineer-103": (410, None),
                  "engineer-102": (404, None), "engineer-101": (200, _as_page("Engineer"))})
    jobs = sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[])
    assert len(jobs) == 1 and len(http.calls) == 4


def test_real_page_with_login_words_is_not_a_block():
    page = _as_page("Engineer").replace("<body>", "<body>Access denied? No. Just a moment of your time.")
    http = _http({"sitemap.xml": (200, _urlset(*_AS_IDS[:2])), "engineer-": (200, page)})
    assert len(sb.fetch_airswift(["engineer"], http, {}, days=BIG, countries=[])) == 2


def test_ld_json_block_with_spaced_or_upper_case_closing_tag():
    page = ('<SCRIPT type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Welder"})
            + "</SCRIPT >")
    assert [p["title"] for p in sb._ld_postings(page)] == ["Welder"]


HOSTS = {"airswift": "www.airswift.com", "briefhq": "briefhq.ai", "emagine": "portal.emagine.org",
         "functionalworks": "functional.works-hub.com", "golangjobs": "www.golangjobs.tech", "joppy": "www.joppy.me",
         "rapyd": "www.rapyd.net", "startupandvc": "www.startupandvc.com", "techtree": "jobs.techtree.dev"}


def test_feeds_are_registered_with_the_kit_and_on_by_default():
    sys.modules.pop("hunt_kit_sites_b_check", None)
    spec = importlib.util.spec_from_file_location("hunt_kit_sites_b_check", _KIT / "hunt_kit.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_sites_b_check"] = hk
    spec.loader.exec_module(hk)  # type: ignore[union-attr]
    assert set(sb.FEEDS) <= set(hk.FEED_SOURCES) and "kit_sites_b" in hk.FEED_MODULES
    assert set(sb.FEEDS) <= set(hk.select_sources("default"))
    assert set(sb.FEEDS) <= set(hk._merged("FEEDS", hk.FEED_MODULES))
    hosts = dict(hk._FEED_HOSTS)
    for feed, host in HOSTS.items():
        assert hosts[feed].search(host), host
    assert not any(rx.search("careers.globex.example") for _, rx in hk._FEED_HOSTS)
    from src.api.routes.hunt_kit import KIT_FILES
    assert "kit_sites_b.py" in KIT_FILES


# ------------------------------------------------------------------------------ review findings
def _hunt_kit_module():
    spec = importlib.util.spec_from_file_location("hunt_kit_sites_b_review", _KIT / "hunt_kit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_sites_b_review"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _ask_why(mod, url):
    import argparse
    return mod.why_missed(argparse.Namespace(sources="default", companies="", companies_extra="",
                                             skip_companies="", work_countries="", titles="", days=30), url)


def test_feed_hosts_cover_only_the_hosts_the_readers_fetch():
    mod = _hunt_kit_module()
    hosts = dict(mod._FEED_HOSTS)
    read = {"airswift": ["www.airswift.com", "airswift.com"], "briefhq": ["briefhq.ai", "www.briefhq.ai"],
            "emagine": ["portal.emagine.org"], "rapyd": ["www.rapyd.net", "rapyd.net"],
            "startupandvc": ["www.startupandvc.com", "startupandvc.com"], "techtree": ["jobs.techtree.dev"],
            "golangjobs": ["www.golangjobs.tech"], "joppy": ["www.joppy.me"],
            "functionalworks": ["functional.works-hub.com"]}
    for feed, names in read.items():
        for host in names:
            assert hosts[feed].search(host), (feed, host)
    other = {"emagine": ["www.emagine.org", "emagine.org", "portal-api.emagine.org"], "rapyd": ["careers.rapyd.net"],
             "briefhq": ["app.briefhq.ai"], "airswift": ["careers.airswift.com"],
             "startupandvc": ["blog.startupandvc.com"], "techtree": ["www.techtree.dev"],
             "golangjobs": ["blog.golangjobs.tech"], "joppy": ["app.joppy.me"],
             "functionalworks": ["www.functional.works-hub.com"]}
    for feed, names in other.items():
        for host in names:
            assert not hosts[feed].search(host), (feed, host)
    for url in ("https://www.emagine.org/careers/1", "https://careers.rapyd.net/jobs/1"):
        assert _ask_why(mod, url)["verdict"] != "feed_covered", url
    r = _ask_why(mod, "https://portal.emagine.org/jobs/178953/x")
    assert (r["verdict"], r["board"]) == ("feed_covered", "feed:emagine")


def test_golangjobs_reads_newest_rows_first_and_warns_when_capped(monkeypatch, capsys):
    monkeypatch.setattr(sb, "GOLANG_PAGE", 2)
    monkeypatch.setattr(sb, "GOLANG_PAGES", 2)
    rows = _go_rows()
    routes = _go_routes([])
    routes["/rest/v1/jobs"] = lambda url: (200, rows[:2])  # always a full page: the board is bigger than the cap
    http = _http(routes)
    sb.fetch_golangjobs([], http, {}, days=BIG, countries=[])
    rest = [c["url"] for c in http.calls if "/rest/v1/" in c["url"]]
    assert len(rest) == 2 and all("order=id.desc" in u and "order=id&" not in u for u in rest)
    assert "golangjobs page cap reached" in capsys.readouterr().err
    routes["/rest/v1/jobs"] = (200, rows[:1])  # a short page: nothing left behind, nothing to warn about
    sb.fetch_golangjobs([], _http(routes), {}, days=BIG, countries=[])
    assert "page cap" not in capsys.readouterr().err


def _em_page(ids, total):
    return {"items": [{"id": i, "title": f"Ad {i}", "jobAdWorkLocation": None} for i in ids], "totalCount": total}


def test_emagine_starts_at_the_newest_ads_when_the_board_outgrows_the_cap(monkeypatch, capsys):
    monkeypatch.setattr(sb, "EMAGINE_PAGE", 2)
    monkeypatch.setattr(sb, "EMAGINE_PAGES", 2)
    everything = list(range(1, 11))  # ids 1..10, oldest first; the cap reads 4
    skips: list[int] = []

    def route(url):  # the skipCount of the request being answered is the last one recorded below
        return 200, _em_page(everything[skips[-1]:skips[-1] + 2], 10)

    inner = _http({"/Search": route, "details": (404, None)})

    def http2(url, **kw):
        if kw.get("data"):
            skips.append(json.loads(kw["data"])["skipCount"])
        return inner(url, **kw)

    http2.calls = inner.calls  # type: ignore[attr-defined]
    jobs = sb.fetch_emagine([], http2, {}, days=BIG, countries=["*"])
    assert sorted(j["extra"]["emagine_id"] for j in jobs) == [7, 8, 9, 10]  # the newest four, not 1..4
    assert skips == [0, 6, 8]  # one probe, then the two pages that end at totalCount
    err = capsys.readouterr().err
    assert "emagine lists 10 ads; 6 were not read" in err


def test_emagine_reads_everything_and_stays_quiet_below_the_cap(monkeypatch, capsys):
    monkeypatch.setattr(sb, "EMAGINE_PAGE", 2)
    monkeypatch.setattr(sb, "EMAGINE_PAGES", 3)
    pages = iter([_em_page([1, 2], 5), _em_page([3, 4], 5), _em_page([5], 5)])
    http = _http({"/Search": lambda url: (200, next(pages)), "details": (404, None)})
    jobs = sb.fetch_emagine([], http, {}, days=BIG, countries=["*"])
    assert len(jobs) == 5
    assert [json.loads(c["data"])["skipCount"] for c in http.calls if c["method"] == "POST"] == [0, 2, 4]
    assert "were not read" not in capsys.readouterr().err


def test_emagine_one_gone_detail_does_not_end_the_other_details():
    first = _em_page([30, 20, 10], 3)
    http = _http({"/Search": (200, first), "details/30/": (404, None),
                  "details/20/": (200, {"status": "Open", "description": "<p>Twenty</p>"}),
                  "details/10/": (200, {"status": "Closed", "description": "x"})})
    jobs = sb.fetch_emagine([], http, {}, days=BIG, countries=["*"])
    detail_urls = [c["url"] for c in http.calls if "details" in c["url"]]
    assert len(detail_urls) == 3  # the 404 on the newest ad ended nothing
    by = {j["extra"]["emagine_id"]: j for j in jobs}
    assert set(by) == {30, 20} and "Twenty" in by[20]["description"]  # the Closed one is dropped


def test_emagine_pauses_before_every_listing_page_after_the_first(monkeypatch):
    monkeypatch.setattr(sb, "EMAGINE_PAGE", 1)
    events: list[str] = []
    monkeypatch.setattr(sb, "_pause", lambda seconds=None: events.append("pause"))
    pages = iter([_em_page([1], 3), _em_page([2], 3), _em_page([3], 3)])

    def search(url):
        events.append("post")
        return 200, next(pages)

    http = _http({"/Search": search, "details": (404, None)})
    sb.fetch_emagine([], http, {}, days=BIG, countries=["*"], )
    assert events[:5] == ["post", "pause", "post", "pause", "post"]


def test_script_scan_is_linear_on_unclosed_tags_and_keeps_case_and_space_variants():
    import time
    opener = '<script type="application/ld+json">{"x":'
    start = time.perf_counter()
    assert sb._ld_postings(opener * 8000) == []
    assert sb.parse_joppy_company('<script id="__NEXT_DATA__" ' * 8000) == []
    assert sb._ld_postings('<script ' * 8000) == []
    assert time.perf_counter() - start < 1.0
    page = '<SCRIPT TYPE="application/ld+json">{"@type":"JobPosting","title":"A"}</SCRIPT >'
    assert [p["title"] for p in sb._ld_postings(page)] == ["A"]
    # `</scripts>` does not close; a script of another type is skipped, not consumed
    page = ('<script type="text/javascript">var a;</script>'
            '<script type="application/ld+json">{"@type":"JobPosting","title":"B</scripts>"}</script\n>')
    assert [p["title"] for p in sb._ld_postings(page)] == ["B</scripts>"]
    assert sb.parse_joppy_company(_joppy_page("Acme", "acme", JOB1).replace("</script>", "</SCRIPT >"))
    late = '<script type="application/ld+json">{"@type":"JobPosting","title":"Z"}</script>'
    assert sb._ld_postings("x" * (sb.SCAN_MAX + 10) + late) == []  # past the scan cap
    assert [p["title"] for p in sb._ld_postings("x" * 1000 + late)] == ["Z"]


def test_every_request_is_sent_once_so_a_429_is_not_retried():
    http = _http({"sitemap.xml": (429, None), "briefhq": (429, None), "functionalworks": (429, None)})
    for fn in (sb.fetch_airswift, sb.fetch_briefhq, sb.fetch_functionalworks):
        fn(["engineer"], http, {}, days=BIG, countries=["*"])
    assert http.calls and all(c["once"] is True for c in http.calls)


def test_brief_job_link_must_be_https_on_briefhq():
    def one(u):
        return sb.parse_briefhq(_ld(title="Engineer", url=u, hiringOrganization={"name": "Brief"}))[0]["url"]
    assert one("http://evil.example/phish") == sb.BRIEFHQ_URL
    assert one("https://evil.example/x") == sb.BRIEFHQ_URL
    assert one("http://briefhq.ai/careers/x") == sb.BRIEFHQ_URL
    assert one("https://www.briefhq.ai/careers/x") == "https://www.briefhq.ai/careers/x"


def test_airswift_sitemap_http_locs_are_not_matched():
    assert not sb._AS_JOB.match("http://www.airswift.com/jobs/engineer-123")
    assert sb._AS_JOB.match("https://www.airswift.com/jobs/engineer-123")


def test_new_south_wales_is_not_the_uk():
    assert sb._iso_hits("Sydney, New South Wales, Australia") == {"AU"}
    assert not sb._place_ok("Sydney, New South Wales, Australia", ["GB"])
    assert sb._place_ok("Cardiff, Wales", ["GB"])


# ------------------------------------------------------------------ final review findings
def test_startupandvc_malformed_apply_href_keeps_the_other_ads():
    lst = ('<a href="/venture-capital-jobs/a-1">a</a><a href="/venture-capital-jobs/b-2">b</a>')
    http = _http({"venture-capital-jobs/a-1": (200, _sv_page(apply="https://www.linkedin.com/jobs/view/1/")),
                  "venture-capital-jobs/b-2": (200, _sv_page(title="Analyst", apply="https://[bad/x")),
                  "venture-capital-jobs": (200, lst)})
    jobs = sb.fetch_startupandvc([], http, {}, days=BIG, countries=[])
    assert sorted(j["url"] for j in jobs) == ["https://www.linkedin.com/jobs/view/1/",
                                              "https://www.startupandvc.com/venture-capital-jobs/b-2"]


def test_startupandvc_apply_link_rejects_userinfo_and_keeps_port():
    assert sb._svc_apply_link("https://www.linkedin.com@evil.example/jobs/view/1/") == ""
    assert sb._svc_apply_link("https://u:p@evil.example/x") == ""
    assert sb._svc_apply_link("https://jobs.example:8443/a/b?x=1#f") == "https://jobs.example/a/b"
    assert sb._svc_apply_link("javascript:alert(1)") == ""
    page = _sv_page(apply="https://www.linkedin.com@evil.example/jobs/view/1/")
    job = sb.parse_startupandvc_ad(page, "https://www.startupandvc.com/venture-capital-jobs/a-1")
    assert job and job["url"] == "https://www.startupandvc.com/venture-capital-jobs/a-1"


def test_link_collectors_are_linear_on_huge_pages():
    import time
    n = 30000
    rp = "".join(f'<a href="/company/careers/positions/p{i}/">x</a>' for i in range(n))
    sv = "".join(f'<a href="/venture-capital-jobs/s{i}-abc">x</a>' for i in range(n))
    sm = "<urlset>" + "".join(
        f"<url><loc>https://jobs.techtree.dev/job/{i:08x}-0000-4000-8000-000000000000</loc></url>"
        for i in range(n)) + "</urlset>"
    t = time.monotonic()
    assert len(sb.rapyd_links(rp)) == sb.LINK_CAP
    assert len(sb.svc_links(sv)) == sb.LINK_CAP
    http = _http({"sitemap": (200, sm)})
    sb.fetch_techtree([], http, {}, days=BIG, countries=[])
    assert time.monotonic() - t < 15


def test_startupandvc_links_are_https_slug_rebuilt_and_deduped():
    page = ('<a href="http://www.startupandvc.com/venture-capital-jobs/a-1">a</a>'
            '<a href="/venture-capital-jobs/b-1?utm=1">b</a><a href="/venture-capital-jobs/b-1">b</a>'
            '<a href="https://www.startupandvc.com:8443/venture-capital-jobs/c-1?x=1">c</a>')
    assert sb.svc_links(page) == ["https://www.startupandvc.com/venture-capital-jobs/a-1",
                                  "https://www.startupandvc.com/venture-capital-jobs/b-1",
                                  "https://www.startupandvc.com/venture-capital-jobs/c-1"]


def test_startupandvc_apply_link_is_https_only_without_port():
    assert sb._svc_apply_link("http://careers.example.com/jobs/1?x=1") == ""
    assert sb._svc_apply_link("https://careers.example.com:444/jobs/1?x=1") == "https://careers.example.com/jobs/1"
