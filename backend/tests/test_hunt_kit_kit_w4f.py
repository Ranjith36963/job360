"""Offline tests for hunt_kit/kit_w4f.py (Telegram.org jobs, BairesDev, Vention, Northstone) and the Paylocity host rule.

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and written fresh.
Nothing touches the network.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w4 = _load("kit_w4f_under_test", "kit_w4f.py")
hk = _load("hunt_kit_w4f_under_test", "hunt_kit.py")
F = w4.ATS_FETCHERS
NAMES = ("bairesdev", "northstone", "telegramcareers", "vention")
TOKENS = {"bairesdev": "talent.bairesdev.com", "northstone": "www.revun.com/careers",
          "telegramcareers": "telegram.org", "vention": "join.ventionteams.com"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        calls.append((url, headers or {}, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name, row, http):
    stats: dict = {}
    return F[name](row, http, stats), stats


def _row(name, token=None, **kw):
    return {"name": name.title() + " Co", "ats": name, "token": TOKENS[name] if token is None else token, **kw}


def _hosts(http):
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_wiring_and_registries():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_w4f" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    assert all(len(line) <= 120 for line in (KIT_DIR / "hunt_kit.py").read_text(encoding="utf-8").split("\n")
               if line.startswith('    "kit_'))
    src = (KIT_DIR / "kit_w4f.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_w4f.py"' in served
    rows = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    assert {r["ats"] for r in rows if r["ats"] in NAMES} == set(NAMES)
    assert any(r["ats"] == "avature" and r["board"] == "jobs.deutschebahngroup.careers" for r in rows)
    assert hk.parse_extra("northstone:www.revun.com/careers")[0]["token"] == "www.revun.com/careers"


@pytest.mark.parametrize("url,ats,token", [
    ("https://recruiting.paylocity.com/Recruiting/Jobs/All/1a06dc72-45ee-4c90-a268-fe881bbeb577", "paylocity",
     "1a06dc72-45ee-4c90-a268-fe881bbeb577"),
    ("https://recruiting.paylocity.com/Recruiting/Jobs/Details/4242", "paylocity", ""),
    ("https://telegram.org/jobs", "telegramcareers", "telegram.org"),
    ("https://talent.bairesdev.com/", "bairesdev", "talent.bairesdev.com"),
    ("https://applicants.bairesdev.com/job/97/123/apply", "bairesdev", "talent.bairesdev.com"),
    ("https://join.ventionteams.com/job-openings/internship-python-krakow", "vention", "join.ventionteams.com"),
])
def test_host_rule(url, ats, token):
    assert hk.board_from_job_url(url) == {"kind": "ats", "ats": ats, "token": token, "host": urlsplit(url).hostname}


def test_paylocity_is_no_longer_unsupported_and_foreign_hosts_are_not_matched():
    assert not hk._UNSUPPORTED_HOSTS.search("recruiting.paylocity.com")
    assert hk.board_from_job_url("https://recruiting.paylocity.com/Recruiting/Jobs/All/abc")["kind"] == "ats"
    for url in ("https://telegram.org/blog", "https://telegram.org.evil.io/jobs", "https://evil.example/telegram.org/jobs",
                "https://recruiting.paylocity.com.evil.io/Recruiting/Jobs/All/abc", "https://talent.bairesdev.com.evil.io/",
                "https://join.ventionteams.com.evil.io/x", "https://evil.example/join.ventionteams.com"):
        assert hk.board_from_job_url(url)["ats"] not in NAMES + ("paylocity",)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("token", ["", "a b", "evil.example/x/y/z", "-bad", "a" * 300, "x\r\nHost: y", "/"])
def test_bad_boards_make_no_request(name, token):
    http = _stub({})
    assert _run(name, _row(name, token), http)[0] == [] and http.calls == []


# --------------------------------------------------------------------------- Telegram
def _tg(*pairs, wrapper='<div id="dev_page_content">'):
    body = "".join(f"<h3>{t}</h3>{b}" for t, b in pairs)
    return 200, f"<html><body>{wrapper}<h1>Jobs</h1><p>intro</p>{body}</div></body></html>"


def test_telegram_splits_roles_on_headings_and_never_bleeds():
    http = _stub({"telegram.org/jobs": _tg(("Content Moderator", "<p>Responsibilities: <b>sort</b> content.</p><ul><li>care</li></ul>"),
                                          ("C/C++ Software Engineer", "<p>Work on storage engines.</p><script>x()</script>"),
                                          ("Content Moderator", "<p>duplicate title</p>"), ("   ", "<p>no title</p>"))})
    jobs, stats = _run("telegramcareers", _row("telegramcareers"), http)
    assert [j["title"] for j in jobs] == ["Content Moderator", "C/C++ Software Engineer"]
    assert [j["url"] for j in jobs] == ["https://telegram.org/jobs#content-moderator", "https://telegram.org/jobs#c-c-software-engineer"]
    assert "Responsibilities" in jobs[0]["description"] and "storage engines" not in jobs[0]["description"]
    assert "x()" not in jobs[1]["description"] and jobs[1]["extra"]["job_id"] == "c-c-software-engineer"
    assert jobs[0]["location"] == "" and jobs[0]["source"] == "telegramcareers"
    assert _hosts(http) == {"telegram.org"} and _job_hosts(jobs) == {"telegram.org"}
    assert stats["ok"] == 1 and stats["jobs"] == 2


def test_telegram_unclosed_tags_do_not_hide_later_roles():
    page = ('<html><body><div id="dev_page_content"><h3>A</h3><p>first <a href="x">link<h3>B</h3><p>second</p>'
            "<h3>C</h3><div>third</div></div></body></html>")
    jobs, _ = _run("telegramcareers", _row("telegramcareers"), _stub({"telegram.org/jobs": (200, page)}))
    assert [j["title"] for j in jobs] == ["A", "B", "C"]
    assert "second" not in jobs[0]["description"] and "third" not in jobs[1]["description"]
    assert "third" in jobs[2]["description"]


def test_telegram_missing_content_refusal_and_hostile_markup():
    assert _run("telegramcareers", _row("telegramcareers"), _stub({"/jobs": (200, "<html><h3>Role</h3></html>")}))[0] == []
    for st in (403, 429, 0, 500):
        jobs, stats = _run("telegramcareers", _row("telegramcareers"), _stub({"/jobs": (st, None)}))
        assert jobs == [] and stats.get("ok", 0) == 0
    deep = "<div>" * 5000 + "<p>deep</p>"
    t0 = time.perf_counter()
    jobs, _ = _run("telegramcareers", _row("telegramcareers"), _stub({"/jobs": _tg(("Fine", "<p>ok</p>"), ("Hostile", deep + "<![CDATA[<<<<" + "<a " * 20000))}))
    assert time.perf_counter() - t0 < 2
    assert "Fine" in [j["title"] for j in jobs]


# --------------------------------------------------------------------------- BairesDev
def _widget(*rows):
    cfg = base64.b64encode(json.dumps({"jobList": [{"page_item_url": u, "jobTitle": t} for u, t in rows]}).encode()).decode()
    return 200, f'<html><div data-widget-config="{cfg}"></div></html>'


def _bd_ok(i, title="Python Developer"):
    return 200, {"title": title, "description": "<p>teaser</p>", "datePosted": "2025-06-02T10:23:21.503",
                 "jobLocationType": "TELECOMMUTE", "hiringOrganization": {"name": "BairesDev LLC"}}


def test_bairesdev_dedups_rows_by_id_unions_locations_and_hydrates():
    http = _stub({
        "talent.bairesdev.com": _widget(
            ("https://applicants.bairesdev.com/job/97/111/apply", "Python Developer | Remote Work | Brazil"),
            ("https://applicants.bairesdev.com/job/97/111/apply", "Python Developer | Remote Work | Colombia"),
            ("https://evil.example/job/97/222/apply", "Go Developer | Remote Work | Remote Work"),
            ("https://applicants.bairesdev.com/not-a-job", "Junk"), ("javascript:alert(1)", "Junk 2")),
        "api/JobPosting?JobPostingId=111": _bd_ok(111),
        "api/JobPosting?JobPostingId=222": (200, {"title": "Go Developer", "description": "<p>go</p>"}),
        "api/Job?JobOfferId=111": (200, {"jobResults": [{"description": "<p>Full <b>text</b></p><script>x()</script>"}]}),
        "api/Job?JobOfferId=222": (500, None),
    })
    jobs, stats = _run("bairesdev", _row("bairesdev"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["111", "222"]
    a, b = jobs
    assert a["url"] == "https://applicants.bairesdev.com/job/97/111/apply" and a["location"] == "Brazil, Colombia"
    assert "Full text" in a["description"] and "x()" not in a["description"] and a["posted"] == "2025-06-02"
    assert a["remote"] is True and a["company"] == "BairesDev LLC"
    assert b["description"] == "go" and b["company"] == "BairesDev" and b["remote"] is None
    assert _hosts(http) == {"talent.bairesdev.com", "applicants.bairesdev.com"} and _job_hosts(jobs) == {"applicants.bairesdev.com"}
    assert stats["ok"] == 1 and stats["jobs"] == 2


def test_bairesdev_bad_posting_refusal_filter_and_cap():
    rows = [(f"https://applicants.bairesdev.com/job/97/{i}/apply", f"Role {i} | Remote Work | Chile") for i in range(1, 41)]
    posting = lambda url, h: _bd_ok(0, title="Ghost") if "=3" in url else (  # noqa: E731
        (200, {"title": ""}) if "=2" in url else _bd_ok(0, title="R"))
    http = _stub({"talent.bairesdev.com": _widget(*rows), "api/JobPosting": posting, "api/Job?": (404, None)})
    jobs, _ = _run("bairesdev", _row("bairesdev"), http)
    assert [j["extra"]["job_id"] for j in jobs][:2] == ["1", "3"]  # the title-less id 2 is skipped alone
    assert sum("api/JobPosting" in u for u, *_ in http.calls) == w4.DETAIL_CAP and all(c[2] for c in http.calls[1:])
    # a refusal ends the detail requests
    http = _stub({"talent.bairesdev.com": _widget(*rows[:5]), "api/JobPosting": lambda u, h: _bd_ok(0) if "=1" in u else (429, None)})
    jobs, _ = _run("bairesdev", _row("bairesdev"), http)
    assert len(jobs) == 1 and sum("api/JobPosting" in u for u, *_ in http.calls) == 2
    # the run's title filter spends no request on a rejected title
    http = _stub({"talent.bairesdev.com": _widget(*rows[:3]), "api/JobPosting": _bd_ok(0), "api/Job?": (404, None)})
    jobs, _ = _run("bairesdev", _row("bairesdev", title_ok=lambda t: t == "Role 2"), http)
    assert len(jobs) == 1 and sum("api/JobPosting" in u for u, *_ in http.calls) == 1


def test_bairesdev_dead_listing_and_bad_config():
    for body in ((200, "<html>no widget</html>"), (200, '<div data-widget-config="@@@@"></div>'),
                 (200, '<div data-widget-config="' + base64.b64encode(b"not json").decode() + '"></div>'),
                 (200, '<div data-widget-config="' + base64.b64encode(b'{"jobList": 5}').decode() + '"></div>'),
                 _widget(("https://applicants.bairesdev.com/nothing", "x")), (403, None), (0, None)):
        jobs, stats = _run("bairesdev", _row("bairesdev"), _stub({"talent.bairesdev.com": body}))
        assert jobs == [] and stats.get("ok", 0) == 0


# --------------------------------------------------------------------------- Vention
def _vi(i, **kw):
    d = {"id": i, "title": f"Role {i}", "meta": {"slug": f"role-{i}", "html_url": f"https://join.ventionteams.com/job-openings/role-{i}",
                                                  "first_published_at": "2026-06-14T18:39:35+00:00"},
         "city": {"name": "Krakow", "country": {"name": "Poland"}}, "work_format": "remote",
         "responsibilities": "&lt;p&gt;Do &lt;b&gt;work&lt;/b&gt;.&lt;/p&gt;", "required_skills": "<p>Python</p>", "benefits": "<p>perks</p>",
         "about": "<script>x()</script><p>us</p>"}
    d.update(kw)
    return d


def test_vention_happy_path_paging_and_mapping():
    pages = {"offset=0": (200, {"meta": {"total_count": 25}, "items": [_vi(i) for i in range(1, 21)]}),
             "offset=20": (200, {"meta": {"total_count": 25}, "items": [_vi(21), _vi(1), _vi(22)]})}
    http = _stub(pages)
    jobs, stats = _run("vention", _row("vention"), http)
    assert len(jobs) == 22 and len({j["url"] for j in jobs}) == 22 and len(http.calls) == 2  # total_count ends the walk
    j = jobs[0]
    assert j["url"] == "https://join.ventionteams.com/job-openings/role-1" and j["location"] == "Krakow, Poland"
    assert j["remote"] is True and j["extra"] == {"job_id": "1", "work_mode": "remote"} and j["posted"] == "2026-06-14"
    assert "Do work." in j["description"] and "Python" in j["description"] and "perks" in j["description"] and "x()" not in j["description"]
    assert "limit=20" in http.calls[0][0] and "type=join_vacancy.VacancyPage" in http.calls[0][0] and "fields=%2A" in http.calls[0][0]
    assert _hosts(http) == {"join.ventionteams.com"} and stats["ok"] == 1 and stats["jobs"] == 22


def test_vention_bad_items_foreign_urls_and_failures():
    items = [_vi(1, meta={"slug": "a b/../c", "html_url": "http://join.ventionteams.com/x"}),  # no valid link: skipped
             _vi(2, meta={"slug": "ok-slug", "html_url": "https://evil.example/job-openings/x"}),  # foreign: rebuilt on the host
             _vi(3, meta={"slug": "role-3", "html_url": "javascript:alert(1)"}), _vi(0), _vi(True), _vi(5, title="  "),
             "junk", None, _vi(6, city=None, work_format="hybrid_x"), _vi(7, meta=None), _vi(8, work_format="onsite", city={"name": "Lodz"})]
    http = _stub({"offset=0": (200, {"meta": {"total_count": 8}, "items": items})})
    jobs, _ = _run("vention", _row("vention"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["2", "3", "6", "8"]
    assert [j["url"] for j in jobs][:2] == ["https://join.ventionteams.com/job-openings/ok-slug", "https://join.ventionteams.com/job-openings/role-3"]
    assert jobs[2]["extra"]["work_mode"] == "hybrid" and jobs[3]["extra"]["work_mode"] == "onsite" and jobs[3]["location"] == "Lodz"
    assert _job_hosts(jobs) == {"join.ventionteams.com"}


def test_vention_later_page_failure_keeps_board_first_page_failure_fails_it():
    ok = (200, {"meta": {"total_count": 500}, "items": [_vi(i) for i in range(1, 21)]})
    jobs, stats = _run("vention", _row("vention"), _stub({"offset=0": ok, "offset=20": (429, None)}))
    assert len(jobs) == 20 and stats["ok"] == 1
    for first in ((403, None), (200, {"items": "x"}), (200, []), (0, None)):
        jobs, stats = _run("vention", _row("vention"), _stub({"offset=0": first}))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"offset=": (200, {"meta": {}, "items": [_vi(1)]})})  # a never-ending list with no new id is bounded
    _run("vention", _row("vention"), http)
    assert len(http.calls) <= w4.VENTION_PAGES


# --------------------------------------------------------------------------- Northstone
def _ld_page(ident="ID-1", **kw):
    d = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Senior Go Engineer", "description": "&lt;p&gt;Build&lt;/p&gt;",
         "datePosted": "2026-05-25", "identifier": {"@type": "PropertyValue", "value": ident},
         "hiringOrganization": {"@type": "Organization", "name": "EnzRossi"},
         "jobLocation": {"@type": "Place", "address": {"addressLocality": "Calgary", "addressRegion": "AB", "addressCountry": "CA"}}}
    d.update(kw)
    return 200, '<html><script type="application/ld+json">' + json.dumps(d).replace("</", "<\\/") + "</script></html>"


def test_northstone_lists_single_segment_links_and_reads_ld_json():
    listing = ('<a href="/careers/go-engineer">Go Engineer</a><a href="https://www.revun.com/careers/ops/?utm=1">Ops</a>'
               '<a href="/careers/">All</a><a href="/careers/faq/deep">Deep</a><a href="/other/x">Other</a>'
               '<a href="https://evil.example/careers/steal">Steal</a><a href="http://www.revun.com/careers/plain">Plain</a>'
               '<a href="javascript:alert(1)">js</a><a href="http://[::1">bad</a><a href="/careers/go-engineer">dup</a>')
    http = _stub({"www.revun.com/careers/go-engineer/": _ld_page("A-1"), "www.revun.com/careers/ops/": _ld_page(
        "B-2", title="Ops Lead", jobLocation=None, jobLocationType="TELECOMMUTE", applicantLocationRequirements=[{"name": "Canada"}, {"name": "USA"}]),
        "www.revun.com/careers/": (200, f"<html>{listing}</html>")})
    jobs, stats = _run("northstone", _row("northstone"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["A-1", "B-2"]
    a, b = jobs
    assert a["url"] == "https://www.revun.com/careers/go-engineer/" and a["location"] == "Calgary, AB, CA" and a["company"] == "EnzRossi"
    assert a["posted"] == "2026-05-25" and a["description"] == "Build"
    assert b["remote"] is True and b["location"] == "Canada, USA"
    assert http.calls[0][0] == "https://www.revun.com/careers/" and _hosts(http) == {"www.revun.com"} and _job_hosts(jobs) == {"www.revun.com"}
    assert stats["ok"] == 1 and stats["jobs"] == 2


def test_northstone_bad_posting_duplicate_id_refusal_filter_and_cap():
    links = "".join(f'<a href="/careers/p{i}">Role {i}</a>' for i in range(40))
    pages = {"/careers/p0/": (200, "<html>no ld</html>"), "/careers/p1/": _ld_page(""), "/careers/p2/": _ld_page("DUP"),
             "/careers/p3/": _ld_page("DUP"), "/careers/p4/": (404, None), "/careers/p5/": (200, '<script type="application/ld+json">{bad</script>'),
             "/careers/p6/": _ld_page("OK", title="  ")}
    ok = _ld_page("N")
    http = _stub({**pages, "www.revun.com/careers/p": ok, "www.revun.com/careers/": (200, f"<html>{links}</html>")})
    jobs, _ = _run("northstone", _row("northstone"), http)
    assert [j["extra"]["job_id"] for j in jobs][:2] == ["DUP", "N"] and len({j["extra"]["job_id"] for j in jobs}) == len(jobs)
    assert len(http.calls) - 1 == w4.DETAIL_CAP and all(c[2] for c in http.calls[1:])
    # a 403 on a posting ends the detail requests, keeping what was read
    http = _stub({"/careers/p0/": _ld_page("X"), "/careers/p1/": (403, None), "www.revun.com/careers/": (200, f"<html>{links}</html>")})
    jobs, _ = _run("northstone", _row("northstone"), http)
    assert len(jobs) == 1 and len(http.calls) == 3
    # the run's title filter spends no request on a rejected title
    http = _stub({"/careers/p": ok, "www.revun.com/careers/": (200, f"<html>{links}</html>")})
    jobs, _ = _run("northstone", _row("northstone", title_ok=lambda t: t == "Role 7"), http)
    assert len(jobs) == 1 and len(http.calls) == 2


def test_northstone_dead_listing_and_hostile_markup():
    for body in ((403, None), (0, None), (200, "<html>no links</html>")):
        jobs, stats = _run("northstone", _row("northstone"), _stub({"/careers/": body}))
        assert jobs == [] and (stats.get("ok", 0) == 1) == (body[0] == 200)
    hostile = "<div>" * 5000 + '<a href="/careers/x">' * 5000 + "<a " * 20000
    t0 = time.perf_counter()
    _run("northstone", _row("northstone"), _stub({"www.revun.com/careers/": (200, hostile), "/careers/x/": _ld_page("H")}))
    assert time.perf_counter() - t0 < 2
