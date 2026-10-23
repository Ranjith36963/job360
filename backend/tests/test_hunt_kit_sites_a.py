"""Offline tests for src/hunt_kit/kit_sites_a.py (fixtures only, no network).

Fixture origins (all shaped like freehire's test data, nothing was fetched live; the hosts are unreachable from the build
container): remote.com listing/posting flights follow remotedotcom_test.go:44-76 (jobsData inside other RSC content; the
posting as a hex-length text row); Djinni follows djinni_test.go (an ld+json ARRAY of JobPostings); WORK180 follows
testdata/work180_jobs_sitemap_au.xml and work180_detail_au.html (ld+json JobPosting plus the page's RSC "job" record);
WUZZUF follows testdata/wuzzuf/search_it.json and jobs_it.json; InfoJobs follows infojobs_test.go:20-35 (double-encoded
__INITIAL_PROPS__); Instaffo and JobiDanmark follow instaffo.go / jobdanmark.go (their tests are not used: the ld+json
shape is the schema.org one); Deel follows testdata/deel_klarna.html (flight chunks, "$N" description reference); Dover
follows dover_test.go:13-60.
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
    monkeypatch.setattr(ks, "WORK180_PAUSE_S", 0)
    monkeypatch.setattr(ks, "DOVER_PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets the url."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
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


def _push(flight: str) -> str:
    """A Next.js page whose one flight chunk is ``flight`` (self.__next_f.push([1,"..."]))."""
    return ('<html><body><script>self.__next_f=self.__next_f||[];self.__next_f.push([0]);</script><script>'
            f'self.__next_f.push([1,{json.dumps(flight)}])</script></body></html>')


def _row(rid: str, body: str) -> str:
    return "\n" + rid + ":T" + format(len(body.encode()), "x") + "," + body


# ------------------------------------------------------------------------------------- remote.com
RDC_JOBS = {"totalPages": 2, "jobs": [
    {"status": "published", "slug": "senior-backend-engineer-123", "title": "Senior Backend Engineer",
     "publishedAt": "2026-10-10T09:00:00Z", "employmentType": "full_time", "seniority": ["senior"],
     "companyProfile": {"name": "Acme Labs", "slug": "acme-labs"},
     "workplaceLocation": {"type": "remote", "city": "", "country": None},
     "hiringLocation": {"type": "location", "includedLocations": [
         {"type": "country", "value": {"code": "DEU", "name": "Germany"}},
         {"type": "region", "value": {"code": "europe", "name": "Europe"}}]},
     "compensation": {"minimum": 1000000, "maximum": 1500000, "frequency": "yearly", "currency": {"code": "USD"}}},
    {"status": "published", "slug": "office-manager-9", "title": "Office Manager",
     "companyProfile": {"name": "Beta", "slug": "beta"},
     "workplaceLocation": {"type": "hybrid", "city": "Lisbon", "country": {"code": "PRT", "name": "Portugal"}}},
    {"status": "draft", "slug": "x", "title": "Draft Engineer", "companyProfile": {"name": "C", "slug": "c"}},
    {"status": "published", "slug": "no-company", "title": "Engineer", "companyProfile": {"name": "", "slug": "d"}},
    {"status": "published", "slug": "tz-engineer", "title": "Platform Engineer", "publishedAt": "2026-10-11T00:00:00Z",
     "companyProfile": {"name": "Gamma [EMEA]", "slug": "gamma"},
     "hiringLocation": {"type": "timezone", "timezone": {"name": "Chicago"}}},
]}


def _rdc_list(jobs=RDC_JOBS):
    return ('2:["$","div",null,{"children":[["$","$L16",null,{"jobsData":' + json.dumps(jobs)
            + '}],["$","footer",null,{}]]}]')


RDC_POSTING = json.dumps({"@context": "https://schema.org", "@type": "JobPosting",
                          "description": "<p>Build reliable <b>APIs</b>.</p>"})


def _rdc_detail(_url=""):
    return 200, '0:["$","html",null,{}]' + _row("a", '{"@type":"BreadcrumbList","itemListElement":[]}') + _row("13", RDC_POSTING)


def test_remotedotcom_happy_path():
    http = _http({"remote.com/jobs/all?page=": (200, _rdc_list()),
                  "remote.com/jobs/acme-labs/": _rdc_detail, "remote.com/jobs/gamma/": (404, None)})
    jobs, stats = _run(ks.fetch_remotedotcom, http, countries=("GB",))
    _check(jobs, "remotedotcom")
    by = {j["title"]: j for j in jobs}
    be = by["Senior Backend Engineer"]
    assert be["url"] == "https://remote.com/jobs/acme-labs/senior-backend-engineer-123"
    assert be["company"] == "Acme Labs" and be["location"] == "Germany, Europe" and be["remote"] is True
    assert be["posted"] == "2026-10-10" and "Build reliable" in be["description"]
    assert be["extra"]["employment_type"] == "full_time" and "title_only" not in be["extra"]
    assert by["Platform Engineer"]["location"] == "Chicago timezone"  # the zone is a label, not a city
    assert by["Platform Engineer"]["extra"].get("title_only") is True  # its detail answered 404
    assert "Office Manager" not in by and "Draft Engineer" not in by  # query filter, status filter
    assert all(c["headers"].get("RSC") == "1" and c["as_json"] is False for c in http.calls)
    assert stats["tried"] == len(http.calls)


def test_remotedotcom_runs_for_every_country_and_stops_on_last_page():
    http = _http({"remote.com/jobs/all?page=": (200, _rdc_list())})
    jobs, _ = _run(ks.fetch_remotedotcom, http, countries=())
    assert jobs and len([c for c in http.calls if "all?page=" in c["url"]]) == 2  # totalPages 2


def test_remotedotcom_hybrid_location_and_empty_and_non200():
    jobs, _ = _run(ks.fetch_remotedotcom, _http({"all?page=": (200, _rdc_list())}), queries=("office manager",))
    assert jobs[0]["location"] == "Lisbon, Portugal" and jobs[0]["remote"] is None
    empty = _http({"all?page=": (200, _rdc_list({"totalPages": 1, "jobs": []}))})
    assert _run(ks.fetch_remotedotcom, empty)[0] == [] and len(empty.calls) == 1
    refused = _http({"all?page=": (403, None)})
    assert _run(ks.fetch_remotedotcom, refused)[0] == [] and len(refused.calls) == 1  # first non-200 ends the run


@pytest.mark.parametrize("body", [None, "", "garbage", '"jobsData":{"jobs":[{"slug":', '"jobsData":[1,2]', 5])
def test_remotedotcom_garbage_never_raises(body):
    jobs, _ = _run(ks.fetch_remotedotcom, _http({"all?page=": (200, body)}))
    assert jobs == []


def test_remotedotcom_http_raising_is_swallowed():
    def boom(*a, **k):
        raise RuntimeError("x")
    assert _run(ks.fetch_remotedotcom, boom)[0] == []


def test_flight_text_row_is_cut_by_byte_length():
    body = '{"description":"café, with a comma"}'
    rows = ks._flight_rows('0:x' + _row("a", body) + _row("1f", "second"))
    assert rows == {"a": body, "1f": "second"}


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


def test_djinni_urls_are_pinned_to_djinni_co():
    bad = ("https://evil.example/apply", "https://user@djinni.co.evil.example/jobs/1/", "https://djinni.co.evil.example/jobs/1/",
           "http://djinni.co/jobs/1/", "https://djinni.co:8443/jobs/1/")
    postings = [{"@type": "JobPosting", "title": "Engineer %d" % i, "url": u, "identifier": i,
                 "hiringOrganization": {"name": "Acme"}} for i, u in enumerate(bad, 1)]
    page = '<script type="application/ld+json">' + json.dumps(postings) + "</script>"
    assert _run(ks.fetch_djinni, _http({"page=": (200, page)}))[0] == []


# ----------------------------------------------------------------------------------------- WORK180
W180_SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://work180.com/en-au/search/advertising-media-and-publishing</loc></url>
<url><loc>https://work180.com/en-au/for-women/employer/queensland-fire-department/job/21552/auxiliary-firefighter---boulia</loc></url>
<url><loc>https://work180.com/en-au/for-women/employer/transgrid/job/1083968/manager-business-improvement</loc></url>
<url><loc>https://work180.com/en-gb/for-women/employer/other/job/5/engineer</loc></url>
<url><loc>http://work180.com/en-au/for-women/employer/transgrid/job/77/engineer</loc></url>
</urlset>"""


def _w180_page(pid="1083968", expired=None, link="https://careers.transgrid.com.au/job/1368929566/", atype="EXTERNAL"):
    ld = {"@context": "https://schema.org/", "@type": "JobPosting", "identifier": {"@type": "PropertyValue", "name": "Transgrid", "value": pid},
          "title": "Manager, Business Improvement", "description": "<p>Lead <b>infrastructure</b> projects.</p>",
          "datePosted": "2026-10-16T01:30:18.629954+00:00", "hiringOrganization": {"@type": "Organization", "name": "Transgrid"},
          "jobLocation": [{"@type": "Place", "address": {"name": "Sydney, NSW, Australia", "addressLocality": "Sydney"}}],
          "employmentType": ["FULL_TIME"]}
    rec = {"locale": "en-au", "job": {"id": pid, "expired": expired, "applicationType": atype, "applicationLink": link,
                                      "employer": {"slug": "transgrid", "name": "Transgrid"}}}
    flight = "2f:" + json.dumps(["$", "$L35", None, rec], separators=(",", ":")) + "\n"
    return (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>'
            f'<script>self.__next_f.push([1,{json.dumps(flight)}])</script></body></html>')


def test_work180_happy_path_keeps_board_url_and_gates_by_country():
    http = _http({"jobs-sitemap.xml": (200, W180_SITEMAP), "/job/1083968/": (200, _w180_page())})
    jobs, _ = _run(ks.fetch_work180, http, queries=("manager",), countries=("AU",))
    _check(jobs, "work180")
    j = jobs[0]
    assert j["company"] == "Transgrid" and j["url"].startswith("https://work180.com/en-au/")
    assert j["extra"]["apply_url"] == "https://careers.transgrid.com.au/job/1368929566/"
    assert j["extra"]["listed_on"].startswith("https://work180.com/en-au/") and j["location"] == "Sydney, NSW, Australia"
    assert j["posted"] == "2026-10-16" and "infrastructure" in j["description"]
    assert [c["url"] for c in http.calls][0] == "https://work180.com/en-au/jobs-sitemap.xml"
    assert len(http.calls) == 2  # sitemap + the one posting whose slug matched; the other locale was never asked for
    assert _run(ks.fetch_work180, _http({}), countries=("FR", "DE"))[0] == [] and True


def test_work180_no_request_for_wrong_or_empty_countries():
    for cs in ((), ("FR",), ("DE", "IN")):
        http = _http({"jobs-sitemap.xml": (200, W180_SITEMAP)})
        assert _run(ks.fetch_work180, http, countries=cs)[0] == [] and http.calls == []


def test_work180_star_reads_all_three_regions_and_nz_shares_the_au_site():
    http = _http({"jobs-sitemap.xml": (200, "<urlset></urlset>")})
    _run(ks.fetch_work180, http, countries=("*",))
    assert sorted(c["url"] for c in http.calls) == [f"https://work180.com/{loc}/jobs-sitemap.xml" for loc in ("en-au", "en-gb", "en-us")]
    nz = _http({"jobs-sitemap.xml": (200, "<urlset></urlset>")})
    _run(ks.fetch_work180, nz, countries=("NZ", "AU"))
    assert len(nz.calls) == 1


def test_work180_expired_wrong_id_and_email_application_and_failures():
    sm = (200, W180_SITEMAP)
    assert _run(ks.fetch_work180, _http({"jobs-sitemap.xml": sm, "/job/1083968/": (200, _w180_page(expired="2026-10-01"))}),
                queries=("manager",), countries=("AU",))[0] == []
    assert _run(ks.fetch_work180, _http({"jobs-sitemap.xml": sm, "/job/1083968/": (200, _w180_page(pid="999"))}),
                queries=("manager",), countries=("AU",))[0] == []
    mail = _run(ks.fetch_work180, _http({"jobs-sitemap.xml": sm, "/job/1083968/": (200, _w180_page(atype="EMAIL"))}),
                queries=("manager",), countries=("AU",))[0]
    assert mail[0]["url"].startswith("https://work180.com/en-au/for-women/employer/transgrid/job/1083968")
    refused = _http({"jobs-sitemap.xml": (403, None)})
    assert _run(ks.fetch_work180, refused, countries=("AU",))[0] == [] and len(refused.calls) == 1
    first_refusal = _http({"jobs-sitemap.xml": sm, "/job/": (429, None)})
    assert _run(ks.fetch_work180, first_refusal, queries=("engineer", "manager", "firefighter"), countries=("AU",))[0] == []
    assert len(first_refusal.calls) == 2  # sitemap + the first posting page; the 429 ended the run


@pytest.mark.parametrize("body", [None, "", "garbage", "<loc>https://work180.com/x</loc>", 3])
def test_work180_garbage_never_raises(body):
    assert _run(ks.fetch_work180, _http({"jobs-sitemap.xml": (200, body), "/job/": (200, body)}), countries=("AU", "GB"))[0] == []


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


def test_wuzzuf_results_are_filtered_by_the_users_queries_locally():
    http = _http({"api/search/job": (200, WUZZUF_SEARCH), "api/job?": (200, {"data": [_w_doc(ID1), _w_doc(ID3, code="SA", uri="saudi/jobs/p/abc-dev")]})})
    assert _run(ks.fetch_wuzzuf, http, queries=("plumber",), countries=("*",))[0] == []


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
def test_parse_sitemap_is_linear_on_hostile_input_and_reads_namespaces():
    import time
    t = time.perf_counter()
    assert ks.parse_sitemap("<url>" * 40000) == []
    assert ks.parse_sitemap("<urlset>" + "<url><loc>https://x.example/a</loc>" * 20000) == []
    assert time.perf_counter() - t < 2
    ns = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc> https://jobs.instaffo.com/de/job/a-0123456789ab </loc>'
          "<lastmod>2026-10-10</lastmod></url><url><lastmod>2026-01-01</lastmod></url></urlset>")
    assert ks.parse_sitemap(ns) == [("https://jobs.instaffo.com/de/job/a-0123456789ab", "2026-10-10")]
    assert ks.parse_sitemap('<!DOCTYPE x [<!ENTITY a "b">]><urlset><url><loc>https://x/&a;</loc></url></urlset>') == []


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


# --------------------------------------------------------------------------------------- JobiDanmark
JD_PAGE = {"totalPages": 1, "items": [
    {"title": "Softwareudvikler", "companyName": "Novo A/S", "companyAddress": "Bagsvaerd", "url": "/job/softwareudvikler-1?utm=x",
     "publishedDate": "07-10-2026"},
    {"title": "Chauffoer", "companyName": "Bil ApS", "companyAddress": "Aarhus", "url": "/job/chauffoer-2", "publishedDate": "08-10-2026"},
    {"title": "No employer", "companyName": "", "url": "/job/x"},
]}


def test_jobdanmark_happy_path_post_body_and_detail():
    detail = _ld_page({"@type": "JobPosting", "description": "<p>Du skal <b>udvikle</b></p>", "datePosted": "2026-10-08"})
    http = _http({"api/jobsearch/search/1": (200, JD_PAGE), "/job/softwareudvikler-1": (200, detail)})
    jobs, _ = _run(ks.fetch_jobdanmark, http, queries=("softwareudvikler",), countries=("DK",))
    _check(jobs, "jobdanmark")
    j = jobs[0]
    assert j["url"] == "https://jobdanmark.dk/job/softwareudvikler-1" and j["company"] == "Novo A/S"
    assert j["location"] == "Bagsvaerd, Denmark" and j["posted"] == "2026-10-08" and "udvikle" in j["description"]
    post = http.calls[0]
    assert post["method"] == "POST" and json.loads(post["data"]) == {"jobTypes": [], "filters": [], "locationMode": "Text", "distance": 50}
    assert len(http.calls) == 2  # totalPages 1: no page 2; the Chauffoer ad does not match and costs no detail


def test_jobdanmark_list_date_fallback_gate_empty_non200_garbage():
    jobs, _ = _run(ks.fetch_jobdanmark, _http({"search/1": (200, JD_PAGE)}), queries=("chauffoer",), countries=("DK",))
    assert jobs[0]["posted"] == "2026-10-08" and jobs[0]["extra"]["title_only"] is True  # detail 404: list date, no body
    for cs in ((), ("SE",), ("NO", "GB")):
        http = _http({"search": (200, JD_PAGE)})
        assert _run(ks.fetch_jobdanmark, http, countries=cs)[0] == [] and http.calls == []
    assert _run(ks.fetch_jobdanmark, _http({"search": (200, {"items": []})}), countries=("DK",))[0] == []
    refused = _http({"search": (429, None)})
    assert _run(ks.fetch_jobdanmark, refused, countries=("DK",))[0] == [] and len(refused.calls) == 1
    for body in (None, "x", [], {"items": "x"}, {"items": [1, None, {"url": 3}]}):
        assert _run(ks.fetch_jobdanmark, _http({"search": (200, body)}), countries=("DK",))[0] == []


# ---------------------------------------------------------------------------------------- Deel (ATS)
def _deel_page(postings=None, desc_ref=True):
    postings = postings if postings is not None else [
        {"id": "5d3636f8-0712", "jobId": "j1", "title": "Customer Trust Consultant (Remote - Sweden)",
         "richtextDescription": "$2" if desc_ref else "<p>inline</p>", "createdAt": "2026-10-05T10:00:00Z",
         "job": {"jobLocations": [{"location": {"name": "Stockholm"}}, {"location": {"name": "Sweden"}}]}},
        {"id": "", "title": "no id"},
    ]
    flight = ('8:["$","$L1f",null,{"careerPageSettings":{"id":"x","urlSlug":"klarna","preferredOrganizationName":"Klarna"},"jobPostings":'
              + json.dumps(postings) + "}]" + _row("2", "<p>Resolve [EMEA] <b>issues</b>, fast</p>"))
    return _push(flight)


ROW = {"name": "Klarna", "ats": "deel", "token": "klarna"}


def test_deel_happy_path_resolves_description_reference():
    http = _http({"jobs.deel.com/klarna": (200, _deel_page())})
    stats: dict = {}
    jobs = ks.ATS_FETCHERS["deel"](ROW, http, stats)
    assert len(jobs) == 1 and stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert j["url"] == "https://jobs.deel.com/klarna/job-details/5d3636f8-0712/overview" and j["company"] == "Klarna"
    assert j["location"] == "Stockholm, Sweden" and j["posted"] == "2026-10-05" and "Resolve [EMEA] issues" in j["description"]
    assert j["remote"] is True and set(j) == JOB_KEYS and j["source"] == "deel" and j["board"] == "klarna"
    assert http.calls[0]["url"] == "https://jobs.deel.com/klarna"
    assert ks.ATS_FETCHERS["deel"](ROW, _http({"klarna": (200, _deel_page(desc_ref=False))}), {})[0]["description"] == "inline"


def test_deel_empty_nonexistent_and_garbage():
    stats: dict = {}
    assert ks.ATS_FETCHERS["deel"](ROW, _http({"klarna": (200, _deel_page(postings=[]))}), stats) == []
    assert stats["ok"] == 1  # a valid empty board is a success
    for resp in ((404, None), (403, None), (200, None), (200, "<html></html>"), (200, 5), (200, _push('"jobPostings":[{"id":'))):
        s: dict = {}
        assert ks.ATS_FETCHERS["deel"](ROW, _http({"klarna": resp}), s) == [] and s.get("ok", 0) == 0
    s = {}
    assert ks.ATS_FETCHERS["deel"]({"name": "x", "token": "../etc"}, _http({}), s) == [] and s.get("tried") == 1


# --------------------------------------------------------------------------------------- Dover (ATS)
DOVER_SLUG = {"id": "72c2afbf-36e4-4eec-8d78-5d2600498018", "slug": "qompyl", "name": "Qompyl"}
DOVER_JOB = {"id": "f9fd123e-e7ff-4bcc-a16a-bb8c47e591b5", "title": "Cloud Infra Engineer", "locations": [], "workplace_type": "REMOTE",
             "is_published": True, "is_sample": False}
DOVER_SAMPLE = {**DOVER_JOB, "id": "sample-1", "title": "Sample Job", "is_sample": True}
DOVER_UNPUB = {**DOVER_JOB, "id": "unpub-1", "title": "Unpublished Job", "is_published": False}
DOVER_ROW = {"name": "Qompyl", "ats": "dover", "token": "qompyl"}


def _dover_detail(**kw):
    d = {"id": DOVER_JOB["id"], "title": "Cloud Infra Engineer", "user_provided_description": "<p>Do <b>things</b></p>",
         "locations": [{"location_type": "REMOTE", "location_option": {"display_name": "Germany", "location_type": "COUNTRY", "country": "DE"}}],
         "workplace_type": "REMOTE", "active": True, "is_private": False, "visa_support": True,
         "compensation": {"offers_equity": True, "employment_type": "FULL_TIME"}}
    d.update(kw)
    return 200, d


def _dover_http(listing=None, detail=None):
    return _http({"careers-page-slug/qompyl": (200, DOVER_SLUG),
                  "careers-page/72c2afbf-36e4-4eec-8d78-5d2600498018/jobs": (200, listing or {"next": None, "results": [DOVER_JOB, DOVER_SAMPLE, DOVER_UNPUB]}),
                  "inbound/application-portal-job/": detail or _dover_detail()})


def test_dover_happy_path_resolves_lists_and_hydrates():
    http = _dover_http()
    stats: dict = {}
    jobs = ks.ATS_FETCHERS["dover"](DOVER_ROW, http, stats)
    assert len(jobs) == 1 and stats == {"tried": 1, "ok": 1, "jobs": 1}  # sample and unpublished never reach detail
    j = jobs[0]
    assert j["url"] == "https://app.dover.com/apply/qompyl/f9fd123e-e7ff-4bcc-a16a-bb8c47e591b5" and j["company"] == "Qompyl"
    assert j["location"] == "Germany" and j["remote"] is True and set(j) == JOB_KEYS
    assert "Do things" in j["description"] and "offers equity" in j["description"] and "sponsors a work visa" in j["description"]
    assert [c["url"].split("/api/v1/")[1].split("/")[0] for c in http.calls] == ["careers-page-slug", "careers-page", "inbound"]


def test_dover_inactive_private_title_gate_next_url_and_failures():
    assert ks.ATS_FETCHERS["dover"](DOVER_ROW, _dover_http(detail=_dover_detail(active=False)), {}) == []
    assert ks.ATS_FETCHERS["dover"](DOVER_ROW, _dover_http(detail=_dover_detail(is_private=True)), {}) == []
    http = _dover_http()
    jobs = ks.ATS_FETCHERS["dover"]({**DOVER_ROW, "title_ok": lambda t: False}, http, {})
    assert len(jobs) == 1 and jobs[0]["description"] == "" and not any("inbound" in c["url"] for c in http.calls)
    nxt = _dover_http(listing={"next": "https://evil.example/x", "results": [DOVER_JOB]})
    assert len(ks.ATS_FETCHERS["dover"](DOVER_ROW, nxt, {})) == 1
    assert not any("evil.example" in c["url"] for c in nxt.calls)
    s: dict = {}
    assert ks.ATS_FETCHERS["dover"](DOVER_ROW, _dover_http(listing={"next": None, "results": []}), s) == [] and s["ok"] == 1
    for http in (_http({}), _http({"careers-page-slug": (404, None)}), _http({"careers-page-slug": (200, {"id": ""})}),
                 _http({"careers-page-slug": (200, DOVER_SLUG), "jobs": (500, None)}),
                 _http({"careers-page-slug": (200, DOVER_SLUG), "jobs": (200, {"results": "x"})}),
                 _http({"careers-page-slug": (200, "garbage")})):
        s = {}
        assert ks.ATS_FETCHERS["dover"](DOVER_ROW, http, s) == [] and s.get("ok", 0) == 0


def test_dover_detail_failure_keeps_listing_fields():
    http = _dover_http(detail=(500, None))
    jobs = ks.ATS_FETCHERS["dover"](DOVER_ROW, http, {})
    assert len(jobs) == 1 and jobs[0]["description"] == "" and jobs[0]["remote"] is True


# ------------------------------------------------------------------------------------------ countries
def test_wanted_puts_the_users_countries_first_then_the_rest_for_star():
    assert ks._wanted(["SA", "GB", "*"], "EG", "SA", "AE") == ["SA", "EG", "AE"]
    assert ks._wanted(["ae", "sa"], "EG", "SA", "AE") == ["AE", "SA"]
    assert ks._wanted(["GB"], "EG", "SA", "AE") == [] and ks._wanted(["*"], "EG", "SA") == ["EG", "SA"]


# ------------------------------------------------------------------------------------------ registry
def test_registries_and_every_feed_survives_total_garbage():
    assert set(ks.FEEDS) == {"remotedotcom", "djinni", "work180", "wuzzuf", "infojobs", "instaffo", "jobdanmark"}
    assert set(ks.ATS_FETCHERS) == {"deel", "dover"}
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


def test_every_feed_and_board_type_is_registered_in_the_core():
    hk = _core()
    assert set(ks.FEEDS) <= set(hk.FEED_SOURCES) and "kit_sites_a" in hk.FEED_MODULES
    assert set(ks.FEEDS) <= set(hk.select_sources("default"))
    assert set(ks.FEEDS) <= set(hk._merged("FEEDS", hk.FEED_MODULES))
    assert set(ks.ATS_FETCHERS) <= set(hk.BOARD_SOURCES) and "kit_sites_a" in hk.ATS_MODULES
    assert set(ks.ATS_FETCHERS) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    hosts = dict(hk._FEED_HOSTS)
    for host, feed in (("djinni.co", "djinni"), ("www.wuzzuf.net", "wuzzuf"), ("www.infojobs.net", "infojobs"),
                       ("jobs.instaffo.com", "instaffo"), ("jobdanmark.dk", "jobdanmark"),
                       ("remote.com", "remotedotcom"), ("work180.com", "work180")):
        assert hosts[feed].search(host), host
        assert hk.board_from_job_url(f"https://{host}/jobs/1")["kind"] != "ats"
    assert not any(rx.search("careers.globex.example") for _, rx in hk._FEED_HOSTS)


def test_deel_and_dover_links_resolve_to_a_board_token():
    board = _core().board_from_job_url
    deel = board("https://jobs.deel.com/klarna/job-details/5d3636f8-0712/overview")
    assert (deel["kind"], deel["ats"], deel["token"]) == ("ats", "deel", "klarna")
    assert board("https://jobs.deel.com/")["token"] == ""
    for url in ("https://app.dover.com/jobs/qompyl", "https://app.dover.com/apply/Qompyl/f9fd123e-e7ff"):
        dover = board(url)
        assert (dover["kind"], dover["ats"], dover["token"]) == ("ats", "dover", "qompyl")
    assert board("https://app.dover.com/")["token"] == "" and board("https://app.dover.com/jobs/")["token"] == ""


# ------------------------------------------------------------------------- review findings (pinned hosts, once, pacing, paging)
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


def test_jobdanmark_accepts_only_site_relative_paths():
    items = [{"title": "Engineer " + n, "companyName": "Co " + n, "url": u, "publishedDate": "07-10-2026"} for n, u in (
        ("a", "https://evil.example/job/a"), ("b", "http://jobdanmark.dk/job/b"), ("c", ".evil.net/x"), ("d", "@evil.net/x"),
        ("e", "//evil.example/job/e"), ("f", "job/f"), ("g", "/job/g?utm=x"))]
    http = _http({"search/1": (200, {"totalPages": 1, "items": items})})
    jobs, _ = _run(ks.fetch_jobdanmark, http, countries=("DK",))
    assert [j["url"] for j in jobs] == ["https://jobdanmark.dk/job/g"]
    assert all(u.startswith("https://jobdanmark.dk/") for u in _all_urls(http))


def test_feed_readers_send_every_request_once():
    for fn, cs, needle in ((ks.fetch_remotedotcom, ("GB",), "remote.com"), (ks.fetch_djinni, ("GB",), "djinni"),
                           (ks.fetch_work180, ("AU",), "work180"), (ks.fetch_wuzzuf, ("EG",), "wuzzuf"),
                           (ks.fetch_infojobs, ("ES",), "infojobs"), (ks.fetch_instaffo, ("DE",), "instaffo"),
                           (ks.fetch_jobdanmark, ("DK",), "jobdanmark")):
        http = _http({needle: (429, None)})
        assert _run(fn, http, countries=cs)[0] == []
        assert len(http.calls) == 1 and http.calls[0]["once"] is True, fn.__name__


def test_feed_detail_requests_are_once_too():
    cases = (
        (ks.fetch_remotedotcom, ("GB",), ("engineer",),
         {"remote.com/jobs/all?page=": (200, _rdc_list()), "remote.com/jobs/acme-labs/": _rdc_detail}),
        (ks.fetch_instaffo, ("DE",), ("java",),
         {"jobs.instaffo.com/sitemap.xml": (200, INSTAFFO_INDEX), "sitemap-jobs-1.xml": (200, INSTAFFO_JOBS),
          "java-entwickler": (200, _ld_page(_insta_posting()))}),
        (ks.fetch_work180, ("AU",), ("manager",),
         {"jobs-sitemap.xml": (200, W180_SITEMAP), "/job/1083968/": (200, _w180_page())}),
        (ks.fetch_jobdanmark, ("DK",), ("softwareudvikler",),
         {"api/jobsearch/search/1": (200, JD_PAGE),
          "/job/softwareudvikler-1": (200, _ld_page({"@type": "JobPosting", "description": "<p>x</p>"}))}),
    )
    for fn, cs, qs, routes in cases:
        http = _http(routes)
        jobs, _ = _run(fn, http, queries=qs, countries=cs)
        assert jobs and len(http.calls) >= 2, fn.__name__  # a detail request really happened
        assert all(c["once"] is True for c in http.calls), fn.__name__


def test_flight_rows_hostile_lengths_stay_linear_and_never_overlap():
    assert ks._flight_rows("x0:Tffffff," * 5000) == {}  # a declared length past the buffer is skipped, not copied
    body = _row("b", "inner")
    outer = ks._flight_rows(_row("a", body))
    assert outer == {"a": body}  # the nested-looking row sits inside row a's bytes: not a second row
    many = "".join(f"\nx{i:x}:Tffffff," for i in range(3000))
    assert ks._flight_rows(many) == {}


def test_work180_application_link_must_be_plain_https():
    entry = {"id": "1083968", "url": "https://work180.com/en-au/for-women/employer/transgrid/job/1083968/x", "slug": "x"}
    good = "https://careers.transgrid.com.au/job/1368929566/"
    posted = ks.parse_work180_posting(_w180_page(link=good), entry)
    assert posted["url"] == entry["url"] and posted["extra"]["apply_url"] == good  # never a foreign-host job url
    evil = ks.parse_work180_posting(_w180_page(link="https://evil.example/x"), entry)
    assert evil["url"].startswith("https://work180.com/")
    for bad in ("http://careers.transgrid.com.au/job/1", "https://work180.com@evil.example/x", "http://user@evil.example/x",
                "https://user:pw@evil.example/x", "https://evil.example:8443/x", "ftp://evil.example/x", "https://"):
        assert ks.parse_work180_posting(_w180_page(link=bad), entry)["url"] == entry["url"], bad
        assert "apply_url" not in ks.parse_work180_posting(_w180_page(link=bad), entry)["extra"], bad


def test_remotedotcom_odd_seniority_does_not_drop_the_run():
    rows = [dict(RDC_JOBS["jobs"][0], slug=f"s-{i}", seniority=v) for i, v in enumerate((3, {}, "senior", ["senior", "", "lead"]))]
    jobs, _ = ks.parse_remotedotcom_listing(_rdc_list({"totalPages": 1, "jobs": rows}))[0], None
    seniority = [j["extra"].get("seniority") for j in jobs]
    assert len(jobs) == 4 and seniority == [None, None, None, ["senior", "lead"]]


def test_work180_paces_every_request_including_sitemaps_and_the_second_region(monkeypatch):
    clock = [100.0]
    sleeps: list[float] = []

    def pause(s):
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(ks, "_pause", pause)
    monkeypatch.setattr(ks, "_now", lambda: clock[0])
    monkeypatch.setattr(ks, "WORK180_PAUSE_S", 5.0)
    sm = lambda loc: (  # noqa: E731
        f'<urlset><url><loc>https://work180.com/{loc}/for-women/employer/acme/job/7/engineer</loc></url>'
        f'<url><loc>https://work180.com/{loc}/for-women/employer/acme/job/8/engineer</loc></url></urlset>')
    stamps: list[float] = []

    def route(url):
        stamps.append(clock[0])
        if "sitemap" in url:
            return 200, sm("en-au" if "en-au" in url else "en-gb")
        return 404, None

    http = _http({"work180.com": route})
    _run(ks.fetch_work180, http, countries=("AU", "GB"))
    assert len(http.calls) == 6  # sitemap, 2 pages, sitemap, 2 pages
    assert sleeps == [5.0] * 5  # before every request but the very first, sitemaps and the second region's first included
    assert all(b - a >= 5.0 for a, b in zip(stamps, stamps[1:]))


def test_work180_pacing_counts_time_already_elapsed(monkeypatch):
    clock = [0.0]
    sleeps: list[float] = []

    def pause(s):
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(ks, "_pause", pause)
    monkeypatch.setattr(ks, "_now", lambda: clock[0])
    monkeypatch.setattr(ks, "WORK180_PAUSE_S", 5.0)

    def route(url):
        clock[0] += 3.0  # each request itself takes 3 s
        return (200, '<urlset><url><loc>https://work180.com/en-au/for-women/employer/a/job/7/engineer</loc></url></urlset>') \
            if "sitemap" in url else (404, None)

    _run(ks.fetch_work180, _http({"work180.com": route}), countries=("AU",))
    assert sleeps == [2.0]


def test_remotedotcom_filtered_out_page_does_not_end_the_walk():
    filtered = {"totalPages": 3, "jobs": [{"status": "draft", "slug": "x", "title": "Draft", "companyProfile": {"name": "C", "slug": "c"}}]}
    assert ks.parse_remotedotcom_listing(_rdc_list(filtered))[0] == [] and ks.parse_remotedotcom_listing(_rdc_list(filtered))[2] == 1
    pages = {1: filtered, 2: filtered, 3: RDC_JOBS | {"totalPages": 3}}
    http = _http({"remote.com/jobs/all?page=": lambda url: (200, _rdc_list(pages[int(url.rsplit("=", 1)[1])])),
                  "remote.com/jobs/acme-labs/": _rdc_detail})
    jobs, _ = _run(ks.fetch_remotedotcom, http)
    assert len([c for c in http.calls if "all?page=" in c["url"]]) == 3
    assert any(j["title"] == "Senior Backend Engineer" for j in jobs)
    empty = _http({"remote.com/jobs/all?page=": (200, _rdc_list({"totalPages": 3, "jobs": []}))})
    _run(ks.fetch_remotedotcom, empty)
    assert len(empty.calls) == 1  # a truly empty page still ends the walk


def test_jobdanmark_filtered_out_page_does_not_end_the_walk():
    bad = {"totalPages": 3, "items": [{"title": "Engineer", "companyName": "", "url": "/job/x"},
                                      {"title": "Engineer", "companyName": "Evil", "url": "https://evil.example/job/y"}]}
    good = {"totalPages": 3, "items": [{"title": "Engineer", "companyName": "Novo", "url": "/job/z", "publishedDate": "07-10-2026"}]}
    items, total, rows = ks.parse_jobdanmark_items(bad)
    assert items == [] and total == 3 and rows == 2
    pages = {1: bad, 2: bad, 3: good}
    http = _http({"api/jobsearch/search/": lambda url: (200, pages[int(url.rsplit("/", 1)[1])])})
    jobs, _ = _run(ks.fetch_jobdanmark, http, countries=("DK",))
    assert len([c for c in http.calls if "search/" in c["url"]]) == 3
    assert [j["url"] for j in jobs] == ["https://jobdanmark.dk/job/z"]
    empty = _http({"search/": (200, {"totalPages": 3, "items": []})})
    _run(ks.fetch_jobdanmark, empty, countries=("DK",))
    assert len(empty.calls) == 1
