"""Offline tests for src/hunt_kit/kit_bigco.py (Amazon, Google, Apple, Meta, IBM, Uber; fixtures only, no network).

Fixture origins (the hosts are unreachable from the build container, nothing here was checked against a live
answer): Amazon rows from freehire internal/ingest/sources/amazon.go:45-50 and amazon_test.go; Google ds:1 records
from google.go:129-144 / testdata/google_list.html and career-ops providers/google.mjs card markup (tests/providers
google test); Apple from apple.go:264-280, :336-347 and apple_test.go; Meta and Uber sitemap + ld+json pages from
metacareers_test.go:11-37 and uber_test.go:12-38; IBM hits from ibm.go:179-190 and testdata/ibm_search_italy.json.
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
_spec = importlib.util.spec_from_file_location("kit_bigco_under_test", _KIT / "kit_bigco.py")
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(kc, "PAUSE_S", 0)


def _http(handler):
    """Fake http: handler(url, data_obj) -> (status, body). Records every call."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=None):
        obj = json.loads(data) if data else None
        calls.append({"url": url, "method": method, "data": obj, "headers": headers or {},
                      "as_json": as_json, "once": once})
        return handler(url, obj)

    http.calls = calls
    return http


def _q(url):
    return parse_qs(urlsplit(url).query)


def _run(fn, queries, http, countries, days=BIG):
    stats: dict = {}
    jobs = fn(queries, http, stats, days=days, countries=countries)
    return jobs, stats


def _shape(jobs):
    for j in jobs:
        assert set(j) == JOB_KEYS
        assert j["title"] and j["url"].startswith("https://") and j["company"]
        assert j["source"].startswith("feed:")


# ------------------------------------------------------------------------------------------------ common
def test_feeds_registry():
    assert set(kc.FEEDS) == {"amazon", "google", "apple", "metacareers", "ibm", "uber"}


@pytest.mark.parametrize("name", sorted(["amazon", "google", "apple", "metacareers", "ibm", "uber"]))
def test_no_country_and_unknown_country_send_nothing(name):
    http = _http(lambda u, d: (200, {}))
    for countries in ([], ["ZZ"], ["xx"]):
        jobs, _ = _run(kc.FEEDS[name], ["engineer"], http, countries)
        assert jobs == []
    assert http.calls == []


@pytest.mark.parametrize("name", sorted(["amazon", "google", "apple", "metacareers", "ibm", "uber"]))
@pytest.mark.parametrize("garbage", [(200, "<html>nope</html>"), (200, {"x": 1}), (200, [1, 2]), (200, None),
                                     (200, ""), (0, None), (500, None)])
def test_garbage_never_raises(name, garbage):
    http = _http(lambda u, d: garbage)
    jobs, stats = _run(kc.FEEDS[name], ["engineer"], http, ["GB"])
    assert isinstance(jobs, list)
    assert stats["jobs"] == len(jobs)


def test_feed_raising_http_is_swallowed():
    def boom(url, **kw):
        raise RuntimeError("boom")

    jobs, _ = _run(kc.fetch_amazon, ["engineer"], boom, ["US"])
    assert jobs == []


def test_searches_capped_at_three_and_spread():
    pairs = kc._searches(["a", "b", "c", "d"], ["GB", "US", None])
    assert len(pairs) == 3
    assert {p[1] for p in pairs} >= {"GB", "US"}  # spread over countries, not three titles in one country


# ------------------------------------------------------------------------------------------------ Amazon
def _amazon_row(i, title="Software Development Engineer", loc="London, GBR", posted="June  3, 2026"):
    return {"id_icims": str(1000 + i), "title": title, "job_path": f"/en/jobs/{1000 + i}/sde",
            "normalized_location": loc, "description": "<p>Build it. 3+ years of experience.</p>",
            "posted_date": posted, "company_name": "Amazon Web Services, Inc.",
            "locations": [json.dumps({"location": "GB, London", "normalizedLocation": "London, GBR"}),
                          json.dumps({"location": "GB, Cardiff", "normalizedLocation": "Cardiff, GBR"})]}


def test_amazon_happy_path_params_and_fields():
    http = _http(lambda u, d: (200, {"hits": 2, "jobs": [_amazon_row(1), _amazon_row(2)]}))
    jobs, stats = _run(kc.fetch_amazon, ["software engineer"], http, ["GB"])
    assert len(jobs) == 2
    _shape(jobs)
    j = jobs[0]
    assert j["company"] == "Amazon Web Services, Inc."  # the employer named in the ad, not the board
    assert j["url"] == "https://www.amazon.jobs/en/jobs/1001/sde"
    assert j["posted"] == "2026-06-03"
    assert j["location"] == "London, GBR · Cardiff, GBR"
    assert "Build it" in j["description"] and "title_only" not in j["extra"]
    q = _q(http.calls[0]["url"])
    assert q["base_query"] == ["software engineer"] and q["normalized_country_code[]"] == ["GBR"]
    assert q["sort"] == ["recent"] and q["result_limit"] == ["100"] and q["offset"] == ["0"]
    assert http.calls[0]["once"] is True
    assert stats["tried"] == 1 and stats["ok"] == 1


def test_amazon_paginates_to_two_pages_then_stops(monkeypatch):
    monkeypatch.setattr(kc, "AMAZON_PAGE", 2)
    pages = {0: [_amazon_row(1), _amazon_row(2)], 2: [_amazon_row(3), _amazon_row(4)], 4: [_amazon_row(5)]}

    def handler(url, d):
        return 200, {"jobs": pages.get(int(_q(url)["offset"][0]), [])}

    http = _http(handler)
    jobs, _ = _run(kc.fetch_amazon, ["engineer"], http, ["GB"])
    assert len(jobs) == 4  # PAGES = 2: the third page is never asked for
    assert [_q(c["url"])["offset"][0] for c in http.calls] == ["0", "2"]


def test_amazon_empty_result():
    http = _http(lambda u, d: (200, {"hits": 0, "jobs": []}))
    jobs, _ = _run(kc.fetch_amazon, ["engineer"], http, ["GB"])
    assert jobs == [] and len(http.calls) == 1


def test_amazon_non_200_stops_the_site():
    http = _http(lambda u, d: (429, None))
    jobs, _ = _run(kc.fetch_amazon, ["a", "b", "c"], http, ["GB", "US"])
    assert jobs == [] and len(http.calls) == 1


def test_amazon_star_has_no_country_filter_and_dedups():
    http = _http(lambda u, d: (200, {"jobs": [_amazon_row(1)]}))
    jobs, _ = _run(kc.fetch_amazon, ["engineer"], http, ["*"])
    assert len(jobs) == 1
    assert "normalized_country_code[]" not in _q(http.calls[0]["url"])
    http2 = _http(lambda u, d: (200, {"jobs": [_amazon_row(1)]}))
    jobs2, _ = _run(kc.fetch_amazon, ["a", "b"], http2, ["GB", "US"])
    assert len(jobs2) == 1  # the same ad from two searches is kept once


def test_amazon_drops_rows_without_path_or_title_and_old_ads():
    rows = [{"title": "No path"}, {"job_path": "/x"}, _amazon_row(1, posted="January  2, 2020"), _amazon_row(2)]
    http = _http(lambda u, d: (200, {"jobs": rows}))
    jobs, _ = _run(kc.fetch_amazon, ["engineer"], http, ["GB"], days=30)
    assert [j["extra"]["id"] for j in jobs] == ["1002"] or jobs == []  # 2026-06-03 may itself be older than 30 days
    jobs_all, _ = _run(kc.fetch_amazon, ["engineer"], http, ["GB"], days=BIG)
    assert len(jobs_all) == 2


# ------------------------------------------------------------------------------------------------ Google
def _g_page(records):
    blob = json.dumps([records, None, len(records), 20])
    return ("<!doctype html><html><body><script nonce=\"x\">var AF_initDataCallback = function(){};</script>"
            "<script nonce=\"x\">AF_initDataCallback({key: 'ds:1', hash: '2', data:" + blob
            + ", sideChannel: {}});</script></body></html>")


def _g_rec(gid, title, brand="DeepMind", loc=("Bengaluru, Karnataka, India", "IN"), secs=1768435200):
    return [gid, title, "https://careers.google.com/apply", [None, "<ul><li>Resp one</li></ul>"],
            [None, "<ul><li>Min qual</li></ul>"], "projects/x", None, brand, "en-US",
            [[loc[0], [loc[0]], "City", None, "KA", loc[1]]], [None, "<p>About the job.</p>"], None, [secs, 0]]


GOOGLE_CARDS = (
    '<!doctype html><ul><li><div><h3 class="QJPWVe">Product Manager II, YouTube Ads &amp; AI/ML</h3>'
    '<p class="l103df">YouTube | <span>Kirkland, WA, USA</span><span>; Mountain View, CA, USA</span>'
    '<span>; +2 more</span></p><a href="jobs/results/127792359805985478-product-manager-ii-youtube-ads?q=pm" '
    'aria-label="Learn more about Product Manager II"></a></div></li>'
    '<li><div><h3>Associate Strategy</h3><p class="l103df">Google | <span>San Jose, CA, USA</span></p>'
    '<a href="jobs/results/95645113710977734-associate-strategy"></a></div></li></ul>')


def test_google_records_happy_path_and_url_has_no_page_param():
    http = _http(lambda u, d: (200, _g_page([_g_rec("111", "Staff Engineer, DeepMind"),
                                             _g_rec("222", "Engineer II", brand="Google")])))
    jobs, _ = _run(kc.fetch_google, ["engineer"], http, ["IN"])
    assert [j["title"] for j in jobs] == ["Staff Engineer, DeepMind", "Engineer II"]
    _shape(jobs)
    assert jobs[0]["company"] == "DeepMind"  # the hiring brand, not "Google Careers"
    assert jobs[0]["url"] == "https://www.google.com/about/careers/applications/jobs/results/111"
    assert jobs[0]["posted"] == "2026-01-15"
    assert "About the job" in jobs[0]["description"] and "Min qual" in jobs[0]["description"]
    q = _q(http.calls[0]["url"])
    assert q["q"] == ["engineer"] and q["location"] == ["India"] and q["sort_by"] == ["date"]
    assert "page" not in q  # robots.txt: career-ops google.mjs:17-33


def test_google_drops_ads_in_other_countries():
    rows = [_g_rec("1", "A engineer"), _g_rec("2", "B engineer", loc=("London, UK", "GB"))]
    http = _http(lambda u, d: (200, _g_page(rows)))
    jobs, _ = _run(kc.fetch_google, ["engineer"], http, ["GB"])
    assert [j["extra"]["id"] for j in jobs] == ["2"]
    assert all("_codes" not in j["extra"] for j in jobs)


def test_google_cards_fallback_employer_and_more_marker():
    http = _http(lambda u, d: (200, GOOGLE_CARDS))
    jobs, _ = _run(kc.fetch_google, ["product"], http, ["US"])
    assert len(jobs) == 2
    first = jobs[0]
    assert first["company"] == "YouTube"  # the employer on the card
    assert first["title"] == "Product Manager II, YouTube Ads & AI/ML"
    assert first["url"].endswith("/results/127792359805985478-product-manager-ii-youtube-ads")
    assert first["location"] == "Kirkland, WA, USA; Mountain View, CA, USA"  # "+2 more" is not a place
    assert jobs[1]["company"] == "Google"


def test_google_star_sends_no_location():
    http = _http(lambda u, d: (200, GOOGLE_CARDS))
    jobs, _ = _run(kc.fetch_google, ["x"], http, ["*"])
    assert len(jobs) == 2 and "location" not in _q(http.calls[0]["url"])


def test_google_empty_page_goes_on_but_block_page_stops():
    http = _http(lambda u, d: (200, "<html><body>No results</body></html>"))
    jobs, _ = _run(kc.fetch_google, ["a", "b"], http, ["US", "GB"])
    assert jobs == [] and len(http.calls) == 3  # MAX_SEARCHES
    blocked = _http(lambda u, d: (200, "<html>Our systems have detected unusual traffic</html>"))
    jobs, _ = _run(kc.fetch_google, ["a", "b"], blocked, ["US", "GB"])
    assert jobs == [] and len(blocked.calls) == 1


def test_google_non_200_stops():
    http = _http(lambda u, d: (403, None))
    jobs, _ = _run(kc.fetch_google, ["a", "b"], http, ["US", "GB"])
    assert jobs == [] and len(http.calls) == 1


# ------------------------------------------------------------------------------------------------- Apple
def _a_row(pid, title="Software Engineer", loc="Cupertino", home=False, cid=None):
    place = {"name": loc}
    if cid:
        place["countryID"] = cid
    return {"positionId": pid, "postingTitle": title, "transformedPostingTitle": title.lower().replace(" ", "-"),
            "postDateInGMT": "2026-04-22T14:44:56.003755186Z", "homeOffice": home, "locations": [place]}


A_DETAIL = {"res": {"jobSummary": "Join the Watch team.", "description": "Build it.",
                    "minimumQualifications": "5+ years experience.\nSwift", "preferredQualifications": ""}}


def _apple_handler(rows_by_page, detail=A_DETAIL):
    def handler(url, d):
        if url.endswith("/api/v1/search"):
            return 200, {"res": {"searchResults": rows_by_page.get(d["page"], []), "totalRecords": 0}}
        return 200, detail

    return handler


def test_apple_happy_path_dedup_details_and_body():
    rows = {1: [_a_row("200600664"), _a_row("200600664", loc="San Diego"),
                _a_row("200659431", "Site Reliability Engineer", "Seattle", home=True)]}
    http = _http(_apple_handler(rows))
    jobs, _ = _run(kc.fetch_apple, ["engineer"], http, ["US"])
    assert [j["extra"]["id"] for j in jobs] == ["200600664", "200659431"]  # one row per position
    _shape(jobs)
    assert jobs[0]["company"] == "Apple"
    assert jobs[0]["url"] == "https://jobs.apple.com/en-us/details/200600664/software-engineer"
    assert jobs[0]["posted"] == "2026-04-22"
    assert "Join the Watch team." in jobs[0]["description"] and "- 5+ years experience." in jobs[0]["description"]
    assert jobs[1]["remote"] is True
    search = [c for c in http.calls if c["method"] == "POST"][0]
    assert search["data"]["filters"] == {} and search["data"]["query"] == "engineer"  # filters is always sent
    assert search["data"]["sort"] == "newest" and search["data"]["locale"] == "en-us"
    assert [c["url"] for c in http.calls if c["method"] == "GET"] == [
        "https://jobs.apple.com/api/v1/jobDetails/200600664?locale=en-us",
        "https://jobs.apple.com/api/v1/jobDetails/200659431?locale=en-us"]


def test_apple_detail_cap_is_ten():
    rows = {1: [_a_row(str(100 + i), f"Engineer {i}") for i in range(12)]}
    http = _http(_apple_handler(rows))
    jobs, _ = _run(kc.fetch_apple, ["engineer"], http, ["US"])
    assert len(jobs) == 12
    assert len([c for c in http.calls if c["method"] == "GET"]) == 10
    assert sum(1 for j in jobs if j["extra"].get("title_only")) == 2  # the other two stay as links


def test_apple_country_filter_from_country_id_and_unchecked_marker():
    rows = {1: [_a_row("1", loc="London", cid="iso-country-GBR"), _a_row("2", loc="Austin", cid="iso-country-USA"),
                _a_row("3", loc="Somewhere")]}
    http = _http(_apple_handler(rows))
    jobs, _ = _run(kc.fetch_apple, ["engineer"], http, ["GB"])
    assert sorted(j["extra"]["id"] for j in jobs) == ["1", "3"]
    assert next(j for j in jobs if j["extra"]["id"] == "3")["extra"]["country_unchecked"] is True


def test_apple_empty_and_non_200_and_detail_refusal():
    empty = _http(_apple_handler({}))
    assert _run(kc.fetch_apple, ["engineer"], empty, ["US"])[0] == []
    assert len(empty.calls) == 1
    refused = _http(lambda u, d: (436, None))
    assert _run(kc.fetch_apple, ["a", "b"], refused, ["US"])[0] == []
    assert len(refused.calls) == 1

    def handler(url, d):
        return (200, {"res": {"searchResults": [_a_row("1"), _a_row("2")], "totalRecords": 2}}) \
            if url.endswith("/search") else (403, None)

    http = _http(handler)
    jobs, _ = _run(kc.fetch_apple, ["engineer"], http, ["US"])
    assert len(jobs) == 2 and all(j["extra"].get("title_only") for j in jobs)
    assert len([c for c in http.calls if c["method"] == "GET"]) == 1  # the first 403 ends the site


def test_apple_paginates_two_pages():
    rows = {1: [_a_row("1")], 2: [_a_row("2")], 3: [_a_row("3")]}
    http = _http(_apple_handler(rows))
    jobs, _ = _run(kc.fetch_apple, ["engineer"], http, ["US"])
    assert sorted(j["extra"]["id"] for j in jobs) == ["1", "2"]
    assert [c["data"]["page"] for c in http.calls if c["method"] == "POST"] == [1, 2]


# ------------------------------------------------------------------------------------------ Meta + Uber
def _sitemap(*entries):
    return ('<?xml version="1.0"?><urlset>'
            + "".join(f"<url><loc>{loc}</loc><lastmod>{mod}</lastmod></url>" for loc, mod in entries)
            + "</urlset>")


def _meta_page(title, names, date="2026-06-01", org=None):
    locs = ",".join('{"@type":"Place","name":"%s","address":{"addressLocality":"Aiken","addressCountry":'
                    '{"@type":"Country","name":["USA","USA"]}}}' % n for n in names)
    org_json = ',"hiringOrganization":{"name":"%s"}' % org if org else ""
    return ('<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"%s",'
            '"description":"<h2>Role<\\/h2><p>Build it.<\\/p>","datePosted":"%s",'
            '"jobLocation":[%s]%s}</script></head></html>' % (title, date, locs, org_json))


def _uber_page(title, locality, country, ident="149574", date="2026-06-19"):
    return ('<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"%s",'
            '"description":"<p>Move the world.<\\/p>","identifier":{"@type":"PropertyValue","value":"%s"},'
            '"datePosted":"%s","jobLocation":[{"@type":"Place","name":"%s","address":{"@type":"PostalAddress",'
            '"addressLocality":"%s","addressRegion":"X","addressCountry":"%s"}}]}</script></head></html>'
            % (title, ident, date, locality, locality, country))


FRESH = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")


def test_meta_happy_path_title_and_country_filters():
    base = "https://www.metacareers.com/profile/job_details/"
    pages = {
        base + "111": _meta_page("Software Engineer, Infra", ["London, UK"], org="Meta Platforms, Inc."),
        base + "222": _meta_page("Recruiter", ["London, UK"]),
        base + "333": _meta_page("Software Engineer, Ads", ["Menlo Park, CA"]),
        base + "444": _meta_page("Software Engineer, AR", ["Dublin, Ireland"]),
        base + "555": _meta_page("Software Engineer, Maps", ["Somewhere"]),
    }
    sitemap = _sitemap(*[(base + str(i), FRESH) for i in (111, 222, 333, 444, 555)])

    def handler(url, d):
        return (200, sitemap) if url.endswith("sitemap.xml") else (200, pages[url])

    http = _http(handler)
    jobs, _ = _run(kc.fetch_metacareers, ["software engineer"], http, ["GB"])
    assert sorted(j["extra"]["id"] for j in jobs) == ["111", "555"]  # recruiter, US and Irish ads dropped
    _shape(jobs)
    j = next(x for x in jobs if x["extra"]["id"] == "111")
    assert j["company"] == "Meta Platforms, Inc." and j["url"] == base + "111"
    assert j["location"] == "London, UK" and j["posted"] == "2026-06-01" and "Build it" in j["description"]
    assert next(x for x in jobs if x["extra"]["id"] == "555")["extra"]["country_unchecked"] is True
    assert http.calls[0]["url"] == "https://www.metacareers.com/jobsearch/sitemap.xml"


def test_meta_detail_cap_and_newest_first_and_stale_skip():
    base = "https://www.metacareers.com/profile/job_details/"
    old = "2020-01-01T00:00:00Z"
    entries = [(base + str(i), FRESH) for i in range(1, 13)] + [(base + "999", old)]
    page = _meta_page("Software Engineer", ["London, UK"], date=FRESH[:10])

    def handler(url, d):
        return (200, _sitemap(*entries)) if url.endswith("sitemap.xml") else (200, page)

    http = _http(handler)
    jobs, _ = _run(kc.fetch_metacareers, ["software engineer"], http, ["GB"], days=30)
    assert len(http.calls) == 1 + 10  # sitemap + DETAIL_CAP
    assert len(jobs) == 10
    http2 = _http(handler)
    _run(kc.fetch_metacareers, ["software engineer"], http2, ["GB"], days=30)
    assert not any(c["url"].endswith("/999") for c in http2.calls)


def test_meta_non_200_empty_and_login_page():
    assert _run(kc.fetch_metacareers, ["x"], _http(lambda u, d: (403, None)), ["US"])[0] == []
    http = _http(lambda u, d: (200, _sitemap()))
    assert _run(kc.fetch_metacareers, ["x"], http, ["US"])[0] == [] and len(http.calls) == 1
    base = "https://www.metacareers.com/profile/job_details/"
    sm = _sitemap((base + "1", FRESH), (base + "2", FRESH))

    def handler(url, d):
        return (200, sm) if url.endswith("sitemap.xml") else (200, "<html>Please verify you are human</html>")

    http2 = _http(handler)
    assert _run(kc.fetch_metacareers, ["x"], http2, ["US"])[0] == []
    assert len(http2.calls) == 2  # sitemap + the one page that was a security page


def test_uber_happy_path_identifier_and_country():
    sm = _sitemap(("https://jobs.uber.com/en/jobs/149574/", FRESH), ("https://jobs.uber.com/en/jobs/149575/", FRESH),
                  ("https://jobs.uber.com/en/careers/", FRESH))
    pages = {"https://jobs.uber.com/en/jobs/149574/": _uber_page("Sr Staff Engineer", "Sao Paulo", "Brazil"),
             "https://jobs.uber.com/en/jobs/149575/": _uber_page("Staff Engineer", "London", "United Kingdom", "2")}

    def handler(url, d):
        return (200, sm) if url.endswith("sitemap.xml") else (200, pages[url])

    http = _http(handler)
    jobs, _ = _run(kc.fetch_uber, ["staff engineer"], http, ["BR"])
    assert [j["extra"]["id"] for j in jobs] == ["149574"]
    _shape(jobs)
    assert jobs[0]["company"] == "Uber" and jobs[0]["url"] == "https://jobs.uber.com/en/jobs/149574/"
    assert "Sao Paulo" in jobs[0]["location"] and jobs[0]["posted"] == "2026-06-19"
    assert len(http.calls) == 3  # the /careers/ URL carries no job id and is not fetched


def test_uber_requires_identifier_and_stops_on_non_200_detail():
    sm = _sitemap(("https://jobs.uber.com/en/jobs/1/", FRESH), ("https://jobs.uber.com/en/jobs/2/", FRESH))
    no_id = _uber_page("Engineer", "Paris", "France", ident="")

    def handler(url, d):
        return (200, sm) if url.endswith("sitemap.xml") else (200, no_id)

    assert _run(kc.fetch_uber, ["engineer"], _http(handler), ["FR"])[0] == []

    def handler2(url, d):
        return (200, sm) if url.endswith("sitemap.xml") else (403, None)

    http = _http(handler2)
    assert _run(kc.fetch_uber, ["engineer"], http, ["FR"])[0] == [] and len(http.calls) == 2


def test_sitemap_parser_sorts_newest_first_and_ignores_other_urls():
    xml = _sitemap(("https://x/job_details/1", "2026-01-01"), ("https://x/job_details/2", "2026-03-01"),
                   ("https://x/about", "2026-05-01"))
    rows = kc.parse_sitemap(xml, kc._META_ID)
    assert [r[1] for r in rows] == ["2", "1"]
    assert kc.parse_sitemap(None, kc._META_ID) == []


# --------------------------------------------------------------------------------------------------- IBM
def _ibm_hit(jid, title="Software Engineer", city="BUDAPEST, HU", country="Hungary", mode="Hybrid",
             level="Professional", body=None):
    area, entity = "Software Engineering", "(0096) IBM Hungary Kft."
    text = body or "Youâ€™ll build things. • item one • item two"
    suffix = " ".join([country, area, mode, level, city, entity])
    return {"_source": {"field_text_01": jid, "title": title, "body": text + " " + suffix, "dcdate": "2026-10-13",
                        "field_keyword_05": country, "field_keyword_08": area, "field_keyword_17": mode,
                        "field_keyword_18": level, "field_keyword_19": city, "field_keyword_20": entity}}


def _ibm_page(*hits):
    return {"hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": list(hits)}}


def test_ibm_happy_path_body_filter_and_description_cleanup():
    http = _http(lambda u, d: (200, _ibm_page(_ibm_hit(136943), _ibm_hit(136944, "Sales Rep", mode="Remote"))))
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["HU"])
    assert [j["extra"]["id"] for j in jobs] == ["136943"]  # the ad that does not match the title is dropped
    _shape(jobs)
    j = jobs[0]
    assert j["company"] == "IBM" and j["url"] == "https://careers.ibm.com/careers/JobDetail?jobId=136943"
    assert j["posted"] == "2026-10-13" and j["location"] == "BUDAPEST, HU"
    assert j["extra"]["entity"] == "(0096) IBM Hungary Kft." and "_codes" not in j["extra"]
    assert "You’ll build things." in j["description"] and "Software Engineering" not in j["description"]
    assert "- item one" in j["description"] and "- item two" in j["description"]
    body = http.calls[0]["data"]
    assert http.calls[0]["url"] == "https://www-api.ibm.com/search/api/v2"
    assert body["post_filter"] == {"bool": {"must": [{"term": {"field_keyword_05": "Hungary"}}]}}
    assert body["size"] == 100 and body["from"] == 0 and body["sm"] == {"query": "software engineer", "lang": "zz"}
    assert body["appId"] == "careers" and body["scopes"] == ["careers2"] and "field_text_01" in body["_source"]
    assert http.calls[0]["headers"]["content-type"] == "application/json"


def test_ibm_multiple_cities_uses_country_and_remote_flag():
    hit = _ibm_hit(1, city="Multiple Cities", country="Italy", mode="Remote")
    http = _http(lambda u, d: (200, _ibm_page(hit)))
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["IT"])
    assert jobs[0]["location"] == "Italy" and jobs[0]["remote"] is True


def test_ibm_drops_other_country_and_two_pages_max(monkeypatch):
    monkeypatch.setattr(kc, "IBM_PAGE", 2)
    pages = {0: _ibm_page(_ibm_hit(1), _ibm_hit(2)), 2: _ibm_page(_ibm_hit(3, city="PARIS, FR", country="France"),
                                                                  _ibm_hit(4)), 4: _ibm_page(_ibm_hit(5))}
    http = _http(lambda u, d: (200, pages.get(d["from"], _ibm_page())))
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["HU"])
    assert sorted(j["extra"]["id"] for j in jobs) == ["1", "2", "4"]  # id 3 is French; page 3 never asked for
    assert [c["data"]["from"] for c in http.calls] == [0, 2]


def test_ibm_star_empty_filter_empty_result_and_refusal():
    http = _http(lambda u, d: (200, _ibm_page(_ibm_hit(1))))
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["*"])
    assert len(jobs) == 1 and http.calls[0]["data"]["post_filter"] == {"bool": {"must": []}}
    empty = _http(lambda u, d: (200, _ibm_page()))
    assert _run(kc.fetch_ibm, ["x"], empty, ["HU"])[0] == []
    refused = _http(lambda u, d: (403, None))
    assert _run(kc.fetch_ibm, ["a", "b"], refused, ["HU", "IT"])[0] == [] and len(refused.calls) == 1
    odd = _http(lambda u, d: (200, {"hits": {"total": 5}}))
    assert _run(kc.fetch_ibm, ["a", "b"], odd, ["HU", "IT"])[0] == [] and len(odd.calls) == 1


def test_ibm_korea_uses_the_facet_spelling():
    http = _http(lambda u, d: (200, _ibm_page()))
    _run(kc.fetch_ibm, ["engineer"], http, ["KR"])
    assert http.calls[0]["data"]["post_filter"]["bool"]["must"][0]["term"]["field_keyword_05"] == "Korea, Republic of"


@pytest.mark.parametrize("city,country", [("Bangalore, IN", "India"), ("Berlin, DE", "Germany"),
                                          ("Toronto, CA", "Canada"), ("Bangalore, IN", "")])
def test_ibm_city_alpha2_is_never_read_as_a_us_state(city, country):
    """'Bangalore, IN' is India (not Indiana): a US-only user must not be sent it."""
    http = _http(lambda u, d: (200, _ibm_page(_ibm_hit(1, city=city, country=country))))
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["US"])
    assert jobs == []
    jobs, _ = _run(kc.fetch_ibm, ["software engineer"], http, ["*"])
    assert len(jobs) == 1
