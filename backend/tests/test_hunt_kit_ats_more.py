"""Offline tests for hunt_kit/kit_ats_more.py.

Fixtures are the sample responses in the career-ops provider tests (tests/providers/<name>.test.mjs, MIT) for every
ATS here, except Phenom paging and Cornerstone paging which use the same file's page builders. Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


more = _load("kit_ats_more_under_test", "kit_ats_more.py")
hk = _load("hunt_kit_more_under_test", "hunt_kit.py")
F = more.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, method, body) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name]({"name": "Acme", "ats": name, **row}, http, stats)
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == name and j["url"].startswith("https://") and "<" not in j["description"]
    return jobs, stats


def test_registry_and_hunt_kit_wiring():
    assert set(F) == {"rippling", "breezy", "pinpoint", "phenom", "ultipro", "adp", "cornerstone", "gem", "jazzhr",
                      "softgarden", "join", "jibe", "keka", "zohorecruit", "ripplehire", "paylocity"}
    assert hk.ATS_MODULES == ("kit_ats", "kit_ats_more", "kit_ats_eu", "kit_ats_extra", "kit_ats_misc1", "kit_ats_misc2", "kit_ats_us", "kit_sites_a", "kit_pubsec", "kit_latam", "kit_ats_misc3", "kit_ats_misc5", "kit_ats_misc4", "kit_markets4", "kit_asia2", "kit_market5", "kit_fh5")
    assert set(F) <= set(hk.BOARD_SOURCES)
    src = (KIT_DIR / "kit_ats_more.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") + src.count("Source: own read") >= len(F)  # every reader names where it came from


@pytest.mark.parametrize("name", sorted(F))
def test_bad_token_and_dead_board_count_as_not_ok(name):
    for row in ({"token": ""}, {"token": "ok-token"}):
        jobs, stats = _run(name, row, _stub({}))
        assert jobs == [] and stats == {"tried": 1, "jobs": 0}  # tried, never ok


# ------------------------------------------------------------------ Rippling (career-ops tests/providers/rippling.test.mjs sample)
def test_rippling_parse_and_paging():
    items = [
        {"id": "1", "name": "Account Executive", "url": "https://ats.rippling.com/acme/jobs/uuid-1", "department": {"name": "Sales"},
         "locations": [{"name": "Remote (United States)"}]},
        {"id": "2", "name": "  ML Engineer  ", "url": "  https://ats.rippling.com/acme/jobs/uuid-2  ",
         "locations": [{"name": "Berlin, Germany"}, {"name": "Hamburg, Germany"}]},
        {"id": "3", "name": "No Loc Role", "url": "https://ats.rippling.com/acme/jobs/uuid-3", "locations": []},
        {"id": "5", "name": "", "url": "https://ats.rippling.com/acme/jobs/uuid-5"},
        {"id": "6", "name": "No URL Role"},
        {"id": "7", "name": "Insecure", "url": "http://ats.rippling.com/acme/jobs/uuid-7"},
        {"id": "8", "name": "Off host", "url": "https://evil.example/acme/jobs/8"},
    ]
    http = _stub({"/api/v2/board/acme/jobs": (200, {"items": items, "page": 0, "totalItems": 7})})
    jobs, stats = _run("rippling", {"token": "acme"}, http)
    assert [j["title"] for j in jobs] == ["Account Executive", "ML Engineer", "No Loc Role"]
    assert jobs[1]["location"] == "Berlin, Germany · Hamburg, Germany" and jobs[1]["url"].endswith("uuid-2")
    assert jobs[0]["remote"] is True and jobs[0]["extra"]["department"] == "Sales"
    assert http.calls[0][0].endswith("?page=0&pageSize=1000") and len(http.calls) == 1  # short page = last page
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}

    full = [{"name": f"R{i}", "url": f"https://ats.rippling.com/acme/jobs/{i}"} for i in range(1000)]
    paged = _stub({"page=0": (200, {"items": full}), "page=1": (200, {"items": full[:3]})})
    assert len(_run("rippling", {"token": "acme"}, paged)[0]) == 1003 and len(paged.calls) == 2
    endless = _stub({"/jobs": (200, {"items": full})})
    _run("rippling", {"token": "acme"}, endless)
    assert len(endless.calls) == more.MAX_PAGES  # the page cap stops a board that never ends


# ------------------------------------------------------------------ Breezy (tests/providers/breezy.test.mjs sample)
def test_breezy_parse():
    sample = [
        {"name": "Assistant Field Manager", "url": "https://new-incentives.breezy.hr/p/b8e6-assistant-field-manager",
         "published_date": "2026-05-25T14:45:23.799Z",
         "location": {"name": "Niger, Sokoto, NG", "city": "Niger", "country": {"name": "NG"}, "is_remote": False}},
        {"name": "Remote Backend Engineer", "url": "https://new-incentives.breezy.hr/p/aa01-backend",
         "location": {"city": "Lagos", "state": "Lagos", "country": {"name": "NG"}, "is_remote": True}},
        {"name": "No URL row", "location": {"name": "Remote"}},
        {"name": "Insecure URL", "url": "http://new-incentives.breezy.hr/p/x", "location": {}},
    ]
    http = _stub({"new-incentives.breezy.hr/json": (200, sample)})
    jobs, _ = _run("breezy", {"token": "new-incentives"}, http)
    assert len(jobs) == 2
    assert jobs[0]["location"] == "Niger, Sokoto, NG" and jobs[0]["posted"] == "2026-05-25"
    assert jobs[1]["location"] == "Lagos, Lagos, NG, Remote" and jobs[1]["remote"] is True


# ------------------------------------------------------------------ Pinpoint (tests/providers/pinpoint.test.mjs sample)
def test_pinpoint_parse_and_demo_filter():
    demo = "<div>x</div> youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw"
    sample = {"data": [
        {"title": "Senior Product Manager", "url": "https://workwithus.pinpointhq.com/en/postings/abc-123",
         "location": {"id": "283", "city": "London", "province": "London", "name": "Remote"}},
        {"title": "  Backend Engineer  ", "url": "  https://workwithus.pinpointhq.com/en/postings/def-456  ",
         "location": {"city": "Berlin", "province": "Berlin"}, "description": "<div>Build pipelines.</div>", "workplace_type": "remote"},
        {"title": "No Location Role", "url": "https://workwithus.pinpointhq.com/en/postings/ghi-789"},
        {"title": "", "url": "https://workwithus.pinpointhq.com/en/postings/jkl"},
        {"title": "Missing URL Role"},
        {"title": "Relative", "url": "/en/postings/mno"},
        {"title": "Insecure", "url": "http://workwithus.pinpointhq.com/en/postings/pqr"},
        {"title": "Head of DEI - UK", "url": "https://workwithus.pinpointhq.com/en/postings/demo", "description": demo},
    ]}
    jobs, _ = _run("pinpoint", {"token": "workwithus"}, _stub({"workwithus.pinpointhq.com/postings.json": (200, sample)}))
    assert [j["title"] for j in jobs] == ["Senior Product Manager", "Backend Engineer", "No Location Role"]
    assert [j["location"] for j in jobs] == ["Remote", "Berlin, Berlin", ""]
    assert jobs[1]["description"] == "Build pipelines." and jobs[1]["remote"] is True


# ------------------------------------------------------------------ Phenom (tests/providers/phenom.test.mjs)
def test_phenom_parse_and_paging():
    one = {"refineSearch": {"status": 200, "totalHits": 42, "data": {"jobs": [
        {"jobId": "98098", "title": "Sr Analyst", "location": "France", "postedDate": "2026-05-07T18:25:30.000+0000"},
        {"jobId": "", "title": "No id - dropped"}, {"jobId": "5", "title": ""}, {"jobId": "12/34", "title": "Slash Id"}]}}}
    http = _stub({"/widgets": (200, one)})
    jobs, _ = _run("phenom", {"token": "careers.exampleco.com"}, http)
    assert [j["url"] for j in jobs] == ["https://careers.exampleco.com/global/en/job/98098/Sr-Analyst",
                                        "https://careers.exampleco.com/global/en/job/12%2F34/Slash-Id"]
    assert jobs[0]["posted"] == "2026-05-07" and jobs[0]["location"] == "France"
    assert http.calls[0][2]["ddoKey"] == "refineSearch" and http.calls[0][2]["size"] == 100 and len(http.calls) == 1

    def page(ids):
        return (200, {"refineSearch": {"totalHits": 150, "data": {"jobs": [{"jobId": str(i), "title": f"Job {i}"} for i in ids]}}})

    seen_from: list[int] = []

    def widgets(url, method, body):
        seen_from.append(body["from"])
        return page(range(1, 101)) if body["from"] == 0 else page([100, 101, 102])  # 100 repeats across pages

    jobs, _ = _run("phenom", {"token": "careers.exampleco.com"}, _stub({"/widgets": widgets}))
    assert seen_from == [0, 100] and len(jobs) == 102  # stops at totalHits, repeated id dropped
    endless = _stub({"/widgets": lambda u, m, b: page(range(b["from"], b["from"] + 100))})
    _run("phenom", {"token": "careers.exampleco.com"}, endless)
    assert len(endless.calls) == 2  # totalHits 150 ends it; a lying board is still capped by MAX_PAGES
    liar = _stub({"/widgets": lambda u, m, b: (200, {"refineSearch": {"totalHits": 10**6, "data": {"jobs": [
        {"jobId": f"{b['from']}-{i}", "title": "J"} for i in range(100)]}}})})
    _run("phenom", {"token": "careers.exampleco.com"}, liar)
    assert len(liar.calls) == more.MAX_PAGES
    assert more._slugify("Ünïcode  Röle (m/w)") == "Unicode-Role-m-w"


# ------------------------------------------------------------------ UKG UltiPro (tests/providers/ultipro.test.mjs sample)
ULTI = "recruiting.ultipro.ca/EXA5001EXCO/11111111-2222-3333-4444-555555555555"


def _opp(i, title, **kw):
    return {"Id": i, "Title": title, "RequisitionNumber": "REQ-1", "Locations": ["Toronto, ON"], "PostedDate": "2026-08-01", **kw}


def test_ultipro_parse_and_paging():
    sample = {"opportunities": [_opp("opp-1", "Instructional Designer", BriefDescription="<p>Great <b>role</b> &amp; team.</p>"),
                                _opp(2001, "Numeric Id Role"), {"Id": "", "Title": "No Id"}, {"Id": "abc", "Title": ""}],
              "totalCount": 37}
    http = _stub({"/LoadSearchResults": (200, sample)})
    jobs, _ = _run("ultipro", {"token": ULTI}, http)
    assert len(jobs) == 2
    assert jobs[0]["url"] == ("https://recruiting.ultipro.ca/EXA5001EXCO/JobBoard/11111111-2222-3333-4444-555555555555/"
                              "OpportunityDetail?opportunityId=opp-1")
    assert jobs[0]["description"] == "Great role & team." and jobs[0]["location"] == "Toronto, ON" and jobs[0]["posted"] == "2026-08-01"
    assert "opportunityId=2001" in jobs[1]["url"] and http.calls[0][2]["opportunitySearch"]["Top"] == 50
    loc = {"opportunities": [{"Id": 1, "Title": "T", "Locations": [{"LocalizedName": "HQ"}, {"Address": {"City": "Ottawa", "State": {"Code": "ON"}}}]}]}
    assert _run("ultipro", {"token": ULTI}, _stub({"/Load": (200, loc)}))[0][0]["location"] == "HQ / Ottawa, ON"

    skips: list[int] = []

    def search(url, method, body):
        skips.append(body["opportunitySearch"]["Skip"])
        n = 50 if body["opportunitySearch"]["Skip"] < 100 else 7
        return 200, {"opportunities": [_opp(f"{skips[-1]}-{i}", "J") for i in range(n)]}

    assert len(_run("ultipro", {"token": ULTI}, _stub({"/Load": search}))[0]) == 107 and skips == [0, 50, 100]
    for bad in ("evil.example.com/t/b", "recruiting.ultipro.ca/t", "recruiting.ultipro.ca/t/b/x"):
        assert _run("ultipro", {"token": bad}, _stub({"/Load": (200, sample)}))[0] == []


# ------------------------------------------------------------------ ADP Workforce Now (tests/providers/adp-workforcenow.test.mjs)
def _adp_job(item, title, ext):
    return {"itemID": item, "requisitionTitle": title, "requisitionLocations": [{"nameCode": {"shortName": "Toronto, ON"}}],
            "customFieldGroup": {"stringFields": [{"nameCode": {"codeValue": "ExternalJobID"}, "stringValue": ext}]} if ext else {},
            "postDate": "2026-08-02T00:00:00Z"}


def test_adp_parse_and_paging():
    sample = {"jobRequisitions": [_adp_job("9200947568911_1", "Instructional Designer", "2026-001"),
                                  _adp_job("9200947568912_1", "No External ID Role", None),
                                  {"itemID": "", "requisitionTitle": "No itemID"}, {"itemID": "abc", "requisitionTitle": ""}],
              "meta": {"totalNumber": 42}}
    jobs, _ = _run("adp", {"token": "cid-1/19000101_000001"}, _stub({"/job-requisitions": (200, sample)}))
    assert len(jobs) == 2 and jobs[0]["location"] == "Toronto, ON" and jobs[0]["posted"] == "2026-08-02"
    assert "jobId=2026-001" in jobs[0]["url"] and "jwId=9200947568911_1" in jobs[0]["url"] and "cid=cid-1" in jobs[0]["url"]
    assert "jobId=9200947568912_1" in jobs[1]["url"]  # ExternalJobID absent: itemID is used

    skips: list[str] = []

    def reqs(url, method, body):
        skips.append(url.split("$skip=")[1].split("&")[0])
        if len(skips) == 1:
            return 200, {"jobRequisitions": [_adp_job(f"item-{i}", f"Job {i}", f"ext-{i}") for i in range(1, 21)]}
        return 200, {"jobRequisitions": [_adp_job("item-20", "Job 20 dup", "e")] + [_adp_job(f"item-{i}", f"Job {i}", "e") for i in range(21, 26)]}

    jobs, _ = _run("adp", {"token": "c/d"}, _stub({"/job-requisitions": reqs}))
    assert skips == ["1", "21"] and len(jobs) == 25  # $skip is one-based and advances by rows returned; the overlap is dropped


# ------------------------------------------------------------------ Cornerstone (tests/providers/csod.test.mjs)
def test_cornerstone_bootstrap_token_and_paging():
    def mk(i):
        return {"requisitionId": i, "displayJobTitle": f"Job {i} &amp; Co", "postingEffectiveDate": "7/3/2026",
                "locations": [{"city": "Bremen", "country": "DE"}]}

    def search(url, method, body):
        assert body["pageSize"] == 25 and body["careerSiteId"] == 4
        if body["pageNumber"] == 1:
            return 200, {"data": {"totalCount": 27, "requisitions": [mk(i) for i in range(1, 26)]}}
        return 200, {"data": {"totalCount": 27, "requisitions": [mk(25), mk(26), mk(27)]}}

    http = _stub({"/careersite/4/home?c=career-ohb": (200, '<script>{"token":"tok.abc"}</script>'), "/v1/search": search})
    jobs, stats = _run("cornerstone", {"token": "career-ohb/4"}, http)
    assert len(jobs) == 27 and len(http.calls) == 3 and stats["ok"] == 1
    assert http.calls[1][3]["Authorization"] == "Bearer tok.abc"
    assert jobs[0]["title"] == "Job 1 & Co" and jobs[0]["posted"] == "2026-07-03" and jobs[0]["location"] == "Bremen, DE"
    assert jobs[0]["url"] == "https://career-ohb.csod.com/ux/ats/careersite/4/home/requisition/1?c=career-ohb"
    no_token = _stub({"/home": (200, "<html/>")})
    assert _run("cornerstone", {"token": "x/1"}, no_token)[1].get("ok", 0) == 0  # no anonymous token: a failed board, not an empty one


# ------------------------------------------------------------------ Gem (tests/providers/gem.test.mjs sample)
def test_gem_list_then_batched_detail():
    lst = [{"data": {"oatsExternalJobPostings": {"jobPostings": [
        {"id": "a", "extId": "1001", "title": "AI Engineer", "locations": [{"name": "San Francisco", "isRemote": False}, {"isRemote": True}],
         "job": {"department": {"name": "Engineering"}}},
        {"id": "b", "extId": "", "title": "Dropped (no extId)"}]}}}]
    det = [{"data": {"oatsExternalJobPosting": {"extId": "1001", "firstPublishedTsSec": 1700000000,
                                                 "descriptionHtml": "<p>Build &amp; ship <b>AI</b> tools.</p><ul><li>Own the roadmap</li></ul>"}}}]
    http = _stub({"graphql/batch": lambda u, m, b: (200, lst if b[0]["operationName"] == "JobBoardList" else det)})
    jobs, _ = _run("gem", {"token": "retool"}, http)
    assert len(jobs) == 1 and jobs[0]["url"] == "https://jobs.gem.com/retool/1001"
    assert jobs[0]["location"] == "San Francisco · Remote" and jobs[0]["posted"] == "2023-11-14"
    assert "Build & ship AI tools." in jobs[0]["description"] and "Own the roadmap" in jobs[0]["description"]
    assert [c[2][0]["operationName"] for c in http.calls] == ["JobBoardList", "ExternalJobPostingQuery"] and http.calls[0][3]["batch"] == "true"
    only_list = _stub({"graphql/batch": lambda u, m, b: (200, lst) if b[0]["operationName"] == "JobBoardList" else (500, None)})
    assert len(_run("gem", {"token": "retool"}, only_list)[0]) == 1  # the detail call failing keeps the listing


# ------------------------------------------------------------------ JazzHR (tests/providers/jazzhr.test.mjs sample)
def test_jazzhr_cards_and_fallback():
    page = ('<ul><li class="list-group-item"><h3 class="list-group-item-heading"><a href="https://exampleco.applytojob.com/apply/A1/Learning-Designer?x=1">'
            'Learning &amp; Development Designer</a></h3><ul><li><i class="fa fa-map-marker"></i>Toronto, ON</li></ul></li>'
            '<li class="list-group-item"><h3><a href="/apply/B2/Analyst">Analyst</a></h3><ul><li><i class="fa fa-map-marker"></i>Remote</li></ul></li>'
            '<li class="list-group-item"><h3><a href="https://elsewhere.example/apply/C3/X">Off origin</a></h3></li>'
            '<li class="list-group-item"><h3><a href="/apply/D4/"></a></h3></li></ul>')
    jobs, _ = _run("jazzhr", {"token": "exampleco"}, _stub({"exampleco.applytojob.com/apply": (200, page)}))
    assert [(j["title"], j["url"], j["location"]) for j in jobs] == [
        ("Learning & Development Designer", "https://exampleco.applytojob.com/apply/A1/Learning-Designer", "Toronto, ON"),
        ("Analyst", "https://exampleco.applytojob.com/apply/B2/Analyst", "Remote")]
    bare = '<div>list-group-item text</div><a href="/apply/D4/Support-Engineer">Support Engineer</a>'
    assert len(_run("jazzhr", {"token": "exampleco"}, _stub({"/apply": (200, bare)}))[0]) == 1


# ------------------------------------------------------------------ Softgarden (tests/providers/softgarden.test.mjs sample)
def test_softgarden_widget():
    def card(i, title, cities):
        spans = "".join(f'<span class="location-view-item">{c}</span>' for c in cities)
        return (f'<div class="matchElement" id="job_id_{i}"><div class="matchValue date">04.07.26</div>'
                f'<div target="_blank" class="matchValue title"><a href="../../job/{i}/slug-{i}?jobDbPVId=9{i}&amp;l=de" target="_blank">{title}</a></div>'
                f'<div class="matchValue ProjectGeoLocationCity"><div><div class="location-container">{spans}</div></div></div></div>')

    html_ = "<html>" + card("111", "Fachkraft (m/w/d) f&#252;r Export &amp; Zoll", ["Hannover"]) + card("222", "SAP Consultant", ["Augsburg", "M&#252;nchen"]) + "</html>"
    http = _stub({"renk-group.softgarden.io/de/widgets/jobs": (200, html_)})
    jobs, _ = _run("softgarden", {"token": "renk-group"}, http)
    assert jobs[0]["title"] == "Fachkraft (m/w/d) für Export & Zoll"
    assert jobs[0]["url"] == "https://renk-group.softgarden.io/job/111/slug-111?jobDbPVId=9111&l=de"
    assert jobs[1]["location"] == "Augsburg / München" and jobs[0]["posted"] == "2026-07-04"
    assert more._sg_date("7/4/2026") == "2026-07-04" and more._sg_date("31.02.26") == ""
    assert _run("softgarden", {"token": "renk-group"}, _stub({"/widgets": (200, "<html>none</html>")}))[0] == []


# ------------------------------------------------------------------ JOIN (tests/providers/join.test.mjs sample)
def test_join_next_data_and_paging():
    def page(items, count):
        data = {"props": {"pageProps": {"initialState": {"company": {"domain": "acme-corp"}, "jobs": {"items": items, "pagination": {"pageCount": count}}}}}}
        return 200, f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'

    first = [{"title": "X", "idParam": "1-x", "city": {"cityName": "Berlin"}}, {"title": "", "idParam": "2"}]
    http = _stub({"?page=2": page([{"title": "Y", "idParam": "3-y"}], 2), "/companies/acme": page(first, 2)})
    jobs, _ = _run("join", {"token": "acme"}, http)
    assert [(j["title"], j["url"], j["location"]) for j in jobs] == [
        ("X", "https://join.com/companies/acme-corp/jobs/1-x", "Berlin"), ("Y", "https://join.com/companies/acme-corp/jobs/3-y", "")]
    assert _run("join", {"token": "acme"}, _stub({"/companies": (200, "<html>no data</html>")}))[1].get("ok", 0) == 0


# ------------------------------------------------------------------ Jibe (tests/providers/jibeapply.test.mjs sample)
def test_jibe_parse_and_paging():
    sample = {"totalCount": 3, "jobs": [
        {"title": "Senior QA", "slug": "senior-qa-berlin", "city": "Berlin", "country": "Germany", "hiring_organization": "Acme GmbH"},
        {"title": "Backend Dev", "req_id": "REQ-123", "full_location": "Remote, Germany"},
        {"data": {"title": "Wrapped Job", "slug": "wrapped-job", "city": "Munich", "country": "Germany"}},
        {"title": "", "slug": "no-title"}, {"title": "No Slug"}, None]}
    jobs, _ = _run("jibe", {"token": "acme"}, _stub({"acme.jibeapply.com/api/jobs": (200, sample)}))
    assert [j["url"] for j in jobs] == ["https://acme.jibeapply.com/jobs/senior-qa-berlin", "https://acme.jibeapply.com/jobs/REQ-123",
                                        "https://acme.jibeapply.com/jobs/wrapped-job"]
    assert jobs[0]["location"] == "Berlin, Germany" and jobs[1]["remote"] is True

    def api(url, method, body):
        n = 10 if "page=2" not in url else 5
        return 200, {"totalCount": 15, "jobs": [{"title": f"J{url[-1]}-{i}", "slug": f"s{url[-1]}-{i}"} for i in range(n)]}

    http = _stub({"/api/jobs": api})
    assert len(_run("jibe", {"token": "acme"}, http)[0]) == 15 and len(http.calls) == 2
    lie = _stub({"/api/jobs": lambda u, m, b: (200, {"totalCount": 10**6, "jobs": [{"title": "T", "slug": f"s{len(u)}"}] * 10})})
    _run("jibe", {"token": "acme", "host": "jobs.booking.com"}, lie)
    assert len(lie.calls) == more.MAX_PAGES and lie.calls[0][0].startswith("https://jobs.booking.com/api/jobs")


# ------------------------------------------------------------------ why-missed: job URL -> (ats, token), and --companies-extra
@pytest.mark.parametrize(
    ("url", "ats", "token"),
    [
        ("https://ats.rippling.com/Acme-Corp/jobs/1234", "rippling", "acme-corp"),
        ("https://foo.breezy.hr/p/b8e6-assistant-field-manager", "breezy", "foo"),
        ("https://workwithus.pinpointhq.com/en/postings/abc-123", "pinpoint", "workwithus"),
        ("https://recruiting.ultipro.ca/YMC5001YMGT/JobBoard/7fde8f3e-7920-4d9d-8394-87bd44cc2330/OpportunityDetail?opportunityId=1",
         "ultipro", "recruiting.ultipro.ca/YMC5001YMGT/7fde8f3e-7920-4d9d-8394-87bd44cc2330"),
        ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=ffe28c5e-dcff&ccId=19000101_000001&jobId=1",
         "adp", "ffe28c5e-dcff/19000101_000001"),
        ("https://career-ohb.csod.com/ux/ats/careersite/4/home/requisition/12?c=career-ohb", "cornerstone", "career-ohb/4"),
        ("https://jobs.gem.com/retool/am9icG9zdDox", "gem", "retool"),
        ("https://ratehubinc.applytojob.com/apply/A1/Learning-Designer", "jazzhr", "ratehubinc"),
        ("https://renk-group.softgarden.io/job/111/slug-111?l=de", "softgarden", "renk-group"),
        ("https://join.com/companies/acme-corp/jobs/1-x", "join", "acme-corp"),
        ("https://acme.jibeapply.com/jobs/senior-qa-berlin", "jibe", "acme"),
    ],
)
def test_board_from_job_url_new_hosts(url: str, ats: str, token: str) -> None:
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)
    assert [r["token"] for r in hk.parse_extra(f"{ats}:{token}")] == [token]  # a board address the kit can also run


def test_board_from_job_url_without_token_and_extra_tokens():
    for url in ("https://ats.rippling.com/", "https://workforcenow.adp.com/mascsr/recruitment.html",
                "https://x.csod.com/ux/ats/careersite/", "https://recruiting.ultipro.ca/t/other/1"):
        info = hk.board_from_job_url(url)
        assert info["kind"] == "ats" and info["token"] == "", url
    rows = hk.parse_extra("ultipro:recruiting.ultipro.ca/T1/B1,adp:c/d,cornerstone:acme/4,phenom:careers.example.com,breezy:foo,evil:x")
    assert [(r["ats"], r["token"]) for r in rows] == [("ultipro", "recruiting.ultipro.ca/T1/B1"), ("adp", "c/d"), ("cornerstone", "acme/4"),
                                                       ("phenom", "careers.example.com"), ("breezy", "foo")]
    assert rows[0]["name"] == "T1"
