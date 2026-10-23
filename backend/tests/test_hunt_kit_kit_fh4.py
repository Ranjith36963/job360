"""Offline tests for hunt_kit/kit_fh4.py (Scalis, Senior Portal de Talentos, Profession.hu).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and written fresh.
Nothing touches the network. Gusto Hiring and JobLeads are not read (Chrome-TLS-only): their links are reported as unsupported.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load("kit_fh4_under_test", "kit_fh4.py")
hk = _load("hunt_kit_fh4_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("profession", "scalis", "senior")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, kwargs)."""
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append((url, kw.get("method", "GET"), kw.get("data")))
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, kw) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _run(name, row, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)  # a keyword the real http() lacks is a run-time TypeError
    return jobs, stats


def _row(ats, token, **kw):
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http):
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_wiring():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_fh4" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_fh4.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_fh4.py"' in served


def test_company_rows_exist_for_every_type():
    rows = hk.load_companies(KIT_DIR / "companies.json")
    assert {r["ats"] for r in rows} >= set(NAMES)


# ----------------------------------------------------------------------------- host rule
def test_host_rule_reads_each_link_and_reports_gusto_and_jobleads_as_unsupported():
    b = hk.board_from_job_url
    r = b("https://boldbusiness.scalis.ai/job/5f78d010-b21f-4d22-b037-e86865530f62")
    assert (r["kind"], r["ats"], r["token"]) == ("ats", "scalis", "boldbusiness")
    r = b("https://intelbras.portaldetalentos.senior.com.br/vacancy/v1")
    assert (r["kind"], r["ats"], r["token"]) == ("ats", "senior", "intelbras")
    r = b("https://www.profession.hu/allas/senior-java-fejleszto-budapest-2990578")
    assert (r["kind"], r["ats"], r["token"]) == ("ats", "profession", "")
    assert b("https://profession.hu/allas/x-1")["ats"] == "profession"
    assert b("https://jobs.gusto.com/postings/acme-0f0f0f0f-0f0f-0f0f-0f0f-0f0f0f0f0f0f")["kind"] == "unsupported"
    assert b("https://www.jobleads.com/job/123")["kind"] == "unsupported"
    assert b("https://evil.example/boldbusiness.scalis.ai/job/1")["kind"] == "other"
    assert b("https://scalis.ai.evil.example/job/1")["kind"] == "other"


# ----------------------------------------------------------------------------- Scalis
def _flight_page(results, rows=None, count=None):
    """An HTML page whose Next.js flight carries the initialData object and the given text rows ({id: text})."""
    flight = '27:{"initialData":' + json.dumps({"results": results, "count": count or len(results)}) + "}\n"
    for rid, text in (rows or {}).items():
        flight += f"{rid}:T{len(text.encode()):x},{text}\n"
    return "<html><body><script>self.__next_f.push([1," + json.dumps(flight).replace("</", "<\\/") + "])</script></body></html>"


def _sc(pid, title, **kw):
    return {"id": pid, "title": title, "company": "$5:2:props:company", "employment": "FULL_TIME", "workplace": "REMOTE",
            "locations": [{"city": "Bogota", "country": "CO"}], "createdAt": "2026-06-18T17:10:41.385Z", **kw}


_SC_LIST = "boldbusiness.scalis.ai/jobs?page="


def _sc_http(pages, **over):
    routes = {f"{_SC_LIST}{n}&": (200, p) if isinstance(p, str) else p for n, p in pages.items()}
    routes.update(over)
    return _stub(routes)


def test_scalis_happy_path_pages_to_the_empty_page_and_stays_on_the_tenant_host():
    p1 = _flight_page([
        _sc("aaaa-1", "Senior Backend Engineer", descriptionHtml="$28", description="$29", skills=["Go"]),
        _sc("aaaa-2", "Support Engineer", workplace="HYBRID", employment="TEMPORARY", descriptionHtml="<p>Inline body</p>",
            locations=[{"city": "Lima", "country": "PE"}, {"city": "Lima", "country": "PE"}]),
    ], {"28": "<p>Build things.</p><script>steal()</script>", "29": "Plain"})
    p2 = _flight_page([_sc("aaaa-3", "Data Analyst", workplace="ON_SITE", description="$28", descriptionHtml="", locations=[])],
                      {"28": "Analyze data."})
    http = _sc_http({1: p1, 2: p2, 3: _flight_page([])})
    jobs, stats = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert [j["title"] for j in jobs] == ["Senior Backend Engineer", "Support Engineer", "Data Analyst"]
    a, b, c = jobs
    assert a["description"] == "Build things." and "steal" not in a["description"]  # HTML row preferred, script dropped
    assert (a["company"], a["location"], a["posted"], a["remote"]) == ("Scalis Co", "Bogota, CO", "2026-06-18", True)
    assert a["url"] == "https://boldbusiness.scalis.ai/job/aaaa-1" and a["extra"]["work_mode"] == "remote"
    assert b["description"] == "Inline body" and b["location"] == "Lima, PE" and b["extra"]["employment_type"] == "contract"
    assert b["extra"]["work_mode"] == "hybrid" and b["remote"] is None
    assert c["description"] == "Analyze data." and c["extra"]["work_mode"] == "onsite" and c["location"] == ""
    assert [urlsplit(u).query for u, *_ in http.calls] == [f"page={n}&limit=10&sortBy=SORT_BEST_MATCH" for n in (1, 2, 3)]
    assert _hosts(http) == {"boldbusiness.scalis.ai"} and _job_hosts(jobs) == {"boldbusiness.scalis.ai"}
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_scalis_one_bad_posting_does_not_lose_the_board():
    p1 = _flight_page([_sc("", "No id"), _sc("aaaa-1", ""), "junk", None, _sc("aaaa-2", 12345),
                       _sc("aaaa-3", "Fine", locations="oops", skills=7, createdAt=[1]), _sc("aaaa-3", "Duplicate")])
    http = _sc_http({1: p1, 2: _flight_page([])})
    jobs, _ = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert [j["title"] for j in jobs] == ["12345", "Fine"]


def test_scalis_refusal_ends_the_board_and_a_repeated_page_does_not_loop():
    http = _sc_http({1: (403, None)})
    assert _run("scalis", _row("scalis", "boldbusiness"), http)[0] == []
    assert len(http.calls) == 1
    p1 = _flight_page([_sc("aaaa-1", "One")])
    http = _sc_http({1: p1, 2: (429, None)})
    jobs, stats = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert [j["title"] for j in jobs] == ["One"] and len(http.calls) == 2 and stats["ok"] == 1  # a later refusal keeps what it holds
    http = _sc_http({}, **{_SC_LIST: (200, p1)})  # a server that repeats the same page forever
    jobs, _ = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert [j["title"] for j in jobs] == ["One"] and len(http.calls) == 2
    http = _sc_http({1: "<html>no flight here</html>"})
    assert _run("scalis", _row("scalis", "boldbusiness"), http)[0] == [] and len(http.calls) == 1


def test_scalis_hostile_ids_and_tokens_never_leave_the_tenant_host():
    ids = ["../../evil", "x@evil.example/", "a b", "https://evil.example/x", "//evil.example", "q?z=1#f"]
    http = _sc_http({1: _flight_page([_sc(f"id{i}-{x}", f"T{i}") for i, x in enumerate(ids)]), 2: _flight_page([])})
    jobs, _ = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert len(jobs) == len(ids) and _job_hosts(jobs) == {"boldbusiness.scalis.ai"}
    assert all(urlsplit(j["url"]).scheme == "https" and urlsplit(j["url"]).path.startswith("/job/") for j in jobs)
    for token in ("evil.example/x", "a.b", "-x", "x-", "", "a b"):
        http = _sc_http({})
        assert _run("scalis", _row("scalis", token), http)[0] == [] and http.calls == []


def test_scalis_hostile_markup_is_cheap():
    body = "<div>" * 5000 + "<script>" * 3000 + "x" * 200_000
    page = _flight_page([_sc("aaaa-1", "Big", descriptionHtml="$28")], {"28": body})
    http = _sc_http({1: page, 2: _flight_page([])})
    t0 = time.perf_counter()
    jobs, _ = _run("scalis", _row("scalis", "boldbusiness"), http)
    assert len(jobs) == 1 and time.perf_counter() - t0 < 1.0


# ----------------------------------------------------------------------------- Senior
_SE = "careersmanagercandidate"


def _sv(vid, title, **kw):
    return {"vacancy": {"id": vid, "title": title, "localization": {"city": "Florianopolis", "province": "SC", "country": "Brasil"},
                        "jobModel": ["IN_PERSON"], "publication": {"startDate": "2026-06-10"}, **kw}}


def _se_http(pages, details, profile=(200, {"profileId": "GUID-1"}), **over):
    def search(url, kw):
        page = json.loads(kw["data"])["page"]
        return pages.get(page, (200, {"totalPages": 0, "contents": []}))

    def detail(url, kw):
        return details.get(json.loads(kw["data"])["id"], (404, None))

    routes = {"getProfileIdBySubdomain": profile, "searchVacancies": search, "findVacancyById": detail}
    routes.update(over)
    return _stub(routes)


def _body_of(http, frag):
    return [json.loads(d) for u, m, d in http.calls if frag in u and m == "POST"]


def test_senior_happy_path_paging_details_and_hosts():
    pages = {0: (200, {"totalPages": 2, "contents": [_sv("v1", "Backend Engineer"),
                                                      _sv("v2", "Remote Data Engineer", jobModel=["REMOTE"], localization={"country": "Brasil"})]}),
             1: (200, {"totalPages": 2, "contents": [_sv("v3", "Hybrid PM", jobModel=["HYBRID"])]})}
    details = {"v1": (200, {"vacancy": {"about": {"description": "<p>Build the backend.</p><script>alert(1)</script>"}}}),
               "v2": (200, {"vacancy": {"about": {"description": "<p>Crunch data.</p>"}}}),
               "v3": (200, {"vacancy": {"about": {"description": "<p>Own the roadmap.</p>"}}})}
    http = _se_http(pages, details)
    jobs, stats = _run("senior", _row("senior", "intelbras"), http)
    assert [j["title"] for j in jobs] == ["Backend Engineer", "Remote Data Engineer", "Hybrid PM"]
    a, b, c = jobs
    assert a["description"] == "Build the backend." and "alert" not in a["description"]
    assert (a["location"], a["posted"], a["remote"], a["extra"]["work_mode"]) == ("Florianopolis, SC, Brasil", "2026-06-10", None, "onsite")
    assert (b["location"], b["remote"], b["extra"]["work_mode"]) == ("Brasil", True, "remote") and c["extra"]["work_mode"] == "hybrid"
    assert a["url"] == "https://intelbras.portaldetalentos.senior.com.br/vacancy/v1" and a["company"] == "Senior Co"
    assert _job_hosts(jobs) == {"intelbras.portaldetalentos.senior.com.br"} and _hosts(http) == {"platform.senior.com.br"}
    assert all(m == "POST" for _u, m, _d in http.calls)
    assert _body_of(http, "getProfileIdBySubdomain") == [{"subdomain": "intelbras"}]
    assert [q["page"] for q in _body_of(http, "searchVacancies")] == [0, 1]
    assert _body_of(http, "searchVacancies")[0]["match"] == {"companies": [{"id": "GUID-1"}], "localizations": []}
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_senior_one_bad_posting_and_a_missing_detail_do_not_lose_the_board():
    pages = {0: (200, {"totalPages": 1, "contents": [_sv("", "No id"), _sv("v1", ""), "junk", {"vacancy": "x"}, _sv("v2", "Gone"),
                                                      _sv("v3", "Fine", localization="oops", jobModel="REMOTE", publication=[1]),
                                                      _sv("v3", "Duplicate")]})}
    details = {"v3": (200, {"vacancy": {"about": {"description": "Body"}}})}  # v2 answers 404: listed without a body
    http = _se_http(pages, details)
    jobs, _ = _run("senior", _row("senior", "intelbras"), http)
    assert [(j["title"], j["description"]) for j in jobs] == [("Gone", ""), ("Fine", "Body")]


def test_senior_refusal_ends_detail_requests_but_keeps_the_listing():
    pages = {0: (200, {"totalPages": 1, "contents": [_sv(f"v{i}", f"Job {i}") for i in range(5)]})}
    http = _se_http(pages, {"v0": (200, {"vacancy": {"about": {"description": "Zero"}}}), "v1": (429, None)})
    jobs, _ = _run("senior", _row("senior", "intelbras"), http)
    assert len(jobs) == 5 and jobs[0]["description"] == "Zero" and all(j["description"] == "" for j in jobs[1:])
    assert len(_body_of(http, "findVacancyById")) == 2  # v0, then the refused v1 ends the run


def test_senior_title_filter_and_cap_limit_detail_requests():
    pages = {0: (200, {"totalPages": 1, "contents": [_sv(f"v{i}", f"Nurse {i}" if i % 2 else f"Engineer {i}") for i in range(80)]})}
    http = _se_http(pages, {})
    row = _row("senior", "intelbras", title_ok=lambda t: t.startswith("Engineer"))
    jobs, _ = _run("senior", row, http)
    assert len(jobs) == 80 and len(_body_of(http, "findVacancyById")) == kb.DETAIL_CAP


def test_senior_unknown_tenant_or_refused_search_is_a_failed_board_and_paging_stops():
    for profile in ((404, None), (200, {"profileId": ""}), (200, {"profileId": 5}), (200, [])):
        http = _se_http({}, {}, profile=profile)
        jobs, stats = _run("senior", _row("senior", "nobody"), http)
        assert jobs == [] and stats.get("ok", 0) == 0 and stats["tried"] == 1 and len(http.calls) == 1
    http = _se_http({0: (403, None)}, {})
    assert _run("senior", _row("senior", "intelbras"), http)[0] == []
    # a repeated page (the server ignores page) must not loop to the cap
    page = (200, {"totalPages": 99, "contents": [_sv("v1", "One")]})
    http = _se_http({n: page for n in range(20)}, {})
    jobs, _ = _run("senior", _row("senior", "intelbras"), http)
    assert len(jobs) == 1 and len(_body_of(http, "searchVacancies")) == 2
    for token in ("evil.example/x", "a.b", "-x", "", "a b"):
        http = _se_http({}, {})
        assert _run("senior", _row("senior", token), http)[0] == [] and http.calls == []


def test_senior_hostile_ids_never_leave_the_tenant_host():
    ids = ["../../evil", "x@evil.example/", "https://evil.example/x", "//evil.example", "q?z=1#f"]
    pages = {0: (200, {"totalPages": 1, "contents": [_sv(x, f"T{i}") for i, x in enumerate(ids)]})}
    jobs, _ = _run("senior", _row("senior", "intelbras"), _se_http(pages, {}))
    assert len(jobs) == len(ids) and _job_hosts(jobs) == {"intelbras.portaldetalentos.senior.com.br"}


# ----------------------------------------------------------------------------- Profession.hu
_PF = "https://www.profession.hu"
_PF_INDEX_XML = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 f"<sitemap><loc>{_PF}/sitemap-listings-education-hu.xml</loc></sitemap>"
                 f"<sitemap><loc>{_PF}/sitemap-listings-itdev-hu.xml</loc></sitemap>"
                 "<sitemap><loc>https://evil.example/sitemap-listings-itops-hu.xml</loc></sitemap>"
                 f"<sitemap><loc>{_PF}/sitemap-companies-hu.xml</loc></sitemap></sitemapindex>")


def _pf_sitemap(*locs):
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc><lastmod>2026-09-02</lastmod></url>" for u in locs) + "</urlset>")


def _pf_page(title="Senior Java fejleszto", company="Acme Kft.", line="Hibrid • 1087 Budapest, Kerepesi út 21.", kind="Teljes munkaidő, Alkalmazotti jogviszony",
             sections=None, ld=True):
    node = {"@type": "JobPosting", "title": title, "datePosted": "2026-09-02", "employmentType": kind,
            "description": "<p>flattened</p>", "hiringOrganization": {"@type": "Organization", "name": company}}
    sec = sections if sections is not None else {"tasks": "<ul><li>Fejlesztes</li><li>Tesztek</li></ul>",
                                                 "requirements": "<p>5 ev <b>Java</b></p><script>steal()</script>",
                                                 "c_info": "<p>Standing blurb</p>", "contact_info": "<p>mail me</p>"}
    ldjson = ('<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "Organization"}, node]}).replace("</", "<\\/")
              + "</script>") if ld else ""
    return ("<html><head>" + ldjson + "</head><body>" + (f'<div class="address-data">{line}</div>' if line is not None else "")
            + "".join(f'<div class="box-container"><h2>H {k}</h2><div id="{k}">{v}</div></div>' for k, v in sec.items()) + "</body></html>")


def _pf_http(posts, board_locs=None, index=(200, _PF_INDEX_XML), **over):
    locs = board_locs if board_locs is not None else list(posts)
    routes = {"sitemap-listings-index-hu.xml": index, "sitemap-listings-itdev-hu.xml": (200, _pf_sitemap(*locs))}
    routes.update({k: v if isinstance(v, tuple) else (200, v) for k, v in posts.items()})
    routes.update(over)
    return _stub(routes)


_U1 = f"{_PF}/allas/senior-java-fejleszto-budapest-2990578"
_U2 = f"{_PF}/allas/devops-mernok-2990579"


def test_profession_happy_path_maps_the_page_and_stays_on_the_host():
    http = _pf_http({_U1: _pf_page(), _U2: _pf_page(title="DevOps", company="Beta Zrt.", line="Távmunka / Remote • Magyarország",
                                                     kind="Részmunkaidő")})
    jobs, stats = _run("profession", _row("profession", "itdev"), http)
    assert [j["title"] for j in jobs] == ["Senior Java fejleszto", "DevOps"]
    a, b = jobs
    assert (a["company"], a["url"], a["posted"], a["location"], a["remote"]) == ("Acme Kft.", _U1, "2026-09-02", "Budapest, Kerepesi út .", None)
    assert a["extra"] == {"job_id": "2990578", "work_mode": "hybrid", "employment_type": "full_time"}
    assert "Fejlesztes" in a["description"] and "5 ev Java" in a["description"] and "steal" not in a["description"]
    assert "flattened" not in a["description"] and "Standing blurb" not in a["description"] and "mail me" not in a["description"]
    assert (b["company"], b["location"], b["remote"], b["extra"]["work_mode"], b["extra"]["employment_type"]) == (
        "Beta Zrt.", "Magyarország", True, "remote", "part_time")
    assert _hosts(http) == {"www.profession.hu"} and _job_hosts(jobs) == {"www.profession.hu"}
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_profession_only_the_it_categories_are_read_and_a_missing_one_is_a_failed_board():
    for board in ("education", "itops-x", ""):
        http = _pf_http({})
        assert _run("profession", _row("profession", board), http)[0] == [] and http.calls == []
    http = _pf_http({}, index=(200, _PF_INDEX_XML))
    assert _run("profession", _row("profession", "itops"), http)[0] == []  # the only itops sitemap listed is off-host: never fetched
    assert _hosts(http) == {"www.profession.hu"} and len(http.calls) == 1
    assert _run("profession", _row("profession", "itdev"), _pf_http({}, index=(403, None)))[0] == []


def test_profession_filters_the_sitemap_and_one_bad_posting_does_not_lose_the_board():
    locs = [_U1, _U1, f"{_PF}/allas/x-1/", "https://evil.example/allas/offhost-1", "http://www.profession.hu/allas/plain-http-2",
            f"{_PF}/cegek/acme-3", f"{_PF}/allas/no-id", "https://user@www.profession.hu/allas/userinfo-4", f"{_PF}/allas/broken-5",
            f"{_PF}/allas/taken-down-6", f"{_PF}/allas/no-body-7", f"{_PF}/allas/no-company-8", _U2]
    posts = {_U1: _pf_page(), f"{_PF}/allas/x-1/": (404, None), f"{_PF}/allas/broken-5": '<script type="application/ld+json">{nope</script>',
             f"{_PF}/allas/taken-down-6": "<html><h1>Category listing</h1></html>",
             f"{_PF}/allas/no-body-7": _pf_page(sections={"c_info": "<p>blurb</p>"}),
             f"{_PF}/allas/no-company-8": _pf_page(company=""), _U2: _pf_page(title="Second")}
    http = _pf_http(posts, board_locs=locs)
    jobs, _ = _run("profession", _row("profession", "itdev"), http)
    assert [j["title"] for j in jobs] == ["Senior Java fejleszto", "Second"]
    fetched = [u for u, *_ in http.calls if "/allas/" in u]
    assert fetched == [_U1, f"{_PF}/allas/x-1/", f"{_PF}/allas/broken-5", f"{_PF}/allas/taken-down-6", f"{_PF}/allas/no-body-7",
                       f"{_PF}/allas/no-company-8", _U2]  # off-host, plain-http, userinfo, non-posting and repeated links are never fetched
    assert _hosts(http) == {"www.profession.hu"}


def test_profession_refusal_on_a_posting_page_ends_the_detail_requests():
    posts = {_U1: _pf_page(), _U2: (429, None), f"{_PF}/allas/third-2990580": _pf_page(title="Third")}
    http = _pf_http(posts, board_locs=[_U1, _U2, f"{_PF}/allas/third-2990580"])
    jobs, _ = _run("profession", _row("profession", "itdev"), http)
    assert [j["title"] for j in jobs] == ["Senior Java fejleszto"]
    assert [u for u, *_ in http.calls if "/allas/" in u] == [_U1, _U2]


def test_profession_title_filter_and_cap_limit_page_requests():
    locs = [f"{_PF}/allas/{'nurse' if i % 2 else 'engineer'}-job-{1000 + i}" for i in range(120)]
    http = _pf_http({}, board_locs=locs)
    row = _row("profession", "itdev", title_ok=lambda t: "engineer" in t)
    _run("profession", row, http)
    assert len([u for u, *_ in http.calls if "/allas/" in u]) == kb.DETAIL_CAP


def test_profession_sitemap_with_a_doctype_or_entities_returns_no_rows():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    http = _pf_http({}, **{"sitemap-listings-itdev-hu.xml": (200, bomb)})
    assert _run("profession", _row("profession", "itdev"), http)[0] == []
    http = _pf_http({}, index=(200, '<!DOCTYPE x [<!ENTITY a "b">]><sitemapindex/>'))
    assert _run("profession", _row("profession", "itdev"), http)[0] == [] and len(http.calls) == 1


def test_profession_address_line_cases():
    p = kb._pf_place
    assert p("Hibrid • 1087 Budapest, Kerepesi út 21.") == ("hybrid", "Budapest, Kerepesi út .")
    assert p("Távmunka / Remote") == ("remote", "")
    assert p("Országos lefedettség • Magyarország") == ("", "Magyarország")
    assert p("1051 Budapest, Nador u. 5") == ("onsite", "Budapest, Nador u.")
    assert p("") == ("onsite", "")
    assert kb._pf_kind("Teljes munkaidő, Részmunkaidő") == "" and kb._pf_kind("Diákmunka") == "internship"
    assert kb._pf_kind("Alkalmazotti jogviszony") == "" and kb._pf_kind(None) == ""


def test_profession_hostile_markup_is_cheap_and_linear():
    def timed(n):
        page = _pf_page(sections={"tasks": "<p>Real body</p>" + "<div>" * n + "<p>deep</p>" + "<li>" * n + "x" * 100_000})
        http = _pf_http({_U1: page})
        t0 = time.perf_counter()
        jobs, _ = _run("profession", _row("profession", "itdev"), http)
        assert len(jobs) == 1 and jobs[0]["description"].startswith("Real body")
        return time.perf_counter() - t0

    times = [timed(n) for n in (1500, 3000, 6000)]  # 1x, 2x, 4x
    assert max(times) < 1.0, times
