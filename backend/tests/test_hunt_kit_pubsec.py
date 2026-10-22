"""Offline tests for hunt_kit/kit_pubsec.py (NEOGOV and SchoolSpring).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) for each reader here
(NEOGOV: neogov_test.go listing and detail fixtures; SchoolSpring: schoolspring_test.go and career-ops
schoolspring.mjs envelope) and written fresh. Nothing touches the network.
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


ps = _load("kit_pubsec_under_test", "kit_pubsec.py")
hk = _load("hunt_kit_pubsec_under_test", "hunt_kit.py")
F = ps.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
NAMES = ("neogov", "schoolspring")


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []
    once_flags: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        once_flags.append((url, once))
        calls.append((url, method, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.once = once_flags
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    return jobs, stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http) -> set:
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs) -> set:
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_every_reader_is_registered():
    assert set(F) == set(NAMES)


def test_hunt_kit_wiring():
    assert "kit_pubsec" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_pubsec.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)  # every reader names the reference file and line it follows
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_pubsec.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://www.schooljobs.com/careers/cochisecollege/jobs/5371857/ft-academic-career-advisor-svc", "neogov",
     "schooljobs.com/cochisecollege"),
    ("https://www.governmentjobs.com/careers/LACounty/jobs/123/x", "neogov", "governmentjobs.com/lacounty"),
    ("https://schooljobs.com/careers/clarkcollege/facultypositions/jobs/9/x", "neogov", "schooljobs.com/clarkcollege"),
    ("https://vansd.schoolspring.com/?jobid=77", "schoolspring", "vansd.schoolspring.com"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


@pytest.mark.parametrize("url,ats", [
    ("https://www.schooljobs.com/careers/home/index", "neogov"),  # the site's own path, not an agency
    ("https://www.schoolspring.com/jobdetail?jobId=5", "schoolspring"),  # the national index: no board token in the link
    ("https://api.schoolspring.com/api/Jobs/5", "schoolspring"),
])
def test_links_that_do_not_show_the_board_token_give_no_token(url, ats):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, "")


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra("neogov:schooljobs.com/cochisecollege,schoolspring:vansd.schoolspring.com")
    assert [(r["ats"], r["token"], r["name"]) for r in rows] == [
        ("neogov", "schooljobs.com/cochisecollege", "cochisecollege"),
        ("schoolspring", "vansd.schoolspring.com", "vansd.schoolspring.com")]


# --------------------------------------------------------------------------- NEOGOV
def _ng_card(jid, title, loc, *, agency="cochisecollege", snippet="Snippet text.", href=None, folder=""):
    href = href if href is not None else f"/careers/{agency}/{folder}jobs/{jid}/{title.lower().replace(' ', '-')}"
    id_attr = f' data-job-id="{jid}"' if jid else ""
    return (f'<li class="list-item"{id_attr}><h3 class="job-item-link-container"><a class="item-details-link" '
            f'data-department-name="X" href="{href}">{title}</a></h3><ul class="list-meta"><li>{loc}</li>'
            f'<li>Full-time <span>-</span> $50,585.60 Annually</li></ul><div class="list-entry">{snippet}</div>'
            f'<div class="list-published"><span>Posted 3 weeks ago</span></div></li>')


def _ng_list(*cards, total=None):
    head = f'<span id="job-postings-number">{total}</span> jobs' if total is not None else ""
    return f'{head}<ul class="list-items">' + "".join(cards) + "</ul>"


NG_DETAIL = ('<html><body><div class="tab-content"><div id="details-info" class="tab-pane active fr-view"><dl>'
             "<dt><h2>Definition</h2></dt><dd><p>Provide advising to students.</p></dd>"
             "<dt><h2>Minimum Qualifications</h2></dt><dd><p>A master's degree.</p></dd></dl></div>"
             '<div id="details-benefits" class="tab-pane fr-view"><p>Benefits blurb.</p></div></div></body></html>')


def _ng_http(pages, detail=NG_DETAIL, detail_status=200):
    def listing(url, headers):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        return (200, pages[page - 1]) if page <= len(pages) else (200, '<div id="jobs-not-found-container"></div>')
    return _stub({"/careers/home/index": listing, "/jobs/": (detail_status, detail if detail_status == 200 else None)})


def test_neogov_happy_path_maps_cards_and_reads_the_detail_body():
    page = _ng_list(_ng_card("5371857", "FT Academic Advisor", "Sierra Vista Campus, AZ"),
                    _ng_card("5379875", "FT Accountant", "Douglas Campus, AZ"),
                    '<li class="list-item">No job id, skipped.</li>', total=2)
    http = _ng_http([page])
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege", name="Cochise College"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert [j["title"] for j in jobs] == ["FT Academic Advisor", "FT Accountant"]
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["source"] == "neogov" and j["company"] == "Cochise College"
    assert j["url"] == "https://www.schooljobs.com/careers/cochisecollege/jobs/5371857/ft-academic-advisor"
    assert j["location"] == "Sierra Vista Campus, AZ" and j["extra"]["job_id"] == "5371857"
    assert "Minimum Qualifications" in j["description"] and "Benefits blurb" not in j["description"]
    assert _hosts(http) == {"www.schooljobs.com"} and _job_hosts(jobs) == {"www.schooljobs.com"}
    assert all(u.startswith("https://") for u, *_ in http.calls)
    lists = [h for u, _, h in http.calls if "/careers/home/index" in u]
    details = [h for u, _, h in http.calls if "/jobs/" in u]
    assert lists and all(h.get("X-Requested-With") == "XMLHttpRequest" for h in lists)  # the XHR header the list needs
    assert details and all("X-Requested-With" not in h for h in details)  # the detail page is plain HTML
    q = parse_qs(urlsplit(next(u for u, *_ in http.calls if "/careers/home/index" in u)).query)
    assert q["agency"] == ["cochisecollege"] and "departmentFolder" not in q


def test_neogov_folder_scopes_the_list_and_snippet_is_the_fallback_description():
    http = _stub({"/careers/home/index": (200, _ng_list(_ng_card("9", "Instructor", "Vancouver, WA", agency="clarkcollege",
                                                                  folder="facultypositions/", snippet="Teach classes.")))})
    http_calls = http.calls
    jobs, _ = _run("neogov", _row("neogov", "www.SchoolJobs.com/clarkcollege/facultypositions", title_ok=lambda t: False), http)
    q = parse_qs(urlsplit(http_calls[0][0]).query)
    assert q["departmentFolder"] == ["facultypositions"] and q["agency"] == ["clarkcollege"]
    assert jobs[0]["description"] == "Teach classes." and jobs[0]["url"].endswith("/clarkcollege/facultypositions/jobs/9/instructor")
    assert not any("/jobs/" in u for u, *_ in http_calls)  # title_ok rejected the card: no detail request


def test_neogov_pages_until_the_stated_total_and_dedups_across_pages():
    p1 = _ng_list(_ng_card("1", "A", "x"), _ng_card("2", "B", "x"), total=3)
    p2 = _ng_list(_ng_card("2", "B", "x"), _ng_card("3", "C", "x"))
    http = _ng_http([p1, p2, _ng_list(_ng_card("4", "D", "x"))])
    jobs, _ = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), http)
    assert [j["title"] for j in jobs] == ["A", "B", "C"]
    pages = [parse_qs(urlsplit(u).query)["page"][0] for u, *_ in http.calls if "/careers/home/index" in u]
    assert pages == ["1", "2"]  # the total (3) was reached: page 3 is never asked for


def test_neogov_a_page_that_adds_nothing_new_ends_the_walk():
    p = _ng_list(_ng_card("1", "A", "x"))
    http = _ng_http([p, p, p, p])
    jobs, _ = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), http)
    assert len(jobs) == 1
    assert len([u for u, *_ in http.calls if "/careers/home/index" in u]) == 2


def test_neogov_one_bad_card_does_not_lose_the_board():
    cards = [_ng_card("1", "Good", "x"),
             _ng_card("2", "Malformed", "x", href="http://[::1/careers/x/jobs/2"),
             _ng_card("3", "Foreign", "x", href="https://evil.example.com/careers/cochisecollege/jobs/3/x"),
             _ng_card("4", "Plain http scheme", "x", href="http://www.schooljobs.com/careers/cochisecollege/jobs/4/x"),
             _ng_card("5", "Not a job path", "x", href="/careers/cochisecollege/about"),
             _ng_card("6", "Also good", "x")]
    http = _ng_http([_ng_list(*cards)])
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), http)
    assert [j["title"] for j in jobs] == ["Good", "Also good"] and stats["ok"] == 1
    assert _hosts(http) == {"www.schooljobs.com"} and _job_hosts(jobs) == {"www.schooljobs.com"}


def test_neogov_refusal_on_a_later_list_page_keeps_page_one_and_a_refused_detail_ends_the_details():
    p1 = _ng_list(_ng_card("1", "A", "x"), _ng_card("2", "B", "x"), total=9)

    def listing(url, headers):
        return (200, p1) if "page=1" in url else (403, None)
    http = _stub({"/careers/home/index": listing, "/jobs/": (429, None)})
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), http)
    assert [j["title"] for j in jobs] == ["A", "B"] and stats["ok"] == 1
    assert [u for u, o in http.once if "/jobs/" in u and o] and len([u for u, _ in http.once if "/jobs/" in u]) == 1


def test_neogov_first_page_refusal_and_a_wrong_agency_fail_the_board():
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), _stub({"/careers/home/index": (403, None)}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    home = "<html><body><div>Welcome to the site home page</div></body></html>"  # a wrong slug lands on the home page
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/nosuch"), _stub({"/careers/home/index": (200, home)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"),
                       _stub({"/careers/home/index": (200, '<div id="jobs-not-found-container">No jobs</div>')}))
    assert jobs == [] and stats["ok"] == 1  # a real empty board is an empty board, not a failure


@pytest.mark.parametrize("token", ["", "cochisecollege", "evil.com/cochisecollege", "schooljobs.com/home",
                                   "schooljobs.com/a b", "schooljobs.com/x/y/z", "schooljobs.com/../x"])
def test_neogov_bad_tokens_make_no_request(token):
    http = _stub({})
    assert _run("neogov", _row("neogov", token), http)[0] == [] and http.calls == []


# --------------------------------------------------------------------------- SchoolSpring
def _ss_page(*rows, success=True):
    return {"success": success, "message": "" if success else "refused",
            "value": {"page": 1, "size": 100, "jobsList": list(rows)}}


def _ss_row(jid, title, loc="Vancouver, WA", employer="Some Elementary", date="2026-09-02T07:00:00"):
    return {"jobId": jid, "title": title, "location": loc, "employer": employer, "displayDate": date}


def _ss_detail(title, employer="Vancouver Public Schools", body="<p>Teach <b>well</b>.</p>", places=("Vancouver, WA",), date="2026-09-02T07:00:00"):
    return {"success": True, "message": "", "value": {
        "jobInfo": {"jobTitle": title, "jobDescription": body, "employerName": employer, "displayDate": date},
        "jobLocations": [{"displayLocation": p} for p in places]}}


def test_schoolspring_district_board_lists_every_row_with_the_board_as_employer():
    rows = [_ss_row(11, "Grade 5 &#8211; Teacher"), _ss_row(12, "Bus Driver", employer="Transport"), _ss_row(0, "No id")]
    http = _stub({"GetPagedJobsWithSearch": (200, _ss_page(*rows)),
                  "/api/Jobs/11?": (200, _ss_detail("Grade 5 - Teacher", places=("Vancouver, WA", "Vancouver, WA", "Camas, WA"))),
                  "/api/Jobs/12?": (404, None)})
    jobs, stats = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com", name="Vancouver Public Schools"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Vancouver Public Schools" and a["source"] == "schoolspring"
    assert a["title"] == "Grade 5 - Teacher" and a["location"] == "Vancouver, WA; Camas, WA"
    assert a["description"] == "Teach well." and a["posted"] == "2026-09-02"
    assert a["url"] == "https://vansd.schoolspring.com/?jobid=11" and a["extra"]["job_id"] == "11"
    assert b["title"] == "Bus Driver" and b["location"] == "Vancouver, WA - Transport" and b["description"] == ""  # detail 404: kept
    first = parse_qs(urlsplit(http.calls[0][0]).query, keep_blank_values=True)
    assert first["domainName"] == ["vansd.schoolspring.com"] and first["keyword"] == [""] and first["size"] == ["100"]
    assert _hosts(http) == {"api.schoolspring.com"} and _job_hosts(jobs) == {"vansd.schoolspring.com"}


def test_schoolspring_keyword_board_uses_the_national_domain_and_lists_only_opened_postings():
    rows = [_ss_row(1, "Network Administrator"), _ss_row(2, "Network Administrator II"), _ss_row(3, "Webmaster")]
    http = _stub({"/api/Jobs/1?": (200, _ss_detail("Network Administrator", employer="Acme Schools")),
                  "/api/Jobs/2?": (200, _ss_detail("Network Administrator II", employer="Acme Schools", body="  ")),
                  "/api/Jobs/3?": (200, {"success": False, "message": "gone", "value": None}),
                  "GetPagedJobsWithSearch": (200, _ss_page(*rows))})
    jobs, _ = _run("schoolspring", _row("schoolspring", "network administrator", name="SchoolSpring"), http)
    assert [(j["title"], j["company"]) for j in jobs] == [("Network Administrator", "Acme Schools")]  # no employer/body: skipped
    q = parse_qs(urlsplit(http.calls[0][0]).query)
    assert q["domainName"] == ["www.schoolspring.com"] and q["keyword"] == ["network administrator"]
    assert jobs[0]["url"] == "https://www.schoolspring.com/jobdetail?jobId=1"
    assert _hosts(http) == {"api.schoolspring.com"} and _job_hosts(jobs) == {"www.schoolspring.com"}


def test_schoolspring_pages_until_a_short_or_repeated_page():
    full = [_ss_row(i, f"Job {i}") for i in range(1, 101)]
    second = [_ss_row(i, f"Job {i}") for i in range(101, 131)]
    seen_pages: list = []

    def listing(url, headers):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        seen_pages.append(page)
        return 200, _ss_page(*{1: full, 2: second}.get(page, full))
    http = _stub({"GetPagedJobsWithSearch": listing})
    jobs, _ = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com", title_ok=lambda t: False), http)
    assert len(jobs) == 130 and seen_pages == [1, 2]  # the short second page ends the walk
    seen_pages.clear()
    http = _stub({"GetPagedJobsWithSearch": lambda u, h: (seen_pages.append(1), (200, _ss_page(*full)))[1]})
    jobs, _ = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com", title_ok=lambda t: False), http)
    assert len(jobs) == 100 and len(seen_pages) == 2  # page 2 repeated page 1: nothing new, stop


def test_schoolspring_success_false_is_a_refusal_and_a_later_page_hiccup_keeps_earlier_pages():
    jobs, stats = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com"),
                       _stub({"GetPagedJobsWithSearch": (200, _ss_page(success=False))}))
    assert jobs == [] and stats.get("ok", 0) == 0
    full = [_ss_row(i, f"Job {i}") for i in range(1, 101)]

    def listing(url, headers):
        return (200, _ss_page(*full)) if "page=1" in url else (403, None)
    jobs, stats = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com", title_ok=lambda t: False),
                       _stub({"GetPagedJobsWithSearch": listing}))
    assert len(jobs) == 100 and stats["ok"] == 1


def test_schoolspring_refused_detail_ends_the_details_and_one_odd_row_costs_only_itself():
    rows = [_ss_row(1, "One"), "junk", {"jobId": True, "title": "bool id"}, _ss_row(2, "Two"), _ss_row(3, "Three")]
    http = _stub({"GetPagedJobsWithSearch": (200, _ss_page(*rows)), "/api/Jobs/": (429, None)})
    jobs, stats = _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com"), http)
    assert [j["title"] for j in jobs] == ["One", "Two", "Three"] and stats["ok"] == 1
    assert len([1 for u, o in http.once if "/api/Jobs/" in u and "GetPaged" not in u and o]) == 1


def test_schoolspring_future_dates_are_dropped_and_detail_is_capped():
    http = _stub({"/api/Jobs/1?": (200, _ss_detail("One", date="2999-01-01T00:00:00")),
                  "GetPagedJobsWithSearch": (200, _ss_page(_ss_row(1, "One", date="2999-01-01T00:00:00")))})
    assert _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com"), http)[0][0]["posted"] == ""
    rows = [_ss_row(i, f"Job {i}") for i in range(1, 61)]
    http = _stub({"GetPagedJobsWithSearch": (200, _ss_page(*rows)), "/api/Jobs/": (200, _ss_detail("x"))})
    _run("schoolspring", _row("schoolspring", "vansd.schoolspring.com"), http)
    assert len([u for u, *_ in http.calls if "GetPaged" not in u]) == 25


@pytest.mark.parametrize("token", ["", "   ", "www.schoolspring.com", "api.schoolspring.com", "evil.com.schoolspring.com.x.y", "a\nb"])
def test_schoolspring_bad_tokens_make_no_request(token):
    http = _stub({})
    jobs, _ = _run("schoolspring", _row("schoolspring", token), http)
    if token == "evil.com.schoolspring.com.x.y":
        assert {urlsplit(u).hostname for u, *_ in http.calls} <= {"api.schoolspring.com"}  # a keyword: only ever the API host
    else:
        assert jobs == [] and http.calls == []


# --------------------------------------------------------------------------- hostile markup
def test_neogov_thousands_of_unclosed_tags_and_a_deep_detail_page_do_not_lose_the_board():
    t0 = time.perf_counter()
    hostile = _ng_card("1", "Good", "x") + "<ul><li class='list-item' data-job-id='2'>" * 3000 + "<div>" * 20000
    deep_detail = '<div id="details-info">' + "<div>" * 30000 + "text" + "</div>" * 30000
    http = _ng_http([_ng_list(hostile)], detail=deep_detail)
    jobs, _ = _run("neogov", _row("neogov", "schooljobs.com/cochisecollege"), http)
    assert jobs and jobs[0]["title"] == "Good"
    assert time.perf_counter() - t0 < 10.0


def test_read_kit_hosts_are_not_listed_as_unsupported():
    for host in ("schooljobs.com", "governmentjobs.com", "schoolspring.com"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)
