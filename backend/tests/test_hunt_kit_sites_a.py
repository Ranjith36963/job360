"""Offline tests for src/hunt_kit/kit_sites_a.py (fixtures only, no network).

Fixture origins (all shaped like freehire's test data, nothing was fetched live; the hosts are unreachable from the build
container): Djinni follows djinni_test.go (an ld+json ARRAY of JobPostings); WUZZUF follows testdata/wuzzuf/search_it.json and jobs_it.json; InfoJobs follows infojobs_test.go:20-35 (double-encoded
__INITIAL_PROPS__); Instaffo follows instaffo.go (its tests are not used: the ld+json
shape is the schema.org one).
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
_spec = importlib.util.spec_from_file_location("kit_sites_a_under_test", _KIT / "kit_sites_a.py")
ks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ks)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(ks, "PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets the url."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append({"url": url, "method": method, "data": data, "headers": headers or {}, "as_json": as_json, "once": once})
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


def _run(fn, http, queries=("engineer",), countries=("GB",), days=BIG):
    stats: dict = {}
    jobs = fn(list(queries), http, stats, days=days, countries=list(countries))
    return jobs, stats


# ------------------------------------------------------------------------------------------ Djinni
DJINNI_HTML = """<html><head>
<script type="application/ld+json">{"@type":"Organization","name":"Djinni"}</script>
<script type="application/ld+json">
[{"@context":"https://schema.org/","@type":"JobPosting","title":"Senior Backend Go Engineer",
"url":"https://djinni.co/jobs/837549-senior-backend-go-engineer/","description":"Build & ship services.\\n\\nWhat you will do:\\n- Design APIs",
"datePosted":"2026-10-06T04:43:20.486231","employmentType":"FULL_TIME","jobLocationType":"TELECOMMUTE","identifier":837549,
"hiringOrganization":{"@type":"Organization","name":"Acme"},
"applicantLocationRequirements":{"@type":"AdministrativeArea","address":{"@type":"PostalAddress","addressCountry":"UA"}}},
{"@context":"https://schema.org/","@type":"JobPosting","title":"Engineer Recruiter","url":"https://djinni.co/jobs/837547-recruiter/",
"description":"Hire people.","identifier":837547,"hiringOrganization":{"@type":"Organization","name":"Beta"}},
{"@context":"https://schema.org/","@type":"JobPosting","title":"Orphan engineer","url":"https://djinni.co/jobs/1-orphan/",
"identifier":1,"hiringOrganization":{"@type":"Organization"}}]
</script></head><body></body></html>"""


def test_djinni_happy_path_and_paging_stops_when_page_repeats():
    http = _http({"djinni.co/jobs/?page=": (200, DJINNI_HTML)})  # every page repeats = the past-the-end redirect shape
    jobs, _ = _run(ks.fetch_djinni, http, countries=("US",))
    _check(jobs, "djinni")
    assert len(jobs) == 2 and len(http.calls) == 2  # page 2 added nothing new: stop
    a = next(j for j in jobs if j["company"] == "Acme")
    assert a["location"] == "UA" and a["remote"] is True and a["posted"] == "2026-10-06" and a["extra"]["applicant_area"] == "UA"
    assert next(j for j in jobs if j["company"] == "Beta")["location"] == ""  # no area stated, not remote


def test_djinni_empty_non200_and_garbage():
    assert _run(ks.fetch_djinni, _http({"page=": (200, "<html></html>")}))[0] == []
    refused = _http({"page=": (403, None)})
    assert _run(ks.fetch_djinni, refused)[0] == [] and len(refused.calls) == 1
    for body in (None, "", "<script type='application/ld+json'>{broken</script>", 7, ["x"]):
        assert _run(ks.fetch_djinni, _http({"page=": (200, body)}))[0] == []


def test_djinni_filters_by_query():
    jobs, _ = _run(ks.fetch_djinni, _http({"page=": (200, DJINNI_HTML)}), queries=("recruiter",))
    assert [j["company"] for j in jobs] == ["Beta"]


# ------------------------------------------------------------------------------------------ WUZZUF
ID1, ID2, ID3 = ("db64539c-1e1d-4c1d-9a37-60d455bbd6e5", "32742d1d-5290-4f46-95ef-81ad9764f97f", "0a1b2c3d-1111-4222-8333-444455556666")


def _w_hit(i, company):
    return {"id": i, "type": "talentSearch", "attributes": {"computedFields": [
        {"name": "distance", "value": [-1.0]}, {"name": "company_name", "value": [company]}]}}


WUZZUF_SEARCH = {"meta": {"totalResultsCount": 3}, "data": [_w_hit(ID1, " Our  Education "), _w_hit(ID2, ""), _w_hit(ID3, "Gulf Co")]}


def _w_doc(i, code="EG", status="active", uri="jobs/p/oxiq6qk0egqn-erpnext-developer-our-education-cairo-egypt"):
    return {"type": "job", "id": i, "attributes": {
        "status": status, "title": "ERPNext  Developer", "uri": uri, "postedAt": "10/18/2026 13:26:53",
        "description": "<p>Customize modules.</p>", "requirements": "<ul><li>2-3 years</li></ul>",
        "workExperienceYears": {"min": 2, "max": 3}, "workplaceArrangement": {"displayedName": "Remote"},
        "location": {"country": {"name": {"EG": "Egypt", "SA": "Saudi Arabia"}.get(code, code), "code": code},
                     "city": {"name": "Cairo"}, "area": {"name": "Nasr City"}}}}


def test_wuzzuf_happy_path_headers_body_and_batching():
    http = _http({"api/search/job": (200, WUZZUF_SEARCH),
                  "api/job?": (200, {"data": [_w_doc(ID1), _w_doc(ID3, code="SA", uri="saudi/jobs/p/abc-dev")]})})
    jobs, _ = _run(ks.fetch_wuzzuf, http, queries=("erpnext",), countries=("EG",))
    _check(jobs, "wuzzuf")
    assert len(jobs) == 1  # the Saudi ad is outside the user's countries
    j = jobs[0]
    assert j["company"] == "Our Education" and j["title"] == "ERPNext Developer"
    assert j["url"] == "https://wuzzuf.net/jobs/p/oxiq6qk0egqn-erpnext-developer-our-education-cairo-egypt"
    assert j["location"] == "Nasr City, Cairo, Egypt" and j["posted"] == "2026-10-18" and j["remote"] is True
    assert j["extra"]["years_min"] == 2 and "Requirements" in j["description"]
    post, get = http.calls
    assert post["method"] == "POST" and post["headers"]["Content-Type"] == "application/vnd.api+json"
    body = json.loads(post["data"])
    assert body["longitude"] == "0" and body["latitude"] == "0" and body["query"] == "erpnext" and body["pageSize"] == 20
    assert get["headers"]["Accept"] == "application/vnd.api+json"
    assert ID1 in get["url"] and ID3 in get["url"] and ID2 not in get["url"]  # the hidden-employer row costs no request


def test_wuzzuf_countries_gate_and_star():
    for cs in ((), ("GB",), ("IN", "US")):
        http = _http({"api/": (200, WUZZUF_SEARCH)})
        assert _run(ks.fetch_wuzzuf, http, countries=cs)[0] == [] and http.calls == []
    http = _http({"api/search/job": (200, WUZZUF_SEARCH),
                  "api/job?": (200, {"data": [_w_doc(ID1), _w_doc(ID3, code="SA", uri="saudi/jobs/p/abc-dev")]})})
    assert len(_run(ks.fetch_wuzzuf, http, queries=("erpnext",), countries=("*",))[0]) == 2


def test_wuzzuf_inactive_bad_uri_empty_non200_garbage():
    http = _http({"api/search/job": (200, WUZZUF_SEARCH),
                  "api/job?": (200, {"data": [_w_doc(ID1, status="closed"), _w_doc(ID3, uri="../evil")]})})
    assert _run(ks.fetch_wuzzuf, http, queries=("erpnext",), countries=("EG",))[0] == []
    empty = _http({"api/search/job": (200, {"meta": {"totalResultsCount": 0}, "data": []})})
    assert _run(ks.fetch_wuzzuf, empty, countries=("EG",))[0] == [] and len(empty.calls) == 1
    refused = _http({"api/search/job": (406, None)})
    assert _run(ks.fetch_wuzzuf, refused, queries=("a", "b"), countries=("EG",))[0] == [] and len(refused.calls) == 1
    for body in (None, "x", [], {"data": "x"}, {"data": [None, 3, {"id": 5}]}):
        assert _run(ks.fetch_wuzzuf, _http({"api/": (200, body)}), countries=("EG",))[0] == []


# ----------------------------------------------------------------------------------------- InfoJobs
def _ij_page(doc):
    return '<html><script>window.__INITIAL_PROPS__ = JSON.parse(' + json.dumps(json.dumps(doc)) + ');</script></html>'


IJ_LIST = {"overview": {"totalElements": 3}, "offers": [
    {"code": "iAAA", "title": "Backend Engineer", "companyName": "Acme SL", "city": "Madrid", "publishedAt": "2026-10-12T08:00:00Z",
     "teleworking": "Presencial", "description": "teaser", "link": "//www.infojobs.net/madrid/backend/of-iAAA?applicationOrigin=search"},
    {"code": "iBBB", "title": "Data Engineer", "companyName": "Beta SA", "city": "Barcelona", "publishedAt": "2026-10-11T09:00:00Z",
     "teleworking": "Solo teletrabajo", "description": "", "link": "//www.infojobs.net/barcelona/data/of-iBBB?applicationOrigin=search"},
    {"code": "iCCC", "title": "Cook", "companyName": "", "link": "//www.infojobs.net/x/of-iCCC"},
]}


def test_infojobs_happy_path_detail_and_double_encoding():
    http = _http({"ofertas-trabajo/informatica-telecomunicaciones": (200, _ij_page(IJ_LIST)),
                  "of-iBBB": (200, _ij_page({"offer": {"description": "<p>Full <b>body</b> here</p>"}}))})
    jobs, _ = _run(ks.fetch_infojobs, http, queries=("engineer",), countries=("ES",))
    _check(jobs, "infojobs")
    by = {j["company"]: j for j in jobs}
    assert set(by) == {"Acme SL", "Beta SA"}
    a, b = by["Acme SL"], by["Beta SA"]
    assert a["url"] == "https://www.infojobs.net/madrid/backend/of-iAAA" and a["location"] == "Madrid, Spain"
    assert a["posted"] == "2026-10-12" and a["remote"] is None and a["extra"]["truncated"] is True  # detail 404: still the teaser
    assert b["remote"] is True and b["description"] == "Full body here" and "truncated" not in b["extra"]
    assert len([c for c in http.calls if "?page=" in c["url"]]) == 1  # totalElements reached after page 1


def test_infojobs_gate_empty_non200_garbage():
    for cs in ((), ("GB",), ("MX",)):
        http = _http({"infojobs": (200, _ij_page(IJ_LIST))})
        assert _run(ks.fetch_infojobs, http, countries=cs)[0] == [] and http.calls == []
    assert _run(ks.fetch_infojobs, _http({"ofertas": (200, _ij_page({"offers": []}))}), countries=("ES",))[0] == []
    refused = _http({"ofertas": (403, None)})
    assert _run(ks.fetch_infojobs, refused, countries=("ES",))[0] == [] and len(refused.calls) == 1
    for body in (None, "", "no props", "window.__INITIAL_PROPS__ = JSON.parse(", 'window.__INITIAL_PROPS__ = JSON.parse("[1]")', 4):
        assert _run(ks.fetch_infojobs, _http({"ofertas": (200, body)}), countries=("ES",))[0] == []


# ----------------------------------------------------------------------------------------- Instaffo
INSTAFFO_INDEX = ("<sitemapindex><sitemap><loc>https://jobs.instaffo.com/sitemap-companies.xml</loc></sitemap>"
                  "<sitemap><loc>https://jobs.instaffo.com/sitemap-jobs-1.xml</loc></sitemap></sitemapindex>")
INSTAFFO_JOBS = ("<urlset>"
                 "<url><loc>https://jobs.instaffo.com/de/job/senior-java-entwickler-0123456789ab</loc><lastmod>2026-10-10</lastmod></url>"
                 "<url><loc>https://jobs.instaffo.com/en/job/senior-java-entwickler-0123456789ab</loc><lastmod>2026-10-10</lastmod></url>"
                 "<url><loc>https://jobs.instaffo.com/de/job/koch-ba98765432fe</loc><lastmod>2026-10-11</lastmod></url>"
                 "<url><loc>https://jobs.instaffo.com/de/job/old-java-entwickler-aaaaaaaaaaaa</loc><lastmod>2025-01-01</lastmod></url>"
                 "</urlset>")


def _ld_page(posting):
    return f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head></html>'


def _insta_posting(date="2026-10-09", org="Acme GmbH"):
    return {"@context": "https://schema.org", "@type": "JobPosting", "title": "Senior Java Entwickler (m/w/d)",
            "description": "&lt;p&gt;Wir suchen&lt;/p&gt;", "datePosted": date,
            "hiringOrganization": {"@type": "Organization", "name": org},
            "jobLocation": {"@type": "Place", "address": {"addressLocality": "Berlin", "addressCountry": "DE"}}}


def test_instaffo_happy_path_dedupes_locale_and_filters_by_slug():
    http = _http({"sitemap.xml": (200, INSTAFFO_INDEX), "sitemap-jobs-1.xml": (200, INSTAFFO_JOBS),
                  "senior-java-entwickler-0123456789ab": (200, _ld_page(_insta_posting())),
                  "old-java-entwickler": (200, _ld_page(_insta_posting(date="2024-01-01")))})
    jobs, _ = _run(ks.fetch_instaffo, http, queries=("java",), countries=("DE",), days=BIG)
    _check(jobs, "instaffo")
    assert len(jobs) == 1  # the /en/ twin is never asked for, the two-year-old ad is dropped
    j = jobs[0]
    assert j["url"] == "https://jobs.instaffo.com/de/job/senior-java-entwickler-0123456789ab" and j["company"] == "Acme GmbH"
    assert j["location"] == "Berlin, DE" and j["description"] == "Wir suchen" and j["posted"] == "2026-10-09"
    assert not any("/en/job/" in c["url"] or "koch" in c["url"] for c in http.calls)


def test_instaffo_gate_empty_non200_garbage_no_employer():
    for cs in ((), ("GB",), ("FR",)):
        http = _http({"sitemap": (200, INSTAFFO_INDEX)})
        assert _run(ks.fetch_instaffo, http, countries=cs)[0] == [] and http.calls == []
    assert _run(ks.fetch_instaffo, _http({"sitemap.xml": (200, "<sitemapindex></sitemapindex>")}), countries=("DE",))[0] == []
    refused = _http({"sitemap.xml": (403, None)})
    assert _run(ks.fetch_instaffo, refused, countries=("DE",))[0] == [] and len(refused.calls) == 1
    noorg = _http({"sitemap.xml": (200, INSTAFFO_INDEX), "sitemap-jobs-1.xml": (200, INSTAFFO_JOBS),
                   "senior-java": (200, _ld_page(_insta_posting(org="")))})
    assert _run(ks.fetch_instaffo, noorg, queries=("java",), countries=("DE",))[0] == []
    for body in (None, "", "<<<", 9):
        assert _run(ks.fetch_instaffo, _http({"sitemap": (200, body)}), countries=("DE",))[0] == []


# ------------------------------------------------------------------------------------------ countries
def test_wanted_puts_the_users_countries_first_then_the_rest_for_star():
    assert ks._wanted(["SA", "GB", "*"], "EG", "SA", "AE") == ["SA", "EG", "AE"]
    assert ks._wanted(["ae", "sa"], "EG", "SA", "AE") == ["AE", "SA"]
    assert ks._wanted(["GB"], "EG", "SA", "AE") == [] and ks._wanted(["*"], "EG", "SA") == ["EG", "SA"]


# ------------------------------------------------------------------------------------------ registry
def test_registries_and_every_feed_survives_total_garbage():
    assert set(ks.FEEDS) == {"djinni", "wuzzuf", "infojobs", "instaffo"}
    junk = _http({"": (200, {"data": [None, 1], "items": 3, "offers": "x"})})
    for fn in ks.FEEDS.values():
        jobs, stats = _run(fn, junk, countries=("*",))
        assert jobs == [] and stats["jobs"] == 0


def _core():
    sys.modules.pop("hunt_kit_sites_a_check", None)
    spec = importlib.util.spec_from_file_location("hunt_kit_sites_a_check", _KIT / "hunt_kit.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_sites_a_check"] = hk
    spec.loader.exec_module(hk)  # type: ignore[union-attr]
    return hk


def test_every_feed_is_registered_in_the_core():
    hk = _core()
    assert set(ks.FEEDS) <= set(hk.FEED_SOURCES) and "kit_sites_a" in hk.FEED_MODULES
    assert set(ks.FEEDS) <= set(hk.select_sources("default"))
    assert set(ks.FEEDS) <= set(hk._merged("FEEDS", hk.FEED_MODULES))
    hosts = dict(hk._FEED_HOSTS)
    for host, feed in (("djinni.co", "djinni"), ("www.wuzzuf.net", "wuzzuf"), ("www.infojobs.net", "infojobs"),
                       ("jobs.instaffo.com", "instaffo")):
        assert hosts[feed].search(host), host
        assert hk.board_from_job_url(f"https://{host}/jobs/1")["kind"] != "ats"
    assert not any(rx.search("careers.globex.example") for _, rx in hk._FEED_HOSTS)


# ------------------------------------------------------------------------- review findings (pinned hosts, once, paging)
def _all_urls(http):
    return [c["url"] for c in http.calls]


def test_instaffo_pins_sub_sitemap_and_job_hosts():
    index = ("<sitemapindex><sitemap><loc>https://evil.example/sitemap-jobs-9.xml</loc></sitemap>"
             "<sitemap><loc>http://jobs.instaffo.com/sitemap-jobs-8.xml</loc></sitemap></sitemapindex>")
    http = _http({"sitemap": (200, index)})
    assert _run(ks.fetch_instaffo, http, countries=("DE",))[0] == []
    assert _all_urls(http) == [ks.INSTAFFO_INDEX]  # no off-host / http sub-sitemap is ever fetched
    urls = ("<urlset><url><loc>https://evil.example/de/job/java-entwickler-0123456789ab</loc></url>"
            "<url><loc>https://jobs.instaffo.com.evil.example/de/job/java-entwickler-0123456789ab</loc></url>"
            "<url><loc>https://jobs.instaffo.com@evil.example/de/job/java-entwickler-0123456789ab</loc></url>"
            "<url><loc>http://jobs.instaffo.com/de/job/java-entwickler-0123456789ab</loc></url>"
            "<url><loc>https://jobs.instaffo.com/de/job/java-entwickler-ba98765432fe</loc></url></urlset>")
    http = _http({"sitemap.xml": (200, INSTAFFO_INDEX), "sitemap-jobs-1.xml": (200, urls),
                  "java-entwickler-ba98765432fe": (200, _ld_page(_insta_posting()))})
    jobs, _ = _run(ks.fetch_instaffo, http, queries=("java",), countries=("DE",))
    assert [j["url"] for j in jobs] == ["https://jobs.instaffo.com/de/job/java-entwickler-ba98765432fe"]
    assert all(u.startswith("https://jobs.instaffo.com/") for u in _all_urls(http))


def test_infojobs_pins_host_to_scheme_relative_www():
    page = {"overview": {"totalElements": 4}, "offers": [
        {"code": "e1", "title": "Engineer", "companyName": "A", "link": "//evil.example/x/of-e1"},
        {"code": "e2", "title": "Engineer", "companyName": "B", "link": "//www.infojobs.net.evil.example/x/of-e2"},
        {"code": "e3", "title": "Engineer", "companyName": "C", "link": "https://www.infojobs.net/x/of-e3"},
        {"code": "e4", "title": "Engineer", "companyName": "D", "link": "//www.infojobs.net/ok/of-e4?applicationOrigin=search"},
    ]}
    http = _http({"ofertas-trabajo": (200, _ij_page(page))})
    jobs, _ = _run(ks.fetch_infojobs, http, countries=("ES",))
    assert [j["url"] for j in jobs] == ["https://www.infojobs.net/ok/of-e4"]
    assert all(u.startswith("https://www.infojobs.net/") for u in _all_urls(http))


def test_feed_readers_send_every_request_once():
    for fn, cs, needle in ((ks.fetch_djinni, ("GB",), "djinni"), (ks.fetch_wuzzuf, ("EG",), "wuzzuf"),
                           (ks.fetch_infojobs, ("ES",), "infojobs"), (ks.fetch_instaffo, ("DE",), "instaffo")):
        http = _http({needle: (429, None)})
        assert _run(fn, http, countries=cs)[0] == []
        assert len(http.calls) == 1 and http.calls[0]["once"] is True, fn.__name__


def test_feed_detail_requests_are_once_too():
    http = _http({"jobs.instaffo.com/sitemap.xml": (200, INSTAFFO_INDEX), "sitemap-jobs-1.xml": (200, INSTAFFO_JOBS),
                  "java-entwickler": (200, _ld_page(_insta_posting()))})
    _run(ks.fetch_instaffo, http, queries=("java",), countries=("DE",))
    assert len(http.calls) > 2 and all(c["once"] is True for c in http.calls)
