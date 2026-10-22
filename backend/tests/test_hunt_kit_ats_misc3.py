"""Offline tests for hunt_kit/kit_ats_misc3.py (Radancy, UKG Ready, eArcu, BeeSite, Workable marketplace).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and career-ops
providers/radancy.mjs and written fresh. Nothing touches the network. HumanBit is not read (its listing page is ~5.6 MB):
its link is reported as unsupported.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m3 = _load("kit_ats_misc3_under_test", "kit_ats_misc3.py")
hk = _load("hunt_kit_misc3_under_test", "hunt_kit.py")
F = m3.ATS_FETCHERS
NAMES = ("beesite", "earcu", "radancy", "ukgready", "workablemarketplace")
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []
    once: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append((url, method, headers or {}))
        once_flags.append((url, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}) if callable(resp) else resp
        return 404, None

    once_flags = once
    http.calls = calls
    http.once = once
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
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_ats_misc3" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_ats_misc3.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_ats_misc3.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://secure4.saashr.com/ta/6162397.careers?ShowJob=1040452356", "ukgready", "secure4.saashr.com/6162397"),
    ("https://aus-secure.prd.mykronos.com/ta/6183838.careers?ShowJob=5", "ukgready", "aus-secure.prd.mykronos.com/6183838"),
    ("https://secure.saashr.com/ta/rest/ui/x", "ukgready", ""),
    ("https://porsche-beesite-production-gjb.app.beesite.de/search", "beesite", "porsche-beesite-production-gjb.app.beesite.de"),
    ("https://jobs.workable.com/view/vqncebKcuVxZHgabGqKWHW/remote-senior-backend", "workablemarketplace", ""),
    ("https://careers.cambridge.org/jobs/vacancy/transfer-pricing-manager-cambridge/7384/description/", "earcu",
     "careers.cambridge.org"),
    ("https://careers.ing.com/job/bucharest/chapter-lead-engineer/3121/39724266176", "radancy", "careers.ing.com"),
    ("https://careers.ing.com/nl/job/bucharest/chapter-lead-engineer/3121/39724266176", "radancy", "careers.ing.com/nl"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


def test_humanbit_is_unsupported_and_loose_paths_are_not_claimed():
    assert hk.board_from_job_url("https://jobs.humanbit.ai/scrabble-jigsaw/jobs/abc")["kind"] == "unsupported"
    assert hk.board_from_job_url("https://example.org/job/london/engineer")["kind"] == "other"
    assert hk.board_from_job_url("https://recruiting.ultipro.com/ABC1000/JobBoard/x/OpportunityDetail")["ats"] == "ultipro"


# --------------------------------------------------------------------------- Radancy
def _rd_card(jid, title, loc, host="careers.ing.com"):
    return (f'<li class="search-results-list__item job-list-12-list__item"><a class="search-results-list__job-link x" '
            f'href="/en/job/bucharest/{title.lower().replace(" ", "-")}/3121/{jid}" data-job-id="{jid}"><h2>{title}</h2></a>'
            f'<ul><li class="search-results-list__job-info--location"><i></i><span>{loc}</span></li></ul></li>')


def _rd_page(*cards, pages=1):
    return {"results": f'<section id="search-results" data-total-results="9" data-total-pages="{pages}"><ul>' + "".join(cards)
            + "</ul></section>", "hasJobs": True}


RD_LD = ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"Chapter Lead Engineer",'
         '"description":"<p>Lead.</p><script>alert(1)<\\/script>","datePosted":"2026-6-7","jobLocationType":"TELECOMMUTE",'
         '"hiringOrganization":{"name":"ING Bank"},"jobLocation":[{"address":{"addressLocality":"Bucharest",'
         '"addressRegion":"10","addressCountry":"RO"}}]}</script></html>')


def _rd_http(pages, detail=(200, RD_LD)):
    def frag(url, headers):
        n = int(parse_qs(urlsplit(url).query)["CurrentPage"][0])
        return (200, pages[n - 1]) if n <= len(pages) else (200, _rd_page())
    return _stub({"/search-jobs/results": frag, "/job/": detail})


def test_radancy_happy_path_fragment_cards_and_jobposting_detail():
    http = _rd_http([_rd_page(_rd_card("111", "Chapter Lead Engineer", "Bucharest, RO"), _rd_card("222", "Analyst", "Cluj, RO"))])
    jobs, stats = _run("radancy", _row("radancy", "careers.ing.com"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["source"] == "radancy" and j["extra"]["job_id"] == "111"
    assert j["url"] == "https://careers.ing.com/en/job/bucharest/chapter-lead-engineer/3121/111"
    assert (j["title"], j["company"], j["location"], j["posted"], j["remote"]) == (
        "Chapter Lead Engineer", "ING Bank", "Bucharest, RO", "2026-06-07", True)  # numeric region dropped, unpadded date
    assert j["description"] == "Lead." and "alert" not in j["description"]
    q = parse_qs(urlsplit(http.calls[0][0]).query)
    assert q["SearchResultsModuleName"] == ["Search Results"] and "SearchFiltersModuleName" not in q
    assert http.calls[0][2].get("X-Requested-With") == "XMLHttpRequest"
    assert _hosts(http) == {"careers.ing.com"} and _job_hosts(jobs) == {"careers.ing.com"}
    assert all(u.startswith("https://") for u, *_ in http.calls)


def test_radancy_pages_to_the_stated_count_dedups_and_legacy_markup_reads():
    legacy = ('<li><a href="/job/austin/dev/9/77" data-job-id="77"><h2>Dev</h2><span class="job-location">Austin, TX</span></a>'
              '<button data-job-id="77">save</button></li>')
    http = _rd_http([_rd_page(_rd_card("1", "A", "x"), pages=2), _rd_page(_rd_card("1", "A", "x"), _rd_card("2", "B", "y"), pages=2),
                     _rd_page(_rd_card("3", "C", "z"), pages=2)])
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com", title_ok=lambda t: False), http)
    assert [j["title"] for j in jobs] == ["A", "B"]
    assert len([u for u, *_ in http.calls if "/search-jobs/results" in u]) == 2
    http = _rd_http([{"results": "<ul>" + legacy + "</ul>"}])
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com", title_ok=lambda t: False), http)
    assert [(j["title"], j["location"]) for j in jobs] == [("Dev", "Austin, TX")]


def test_radancy_bad_href_and_foreign_host_cards_are_skipped_not_the_board():
    bad = _rd_card("5", "Evil", "x").replace('href="/en/job', 'href="https://evil.example/en/job')
    http = _rd_http([_rd_page(bad, _rd_card("6", "Good", "y").replace('href="/en', 'href="http://[bad'), _rd_card("7", "Fine", "z"))])
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com", title_ok=lambda t: False), http)
    assert [j["title"] for j in jobs] == ["Fine"] and _job_hosts(jobs) == {"careers.ing.com"}


def test_radancy_refusal_ends_the_detail_requests_and_keeps_the_cards():
    http = _rd_http([_rd_page(_rd_card("1", "A", "x"), _rd_card("2", "B", "y"))], detail=(429, None))
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com"), http)
    assert len(jobs) == 2 and len([u for u, *_ in http.calls if "/job/" in u and "results" not in u]) == 1
    assert all(o for u, o in http.once if "/job/" in u and "results" not in u)


def test_radancy_falls_back_to_the_sitemap_and_keeps_only_opened_jobs():
    loc = "https://careers.astrazeneca.com/job/lafayette/regional-account-manager/43991/96556869600"
    xml = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"<url><loc>https://careers.astrazeneca.com</loc></url><url><loc>https://careers.astrazeneca.com/en/category/x/2618/3/1</loc></url>"
           f"<url><loc>{loc}</loc></url><url><loc>https://evil.example/job/a/b/1/2</loc></url></urlset>")
    http = _stub({"/search-jobs/results": (404, None), "/sitemap.xml": (200, xml), "/job/": (200, RD_LD)})
    jobs, _ = _run("radancy", _row("radancy", "careers.astrazeneca.com"), http)
    assert [(j["url"], j["extra"]["job_id"]) for j in jobs] == [(loc, "96556869600")]
    assert _hosts(http) == {"careers.astrazeneca.com"}
    http = _stub({"/search-jobs/results": (404, None), "/sitemap.xml": (200, xml), "/job/": (200, RD_LD)})
    jobs, _ = _run("radancy", _row("radancy", "careers.astrazeneca.com", title_ok=lambda t: False), http)
    assert jobs == []  # no title, no page: nothing is listed from a slug
    assert _run("radancy", _row("radancy", "x"), _stub({"/sitemap.xml": (403, None)}))[0] == []


@pytest.mark.parametrize("token", ["", "not a host", "a.com/b/c", "a.com/EN-X1", "http://a.com"])
def test_radancy_bad_tokens_make_no_request(token):
    http = _stub({})
    assert _run("radancy", _row("radancy", token), http)[0] == [] and http.calls == []


# --------------------------------------------------------------------------- UKG Ready
def _ukg_row(rid, title, **kw):
    return {"id": rid, "job_title": title, "location": {"city": "Chicago", "state": "IL", "country": "USA"},
            "job_description": "Preview...", "is_remote_job": False, "employee_type": {"name": "Full Time"}, **kw}


def _ukg_http(pages, total, detail=None):
    def lst(url, headers):
        n = int(parse_qs(urlsplit(url).query)["offset"][0])
        return 200, {"job_requisitions": pages[n - 1] if n <= len(pages) else [], "_paging": {"total": total}}

    def det(url, headers):
        rid = int(url.rsplit("/", 1)[1])
        return 200, {"id": rid, "job_description": "<p>Full body.</p>", "job_requirement": "<p>Must lift.</p>"}
    return _stub({"/job-requisitions?": lst, "/job-requisitions/": detail or det})


def test_ukgready_happy_path_one_based_pages_detail_and_public_url():
    http = _ukg_http([[_ukg_row(1040452356, "Warehouse Lead", is_remote_job=True), _ukg_row(2, "Driver")], [_ukg_row(3, "Clerk")]], 3)
    jobs, stats = _run("ukgready", _row("ukgready", "Secure4.saashr.com/6162397"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["url"] == "https://secure4.saashr.com/ta/6162397.careers?ShowJob=1040452356"
    assert (j["location"], j["remote"], j["description"]) == ("Chicago, IL, USA", True, "Full body.\nMust lift.")
    assert j["extra"] == {"job_id": "1040452356", "employment_type": "Full Time"}
    lists = [parse_qs(urlsplit(u).query) for u, *_ in http.calls if "?offset" in u or "?size" in u or "offset=" in u]
    assert [q["offset"][0] for q in lists] == ["1", "2"] and all(q["size"] == ["200"] for q in lists)
    assert "%7C6162397" in http.calls[0][0]
    assert _hosts(http) == {"secure4.saashr.com"} and _job_hosts(jobs) == {"secure4.saashr.com"}


def test_ukgready_title_filter_cap_refusal_and_later_page_hiccup():
    rows = [_ukg_row(i, f"Role {i}") for i in range(1, 40)]
    http = _ukg_http([rows], 39)
    jobs, _ = _run("ukgready", _row("ukgready", "secure4.saashr.com/1"), http)
    assert len(jobs) == 39 and len([u for u, *_ in http.calls if "/job-requisitions/" in u]) == 25
    assert jobs[30]["description"] == "Preview..."  # beyond the cap the listing preview stays
    http = _ukg_http([[_ukg_row(1, "A"), _ukg_row(2, "B")]], 2, detail=(403, None))
    jobs, _ = _run("ukgready", _row("ukgready", "secure4.saashr.com/1"), http)
    assert len(jobs) == 2 and len([u for u, *_ in http.calls if "/job-requisitions/" in u]) == 1
    http = _stub({"offset=1": (200, {"job_requisitions": [_ukg_row(1, "A")], "_paging": {"total": 5}}), "offset=2": (500, None),
                  "/job-requisitions/": (404, None)})
    assert len(_run("ukgready", _row("ukgready", "secure4.saashr.com/1", title_ok=lambda t: False), http)[0]) == 1
    assert _run("ukgready", _row("ukgready", "secure4.saashr.com/1"), _stub({"offset=1": (403, None)}))[0] == []


def test_ukgready_one_odd_row_costs_only_itself():
    http = _ukg_http([[_ukg_row(1, "A", location="oops", employee_type=7), {"id": "x"}, "junk", _ukg_row(3, "")]], 4)
    jobs, _ = _run("ukgready", _row("ukgready", "secure4.saashr.com/1", title_ok=lambda t: False), http)
    assert [j["title"] for j in jobs] == ["A"]


@pytest.mark.parametrize("token", ["secure4.saashr.com", "secure4.saashr.com/1/x", "evil.example/123", "saashr.com.evil.io/1",
                                   "secure4.saashr.com/a b", ""])
def test_ukgready_bad_boards_make_no_request(token):
    http = _stub({})
    assert _run("ukgready", _row("ukgready", token), http)[0] == [] and http.calls == []


# --------------------------------------------------------------------------- eArcu
def _ea_item(jid, title, desc, link=None, pub="Tue, 07 Jul 2026 15:53:15 Z"):
    link = link or f"https://careers.cambridge.org/jobs/vacancy/role-{jid}/{jid}/description/"
    return (f"<item><guid>{link}</guid><link>{link}</link><title>{title}</title><description>{desc}</description>"
            f"<pubDate>{pub}</pubDate></item>")


def _ea_feed(*items):
    return '<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs</title>' + "".join(items) + "</channel></rss>"


EA_DESC = ("Country: UK, Business Unit: Finance, Salary: &#163;75,300-&#163;90,000, Location: Cambridge, eArcu Vacancy Reference: 7367"
           '&lt;div id="rssjobdesc"&gt;Job Title: Manager&lt;br /&gt;Build the finance pipeline.&lt;/div&gt;')


def test_earcu_happy_path_inline_body_location_and_dates():
    feed = _ea_feed(_ea_item(7384, "Transfer Pricing Manager (7367)", EA_DESC),
                    _ea_item(7375, "Go Engineer (7358)", "Country: UK, Location: Remote, eArcu Vacancy Reference: 7358"
                             "&lt;div id=&quot;rssjobdesc&quot;&gt;x&lt;/div&gt;", pub="Fri, 3 Jul 2026 09:00:00 Z"),
                    _ea_item(1, "No id", "x", link="https://careers.cambridge.org/jobs/"))
    http = _stub({"/jobs/rss": (200, feed)})
    jobs, stats = _run("earcu", _row("earcu", "careers.cambridge.org"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    j = jobs[0]
    assert set(j) == JOB_KEYS and (j["title"], j["location"], j["posted"]) == ("Transfer Pricing Manager", "Cambridge", "2026-07-07")
    assert "Build the finance pipeline." in j["description"] and "Country:" not in j["description"]
    assert j["url"].endswith("/7384/description/") and j["extra"]["job_id"] == "7384"
    assert (jobs[1]["posted"], jobs[1]["remote"]) == ("2026-07-03", True)
    assert _hosts(http) == {"careers.cambridge.org"} and len(http.calls) == 1


def test_earcu_location_scope_country_fallback_and_detail_fallback():
    feed = _ea_feed(_ea_item(1, "A (1)", "Country: UK, Location: London, UK, eArcu Vacancy Reference: 1&lt;div id=&quot;rssjobdesc&quot;&gt;"
                             "Country: Ireland&lt;/div&gt;"),
                    _ea_item(2, "B (2)", "Country: UK, eArcu Vacancy Reference: 2&lt;div id=&quot;rssjobdesc&quot;&gt;Country: Ireland"
                             "&lt;/div&gt;"),
                    _ea_item(3, "C (3)", "Location: Leeds, eArcu Vacancy Reference: 3"))
    ld = '<script type="application/ld+json">{"@type":"JobPosting","description":"<p>Detail body.</p>"}</script>'
    http = _stub({"/jobs/rss": (200, feed), "/3/description": (200, ld)})
    jobs, _ = _run("earcu", _row("earcu", "careers.cambridge.org"), http)
    assert [j["location"] for j in jobs] == ["London, UK", "UK", "Leeds"] and jobs[2]["description"] == "Detail body."
    assert [u for u, *_ in http.calls if "/description" in u] == ["https://careers.cambridge.org/jobs/vacancy/role-3/3/description/"]


def test_earcu_off_host_https_and_dup_items_and_refusals():
    feed = _ea_feed(_ea_item(1, "Evil (1)", "x", link="https://evil.example/jobs/vacancy/a/1/description/"),
                    _ea_item(2, "Plain (2)", "x", link="http://careers.cambridge.org/jobs/vacancy/a/2/description/"),
                    _ea_item(3, "Ok (3)", "Location: A, eArcu Vacancy Reference: 3"), _ea_item(3, "Ok again (3)", "x"))
    http = _stub({"/jobs/rss": (200, feed), "/3/description": (403, None)})
    jobs, _ = _run("earcu", _row("earcu", "careers.cambridge.org"), http)
    assert [j["title"] for j in jobs] == ["Ok"] and _job_hosts(jobs) == {"careers.cambridge.org"}
    assert _hosts(http) == {"careers.cambridge.org"}
    assert _run("earcu", _row("earcu", "careers.cambridge.org"), _stub({"/jobs/rss": (403, None)}))[0] == []
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert _run("earcu", _row("earcu", "careers.cambridge.org"), _stub({"/jobs/rss": (200, bomb)}))[0] == []
    assert _run("earcu", _row("earcu", "bad host/x"), _stub({}))[0] == []


# --------------------------------------------------------------------------- BeeSite
def _bs_rec(ident, title, *, uri=None, body=True, level=None):
    d = {"PositionTitle": title, "PositionURI": uri or f"https://jobs.mercedes-benz.com/job-{ident}",
         "PublicationStartDate": "2026-10-12", "PositionSchedule": [{"Name": "Full time"}],
         "PositionLocation": [{"CityName": "Stuttgart", "CountryName": "Germany"}, {"CityName": "Stuttgart", "CountryName": "Germany"}]}
    if body:
        d["PositionFormattedDescription"] = [{"Content": "<p>Intro.</p>", "Tasks": "<p>Do.</p>", "Qualifications": "<p>Know.</p>"}]
    if level:
        d["CareerLevel"] = [{"Name": level}]
    return {"MatchedObjectId": str(ident), "MatchedObjectDescriptor": d}


def _bs_http(pages, total):
    def search(url, headers):
        q = json.loads(parse_qs(urlsplit(url).query)["data"][0])
        first = q["SearchParameters"]["FirstItem"]
        n = (first - 1) // 100
        return 200, {"SearchResult": {"SearchResultCountAll": total, "SearchResultItems": pages[n] if n < len(pages) else []}}
    return _stub({"/search?data=": search})


def test_beesite_happy_path_record_body_places_internship_and_no_employer_request():
    http = _bs_http([[_bs_rec(241939, "Praktikant", level="Internship"), _bs_rec(2, "Engineer", body=False)]], 2)
    host = "mercedes-benz-beesite-production-gjb.app.beesite.de"
    jobs, stats = _run("beesite", _row("beesite", host), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["url"] == "https://jobs.mercedes-benz.com/job-241939" and j["posted"] == "2026-10-12"
    assert (j["location"], j["description"], j["extra"]["employment_type"]) == ("Stuttgart, Germany", "Intro.\nDo.\nKnow.", "Internship")
    assert jobs[1]["description"] == "" and jobs[1]["extra"]["employment_type"] == "Full time"
    assert _hosts(http) == {host} and len(http.calls) == 1  # the employer's page is never opened
    q = json.loads(parse_qs(urlsplit(http.calls[0][0]).query)["data"][0])
    assert q == {"LanguageCode": "EN", "SearchParameters": {"FirstItem": 1, "CountItem": 100}}


def test_beesite_pages_by_first_item_dedups_and_stops_on_short_page():
    full = [_bs_rec(i, f"R{i}") for i in range(1, 101)]
    http = _bs_http([full, [_bs_rec(100, "dup"), _bs_rec(101, "R101")], full], 101)
    jobs, _ = _run("beesite", _row("beesite", "p.app.beesite.de"), http)
    assert len(jobs) == 101
    firsts = [json.loads(parse_qs(urlsplit(u).query)["data"][0])["SearchParameters"]["FirstItem"] for u, *_ in http.calls]
    assert firsts == [1, 101]


def test_beesite_drops_postings_whose_link_has_userinfo_blanks_odd_port_or_a_dotless_host():
    bad = ["https://user:pw@jobs.example.com/1", "https://user@jobs.example.com/2", "https://jobs.example.com/a b",
           "https://jobs.example.com:8443/4", "https://jobs.example.com:abc/5", "https://intranet/6", "https://localhost/7",
           "https://jobs.example.com/\t8"]
    recs = [_bs_rec(i, f"Bad {i}", uri=u) for i, u in enumerate(bad, 1)]
    recs += [_bs_rec(20, "Good", uri="https://jobs.example.com/20"), _bs_rec(21, "Good443", uri="https://jobs.example.com:443/21")]
    jobs, _ = _run("beesite", _row("beesite", "p.app.beesite.de"), _bs_http([recs], len(recs)))
    assert [j["title"] for j in jobs] == ["Good", "Good443"]
    assert _job_hosts(jobs) == {"jobs.example.com"}


def test_beesite_bad_records_skip_and_http_urls_are_refused_and_failures():
    http = _bs_http([[_bs_rec(1, "Plain", uri="http://jobs.example.com/1"), _bs_rec(2, "Rel", uri="/job/2"), {"MatchedObjectId": 5},
                      _bs_rec(3, "", uri="https://jobs.example.com/3"), _bs_rec(4, "Ok", uri="https://jobs.example.com/4#x"),
                      "junk"]], 6)
    jobs, _ = _run("beesite", _row("beesite", "p.app.beesite.de"), http)
    assert [(j["title"], j["url"]) for j in jobs] == [("Ok", "https://jobs.example.com/4")]
    assert _run("beesite", _row("beesite", "p.app.beesite.de"), _stub({"/search": (403, None)}))[0] == []
    for tok in ("jobs.porsche.com", "a.beesite.de.evil.io", "p.app.beesite.de/x", ""):
        h = _stub({})
        assert _run("beesite", _row("beesite", tok), h)[0] == [] and h.calls == []


# --------------------------------------------------------------------------- Workable marketplace
def _wm(ident, title, state="published", url=None, **kw):
    return {"id": ident, "title": title, "state": state, "employmentType": "Contract", "workplace": "remote",
            "url": url or f"https://jobs.workable.com/view/abc{ident}/{title.lower()}", "created": "2026-01-08T15:25:12.375Z",
            "location": {"city": "Buenos Aires", "countryName": "Argentina"}, "description": "<p>Build <b>things</b>.</p><script>x()</script>",
            **kw}


def _wm_http(pages):
    def feed(url, headers):
        tok = parse_qs(urlsplit(url).query).get("pageToken", [""])[0]
        return 200, pages[int(tok) if tok else 0]
    return _stub({"/api/v1/companies/": feed})


def test_workablemarketplace_happy_path_drafts_dropped_and_token_paging():
    p1 = {"jobs": [_wm("a", "Backend"), _wm("d", "Draft", state="draft")], "nextPageToken": "1"}
    p2 = {"jobs": [_wm("a", "Backend"), _wm("b", "Frontend", workplace="onsite")], "nextPageToken": "2"}
    p3 = {"jobs": [_wm("a", "Backend")], "nextPageToken": "3"}  # nothing new: ends
    http = _wm_http([p1, p2, p3, p3])
    jobs, stats = _run("workablemarketplace", _row("workablemarketplace", "ix7jhWd4JnugmXDK5RaQSZ"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    j = jobs[0]
    assert set(j) == JOB_KEYS and (j["title"], j["location"], j["posted"], j["remote"]) == (
        "Backend", "Buenos Aires, Argentina", "2026-01-08", True)
    assert j["description"] == "Build things." and j["extra"]["employment_type"] == "Contract" and jobs[1]["remote"] is None
    assert len(http.calls) == 3 and http.calls[0][0] == "https://jobs.workable.com/api/v1/companies/ix7jhWd4JnugmXDK5RaQSZ"
    assert _hosts(http) == {"jobs.workable.com"} and _job_hosts(jobs) == {"jobs.workable.com"}


def test_workablemarketplace_off_host_and_http_urls_skip_the_posting_and_refusals_end_the_board():
    page = {"jobs": [_wm("a", "A", url="https://evil.example/view/a"), _wm("b", "B", url="http://jobs.workable.com/view/b"),
                     _wm("c", "C", url="u/c"), {"id": 5}, "junk", _wm("e", "Ok")], "nextPageToken": ""}
    jobs, _ = _run("workablemarketplace", _row("workablemarketplace", "HASH"), _wm_http([page]))
    assert [j["title"] for j in jobs] == ["Ok"]
    h = _stub({"pageToken=1": (429, None), "/api/v1/companies/": (200, {"jobs": [_wm("a", "A")], "nextPageToken": "1"})})
    assert len(_run("workablemarketplace", _row("workablemarketplace", "HASH"), h)[0]) == 1
    assert _run("workablemarketplace", _row("workablemarketplace", "HASH"), _stub({"/api": (403, None)}))[0] == []
    for tok in ("", "a/b", "a b", "x" * 80):
        h = _stub({})
        assert _run("workablemarketplace", _row("workablemarketplace", tok), h)[0] == [] and h.calls == []


# --------------------------------------------------------------------------- hostile markup
def test_radancy_hostile_nesting_and_a_deep_ld_page_do_not_lose_the_board():
    t0 = time.perf_counter()
    hostile = (_rd_card("1", "Good", "x") + "<ul><li class='search-results-list__item'><a data-job-id='2' href='/job/a/b/1/2'>" * 3000
               + "<div>" * 20000)
    deep = '<script type="application/ld+json">' + "[" * 100000 + "</script>" + "<div>" * 30000
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com"), _rd_http([_rd_page(hostile)], detail=(200, deep)))
    assert jobs and jobs[0]["title"] == "Good"
    assert time.perf_counter() - t0 < 10.0


def test_earcu_beesite_ukg_hostile_inputs_stay_fast():
    t0 = time.perf_counter()
    desc = "Location: " + "a," * 200000 + "&lt;div id=&quot;rssjobdesc&quot;&gt;" + "&lt;p&gt;" * 50000
    jobs, _ = _run("earcu", _row("earcu", "careers.cambridge.org"), _stub({"/jobs/rss": (200, _ea_feed(_ea_item(1, "A (1)", desc)))}))
    assert [j["title"] for j in jobs] == ["A"]
    deep = [{"MatchedObjectId": "1", "MatchedObjectDescriptor": {"PositionTitle": "Deep &amp; Wide " + "<" * 100000, "PositionURI": "https://x.example/1",
                                                                  "PositionFormattedDescription": [{"Content": "<div>" * 50000}]}}]
    assert _run("beesite", _row("beesite", "p.app.beesite.de"), _bs_http([deep], 1))[0][0]["title"] == "Deep & Wide"
    row = _ukg_row(1, "A", job_description="<p>" * 100000)
    assert _run("ukgready", _row("ukgready", "secure4.saashr.com/1", title_ok=lambda t: False), _ukg_http([[row]], 1))[0]
    assert time.perf_counter() - t0 < 10.0


def test_read_kit_hosts_are_not_listed_as_unsupported():
    for host in ("jobs.workable.com", "secure4.saashr.com", "p.app.beesite.de"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)
