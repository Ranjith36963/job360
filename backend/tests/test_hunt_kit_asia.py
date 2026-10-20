"""Offline tests for src/hunt_kit/kit_asia.py (Glints, Kalibrr, Tech in Asia, ITviec; fixtures only, no network).

Fixture origins: Kalibrr rows follow freehire's kalibrr.go:71-106 field names and testdata/kalibrr/indonesia_it.json
(first row's values); Tech in Asia hits follow techinasia.go:108-133 and testdata/techinasia/list.json (Sigmatech
row); ITviec cards and posting follow freehire testdata/itviec_list_page1.html and itviec_detail_salary.html;
Glints answers follow career-ops providers/glints.mjs:38-67 and the searchJobsV3 response shape in career-ops
tests/providers/glints.test.mjs (:119-135 a full data.searchJobsV3 { jobsInPage [...], hasMore: false } answer,
:150 and :163 more; company name/brandName at :29 and :49). The hosts are unreachable from the build container: nothing
here was checked against a live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_asia_under_test", _KIT / "kit_asia.py")
ka = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ka)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(ka, "PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets (url, body_obj)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=None):
        obj = json.loads(data) if data else None
        calls.append({"url": url, "method": method, "body": obj, "headers": headers or {}, "once": once})
        for needle, resp in routes.items():
            if needle in url:
                return resp(url, obj) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["url"].startswith("https://") and j["extra"]["attribution"]
        assert ("title_only" in j["extra"]) == (not j["description"])


def _run(fn, http, countries, queries=("engineer",), days=BIG):
    stats: dict = {}
    return fn(list(queries), http, stats, days=days, countries=countries), stats


# ----------------------------------------------------------------------------------- Glints
def _glints_row(i, title="Backend Engineer", company="Gojek", **kw):
    row = {"id": f"id-{i}", "title": title, "company": {"name": company, "brandName": "Brand"},
           "city": {"name": "Jakarta Selatan"}, "country": {"code": "ID", "name": "Indonesia"},
           "createdAt": "2026-10-10T08:00:00.000Z"}
    row.update(kw)
    return row


def _glints_page(rows, more=False):
    return {"data": {"searchJobsV3": {"jobsInPage": rows, "expInfo": "x", "hasMore": more}}}


def test_glints_happy_path_request_and_job():
    http = _http({"glints.com/api/v2-alc/graphql": (200, _glints_page([
        _glints_row(1), {**_glints_row(2), "company": {"brandName": "Brand"}},
        {**_glints_row(3), "company": {}}, {"title": "no id", "company": {"name": "X"}}, "junk"]))})
    jobs, stats = _run(ka.fetch_glints, http, ["ID"])
    _check(jobs, "glints")
    assert [j["company"] for j in jobs] == ["Gojek", "Brand"]  # no employer at all (row 3) is dropped
    j = jobs[0]
    assert j["company"] == "Gojek" and j["url"] == "https://glints.com/id/opportunities/jobs/id-1"
    assert j["location"] == "Jakarta Selatan, Indonesia" and j["posted"] == "2026-10-10"
    call = http.calls[0]
    assert call["method"] == "POST" and call["once"] is True
    assert call["headers"]["origin"] == "https://glints.com" and "Chrome" in call["headers"]["user-agent"]
    v = call["body"]["variables"]["data"]
    assert call["body"]["operationName"] == "searchJobsV3"
    assert v["SearchTerm"] == "engineer" and v["CountryCode"] == "ID" and v["page"] == 1
    assert v["includeExternalJobs"] is True and stats["jobs"] == 2


def test_glints_brand_name_fallback_and_paging_stops_when_no_more():
    rows = [_glints_row(i, company="") for i in range(ka.GLINTS_PAGE)]
    for r in rows:
        r["company"] = {"brandName": "Acme"}
    http = _http({"graphql": (200, _glints_page(rows, more=True))})
    jobs, _ = _run(ka.fetch_glints, http, ["SG"])
    assert len(http.calls) == ka.GLINTS_PAGES and {j["company"] for j in jobs} == {"Acme"}
    assert http.calls[0]["body"]["variables"]["data"]["CountryCode"] == "SG"
    http2 = _http({"graphql": (200, _glints_page(rows, more=False))})
    _run(ka.fetch_glints, http2, ["SG"])
    assert len(http2.calls) == 1


def test_glints_empty_result():
    jobs, stats = _run(ka.fetch_glints, _http({"graphql": (200, _glints_page([]))}), ["ID"])
    assert jobs == [] and stats["ok"] == 1


def test_glints_non_200_stops_the_site():
    http = _http({"graphql": (403, None)})
    jobs, stats = _run(ka.fetch_glints, http, ["ID", "SG", "MY"], queries=("a", "b"))
    assert jobs == [] and len(http.calls) == 1 and stats["tried"] == 1


def test_glints_countries_gating():
    http = _http({"graphql": (200, _glints_page([_glints_row(1)]))})
    for cs in ([], ["GB"], ["TW"], ["us", "de"]):  # TW is NOT IN REFERENCE
        assert _run(ka.fetch_glints, http, cs)[0] == []
    assert http.calls == []
    _run(ka.fetch_glints, http, ["id", "vn"])
    assert {c["body"]["variables"]["data"]["CountryCode"] for c in http.calls} == {"ID", "VN"}
    http.calls.clear()
    _run(ka.fetch_glints, http, ["*"])
    assert {c["body"]["variables"]["data"]["CountryCode"] for c in http.calls} == set(ka.GLINTS_MARKETS)


def test_glints_pages_on_the_raw_page_size_not_the_parsed_count():
    rows = [_glints_row(i) for i in range(ka.GLINTS_PAGE - 5)] + [{**_glints_row(i), "company": {}}
                                                                  for i in range(100, 105)]
    http = _http({"graphql": (200, _glints_page(rows, more=True))})
    _run(ka.fetch_glints, http, ["ID"])
    assert len(http.calls) == ka.GLINTS_PAGES  # 30 rows came back (5 without an employer): there is a page 2


def test_wanted_puts_the_users_own_markets_first():
    assert ka._wanted(["VN", "*"], *ka.GLINTS_MARKETS) == ["VN", "ID", "SG", "MY"]
    assert ka._wanted(["my", "vn"], *ka.GLINTS_MARKETS) == ["MY", "VN"]
    assert ka._wanted(["VN", "GB"], "ID", "SG") == []


def test_glints_star_spreads_the_budget_so_the_users_market_is_read():
    http = _http({"graphql": (200, _glints_page([_glints_row(i) for i in range(ka.GLINTS_PAGE)], more=True))})
    _run(ka.fetch_glints, http, ["VN", "*"], queries=("a", "b", "c", "d", "e"))
    seen = [(c["body"]["variables"]["data"]["CountryCode"], c["body"]["variables"]["data"]["page"])
            for c in http.calls]
    assert seen[0] == ("VN", 1) and len(seen) == ka.ASIA_MAX_REQUESTS
    first = [i for i, (_, p) in enumerate(seen) if p == 1]
    assert {cc for cc, p in seen if p == 1} == set(ka.GLINTS_MARKETS)  # every market read before any page 2
    assert first == list(range(len(first))) and len(first) == 5 * len(ka.GLINTS_MARKETS)


def test_glints_days_window_applies_locally():
    old = _glints_row(1, createdAt="2020-01-01T00:00:00Z")
    new = _glints_row(2, createdAt=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    jobs, _ = _run(ka.fetch_glints, _http({"graphql": (200, _glints_page([old, new]))}), ["ID"], days=7)
    assert [j["extra"]["id"] for j in jobs] == ["id-2"]


# ----------------------------------------------------------------------------------- Kalibrr
def _kal_row(i=23001, country="Indonesia", **kw):
    row = {"id": i, "name": "IT  Engineer", "slug": "it-engineer", "function": "IT and Software",
           "company_name": "PT Bank Central Asia Tbk", "company": {"code": "bank-central-asia-1", "name": "BCA"},
           "description": "<p>Join the IT team</p>", "qualifications": "<p>5 years</p>",
           "activation_date": "2026-09-17T08:27:25.188443+00:00", "is_work_from_home": False,
           "apply_redirect_url": "https://karir.bca.co.id/candidate/id/career/it-engineer",
           "google_location": {"address_components": {"city": "Jakarta", "region": "DKI Jakarta",
                                                       "country": country}}}
    row.update(kw)
    return row


def test_kalibrr_happy_path():
    http = _http({"kalibrr.com/kjs/job_board/search": (200, {"count": 2, "jobs": [
        _kal_row(1), _kal_row(2, is_work_from_home=True, apply_redirect_url="https://bit.ly/x")]})})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["ID"])
    _check(jobs, "kalibrr")
    j = jobs[0]
    assert j["title"] == "IT Engineer" and j["company"] == "PT Bank Central Asia Tbk"
    assert j["url"] == "https://www.kalibrr.com/c/bank-central-asia-1/jobs/1/it-engineer"
    assert j["location"] == "Jakarta, DKI Jakarta, Indonesia" and j["posted"] == "2026-09-17"
    assert "Join the IT team" in j["description"] and "5 years" in j["description"]
    assert j["extra"]["apply_url"].startswith("https://karir.bca.co.id/") and j["remote"] is None
    assert "apply_url" not in jobs[1]["extra"] and jobs[1]["remote"] is True
    q = parse_qs(urlsplit(http.calls[0]["url"]).query)
    assert q["country"] == ["Indonesia"] and q["text"] == ["engineer"] and q["offset"] == ["0"]
    assert "function" not in q  # NOT IN REFERENCE to combine; the kit does not pin a job function


def test_kalibrr_drops_other_countries_unsafe_segments_and_missing_employer():
    rows = [_kal_row(1, country="Thailand"), _kal_row(2, slug="bad/slug"), _kal_row(3, company_name="",
            company={"code": "c", "name": ""}), _kal_row(4), "junk", {"id": "x"}]
    jobs, _ = _run(ka.fetch_kalibrr, _http({"job_board/search": (200, {"count": 6, "jobs": rows})}), ["ID"])
    assert [j["extra"]["id"] for j in jobs] == [4]


def test_kalibrr_pages_by_offset_and_dedups_repeated_rows():
    full = {"count": 150, "jobs": [_kal_row(i) for i in range(1, ka.KALIBRR_LIMIT + 1)]}
    http = _http({"job_board/search": (200, full)})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["ID"])
    assert len(http.calls) == 2 and parse_qs(urlsplit(http.calls[1]["url"]).query)["offset"] == ["100"]
    assert len(jobs) == ka.KALIBRR_LIMIT  # the repeated second page is deduplicated


def test_kalibrr_empty_non_200_and_gating():
    assert _run(ka.fetch_kalibrr, _http({"search": (200, {"count": 0, "jobs": []})}), ["PH"])[0] == []
    http = _http({"search": (429, None)})
    jobs, _ = _run(ka.fetch_kalibrr, http, ["PH", "ID"], queries=("a", "b"))
    assert jobs == [] and len(http.calls) == 1
    http = _http({"search": (200, {"count": 0, "jobs": []})})
    for cs in ([], ["SG"], ["VN"]):
        assert _run(ka.fetch_kalibrr, http, cs)[0] == []
    assert http.calls == []
    _run(ka.fetch_kalibrr, http, ["*"])
    assert {parse_qs(urlsplit(c["url"]).query)["country"][0] for c in http.calls} == {"Philippines", "Indonesia"}


# -------------------------------------------------------------------------------- Tech in Asia
SEARCH_PAGE = '<html><script src="//static.techinasia.com/assets/v5/app.1a2b3c4d.js"></script></html>'
BUNDLE = 'x={apiKey:"notarealkeyjustafixturenotarealkey",appId:"ABCDE12345",foo:1}'


def _hit(i, country="Indonesia", **kw):
    h = {"id": f"id-{i}", "title": "Backend Engineer (Golang)", "description": "<p>Golang, SQL</p>",
         "external_link": f"https://careers.sigmatech.example/jobs/{i}", "published_at": "2026-10-12 09:30:00",
         "company": {"name": "SIGMATECH"}, "city": {"name": "Tangerang", "country_name": country},
         "work_arrangement": "on-site", "is_remote": 0, "experience_min": 5}
    h.update(kw)
    return h


def _tia(hits, pages=1):
    return {"hits": hits, "nbHits": len(hits), "nbPages": pages, "exhaustiveNbHits": True}


def _tia_routes(result):
    return {"techinasia.com/jobs/search": (200, SEARCH_PAGE),
            "static.techinasia.com/assets/v5/app.1a2b3c4d.js": (200, BUNDLE),
            "algolia.net": (200, result)}


def test_techinasia_happy_path_and_algolia_request():
    hits = [_hit(1), _hit(2, work_arrangement="remote", external_link="https://bit.ly/z", experience_min=0),
            _hit(3, country="Japan")]
    http = _http(_tia_routes(_tia(hits)))
    jobs, _ = _run(ka.fetch_techinasia, http, ["ID"], queries=("golang",))
    _check(jobs, "techinasia")
    assert [j["extra"]["id"] for j in jobs] == ["id-1", "id-2"]
    a, b = jobs
    assert a["company"] == "SIGMATECH" and a["url"] == "https://careers.sigmatech.example/jobs/1"
    assert a["extra"]["years_min"] == 5 and a["extra"]["board_url"] == "https://www.techinasia.com/jobs/id-1"
    assert a["posted"] == "2026-10-12" and a["location"] == "Tangerang, Indonesia" and a["remote"] is None
    assert b["url"] == "https://www.techinasia.com/jobs/id-2" and b["remote"] is True
    assert "years_min" not in b["extra"]
    algolia = http.calls[2]
    assert algolia["url"] == "https://abcde12345-dsn.algolia.net/1/indexes/job_postings/query"
    assert algolia["method"] == "POST" and algolia["once"] is True
    assert algolia["headers"]["X-Algolia-Application-Id"] == "ABCDE12345"
    assert algolia["headers"]["X-Algolia-API-Key"] == "notarealkeyjustafixturenotarealkey"
    assert algolia["headers"]["Referer"] == "https://www.techinasia.com/"
    assert "hitsPerPage=1000" in algolia["body"]["params"] and len(http.calls) == 3


def test_techinasia_shared_external_link_falls_back_to_posting_page():
    hits = [_hit(1, external_link="https://forms.example.com/intake"),
            _hit(2, external_link="https://forms.example.com/intake")]
    jobs, _ = _run(ka.fetch_techinasia, _http(_tia_routes(_tia(hits))), ["ID"], queries=("golang",))
    assert [j["url"] for j in jobs] == ["https://www.techinasia.com/jobs/id-1", "https://www.techinasia.com/jobs/id-2"]


def test_techinasia_link_shared_across_pages_falls_back_to_posting_page():
    link = "https://forms.example.com/intake"
    pages = {0: [_hit(1, external_link=link)], 1: [_hit(2, external_link=link)]}
    calls = {"n": 0}

    def algolia(_url, _body):
        page = calls["n"]
        calls["n"] += 1
        return 200, _tia(pages[page], pages=2)

    routes = {**_tia_routes(_tia([])), "algolia.net": algolia}
    jobs, _ = _run(ka.fetch_techinasia, _http(routes), ["ID"], queries=("golang",))
    assert [j["url"] for j in jobs] == ["https://www.techinasia.com/jobs/id-1", "https://www.techinasia.com/jobs/id-2"]


def test_techinasia_keyword_filter_pages_and_star():
    hits = [_hit(1), _hit(2, title="Accountant", description="<p>Books</p>"), _hit(3, country="Singapore")]
    http = _http(_tia_routes(_tia(hits, pages=2)))
    jobs, _ = _run(ka.fetch_techinasia, http, ["ID", "SG"], queries=("golang",))
    assert {j["extra"]["id"] for j in jobs} == {"id-1", "id-3"}
    assert len(http.calls) == 2 + ka.TIA_PAGES  # nbPages 2: both pages read
    jobs, _ = _run(ka.fetch_techinasia, _http(_tia_routes(_tia(hits))), ["*"], queries=("accountant",))
    assert [j["extra"]["id"] for j in jobs] == ["id-2"]


def test_techinasia_empty_non_200_gating_and_missing_credentials():
    jobs, _ = _run(ka.fetch_techinasia, _http(_tia_routes(_tia([]))), ["ID"])
    assert jobs == []
    http = _http({"techinasia.com/jobs/search": (403, None)})
    assert _run(ka.fetch_techinasia, http, ["ID"])[0] == [] and len(http.calls) == 1
    http = _http({**_tia_routes(_tia([_hit(1)])), "algolia.net": (429, None)})
    assert _run(ka.fetch_techinasia, http, ["ID"])[0] == [] and len(http.calls) == 3
    http = _http({"techinasia.com/jobs/search": (200, "<html>no bundle</html>")})
    assert _run(ka.fetch_techinasia, http, ["ID"])[0] == [] and len(http.calls) == 1
    http = _http({"jobs/search": (200, SEARCH_PAGE), "app.1a2b3c4d.js": (200, "var nothing")})
    assert _run(ka.fetch_techinasia, http, ["ID"])[0] == [] and len(http.calls) == 2
    http = _http(_tia_routes(_tia([_hit(1)])))
    for cs in ([], ["GB"], ["IN"], ["US"]):  # India is NOT IN REFERENCE
        assert _run(ka.fetch_techinasia, http, cs)[0] == []
    assert http.calls == []


# ----------------------------------------------------------------------------------- ITviec
def _card(key, slug):
    return (f"<div class='job-card' data-controller='search--job-selection' data-job-key='{key}' "
            f"data-search--job-selection-job-slug-value='{slug}' data-search--pagination-target='jobCard'>\n</div>\n")


LIST_PAGE = ("<html><body><h1>658 IT jobs in Vietnam</h1>\n"
             + _card("747e3af5-f7ef-4bd9-87f2-69c6499f4914", "tmo-devops-engineer-acb-0103")
             + _card("4bf8bdb8-e735-4205-a17e-891be1e1ffbd", "data-quality-specialist-mb-bank-5856")
             + "<a href='/it-jobs/spotlight-job-netcompany-9999' data-search--job-selection-job-slug-value='x'>s</a>\n"
             + "</body></html>")
PAST_END = "<html><body><a href='/it-jobs/netcompany-spotlight-1234'>spot</a></body></html>"


def _detail(company="MB Bank", desc="Top 3 Reasons To Join Us\\r\\nGood pay\\r\\nThe Job\\r\\n<p>Build AI</p>",
            badge="At office", title="AI Software Engineer (Python/Go &amp; AI Agents)"):
    ld = ('{"@context":"http://schema.org","@type":"JobPosting","title":"AI Software Engineer","datePosted":"2026-09-24",'
          f'"description":"{desc}","hiringOrganization":{{"@type":"Organization","name":"{company}"}},'
          '"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Not Available",'
          '"addressRegion":"Hà Nội","addressCountry":"VN"}}]}')
    return (f"<html><body><h1 class='x'>{title}</h1><script type='application/ld+json'>\n{ld}\n</script>"
            "<script type='application/ld+json'>{\"@type\":\"BreadcrumbList\"}</script>"
            f"<div class='d-inline-block preview-header-item'><div class='d-flex'><span>{badge}</span></div></div>"
            "</body></html>")


def test_itviec_cards_ignore_spotlight_links():
    assert ka.parse_itviec_cards(LIST_PAGE) == [("747e3af5-f7ef-4bd9-87f2-69c6499f4914", "tmo-devops-engineer-acb-0103"),
                                                ("4bf8bdb8-e735-4205-a17e-891be1e1ffbd",
                                                 "data-quality-specialist-mb-bank-5856")]
    assert ka.parse_itviec_cards(PAST_END) == [] and ka.parse_itviec_cards(None) == []


def test_itviec_posting_parse():
    j = ka.parse_itviec_posting(_detail(), "https://itviec.com/it-jobs/s-1", "key-1")
    assert j is not None
    _check([j], "itviec")
    assert j["title"] == "AI Software Engineer (Python/Go & AI Agents)"  # the heading keeps the punctuation
    assert j["company"] == "MB Bank" and j["location"] == "Hà Nội, Vietnam" and j["posted"] == "2026-09-24"
    assert "Build AI" in j["description"] and j["remote"] is None and j["extra"]["id"] == "key-1"
    r = ka.parse_itviec_posting(_detail(badge="Remote"), "https://itviec.com/it-jobs/s-1", "k")
    assert r is not None and r["remote"] is True
    assert ka.parse_itviec_posting(_detail(company=""), "https://itviec.com/it-jobs/s", "k") is None
    assert ka.parse_itviec_posting("<html>no ld</html>", "https://itviec.com/it-jobs/s", "k") is None
    assert ka.parse_itviec_posting(None, "https://itviec.com/it-jobs/s", "k") is None


def test_itviec_happy_path_requests_and_links():
    http = _http({"/it-jobs/tmo-devops": (200, _detail()), "/it-jobs/data-quality": (200, _detail(company="Acme")),
                  "/it-jobs/engineer": (200, LIST_PAGE), "/it-jobs/engineer?page=2": (200, PAST_END)})
    jobs, stats = _run(ka.fetch_itviec, http, ["VN"])
    _check(jobs, "itviec")
    assert [j["url"] for j in jobs] == ["https://itviec.com/it-jobs/tmo-devops-engineer-acb-0103",
                                        "https://itviec.com/it-jobs/data-quality-specialist-mb-bank-5856"]
    assert [j["company"] for j in jobs] == ["MB Bank", "Acme"]
    urls = [c["url"] for c in http.calls]
    assert urls[0] == "https://itviec.com/it-jobs/engineer" and urls[1] == "https://itviec.com/it-jobs/engineer?page=2"
    assert all(c["once"] is True and c["method"] == "GET" for c in http.calls) and stats["jobs"] == 2


def test_itviec_multiword_query_slug_and_detail_cap(monkeypatch):
    assert ka._itviec_list_url("Data Engineer", 3) == "https://itviec.com/it-jobs/data-engineer?page=3"
    monkeypatch.setattr(ka, "ITVIEC_DETAILS", 1)
    http = _http({"/it-jobs/tmo-devops": (200, _detail()), "/it-jobs/engineer": (200, LIST_PAGE)})
    jobs, _ = _run(ka.fetch_itviec, http, ["VN"])
    assert len(jobs) == 1 and len([c for c in http.calls if "tmo-devops" in c["url"]]) == 1


def test_itviec_keyword_slug_drops_slashes():
    assert ka._itviec_list_url("CI/CD Engineer", 1) == "https://itviec.com/it-jobs/cicd-engineer"  # itviec.mjs:140


def test_itviec_details_round_robin_across_queries_and_no_page_2_once_full():
    qs = ("alpha", "bravo", "charlie", "delta", "echo")

    def route(url, _body):
        seg = urlsplit(url).path.rsplit("/", 1)[-1]
        if seg in qs:
            return 200, "<html>" + "".join(_card(f"{seg}-k{i}", f"{seg}-job-{i}") for i in range(20)) + "</html>"
        return 200, _detail()

    http = _http({"itviec.com": route})
    jobs, _ = _run(ka.fetch_itviec, http, ["VN"], queries=qs)
    urls = [c["url"] for c in http.calls]
    assert not any("?page=2" in u for u in urls)  # each query filled its 6 slots on page 1
    details = [u for u in urls if "-job-" in u]
    assert len(details) == ka.ITVIEC_DETAILS and len(jobs) == ka.ITVIEC_DETAILS
    assert {u.rsplit("/", 1)[-1].split("-job-")[0] for u in details} == set(qs)
    assert details[:5] == [f"https://itviec.com/it-jobs/{q}-job-0" for q in qs]


def test_itviec_empty_non_200_blocked_and_gating():
    http = _http({"/it-jobs/engineer": (200, PAST_END)})
    assert _run(ka.fetch_itviec, http, ["VN"])[0] == [] and len(http.calls) == 1
    http = _http({"/it-jobs": (429, None)})
    assert _run(ka.fetch_itviec, http, ["VN"], queries=("a", "b"))[0] == [] and len(http.calls) == 1
    http = _http({"/it-jobs/engineer": (200, LIST_PAGE), "/it-jobs/tmo-devops": (403, None)})
    jobs, _ = _run(ka.fetch_itviec, http, ["VN"])
    assert jobs == [] and len([c for c in http.calls if "tmo-devops" in c["url"]]) == 1
    assert not any("data-quality" in c["url"] for c in http.calls)  # the first refusal ended the site
    http = _http({"/it-jobs": (200, "<html><title>Just a moment...</title>captcha</html>")})
    assert _run(ka.fetch_itviec, http, ["VN"], queries=("a", "b"))[0] == [] and len(http.calls) == 1
    http = _http({"/it-jobs": (200, LIST_PAGE)})
    for cs in ([], ["IN"], ["SG"], ["ID"]):
        assert _run(ka.fetch_itviec, http, cs)[0] == []
    assert http.calls == []
    _run(ka.fetch_itviec, http, ["*"])
    assert http.calls


# ----------------------------------------------------------------------------- shared contract
GARBAGE = [None, 123, "<<<not json>>>", [], {}, {"hits": "x"}, {"jobs": [None, 5, "x"]}, {"data": []},
           {"data": {"searchJobsV3": {"jobsInPage": [None, 1]}}}]


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
@pytest.mark.parametrize("body", GARBAGE)
def test_feeds_never_raise_on_garbage(name, body):
    http = _http({"": (200, body)})
    stats: dict = {}
    out = ka.FEEDS[name](["engineer"], http, stats, days=30, countries=["*"])
    assert out == [] and stats["jobs"] == 0


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
def test_feeds_never_raise_when_http_raises_or_status_zero(name):
    def boom(url, **kw):
        raise OSError("down")

    assert ka.FEEDS[name](["engineer"], boom, {}, days=30, countries=["*"]) == []
    http = _http({"": (0, None)})
    assert ka.FEEDS[name](["engineer"], http, {}, days=30, countries=["*"]) == [] and len(http.calls) == 1


@pytest.mark.parametrize("name", sorted(ka.FEEDS))
def test_feeds_respect_request_cap_and_empty_queries(name, monkeypatch):
    monkeypatch.setattr(ka, "ASIA_MAX_REQUESTS", 3)
    http = _http({"glints": (200, _glints_page([_glints_row(1)], more=True)),
                  "kalibrr": (200, {"count": 9999, "jobs": [_kal_row(i) for i in range(1, 101)]}),
                  "techinasia.com/jobs/search": (200, SEARCH_PAGE), "app.": (200, BUNDLE),
                  "algolia": (200, _tia([_hit(1)], pages=50)), "itviec": (200, LIST_PAGE)})
    ka.FEEDS[name](["a", "b", "c", "d"], http, {}, days=30, countries=["*"])
    assert len(http.calls) <= 3


def test_feed_names_are_the_dict_keys():
    assert set(ka.FEEDS) == {"glints", "kalibrr", "techinasia", "itviec"}
