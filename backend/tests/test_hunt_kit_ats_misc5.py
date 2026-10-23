"""Offline tests for hunt_kit/kit_ats_misc5.py (SelfRecruit, Enlizt, Hurma, Compleo, Spark).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and written fresh.
Nothing touches the network. Zoho Recruit is not built here: ``zohorecruit`` in kit_ats_more.py already reads it.
"""
from __future__ import annotations

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


m5 = _load("kit_ats_misc5_under_test", "kit_ats_misc5.py")
hk = _load("hunt_kit_misc5_under_test", "hunt_kit.py")
F = m5.ATS_FETCHERS
NAMES = ("compleo", "enlizt", "hurma", "selfrecruit", "spark")
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
U1, U2, U3 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
              "33333333-3333-4333-8333-333333333333")


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        calls.append((url, headers or {}, max_bytes))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
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


def _ld(**kw):
    d = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Engineer", "description": "&lt;p&gt;Build&lt;/p&gt;",
         "datePosted": "2026-05-25T03:00:00.000Z", "identifier": {"@type": "PropertyValue", "value": "ID-1"},
         "hiringOrganization": {"@type": "Organization", "name": "Real Employer Ltd"},
         "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Lisboa", "addressCountry": "PT"}}}
    d.update(kw)
    return d


def _ld_page(**kw):
    return '<html><head><script type="application/ld+json">' + json.dumps(_ld(**kw)).replace("</", "<\\/") + "</script></head></html>"


def test_wiring_and_hosts():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_ats_misc5" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    assert all(len(line) <= 120 for line in (KIT_DIR / "hunt_kit.py").read_text(encoding="utf-8").split("\n")
               if line.startswith('    "kit_'))
    src = (KIT_DIR / "kit_ats_misc5.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_ats_misc5.py"' in served
    rows = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    assert {r["ats"] for r in rows if r["ats"] in NAMES} == set(NAMES)


@pytest.mark.parametrize("url,ats,token", [
    (f"https://dressup.selfrecruit.ge/{U1}", "selfrecruit", "dressup"),
    ("https://www.selfrecruit.ge/", "selfrecruit", ""),
    ("https://tributodevido.enlizt.me/vagas/full-stack-280426", "enlizt", "tributodevido"),
    ("https://scrumlaunch.hurma.work/public-vacancies/406", "hurma", "scrumlaunch"),
    ("https://jobs.compleo.app/wafx/jobdetail/HJ06276A", "compleo", "wafx"),
    ("https://jobs.compleo.app/wafx/home", "compleo", ""),
    ("https://volo.spark.work/career/job/203/Business-Development-Assistant", "spark", "volo"),
    ("https://hub.spark.work/api/jobOpenings", "spark", ""),
])
def test_host_rule(url, ats, token):
    assert hk.board_from_job_url(url) == {"kind": "ats", "ats": ats, "token": token, "host": urlsplit(url).hostname}


def test_foreign_hosts_are_not_matched():
    for url in ("https://selfrecruit.ge.evil.io/x", "https://evil.example/enlizt.me", "https://hurma.work.evil.io/public-vacancies/1",
                "https://jobs.compleo.app.evil.io/wafx/jobdetail/A1", "https://notspark.work/career/job/1/x"):
        assert hk.board_from_job_url(url)["ats"] not in NAMES


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("token", ["", "a b", "evil.example/x", "-bad", "a" * 90, "x\r\nHost: y"])
def test_bad_boards_make_no_request(name, token):
    http = _stub({})
    assert _run(name, _row(name, token), http)[0] == [] and http.calls == []


# --------------------------------------------------------------------------- SelfRecruit
def _sr_list(*targets):
    links = "".join(f'<a href="{t}">A Role</a>' for t in targets)
    return 200, f'<html><body><a href="/cv">Submit CV</a>{links}</body></html>'


def _sr_detail(title="Digital Marketing Lead", body="<p>Build campaigns.</p><script>alert(1)</script>"):
    return 200, f'<div class="vacancy_title_inner">{title}</div><div class="pub_vac_text_detail">{body}</div>'


def test_selfrecruit_happy_path_paging_and_hostile_links():
    http = _stub({
        "/vacancies/0": _sr_list(f"/{U1}", f"https://dressup.selfrecruit.ge/{U2}?tmpl=modal", "http://dressup.selfrecruit.ge/" + U3,
                                 f"https://evil.example/{U3}", f"/articles/{U3}", "/vacancies/10", "javascript:alert(1)", "http://[::1"),
        "/vacancies/10": _sr_list(f"/{U3}", f"/{U1}"),
        "/vacancies/20": _sr_list(f"/{U1}"),  # past the end the platform repeats: nothing new ends the walk
        f"/{U1}": _sr_detail(),
        f"/{U2}": _sr_detail("Second Role", "<ul><li>Two</li></ul>"),
        f"/{U3}": _sr_detail("Third Role", "<p>Three</p>"),
    })
    jobs, stats = _run("selfrecruit", _row("selfrecruit", "dressup", name="Dressup"), http)
    assert [j["title"] for j in jobs] == ["Digital Marketing Lead", "Second Role", "Third Role"]
    assert all(set(j) == JOB_KEYS and j["company"] == "Dressup" and j["source"] == "selfrecruit" for j in jobs)
    assert jobs[0]["extra"]["job_id"] == U1 and "Build campaigns" in jobs[0]["description"] and "alert" not in jobs[0]["description"]
    assert _hosts(http) == {"dressup.selfrecruit.ge"} and _job_hosts(jobs) == {"dressup.selfrecruit.ge"}
    assert all(urlsplit(u).scheme == "https" for u, *_ in http.calls) and all(j["url"].startswith("https://") for j in jobs)
    assert not any("/articles/" in u or "/cv" in u for u, *_ in http.calls) and stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_selfrecruit_bad_posting_and_refusal():
    http = _stub({"/vacancies/0": _sr_list(f"/{U1}", f"/{U2}", f"/{U3}"), "/vacancies/10": (200, "<html></html>"),
                  f"/{U1}": (200, "<html>no markup</html>"), f"/{U2}": _sr_detail("Good"), f"/{U3}": _sr_detail("Also good")})
    assert [j["title"] for j in _run("selfrecruit", _row("selfrecruit", "dressup"), http)[0]] == ["Good", "Also good"]
    http = _stub({"/vacancies/0": _sr_list(f"/{U1}", f"/{U2}"), f"/{U1}": (403, None), f"/{U2}": _sr_detail()})
    assert _run("selfrecruit", _row("selfrecruit", "dressup"), http)[0] == []
    assert len([u for u, *_ in http.calls if U2 in u]) == 0  # the first refusal ends the page requests
    assert _run("selfrecruit", _row("selfrecruit", "dressup"), _stub({"/vacancies/0": (403, None)}))[0] == []


def test_selfrecruit_cap_and_later_page_failure_keeps_board():
    ids = [f"{i:08d}-1111-4111-8111-111111111111" for i in range(1, 31)]
    http = _stub({"/vacancies/0": _sr_list(*[f"/{i}" for i in ids]), "/vacancies/10": (500, None), "-1111-4111": _sr_detail()})
    jobs, _ = _run("selfrecruit", _row("selfrecruit", "dressup"), http)
    assert len(jobs) == 25 and len([u for u, *_ in http.calls if u.count("-1111-4111-8111-111111111111") and "vacancies" not in u]) == 25


# --------------------------------------------------------------------------- Enlizt
def test_enlizt_happy_path_and_link_filtering():
    root = ('<a href="/vagas/full-stack-280426">Full</a><a href="/vagas/full-stack-280426/apply">Apply</a>'
            '<a href="https://evil.example/vagas/x">x</a><a href="http://t.enlizt.me/vagas/plain">p</a><a href="/sobre">About</a>'
            '<a href="/vagas/analista-090426">Analista</a><a href="/vagas/broken">Broken</a>')
    http = _stub({
        "/vagas/full-stack-280426": (200, _ld_page(title="Desenvolvedor Full-Stack", identifier={"value": "07f9c460"})),
        "/vagas/analista-090426": (200, _ld_page(title="Analista", identifier={}, jobLocationType="TELECOMMUTE", hiringOrganization={})),
        "/vagas/broken": (200, "<html><script type=\"application/ld+json\">{not json</script></html>"),
        "t.enlizt.me/": (200, root),
    })
    jobs, _ = _run("enlizt", _row("enlizt", "t", name="Tributo Devido"), http)
    assert [j["title"] for j in jobs] == ["Desenvolvedor Full-Stack", "Analista"]
    first, second = jobs
    assert first["company"] == "Real Employer Ltd" and first["extra"]["job_id"] == "07f9c460" and first["location"] == "Lisboa, PT"
    assert first["posted"] == "2026-05-25" and first["description"] == "Build" and first["remote"] is None
    assert second["company"] == "Tributo Devido" and second["extra"]["job_id"] == "analista-090426" and second["remote"] is True
    assert _hosts(http) == {"t.enlizt.me"} and _job_hosts(jobs) == {"t.enlizt.me"}
    assert not any(u.endswith("/apply") or "/sobre" in u for u, *_ in http.calls)


def test_enlizt_hostile_markup_refusal_and_dead_listing():
    page = _ld_page(description="<p>Safe</p><script>steal()</script><style>x{}</style>" + "<div>" * 5000, title="<b>Eng</b>")
    http = _stub({"/vagas/a": (200, page), "t.enlizt.me/": (200, '<a href="/vagas/a">A</a>' + '<a href="/vagas/b">' * 3000)})
    t0 = time.time()
    jobs, _ = _run("enlizt", _row("enlizt", "t"), http)
    assert time.time() - t0 < 10 and jobs and "steal" not in jobs[0]["description"] and "Safe" in jobs[0]["description"]
    http = _stub({"/vagas/a": (429, None), "/vagas/b": (200, _ld_page()), "t.enlizt.me/": (200, '<a href="/vagas/a">A</a><a href="/vagas/b">B</a>')})
    assert _run("enlizt", _row("enlizt", "t"), http)[0] == [] and not any("/vagas/b" in u for u, *_ in http.calls)
    assert _run("enlizt", _row("enlizt", "t"), _stub({"t.enlizt.me/": (500, None)}))[0] == []


# --------------------------------------------------------------------------- Hurma
def _hu(page_rows, last=None):
    return 200, {"data": page_rows, "meta": {"current_page": 1, "last_page": last} if last else {}}


def _hu_item(n, name=None, host="s.hurma.work", **kw):
    return {"name": name or f"#{n} Role {n}", "public_url": f"https://{host}/public-vacancies/{n}", "residence": "Kyiv",
            "work_types": "Full-time, Remote work", **kw}


def test_hurma_happy_path_paging_headers_and_description():
    detail = {"data": {"name": "#406 Senior Designer", "residence": "remote", "work_types": "Contract", "description": "<p>Lead</p>",
                       "demand": "<p>5 years</p>", "responsibility": "", "working_conditions": "<p>12 days</p>",
                       "addition": "<script>x()</script><p>Extra</p>"}}
    http = _stub({"page=1&": (200, {"data": [_hu_item(406), _hu_item(407)], "meta": {"last_page": 2}}),
                  "page=2&": (200, {"data": [_hu_item(408), _hu_item(406)], "meta": {"last_page": 2}}),
                  "/public-vacancies/406": (200, detail), "/public-vacancies/": (404, None)})
    jobs, _ = _run("hurma", _row("hurma", "s", name="ScrumLaunch"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["406", "407", "408"]
    j = jobs[0]
    assert j["title"] == "Senior Designer" and j["url"] == "https://s.hurma.work/public-vacancies/406" and j["location"] == "remote"
    assert j["extra"]["employment_type"] == "contract" and j["remote"] is True
    assert "Requirements" in j["description"] and "12 days" in j["description"] and "x()" not in j["description"]
    assert jobs[1]["title"] == "Role 407" and jobs[1]["extra"]["employment_type"] == "full_time" and jobs[1]["description"] == ""
    assert all(h.get("X-Requested-With") == "XMLHttpRequest" for _, h, _ in http.calls)
    assert _hosts(http) == {"s.hurma.work"} and _job_hosts(jobs) == {"s.hurma.work"}


def test_hurma_bad_items_foreign_urls_and_odd_pages():
    rows = [_hu_item(1), _hu_item(2, host="evil.example"), {"name": "no url"}, "junk", None,
            {"name": "x", "public_url": "http://s.hurma.work/public-vacancies/3"}, _hu_item(4, name=None),
            {"name": "x", "public_url": "https://s.hurma.work/public-vacancies/"}]
    http = _stub({"/api/v1/public-vacancies?": _hu(rows), "/public-vacancies/": (404, None)})
    jobs, _ = _run("hurma", _row("hurma", "s", title_ok=lambda t: False), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["1", "4"] and _job_hosts(jobs) == {"s.hurma.work"} and _hosts(http) == {"s.hurma.work"}
    assert _run("hurma", _row("hurma", "s"), _stub({"public-vacancies?": (403, None)}))[0] == []
    http = _stub({"page=1&": _hu([_hu_item(1)], 3), "page=2&": (500, None)})
    assert len(_run("hurma", _row("hurma", "s", title_ok=lambda t: False), http)[0]) == 1  # a later page failing keeps the board


def test_hurma_title_filter_cap_and_refusal():
    http = _stub({"/api/v1/public-vacancies?": _hu([_hu_item(i) for i in range(1, 41)]),
                  "/public-vacancies/": (200, {"data": {"description": "<p>D</p>"}})})
    jobs, _ = _run("hurma", _row("hurma", "s", title_ok=lambda t: "Role" in t), http)
    assert len(jobs) == 40 and len([u for u, *_ in http.calls if "?" not in u]) == 25 and jobs[24]["description"] == "D"
    assert jobs[25]["description"] == ""
    http = _stub({"/api/v1/public-vacancies?": _hu([_hu_item(1), _hu_item(2)]), "/public-vacancies/": (429, None)})
    assert len(_run("hurma", _row("hurma", "s"), http)[0]) == 2 and len([u for u, *_ in http.calls if "?" not in u]) == 1


# --------------------------------------------------------------------------- Compleo
def _sitemap(*locs):
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{loc}</loc></url>" for loc in locs) + "</urlset>")


def test_compleo_takes_only_this_tenants_postings():
    mine, other, neigh = ("https://jobs.compleo.app/wafx/jobdetail/HJ06276A", "https://jobs.compleo.app/agiliza/jobdetail/BA00898A",
                          "https://jobs.compleo.app/wafxpro/jobdetail/CC00111B")
    xml = _sitemap(mine, other, neigh, "https://jobs.compleo.app/wafx/home", "http://jobs.compleo.app/wafx/jobdetail/PLAIN1",
                   "https://evil.example/wafx/jobdetail/EVIL1", "https://jobs.compleo.app/wafx/jobdetail/HJ06276A")
    http = _stub({"/sitemap.xml": (200, xml), "/wafx/jobdetail/HJ06276A": (200, _ld_page(title="Java Tech Lead"))})
    jobs, _ = _run("compleo", _row("compleo", "wafx", name="WA FENIX"), http)
    assert len(jobs) == 1 and jobs[0]["url"] == mine and jobs[0]["extra"]["job_id"] == "HJ06276A"
    assert jobs[0]["company"] == "Real Employer Ltd" and jobs[0]["posted"] == "2026-05-25" and jobs[0]["title"] == "Java Tech Lead"
    assert _hosts(http) == {"jobs.compleo.app"} and _job_hosts(jobs) == {"jobs.compleo.app"}
    assert [u for u, *_ in http.calls] == ["https://jobs.compleo.app/sitemap.xml", mine]
    assert http.calls[0][2] == 10_000_000  # a platform-wide sitemap may exceed the 2 MB page default


def test_compleo_dotted_tenant_bad_page_refusal_and_xml_attacks():
    a, b, c = (f"https://jobs.compleo.app/huntrh.huntit/jobdetail/{x}" for x in ("AA1", "BB2", "CC3"))
    http = _stub({"/sitemap.xml": (200, _sitemap(a, b, c)), "/AA1": (200, "<html>nothing</html>"),
                  "/BB2": (200, _ld_page(hiringOrganization={})), "/CC3": (200, _ld_page(title="Third"))})
    jobs, _ = _run("compleo", _row("compleo", "huntrh.huntit"), http)
    assert [j["title"] for j in jobs] == ["Third"]  # no JobPosting, and no employer, drop only their own posting
    http = _stub({"/sitemap.xml": (200, _sitemap(a, b, c)), "/AA1": (403, None), "/BB2": (200, _ld_page())})
    assert _run("compleo", _row("compleo", "huntrh.huntit"), http)[0] == [] and not any("BB2" in u for u, *_ in http.calls)
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><urlset><url><loc>&b;</loc></url></urlset>'
    assert _run("compleo", _row("compleo", "wafx"), _stub({"/sitemap.xml": (200, bomb)}))[0] == []
    assert _run("compleo", _row("compleo", "wafx"), _stub({"/sitemap.xml": (403, None)}))[0] == []


# --------------------------------------------------------------------------- Spark
def _sp_item(i, name="Business Development Assistant", status=20, **fields):
    return {"id": i, "name": name, "vacancyStatusId": status, "openDate": "2026-07-14T00:00:00+00:00",
            "fields": {"50": "Yerevan", "60": "Permanent", "120": "<p>Build &amp; grow.</p><script>x()</script>", "130": "Onsite", **fields}}


def test_spark_happy_path_header_filter_and_mapping():
    items = [_sp_item(203, "  Business  Development Assistant  "), _sp_item(9, "Old Role", status=30), _sp_item(5, "Remote Dev", **{"130": "Remote"})]
    http = _stub({"/api/jobOpenings": (200, {"pageCount": 1, "items": items})})
    jobs, _ = _run("spark", _row("spark", "volo", name="VOLO"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["203", "5"]
    assert jobs[0]["url"] == "https://volo.spark.work/career/job/203/Business-Development-Assistant"
    assert jobs[0]["title"] == "Business Development Assistant" and jobs[0]["posted"] == "2026-07-14"
    assert jobs[0]["extra"]["work_mode"] == "onsite" and jobs[0]["remote"] is None and jobs[1]["remote"] is True
    assert "Build & grow" in jobs[0]["description"] and "x()" not in jobs[0]["description"]
    assert _hosts(http) == {"hub.spark.work"} and _job_hosts(jobs) == {"volo.spark.work"}
    assert all(h.get("X-workspace") == "volo" for _, h, _ in http.calls)


def test_spark_paging_bad_items_and_refusal():
    page1 = [_sp_item(i, f"Role {i}") for i in range(1, 101)]
    page2 = [_sp_item(101, "Last"), _sp_item(1, "Dup"), {"id": "x"}, "junk", {"id": 102, "name": "", "vacancyStatusId": 20},
             {"id": 103, "name": "Odd", "vacancyStatusId": 20, "fields": {"1": None, "2": 7, "3": "<b>ok</b>"}}, {"id": True, "name": "Bool"}]
    http = _stub({"pageNumber=1&": (200, {"pageCount": 2, "items": page1}), "pageNumber=2&": (200, {"pageCount": 2, "items": page2}),
                  "pageNumber=3&": (200, {"pageCount": 2, "items": [_sp_item(999)]})})
    jobs, _ = _run("spark", _row("spark", "volo"), http)
    assert len(jobs) == 102 and jobs[-1]["title"] == "Odd" and jobs[-1]["description"] == "ok"
    assert len(http.calls) == 2 and not any(j["extra"]["job_id"] == "999" for j in jobs)
    http = _stub({"pageNumber=1&": (200, {"items": page1}), "pageNumber=2&": (500, None)})
    assert len(_run("spark", _row("spark", "volo"), http)[0]) == 100  # a later page failing keeps what was read
    assert _run("spark", _row("spark", "volo"), _stub({"/api/jobOpenings": (403, None)}))[0] == []
