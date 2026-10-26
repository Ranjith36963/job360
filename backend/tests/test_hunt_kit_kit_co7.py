"""Offline tests for hunt_kit/kit_co7.py (PrevueAPS, Red Rover K12, Senjob).

Fixtures are shaped like the sample data in career-ops' tests/providers/senjob.test.mjs and the response
shapes documented in providers/{prevueaps,redrover}.mjs (MIT, 8f48928) and written fresh. Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests._timing import best_of

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


co = _load("kit_co7_under_test", "kit_co7.py")
hk = _load("hunt_kit_co7_under_test", "hunt_kit.py")
F = co.ATS_FETCHERS
NAMES = ("prevueaps", "redrover", "senjob")
NOW = datetime.now(timezone.utc)


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append((url, method, headers or {}, data, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}, data) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    return F[name](row, http, stats), stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http) -> set:
    return {urlsplit(c[0]).hostname for c in http.calls}


def test_every_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_co7" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_co7.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from career-ops") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_co7.py"' in served
    rows = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    assert {r["ats"] for r in rows if r["ats"] in NAMES} == {"prevueaps", "redrover", "senjob"}


@pytest.mark.parametrize("url,ats,token", [
    ("https://tbca.prevueaps.ca/jobs/4411", "prevueaps", "tbca"),
    ("https://www.prevueaps.ca/jobs/1", "prevueaps", ""),
    ("https://jobs.redroverk12.com/org/1651/opening/9001", "redrover", "1651"),
    ("https://jobs.redroverk12.com/org/abc/opening/1", "redrover", ""),
    ("https://senjob.com/jobseekers/developpeur_e_163401.html", "senjob", ""),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    r = hk.board_from_job_url(url)
    assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, token)


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra("prevueaps:tbca,redrover:1651,senjob:all")
    assert [(r["ats"], r["token"]) for r in rows] == [("prevueaps", "tbca"), ("redrover", "1651"), ("senjob", "all")]


# ----------------------------------------------------------------------------- PrevueAPS
PV_PAGE = '<html><script>$.get("/core/jobs/889?getParams=%7B%7D")</script></html>'
PV_JSON = {"success": True, "data": {"jobCount": 3, "jobs": [
    {"title": "Registered Nurse", "jobUrl": "https://tbca.prevueaps.ca/jobs/4411", "jobLocation": "Toronto, ON",
     "employmentType": "Full-time", "classification": "Nursing", "minSalary": "$40", "maxSalary": "$50", "payTypeFrame": "per hour"},
    {"title": "Clerk", "jobUrl": "https://tbca.prevueaps.ca/jobs/4412", "city": "Ottawa", "abbreviation": "ON"},
    {"title": "Offsite", "jobUrl": "https://evil.example.com/jobs/1"},
    {"title": "Plain http", "jobUrl": "http://tbca.prevueaps.ca/jobs/2"},
    {"title": "Dup", "jobUrl": "https://tbca.prevueaps.ca/jobs/4412"},
    "not a posting", {"title": "", "jobUrl": "https://tbca.prevueaps.ca/jobs/3"}, {"title": "No link"},
]}}


def _pv(page=PV_PAGE, data=(200, PV_JSON)):
    return _stub({"/core/jobs/": data, "/jobs/": (200, page)} if page is not None else {"/core/jobs/": data})


def test_prevueaps_happy_path_maps_fields_and_stays_on_the_tenant_host():
    http = _pv()
    jobs, st = _run("prevueaps", _row("prevueaps", "tbca"), http)
    assert [j["title"] for j in jobs] == ["Registered Nurse", "Clerk"]
    assert jobs[0]["location"] == "Toronto, ON" and jobs[1]["location"] == "Ottawa, ON"
    assert jobs[0]["description"] == "Full-time · Nursing · $40 - $50 per hour"
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"tbca.prevueaps.ca"} and all(j["url"].startswith("https://") for j in jobs)
    assert _hosts(http) == {"tbca.prevueaps.ca"}
    api = [c[0] for c in http.calls if "/core/jobs/" in c[0]]
    assert api == ["https://tbca.prevueaps.ca/core/jobs/889?getParams=%7B%22showDate%22%3Atrue%2C%22showLocation%22%3Atrue%2C"
                   "%22showEmploymentType%22%3Atrue%2C%22showCategory%22%3Atrue%2C%22showClassification%22%3Atrue%2C"
                   "%22showWorkplaceType%22%3Atrue%2C%22customCategoryTitle%22%3A%22%22%7D"]
    assert st["ok"] == 1 and st["jobs"] == 2


@pytest.mark.parametrize("page,dom", [
    ('<div data-domain-id="813"></div>', "813"), ("var domainId = '7';", "7"), ('{"domainId":55}', "55"),
    ("var siteId: 9", "9"), ('x /core/jobs/12 data-domain-id="99"', "12")])
def test_prevueaps_site_id_shapes_in_order(page, dom):
    assert co._domain_id(page) == dom


def test_prevueaps_failures_end_the_board_and_a_quiet_tenant_is_not_a_failure():
    empty = {"success": True, "message": "No job found", "data": {"jobs": [], "jobCount": 0}}
    assert _run("prevueaps", _row("prevueaps", "tbca"), _pv(data=(200, empty)))[0] == []
    assert _run("prevueaps", _row("prevueaps", "tbca"), _pv(data=(200, empty)))[1]["ok"] == 1
    for http in (_pv(page="<html>no id</html>"), _pv(data=(200, {"data": {"jobs": None}})), _pv(data=(403, None)),
                 _pv(data=(200, {"success": True})), _stub({})):
        jobs, st = _run("prevueaps", _row("prevueaps", "tbca"), http)
        assert jobs == [] and "ok" not in st
    for bad in ("", "-x", "a.b", "a/b", "x" * 64, "evil.com#"):
        http = _stub({})
        assert _run("prevueaps", _row("prevueaps", bad), http)[0] == [] and http.calls == []


# ----------------------------------------------------------------------------- Red Rover
def _rr_body(rows, more=False):
    return {"data": {"jobSeekerSiteUnauthenticated": {"jobPostingSearch": {"results": rows, "hasMoreData": more}}}}


RR_ROWS = [
    {"id": 9001, "name": "Teacher", "organizationName": "Ridgefield SD", "location": {"name": "North Elementary"},
     "activePublicOnDateUtc": "2026-09-01T07:00:00Z", "closedOnDateUtc": None, "allowsRemote": False},
    {"id": "9002", "name": "Tutor", "organizationName": "Ridgefield SD", "location": None,
     "activePublicOnDateUtc": "2026-09-02T07:00:00Z", "allowsRemote": True},
    {"id": 9003, "name": "Internal only", "activePublicOnDateUtc": None},
    {"id": 9004, "name": "Closed", "activePublicOnDateUtc": "2026-08-01T00:00:00Z", "closedOnDateUtc": "2026-09-01T00:00:00Z"},
    {"id": "../x", "name": "Bad id", "activePublicOnDateUtc": "2026-08-01T00:00:00Z"}, {"id": 9005, "name": " "}, None, "junk",
    {"id": 9001, "name": "Teacher again", "activePublicOnDateUtc": "2026-09-01T07:00:00Z"},
]


def test_redrover_happy_path_posts_the_query_and_builds_posting_links():
    http = _stub({"graphql": (200, _rr_body(RR_ROWS))})
    jobs, st = _run("redrover", _row("redrover", "1651"), http)
    assert [(j["title"], j["url"], j["posted"]) for j in jobs] == [
        ("Teacher", "https://jobs.redroverk12.com/org/1651/opening/9001", "2026-09-01"),
        ("Tutor", "https://jobs.redroverk12.com/org/1651/opening/9002", "2026-09-02")]
    assert jobs[0]["location"] == "North Elementary - Ridgefield SD" and jobs[1]["location"] == "Ridgefield SD (Remote)"
    assert jobs[0]["remote"] is None and jobs[1]["remote"] is True
    (url, method, headers, data, _once), = http.calls
    assert (url, method) == ("https://api.redroverk12.com/graphql", "POST") and headers["Origin"] == "https://jobs.redroverk12.com"
    body = json.loads(data)
    assert body["operationName"] == "GetJobPostings" and body["variables"]["search"] == {
        "orgId": "1651", "searchTerm": "", "locationIds": [], "jobPostingCategoryIds": []}
    assert "hasMoreData" in body["query"] and st["jobs"] == 2


def test_redrover_a_board_one_call_cannot_hold_and_api_errors_fail_the_board():
    for resp in ((200, _rr_body(RR_ROWS, more=True)), (200, {"errors": [{"message": "boom"}]}), (500, None), (200, []),
                 (200, {"data": {"jobSeekerSiteUnauthenticated": {"other": 1}}})):
        jobs, st = _run("redrover", _row("redrover", "1651"), _stub({"graphql": resp}))
        assert jobs == [] and "ok" not in st
    for resp in ((200, {"data": None}), (200, {"data": {"jobSeekerSiteUnauthenticated": None}}), (200, _rr_body(None))):
        assert _run("redrover", _row("redrover", "1651"), _stub({"graphql": resp}))[1]["ok"] == 1
    for bad in ("", "abc", "12/3", "1" * 13):
        http = _stub({})
        assert _run("redrover", _row("redrover", bad), http)[0] == [] and http.calls == []


# ----------------------------------------------------------------------------- Senjob
SEN_BASE = "https://senjob.com/jobseekers/"
SEN_ROW = f"""
<tr style="height:70px;"><td><div align=left>
  <a href="{SEN_BASE}developpeur-fullstack_e_163401.html" style="font-size:18px;">
    Developpeur Fullstack <!-- d ico postulez --><img src="images/blank.gif"><!-- f ico postulez -->
  </a></div></td><td align=left>Dakar</td></tr>
<tr><td><a href="{SEN_BASE}developpeur-fullstack_e_163401.html">&nbsp;</a>
  <span style='color:#999;'> Publi&eacute;: </span><span style="display:none;">2026-08-14</span>14&nbsp;Aou.</td></tr>"""
SEN_INLINE = f"""
<tr><td><div align=left><a href="{SEN_BASE}software-and-data-engineer_e_163402.html">Software &amp; Data Engineer</a></div></td>
<td>Dakar <span style='color:#999;'> Publi&eacute;: </span><span style="display:none;">2026-08-12</span>12&nbsp;Aou.</td></tr>"""
SEN_PAGE = f"<html><body><table>{SEN_ROW}{SEN_INLINE}</table></body></html>"
SEN_URL = "https://senjob.com/offres-d-emploi.php"


def test_senjob_merges_sibling_rows_and_reads_title_place_and_date():
    http = _stub({"page=2": (200, f"<table>{SEN_INLINE}</table>"), SEN_URL: (200, SEN_PAGE)})
    jobs, st = _run("senjob", _row("senjob", "all"), http)
    assert [(j["title"], j["location"], j["posted"], j["company"]) for j in jobs] == [
        ("Developpeur Fullstack", "Dakar", "2026-08-14", "?"), ("Software & Data Engineer", "Dakar", "2026-08-12", "?")]
    assert [j["url"] for j in jobs] == [SEN_BASE + "developpeur-fullstack_e_163401.html", SEN_BASE + "software-and-data-engineer_e_163402.html"]
    assert [c[0] for c in http.calls] == [SEN_URL, SEN_URL + "?page=2"]  # page 2 is only a sticky repeat: the board ends
    assert _hosts(http) == {"senjob.com"} and st["jobs"] == 2


def test_senjob_pages_until_a_page_is_empty_and_a_later_refusal_keeps_what_was_read():
    def page(n):
        return f'<table><tr><td><a href="{SEN_BASE}job-{n}_e_{n}.html">Job {n}</a></td><td>Thies</td></tr></table>'
    routes = {"page=3": (429, None), "page=2": (200, page(2)), SEN_URL: (200, page(1))}
    http = _stub(routes)
    jobs, _ = _run("senjob", _row("senjob", "all"), http)
    assert [j["title"] for j in jobs] == ["Job 1", "Job 2"] and len(http.calls) == 3
    assert [j["title"] for j in _run("senjob", _row("senjob", "all"), _stub({"page=2": (200, "<html>No results.</html>"), SEN_URL: (200, page(1))}))[0]] == ["Job 1"]
    endless = _stub({SEN_URL: lambda u, h, d: (200, page(len(_stub_pages(u))))})
    assert len(_run("senjob", _row("senjob", "all"), endless)[0]) <= 10


def _stub_pages(u):
    q = urlsplit(u).query
    return [int(q.split("=")[1])] if q else [1]


def test_senjob_first_page_failures_and_a_broken_parser_are_failed_boards_not_empty_ones():
    for http in (_stub({}), _stub({SEN_URL: (403, None)})):
        jobs, st = _run("senjob", _row("senjob", "all"), http)
        assert jobs == [] and "ok" not in st
    broken = '<html><a href="http://senjob.com/jobseekers/x_e_1.html">x</a></html>'  # posting-link shape, but not https
    assert "ok" not in _run("senjob", _row("senjob", "all"), _stub({SEN_URL: (200, broken)}))[1]
    quiet = _run("senjob", _row("senjob", "all"), _stub({SEN_URL: (200, "<html><body>No results.</body></html>")}))
    assert quiet[0] == [] and quiet[1]["ok"] == 1


def test_senjob_off_host_or_malformed_links_are_not_postings():
    page = (f'<table><tr><td><a href="https://evil.example.com/jobseekers/a_e_1.html">Evil</a></td></tr>'
            f'<tr><td><a href="https://senjob.com.evil.example/jobseekers/a_e_2.html">Evil2</a></td></tr>'
            f'<tr><td><a href="/jobseekers/rel_e_3.html">Relative</a></td></tr>'
            f'<tr><td><a href="{SEN_BASE}ok_e_4.html?x=1">Query</a></td></tr>'
            f'<tr><td><a href="{SEN_BASE}fine_e_5.html">Fine</a></td><td>Dakar</td></tr></table>')
    http = _stub({SEN_URL: (200, page)})
    jobs, _ = _run("senjob", _row("senjob", "all"), http)
    assert [j["title"] for j in jobs] == ["Fine"]
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"senjob.com"} and _hosts(http) == {"senjob.com"}


def test_senjob_unclosed_rows_are_linear_and_lose_nothing():
    def run(n):
        rows = "".join(f'<tr><td><a href="{SEN_BASE}j-{i}_e_{i}.html">Job {i}</a></td><td>Dakar<span style="display:none">2026-08-{10 + i % 10}</span>' for i in range(1, n + 1))
        # Best of three: noise on a busy runner only adds time, so the minimum is the real cost.
        (jobs, _), t = best_of(lambda: _run("senjob", _row("senjob", "all"), _stub({"page=2": (404, None), SEN_URL: (200, "<table>" + rows)})))
        return jobs, t
    (j1, t1), (j2, _t2), (j4, t4) = run(150), run(300), run(600)  # no row is ever closed
    assert (len(j1), len(j2), len(j4)) == (150, 300, 600) and j4[-1]["title"] == "Job 600" and j4[-1]["location"] == "Dakar"
    assert j4[0]["posted"] == "2026-08-11" and t4 < 2.0 and t4 < max(t1, 0.02) * 12
    long = '<a href="' + SEN_BASE + 'a_e_1.html">' + "x " * 200_000
    assert [len(j["title"]) for j in _run("senjob", _row("senjob", "all"), _stub({SEN_URL: (200, long)}))[0]] == [300 - 1]
