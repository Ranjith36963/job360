"""Offline tests for hunt_kit/kit_fh5.py (Vouch board, BDJobs feed; HelloWork reported unsupported).

Fixtures are shaped like freehire's vouch_test.go (MIT) and JobSpy's bdjobs model (MIT) and written fresh. Nothing touches the
network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fh = _load("kit_fh5_under_test", "kit_fh5.py")
hk = _load("hunt_kit_fh5_under_test", "hunt_kit.py")
fh.PAUSE_S = 0
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}
TODAY = datetime.now(timezone.utc).date()  # the one clock every date in this file comes from


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets the URL."""
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append(url)
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _hosts(http):
    return {urlsplit(u).hostname for u in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


# ----------------------------------------------------------------------------- wiring and host rules
def test_wiring():
    assert set(fh.ATS_FETCHERS) == {"vouch"} and set(fh.FEEDS) == {"bdjobs"}
    assert "vouch" in hk.BOARD_SOURCES and "bdjobs" in hk.FEED_SOURCES
    assert "kit_fh5" in hk.ATS_MODULES and "kit_fh5" in hk.FEED_MODULES
    assert "vouch" in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) and "bdjobs" in hk._merged("FEEDS", hk.FEED_MODULES)
    src = (KIT_DIR / "kit_fh5.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= 2
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_fh5.py"' in served
    assert "kit_fh5.py" in (KIT_DIR / "NOTICE.md").read_text(encoding="utf-8")


def test_host_rules():
    b = hk.board_from_job_url
    r = b("https://vouch.careers/companies/cmghojtua00p5et0dvusuxx5o/cmjob1")
    assert (r["kind"], r["ats"], r["token"]) == ("ats", "vouch", "cmghojtua00p5et0dvusuxx5o")
    assert b("https://vouch.careers/")["token"] == "" and b("https://vouch.careers/companies/a%20b")["token"] == ""
    assert b("https://vouch.careers.evil.example/companies/abc")["kind"] == "other"
    assert b("https://evil.example/vouch.careers/companies/abc")["kind"] == "other"
    assert b("https://www.hellowork.com/fr-fr/emplois/123.html")["kind"] == "unsupported"
    assert b("https://hellowork.com.evil.example/x")["kind"] == "other"
    # bdjobs is read as a feed, never as an unsupported site
    assert hk._FEED_HOSTS and next(n for n, rx in hk._FEED_HOSTS if rx.search("bdjobs.com")) == "bdjobs"
    assert not any(rx.search("bdjobs.com.evil.example") for n, rx in hk._FEED_HOSTS if n == "bdjobs")


# ----------------------------------------------------------------------------- Vouch
def _page(flight_body: str) -> str:
    """A vouch company page: the flight body JSON-string-escaped inside a self.__next_f.push script."""
    return "<html><body><script>self.__next_f.push([1," + json.dumps(flight_body) + "])</script></body></html>"


def _flight(listings) -> str:
    raw = listings if isinstance(listings, str) else json.dumps(listings)
    return '["$","div",null,{"id":"jobs","children":["$","$L37",null,{"listings":' + raw + '}]}],"footer":1'


def _listing(**kw):
    base = {"id": "cmjob1", "url": "/companies/acme/cmjob1", "title": "Senior Software Engineer", "pitch": "Shape an AI platform.",
            "must": "<p>Ship production systems.</p>", "nice": "<p>NestJS.</p>", "description": "",
            "employmentType": ["Full-time", "Remote"], "publishedAt": "2025-10-21T07:15:49.762Z", "activated": True,
            "draft": False, "unlisted": False, "company": {"name": "Laine"}, "locations": []}
    base.update(kw)
    return base


def _vouch(http, board="acme", name="Acme Co"):
    stats: dict = {}
    jobs = fh.ATS_FETCHERS["vouch"]({"name": name, "ats": "vouch", "token": board}, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)  # a keyword the real http() lacks is a run-time TypeError
    return jobs, stats


def test_vouch_happy_path_facts_and_non_live_listings_dropped():
    listings = [
        _listing(),
        _listing(id="d", draft=True), _listing(id="u", unlisted=True), _listing(id="x", activated=False), _listing(id=""),
        _listing(id="loc", url="/companies/acme/loc", title="Analyst", employmentType=["Hybrid"], company={},
                 locations=[{"address": "", "city": "Dhaka", "administrative_area": "", "country": "BD"}],
                 pitch="Join a <10 person team.", must="<script>steal()</script><p>Count.</p>"),
    ]
    http = _stub({"/companies/acme": (200, _page(_flight(listings)))})
    jobs, stats = _vouch(http)
    assert [j["title"] for j in jobs] == ["Senior Software Engineer", "Analyst"] and stats["ok"] == 1
    a, b = jobs
    assert (a["company"], a["url"], a["posted"], a["remote"], a["source"]) == (
        "Laine", "https://vouch.careers/companies/acme/cmjob1", "2025-10-21", True, "vouch")
    assert a["extra"]["work_mode"] == "remote" and a["location"] == ""
    assert "Shape an AI platform" in a["description"] and "Ship production systems" in a["description"] and "NestJS" in a["description"]
    assert (b["company"], b["location"], b["remote"], b["extra"]["work_mode"]) == ("Acme Co", "Dhaka, BD", None, "hybrid")
    assert "Join a <10 person team." in b["description"] and "Count." in b["description"] and "steal" not in b["description"]
    assert http.calls == ["https://vouch.careers/companies/acme"]


def test_vouch_links_never_leave_the_board_host_or_scheme():
    bad = ["https://evil.example/companies/acme/x1", "http://vouch.careers/companies/acme/x2", "//evil.example/companies/acme/x3",
           "https://user@vouch.careers/companies/acme/x4", "https://vouch.careers:8443/companies/acme/x5",
           "https://vouch.careers.evil.example/companies/acme/x6", "javascript:alert(1)", "/not-companies/x7", "https://[bad/x8"]
    listings = [_listing(id=f"x{i + 1}", url=u, title=f"T{i}") for i, u in enumerate(bad)]
    listings.append(_listing(id="ok", url="https://vouch.careers/companies/acme/ok#frag", title="Fine"))
    listings.append(_listing(id="no/slash", url=None, title="Canonical"))
    http = _stub({"/companies/acme": (200, _page(_flight(listings)))})
    jobs, _ = _vouch(http)
    assert len(jobs) == len(bad) + 2 and _job_hosts(jobs) == {"vouch.careers"} and _hosts(http) == {"vouch.careers"}
    assert all(urlsplit(j["url"]).scheme == "https" and urlsplit(j["url"]).netloc == "vouch.careers" for j in jobs)
    by_title = {j["title"]: j["url"] for j in jobs}
    assert by_title["Fine"] == "https://vouch.careers/companies/acme/ok"
    assert by_title["Canonical"] == "https://vouch.careers/companies/acme/no%2Fslash"
    assert by_title["T0"] == "https://vouch.careers/companies/acme/x1"  # off-host link replaced by the canonical one


def test_vouch_one_bad_posting_does_not_lose_the_board():
    listings = [None, "text", 7, [], _listing(id="a", title=""), _listing(id="b", title=None), _listing(id=5, title="Numeric id"),
                _listing(id="c", employmentType="remote", locations="x", company="x", publishedAt=[1]),
                _listing(id="c", title="Duplicate id"), _listing(id="e", title="Good", locations=[None, {"city": None}])]
    http = _stub({"/companies/acme": (200, _page(_flight(listings)))})
    jobs, _ = _vouch(http)
    assert [j["title"] for j in jobs] == ["Senior Software Engineer", "Good"]


def test_vouch_refusal_missing_flight_or_listings_and_bad_board_give_nothing():
    for resp in ((403, None), (429, None), (0, None), (200, "<html>no flight here</html>"),
                 (200, _page('["$","div",null,{"children":"nothing"}]')), (200, _page(_flight('{"not":"a list"}'))),
                 (200, _page(_flight("[{broken json")))):
        http = _stub({"/companies/acme": resp})
        assert _vouch(http)[0] == [] and http.calls == ["https://vouch.careers/companies/acme"]
    assert _vouch(_stub({"/companies/": (200, "x")}), board="a/../b")[0] == []
    http = _stub({})
    assert _vouch(http, board="has space")[0] == [] and http.calls == []
    empty = _stub({"/companies/acme": (200, _page(_flight([])))})
    jobs, stats = _vouch(empty)
    assert jobs == [] and stats["ok"] == 1  # an empty array is a valid, empty board


def test_vouch_hostile_markup_is_fast_and_bounded():
    t = time.perf_counter()
    nested = "[" * 4000 + "]" * 4000
    for body in (_flight(nested), _flight("[" + "{" * 5000), "x" * 200_000 + _flight([_listing()])):
        _vouch(_stub({"/companies/acme": (200, _page(body))}))
    big = [_listing(id=f"j{i}", title=f"Role {i}", must="<p>" + "<b>" * 50 + "text") for i in range(400)]
    jobs, _ = _vouch(_stub({"/companies/acme": (200, _page(_flight(big)))}))
    assert len(jobs) == 400
    assert time.perf_counter() - t < 2.5


# ----------------------------------------------------------------------------- BDJobs
def _card(jid, title="Software Engineer", **kw):
    row = {"Jobid": str(jid), "publishDate": f"{TODAY.isoformat()}T08:00:00Z", "WorkPlace": "Office", "location": "Dhaka",
           "jobTitle": title, "companyName": "Acme BD", "JobType": "FullTime", "Salary": "Tk. 40000 - 60000 (Monthly)",
           "logoUrl": None, "Vacancies": 2, "experience": "2 years"}
    row.update(kw)
    return row


def _search(cards, premium=None, pages=1):
    return 200, {"data": cards, "premiumData": premium, "common": {"totalpages": pages}}


def _bd_http(search, details=None):
    routes = {"GetJobSearch": search}
    routes["Job-Details"] = details if details is not None else (200, {"data": [{
        "JobKeyPoints": "<ul><li>Hybrid</li></ul>", "JobDescription": "<p>Build &amp; ship.</p><script>steal()</script>",
        "EducationRequirements": "BSc", "ApplyURL": "<a href='https://evil.example/apply'>Apply</a>"}]})
    return _stub(routes)


def _bd(http, queries=("engineer",), countries=("BD",), days=0):
    stats: dict = {}
    jobs = fh.fetch_bdjobs(list(queries), http, stats, days=days, countries=list(countries))
    assert all(set(k) <= ALLOWED_KW for k in http.kws)
    return jobs, stats


def test_bdjobs_happy_path_premium_first_salary_and_description_from_the_detail_call():
    http = _bd_http(_search([_card(2), _card(3, "Remote Engineer", WorkPlace="Home", location="Anywhere in Bangladesh", Salary="Negotiable")],
                            premium=[_card(1, "Lead Engineer")]))
    jobs, stats = _bd(http)
    assert [j["title"] for j in jobs] == ["Lead Engineer", "Software Engineer", "Remote Engineer"]
    a, _b, c = jobs
    assert (a["company"], a["url"], a["posted"], a["source"], a["location"]) == (
        "Acme BD", "https://bdjobs.com/h/details/1", TODAY.isoformat(), "feed:bdjobs", "Dhaka, Bangladesh")
    assert a["extra"]["salary"] == {"min": 40000, "max": 60000, "currency": "BDT", "interval": "month"}
    assert "Build & ship" in a["description"] and "Hybrid" in a["description"] and "steal" not in a["description"]
    assert "title_only" not in a["extra"]
    assert c["remote"] is True and c["location"] == "Bangladesh" and "salary" not in c["extra"]
    assert _hosts(http) == {"api.bdjobs.com", "gateway.bdjobs.com"} and _job_hosts(jobs) == {"bdjobs.com"}
    assert "evil.example" not in json.dumps(jobs)  # the ad's ApplyURL is never used
    q = parse_qs(urlsplit(http.calls[0]).query)
    assert q["keyword"] == ["engineer"] and q["pg"] == ["1"] and q["rpp"] == ["50"] and q["isFresher"] == ["false"]
    assert stats["tried"] == 4 and stats["ok"] == 4


def test_bdjobs_paging_ends_at_totalpages_or_a_page_with_nothing_new():
    pages = {1: [_card(1), _card(2)], 2: [_card(2), _card(3)], 3: [_card(3)], 4: [_card(9)]}

    def search(url):
        return _search(pages[int(parse_qs(urlsplit(url).query)["pg"][0])], pages=4)

    http = _bd_http(search, details=(404, None))
    jobs, _ = _bd(http)
    assert [j["extra"]["job_id"] for j in jobs] == ["1", "2", "3"]  # duplicates dropped, page 3 added nothing new, so page 4 is never asked
    assert [parse_qs(urlsplit(u).query)["pg"][0] for u in http.calls if "GetJobSearch" in u] == ["1", "2", "3"]
    assert all(j["extra"].get("title_only") for j in jobs)  # a gone detail leaves the posting listed without a body
    only = _bd_http(lambda url: _search([_card(int(parse_qs(urlsplit(url).query)["pg"][0]))], pages=2), details=(404, None))
    assert [j["extra"]["job_id"] for j in _bd(only)[0]] == ["1", "2"] and sum("GetJobSearch" in u for u in only.calls) == 2


def test_bdjobs_one_bad_posting_does_not_lose_the_search():
    cards = [None, "x", 5, _card(1, title=""), _card(2, companyName=None), _card("../evil", "Path trick"), _card(3, Jobid=None),
             _card(4, publishDate="garbage", Salary=None, location=None), {"Jobid": "6"}, _card(7, "Good engineer")]
    jobs, _ = _bd(_bd_http(_search(cards), details=(200, {"data": "oops"})))
    assert [j["extra"]["job_id"] for j in jobs] == ["4", "7"] and jobs[0]["posted"] == ""


def test_bdjobs_refusal_ends_the_run_and_a_gone_detail_only_skips_that_posting():
    for st in (403, 429, 0, 503):
        http = _bd_http((st, None))
        assert _bd(http)[0] == [] and len(http.calls) == 1
    seen = {"n": 0}

    def details(url):
        seen["n"] += 1
        return (404, None) if seen["n"] == 1 else (200, {"data": [{"JobDescription": "<p>Second body</p>"}]})

    jobs, _ = _bd(_bd_http(_search([_card(1), _card(2)]), details=details))
    assert [bool(j["description"]) for j in jobs] == [False, True] and "Second body" in jobs[1]["description"]
    # a refusal on a detail call ends the detail requests but keeps the listed postings
    http = _bd_http(_search([_card(1), _card(2), _card(3)]), details=(429, None))
    jobs, _ = _bd(http)
    assert len(jobs) == 3 and sum("Job-Details" in u for u in http.calls) == 1


def test_bdjobs_country_gate_request_caps_and_recency():
    http = _bd_http(_search([_card(1)]))
    assert _bd(http, countries=("US",))[0] == [] and http.calls == []
    assert len(_bd(_bd_http(_search([_card(1)])), countries=("*",))[0]) == 1
    many = _bd_http(lambda url: _search([_card(int(parse_qs(urlsplit(url).query)["pg"][0]) * 100 + i) for i in range(50)], pages=99))
    jobs, _ = _bd(many, queries=["engineer", "developer", "analyst", "manager", "tester", "designer", "extra"])
    assert len(many.calls) <= fh.BDJOBS_MAX_REQUESTS and sum("Job-Details" in u for u in many.calls) <= fh.BDJOBS_DETAILS_CAP
    old = (TODAY - timedelta(days=400)).isoformat()
    fresh_only = _bd(_bd_http(_search([_card(1, publishDate=old + "T00:00:00Z"), _card(2)]), details=(404, None)), days=30)[0]
    assert [j["extra"]["job_id"] for j in fresh_only] == ["2"]
    assert _bd(_bd_http(_search([_card(1, "Cook")]), details=(404, None)), queries=["engineer"])[0] == []  # local keyword match


def test_bdjobs_hostile_markup_is_fast():
    t = time.perf_counter()
    evil = "<b>" * 20000 + "<script>" * 2000 + "x" * 100_000
    http = _bd_http(_search([_card(i, title=f"Engineer {i}", location=evil[:200]) for i in range(40)]),
                    details=(200, {"data": [{"JobDescription": evil, "JobKeyPoints": "<" * 50000, "experience": 5}]}))
    jobs, _ = _bd(http)
    assert jobs and all("<" not in j["description"] for j in jobs)
    assert time.perf_counter() - t < 2.5
