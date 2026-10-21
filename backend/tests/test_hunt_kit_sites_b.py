"""Offline tests for src/hunt_kit/kit_sites_b.py (fixtures only, no network).

Fixture origins (freehire, MIT): Airswift sitemap and ad page follow airswift_test.go:9-33; Brief follows
briefhq_test.go:12-39; Functional Works follows functionalworks_test.go:45-52; Rapyd follows rapyd_test.go:14-36;
StartupAndVC follows startupandvc_test.go:17-48; TechTree follows techtree_test.go:22-40 and
testdata/techtree_sitemap.xml. The hosts are unreachable from the build container: nothing here was checked against a
live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
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
RUN_ARGS = {"airswift": [], "briefhq": [], "functionalworks": [], "rapyd": [], "startupandvc": [], "techtree": []}


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
    for fn, args in ((sb.parse_airswift_ad, (None, "https://x")), (sb.parse_briefhq, (123,)), (sb.parse_functionalworks, ({"data":
                     []},)), (sb.parse_rapyd_ad, (None, "https://x")), (sb.rapyd_links, (None,)),
                     (sb.parse_startupandvc_ad, (None, "https://x")), (sb.svc_links, (5,)),
                     (sb.parse_techtree_ad, (None, "https://x"))):
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


HOSTS = {"airswift": "www.airswift.com", "briefhq": "briefhq.ai", "functionalworks": "functional.works-hub.com", "rapyd":
         "www.rapyd.net", "startupandvc": "www.startupandvc.com", "techtree": "jobs.techtree.dev"}


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
    read = {"airswift": ["www.airswift.com", "airswift.com"], "briefhq": ["briefhq.ai", "www.briefhq.ai"], "rapyd":
            ["www.rapyd.net", "rapyd.net"], "startupandvc": ["www.startupandvc.com", "startupandvc.com"],
            "techtree": ["jobs.techtree.dev"], "functionalworks": ["functional.works-hub.com"]}
    for feed, names in read.items():
        for host in names:
            assert hosts[feed].search(host), (feed, host)
    other = {"rapyd": ["careers.rapyd.net"], "briefhq": ["app.briefhq.ai"], "airswift": ["careers.airswift.com"],
             "startupandvc": ["blog.startupandvc.com"], "techtree": ["www.techtree.dev"], "functionalworks":
             ["www.functional.works-hub.com"]}
    for feed, names in other.items():
        for host in names:
            assert not hosts[feed].search(host), (feed, host)
    for url in ("https://careers.rapyd.net/jobs/1",):
        assert _ask_why(mod, url)["verdict"] != "feed_covered", url


def test_script_scan_is_linear_on_unclosed_tags_and_keeps_case_and_space_variants():
    import time
    opener = '<script type="application/ld+json">{"x":'
    start = time.perf_counter()
    assert sb._ld_postings(opener * 8000) == []
    assert sb._ld_postings('<script ' * 8000) == []
    assert time.perf_counter() - start < 1.0
    page = '<SCRIPT TYPE="application/ld+json">{"@type":"JobPosting","title":"A"}</SCRIPT >'
    assert [p["title"] for p in sb._ld_postings(page)] == ["A"]
    # `</scripts>` does not close; a script of another type is skipped, not consumed
    page = ('<script type="text/javascript">var a;</script>'
            '<script type="application/ld+json">{"@type":"JobPosting","title":"B</scripts>"}</script\n>')
    assert [p["title"] for p in sb._ld_postings(page)] == ["B</scripts>"]
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
