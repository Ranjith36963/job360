"""Offline tests for src/hunt_kit/kit_regional.py (fixtures only, no network).

Fixture origins: Naukri rows are trimmed from freehire's testdata/naukri_search.json (field names and the
first row's values); Bayt and GulfTalent detail pages follow the ld+json shape of freehire's
bayt_test.go:19-34 and gulftalent_test.go:34-47, and the sitemap shapes of gulftalent_test.go:10-31.
Bayt's search CARDS have no fixture in either reference: they are built from the selectors JobSpy reads
(jobspy/bayt/__init__.py:157-257). The hosts are unreachable from the build container: nothing here was
checked against a live response.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_regional_under_test", _KIT / "kit_regional.py")
kr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kr)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(kr, "PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets the url."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append({"url": url, "headers": headers or {}, "as_json": as_json})
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


# ----------------------------------------------------------------------------------- Naukri
NAUKRI_ROW = {
    "title": "Salesforce commerce cloud Support Engineer", "jobId": "181026004692",
    "companyName": "Tata Consultancy Services",
    "placeholders": [{"type": "experience", "label": "6-11 Yrs"}, {"type": "salary", "label": "Not disclosed"},
                     {"type": "location", "label": "Pune, Chennai, Bengaluru"}],
    "jdURL": "/job-listings-salesforce-commerce-cloud-support-engineer-tata-consultancy-services-"
             "pune-chennai-bengaluru-6-to-11-years-181026004692",
    "jobDescription": "Role - Salesforce commerce cloud (SFCC) Support Engineer. <br><br> Job Summary- .",
    "createdDate": 1792346647302,
}
NAUKRI = {"noOfJobs": 4, "jobDetails": [
    NAUKRI_ROW,
    {**NAUKRI_ROW, "jobId": "2", "title": "Data  Engineer", "companyName": "Acme",
     "placeholders": [{"type": "location", "label": "Hybrid - Pune"}],
     "jdURL": "/job-listings-data-engineer-acme-pune-2", "jobDescription": ""},
    {**NAUKRI_ROW, "jobId": "3", "jdURL": "/some-other-page-3"},  # not a posting path: dropped
    {**NAUKRI_ROW, "jobId": "4", "companyName": " "},  # no employer: dropped
]}


def test_naukri_happy_path_headers_and_params():
    http = _http({"naukri.com/jobapi/v3/search": (200, NAUKRI)})
    stats: dict = {}
    jobs = kr.fetch_naukri(["salesforce"], http, stats, days=BIG, countries=["IN"])
    assert [j["title"] for j in jobs] == ["Salesforce commerce cloud Support Engineer", "Data Engineer"]
    _check(jobs, "naukri")
    j = jobs[0]
    assert j["company"] == "Tata Consultancy Services"  # the employer, never the board
    assert j["url"] == "https://www.naukri.com" + NAUKRI_ROW["jdURL"]
    assert j["location"] == "Pune, Chennai, Bengaluru, India"
    assert j["posted"] == "2026-10-18"
    assert "<br>" not in j["description"] and j["description"].startswith("Role - Salesforce")
    assert j["extra"]["truncated"] is True and j["extra"]["experience"] == "6-11 Yrs"
    assert jobs[1]["location"] == "Pune, India" and jobs[1]["extra"]["title_only"] is True  # "Hybrid - " dropped
    assert len(http.calls) == 1 and stats["tried"] == 1 and stats["ok"] == 1 and stats["jobs"] == 2
    call = http.calls[0]
    assert call["headers"] == {"User-Agent": "okhttp/4.9.0", "appid": "105", "systemid": "105"}
    for part in ("noOfResults=100", "urlType=search_by_keyword", "searchType=adv", "keyword=salesforce",
                 "sort=f", "pageNo=1", f"jobAge={BIG}"):
        assert part in call["url"]


def test_naukri_empty_non_200_and_remote():
    assert kr.fetch_naukri(["x"], _http({"": (200, {"noOfJobs": 0, "jobDetails": []})}), {}, days=30,
                           countries=["IN"]) == []
    assert kr.fetch_naukri(["x"], _http({"": (200, [])}), {}, days=30, countries=["IN"]) == []
    http = _http({"": (406, None)})  # "recaptcha required": a refusal ends the run after one request
    stats: dict = {}
    assert kr.fetch_naukri(["a", "b", "c"], http, stats, days=30, countries=["IN"]) == []
    assert len(http.calls) == 1 and stats["ok"] == 0 and stats["jobs"] == 0
    remote = {"noOfJobs": 1, "jobDetails": [{**NAUKRI_ROW, "placeholders": [{"type": "location", "label": "Remote"}]}]}
    got = kr.parse_naukri(remote)
    assert got[0]["remote"] is True and got[0]["location"] == "India"


def test_naukri_wrong_country_makes_no_request():
    http = _http({"": (200, NAUKRI)})
    assert kr.fetch_naukri(["x"], http, {}, days=30, countries=["AE", "GB"]) == []
    assert http.calls == []


def test_naukri_pages_and_request_cap():
    full = {"noOfJobs": 500, "jobDetails": [
        {**NAUKRI_ROW, "jobId": str(i), "jdURL": f"/job-listings-x-{i}"} for i in range(100)]}
    http = _http({"": (200, full)})
    kr.fetch_naukri(["a", "b", "c", "d", "e", "f", "g"], http, {}, days=30, countries=["IN"])
    assert 0 < len(http.calls) <= kr.REGIONAL_MAX_REQUESTS
    assert len(http.calls) == 5 * kr.NATIONAL_PAGES  # five titles, NATIONAL_PAGES pages each


# ------------------------------------------------------------------------------------- Bayt
def _card(jid, title, company, spans, stamp, remote=False):
    rem = '<dt class="jb-label-remote">Remote</dt>' if remote else ""
    sp = "".join(f"<span>{s}</span>" for s in spans)
    return (f'<li data-js-job data-job-id="{jid}"><h2><a href="/en/uae/jobs/{title.lower().replace(" ", "-")}-{jid}/">'
            f'{title}</a></h2><div class="job-company-location-wrapper"><b><a href="/en/uae/company/x-9/">{company}</a>'
            f'</b></div><dl><dt class="jb-label-location">{sp}</dt>{rem}'
            f'<dd data-automation-jobactivedate="{stamp}"></dd></dl></li>')


BAYT_PAGE = ("<html><body><ul>"
             + _card(5466655, "Machine Learning Engineer", "SADAFCO", ["Dubai", "United Arab Emirates"], 1792346647)
             + _card(42, "Data Scientist", "Careem", ["Dubai", "UAE"], 1792346000, remote=True)
             + '<li class="ad">not a job</li></ul><a href="/en/uae/jobs/x-1/">nav</a></body></html>')


def _detail(company='"hiringOrganization":{"@type":"Organization","name":"SADAFCO"},',
            desc="<p>Do the thing. We sponsor visa.<\\/p><script>alert(1)<\\/script>"):
    return ('<html><head><script type="application/ld+json">\n'
            '{"@context":"http://schema.org/","@type":"JobPosting","title":"Machine Learning Engineer",'
            f'"description":"{desc}","datePosted":"2026-07-03",{company}'
            '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Dammam",'
            '"addressCountry":"SA"}}}\n</script></head><body>page</body></html>')


def test_bayt_happy_path_cards_then_detail():
    http = _http({"/jobs/machine-learning-engineer-jobs/": (200, BAYT_PAGE),
                  "/jobs/machine-learning-engineer-5466655/": (200, _detail())})
    stats: dict = {}
    jobs = kr.fetch_bayt(["machine learning engineer"], http, stats, days=BIG, countries=["AE"])
    assert len(jobs) == 2
    _check([jobs[0]], "bayt")
    j = jobs[0]
    assert j["title"] == "Machine Learning Engineer" and j["company"] == "SADAFCO"  # the card's employer
    assert j["url"] == "https://www.bayt.com/en/uae/jobs/machine-learning-engineer-5466655/"
    assert j["location"] == "Dubai, United Arab Emirates" and j["posted"] == "2026-10-18"
    assert j["description"] == "Do the thing. We sponsor visa." and "title_only" not in j["extra"]
    assert jobs[1]["remote"] is True and jobs[1]["extra"]["title_only"] is True  # detail 404: card data only
    search = http.calls[0]
    assert search["url"].startswith("https://www.bayt.com/en/uae/jobs/machine-learning-engineer-jobs/?")
    assert search["url"].endswith("?page=1")  # a window wider than 30 days sends no date filter
    week = kr._bayt_search_url("uae", "Data Scientist", 2, 7)
    assert "jobs/data-scientist-jobs/?" in week and "jb_last_modification_date_interval" in week
    assert "=2&" in week and "options%5Bsort%5D%5B%5D=d" in week and week.endswith("page=2")
    assert search["headers"]["User-Agent"].startswith("Mozilla/5.0") and search["headers"]["sec-fetch-dest"] == "document"
    assert search["as_json"] is False
    assert stats["jobs"] == 2 and len(http.calls) == 3  # one search page, two detail pages


def test_bayt_company_falls_back_to_the_ad_and_empty_or_blocked():
    cardless = BAYT_PAGE.replace('<a href="/en/uae/company/x-9/">SADAFCO</a>', "")  # wrapper with no name
    http = _http({"jobs/?": (200, cardless), "-jobs/": (200, cardless),
                  "machine-learning-engineer-5466655": (200, _detail())})
    jobs = kr.fetch_bayt(["x"], http, {}, days=BIG, countries=["AE"])
    assert jobs[0]["company"] == "SADAFCO"  # from hiringOrganization
    assert kr.fetch_bayt(["x"], _http({"": (200, "<html><body></body></html>")}), {}, days=30,
                         countries=["AE"]) == []
    blocked = _http({"": (403, None)})
    stats: dict = {}
    assert kr.fetch_bayt(["x", "y"], blocked, stats, days=30, countries=["AE", "SA"]) == []
    assert len(blocked.calls) == 1 and stats["ok"] == 0  # 403 ends the run at once


def test_bayt_wrong_country_makes_no_request():
    http = _http({"": (200, BAYT_PAGE)})
    assert kr.fetch_bayt(["x"], http, {}, days=30, countries=["IN", "GB", "EG"]) == []
    assert http.calls == []


def test_bayt_star_spans_gulf_markets_within_the_request_cap():
    http = _http({"-jobs/": (200, BAYT_PAGE), "": (200, _detail())})
    kr.fetch_bayt(["a", "b", "c", "d", "e"], http, {}, days=BIG, countries=["IN", "*"])
    slugs = {c["url"].split("/en/")[1].split("/")[0] for c in http.calls}
    assert slugs == {"uae", "saudi-arabia", "qatar", "kuwait", "bahrain", "oman"}
    assert len(http.calls) <= kr.REGIONAL_MAX_REQUESTS



def test_wanted_puts_the_users_own_markets_first():
    assert kr._wanted(["OM", "*"], *kr.BAYT_MARKETS)[0] == "OM"
    assert kr._wanted(["OM", "*"], *kr.BAYT_MARKETS)[1:] == ["AE", "SA", "QA", "KW", "BH"]
    assert kr._wanted(["OM"], *kr.BAYT_MARKETS) == ["OM"]


def test_bayt_star_searches_the_users_own_market_first():
    http = _http({"-jobs/": (200, BAYT_PAGE), "": (200, _detail())})
    kr.fetch_bayt(["a"], http, {}, days=BIG, countries=["OM", "*"])
    assert http.calls[0]["url"].split("/en/")[1].split("/")[0] == "oman"

# ------------------------------------------------------------------------------- GulfTalent
GT_JOB = "https://www.gulftalent.com/uae/jobs/maintenance-specialist-604168"
GT_INDEX = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://www.gulftalent.com/sitemaps/sitemap_jx000.xml</loc></sitemap>"
            "<sitemap><loc>https://www.gulftalent.com/sitemaps/sitemap_jl000.xml</loc></sitemap></sitemapindex>")


def _shard(*urls):
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")


def _gt_detail(company="RTC-1 Employment Services", title="Maintenance Specialist"):
    org = f'"hiringOrganization":{{"@type":"Organization","name":"{company}"}},' if company else ""
    return ('<html><head><script type="application/ld+json">\n{"@context":"https://schema.org","@type":"JobPosting",'
            f'"title":"{title}","description":"<p>Keep the plant running. Visa sponsored.<\\/p>",'
            f'"datePosted":"2026-07-03T00:00:00+00:00","employmentType":["FULL_TIME"],{org}'
            '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Dubai",'
            '"addressCountry":"UAE"}}}\n</script></head><body>page</body></html>')


def test_gulftalent_happy_path_matches_urls_then_reads_only_those_ads():
    other = "https://www.gulftalent.com/uae/jobs/senior-accountant-700001"
    elsewhere = "https://www.gulftalent.com/qatar/jobs/maintenance-specialist-700002"
    http = _http({"/sitemap.xml": (200, GT_INDEX),
                  "sitemap_jx000.xml": (200, _shard(GT_JOB, other, elsewhere,
                                                    "https://www.gulftalent.com/uae/jobs/category/it")),
                  "maintenance-specialist-604168": (200, _gt_detail())})
    stats: dict = {}
    jobs = kr.fetch_gulftalent(["maintenance specialist"], http, stats, days=BIG, countries=["AE"])
    _check(jobs, "gulftalent")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Maintenance Specialist" and j["company"] == "RTC-1 Employment Services"
    assert j["url"] == GT_JOB and j["location"] == "Dubai, United Arab Emirates" and j["posted"] == "2026-07-03"
    assert j["description"].startswith("Keep the plant running")
    urls = [c["url"] for c in http.calls]
    assert urls == ["https://www.gulftalent.com/sitemap.xml",
                    "https://www.gulftalent.com/sitemaps/sitemap_jx000.xml", GT_JOB]  # jl shard and others untouched
    assert all(c["headers"]["sec-fetch-mode"] == "navigate" and not c["as_json"] for c in http.calls)
    assert stats["jobs"] == 1 and stats["tried"] == 3 and stats["ok"] == 3


def test_gulftalent_empty_company_less_and_non_200():
    http = _http({"/sitemap.xml": (200, GT_INDEX), "sitemap_jx000.xml": (200, _shard()),})
    assert kr.fetch_gulftalent(["maintenance"], http, {}, days=30, countries=["AE"]) == []
    http = _http({"/sitemap.xml": (200, GT_INDEX), "sitemap_jx000.xml": (200, _shard(GT_JOB)),
                  "604168": (200, _gt_detail(company=""))})
    assert kr.fetch_gulftalent(["maintenance"], http, {}, days=30, countries=["AE"]) == []  # no employer: dropped
    down = _http({"": (403, None)})
    stats: dict = {}
    assert kr.fetch_gulftalent(["maintenance"], down, stats, days=30, countries=["AE"]) == []
    assert len(down.calls) == 1 and stats["ok"] == 0
    assert kr.fetch_gulftalent(["x"], _http({"": (200, "not xml <<<")}), {}, days=30, countries=["AE"]) == []


def test_gulftalent_wrong_country_makes_no_request():
    http = _http({"": (200, GT_INDEX)})
    assert kr.fetch_gulftalent(["maintenance"], http, {}, days=30, countries=["IN", "GB"]) == []
    assert http.calls == []


def test_gulftalent_star_covers_every_gulf_country_and_caps_detail_pages():
    urls = [f"https://www.gulftalent.com/{seg}/jobs/engineer-{n}" for n, seg in enumerate(
        ["uae", "saudi-arabia", "qatar", "kuwait", "bahrain", "oman"] * 10, start=1)]
    http = _http({"/sitemap.xml": (200, GT_INDEX), "sitemap_jx000.xml": (200, _shard(*urls)),
                  "/jobs/engineer-": (200, _gt_detail(title="Engineer"))})
    jobs = kr.fetch_gulftalent(["engineer"], http, {}, days=BIG, countries=["IN", "*"])
    assert len(http.calls) == 2 + kr.GT_DETAILS and len(jobs) == kr.GT_DETAILS  # newest ids first, capped
    assert http.calls[2]["url"].endswith("engineer-60")


# ----------------------------------------------------------------------------- shared contract
def test_feeds_registry_and_never_raises():
    assert set(kr.FEEDS) == {"naukri", "bayt", "gulftalent"}

    def boom(*a, **k):
        raise RuntimeError("down")

    for name, countries in (("naukri", ["IN"]), ("bayt", ["AE"]), ("gulftalent", ["AE"])):
        stats: dict = {}
        assert kr.FEEDS[name](["x"], boom, stats, days=30, countries=countries) == []
        assert stats["jobs"] == 0
    for name in kr.FEEDS:  # no titles: nothing is sent
        http = _http({"": (200, {})})
        assert kr.FEEDS[name]([], http, {}, days=30, countries=["IN", "AE"]) == []
        assert http.calls == []


def test_star_runs_naukri_for_the_visa_onsite_core_filter():
    http = _http({"": (200, NAUKRI)})
    assert kr.fetch_naukri(["dev"], http, {}, days=BIG, countries=["US", "*"])
    assert http.calls  # "*" runs it; keeping only visa-sponsoring ads is the core's job, as for kit_gov


def test_naukri_minimum_experience_is_the_stated_floor() -> None:
    # Owner's PC smoke test 2026-10-20: "12-to-18-years" was kept as years_min=3 (a guess from the snippet).
    got = kr.parse_naukri({"noOfJobs": 2, "jobDetails": [{**NAUKRI_ROW, "minimumExperience": "12"},
                                                          {**NAUKRI_ROW, "jobId": "9", "minimumExperience": "0"}]})
    assert got[0]["extra"]["years_min"] == 12 and "years_min" not in got[1]["extra"]


# --------------------------------------------------------------------------- regex scaling (hostile markup)
def _grows_linearly(fn, make, n=6000):
    """Time fn(make(n)) and fn(make(4n)): linear stays within 8x (+50 ms of noise room), quadratic takes ~16x."""
    import time

    def run(k):
        s, best = make(k), 9e9
        for _ in range(2):
            t0 = time.perf_counter()
            fn(s)
            best = min(best, time.perf_counter() - t0)
        return best

    t1, t4 = run(n), run(4 * n)
    assert t4 < 8 * t1 + 0.05, (t1, t4)


def test_ld_posting_scan_is_linear_on_unclosed_scripts():
    page = '<script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>'
    assert kr._ld_posting(page) == {"@type": "JobPosting", "title": "T"}
    _grows_linearly(kr._ld_posting, lambda k: '<script type="application/ld+json">' * k)
    _grows_linearly(kr._ld_posting, lambda k: "<script " * k)
