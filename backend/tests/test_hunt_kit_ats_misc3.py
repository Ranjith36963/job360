"""Offline tests for hunt_kit/kit_ats_misc3.py (Radancy, UKG Ready).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and career-ops
providers/radancy.mjs and written fresh. Nothing touches the network. HumanBit is not read (its listing page is ~5.6 MB):
its link is reported as unsupported.
"""
from __future__ import annotations

import importlib.util
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
NAMES = ("radancy", "ukgready")
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


# --------------------------------------------------------------------------- hostile markup
def test_radancy_hostile_nesting_and_a_deep_ld_page_do_not_lose_the_board():
    t0 = time.perf_counter()
    hostile = (_rd_card("1", "Good", "x") + "<ul><li class='search-results-list__item'><a data-job-id='2' href='/job/a/b/1/2'>" * 3000
               + "<div>" * 20000)
    deep = '<script type="application/ld+json">' + "[" * 100000 + "</script>" + "<div>" * 30000
    jobs, _ = _run("radancy", _row("radancy", "careers.ing.com"), _rd_http([_rd_page(hostile)], detail=(200, deep)))
    assert jobs and jobs[0]["title"] == "Good"
    assert time.perf_counter() - t0 < 10.0


def test_ukgready_hostile_description_stays_fast():
    t0 = time.perf_counter()
    row = _ukg_row(1, "A", job_description="<p>" * 100000)
    assert _run("ukgready", _row("ukgready", "secure4.saashr.com/1", title_ok=lambda t: False), _ukg_http([[row]], 1))[0]
    assert time.perf_counter() - t0 < 10.0


def test_read_kit_hosts_are_not_listed_as_unsupported():
    for host in ("secure4.saashr.com", "careers.ing.com"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)
