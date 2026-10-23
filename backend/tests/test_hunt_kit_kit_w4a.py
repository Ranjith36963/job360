"""Offline tests for hunt_kit/kit_w4a.py (Jobindex.dk, Jobbank.dk, hh.ru, Habr Career, Getmatch).

Fixtures are shaped like the data in freehire's internal/ingest/sources/{hhru,habrcareer,getmatch}_test.go and the ajs jobindex /
jobbank skills (MIT) and written fresh. Nothing touches the network.
"""
from __future__ import annotations

import html
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


kb = _load("kit_w4a_under_test", "kit_w4a.py")
hk = _load("hunt_kit_w4a_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("getmatch", "habrcareer", "hhru", "jobbankdk", "jobindex")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append((url, kw.get("method", "GET"), kw.get("headers") or {}))
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, kw.get("headers") or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _run(name, row, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)  # a keyword the real http() lacks is a run-time TypeError
    return jobs, stats


def _row(ats, token="all", **kw):
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http):
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_wiring():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_w4a" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_w4a.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_w4a.py"' in served
    assert {r["ats"] for r in hk.load_companies(KIT_DIR / "companies.json")} >= set(NAMES)


def test_host_rule_reads_each_link():
    b = hk.board_from_job_url
    for url, ats in (("https://www.jobindex.dk/jobannonce/h1647303", "jobindex"), ("https://jobbank.dk/job/12345/acme/dev", "jobbankdk"),
                     ("https://hh.ru/vacancy/123456", "hhru"), ("https://career.habr.com/vacancies/1000012345", "habrcareer"),
                     ("https://getmatch.ru/vacancies/777-dev", "getmatch")):
        r = b(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    assert b("https://evil.example/hh.ru/vacancy/1")["kind"] == "other"
    assert b("https://hh.ru.evil.example/vacancy/1")["kind"] == "other"
    assert b("https://www.jobbank.gc.ca/jobsearch/jobposting/1")["ats"] != "jobbankdk"


# ----------------------------------------------------------------------------- Jobindex
def _stash(results, hitcount=None):
    blob = {"jobsearch/result_app": {"storeData": {"searchResponse": {"hitcount": hitcount if hitcount is not None else len(results),
                                                                       "results": results}}}}
    return "<html><script>var Stash = " + json.dumps(blob).replace("</", "<\\/") + ";\nvar Other = {};</script></html>"


def _ji(tid, title="Python Developer", company="Acme A/S", **kw):
    return {"tid": tid, "headline": title, "company": {"name": company}, "area": "Aarhus", "firstdate": "2026-10-20", **kw}


def test_jobindex_happy_path_and_paging():
    page1 = _stash([_ji("h1000001"), _ji("h1000002", company="", companytext="Beta ApS", area="", geojson={"features": [{"properties": {"title": "Odense"}}]})], hitcount=3)
    page2 = _stash([_ji("h1000002"), _ji("r1000003", title="Data Engineer")], hitcount=3)
    http = _stub({"page=1&": (200, page1), "page=2&": (200, page2), "page=3&": (200, _stash([]))})
    jobs, stats = _run("jobindex", _row("jobindex", "python aarhus"), http)
    assert [j["url"] for j in jobs] == ["https://www.jobindex.dk/jobannonce/h1000001", "https://www.jobindex.dk/jobannonce/h1000002",
                                         "https://www.jobindex.dk/jobannonce/r1000003"]
    assert [j["company"] for j in jobs] == ["Acme A/S", "Beta ApS", "Acme A/S"]
    assert jobs[0]["location"] == "Aarhus" and jobs[1]["location"] == "Odense" and jobs[0]["posted"] == "2026-10-20"
    assert jobs[0]["source"] == "jobindex" and stats["ok"] == 1 and len(http.calls) == 2  # hitcount reached: no third request
    assert "q=python+aarhus" in http.calls[0][0] and _hosts(http) == {"www.jobindex.dk"} and _job_hosts(jobs) == {"www.jobindex.dk"}


def test_jobindex_bad_posting_refusal_and_empty():
    bad = [_ji("h1"), None, 5, {"tid": "h2"}, _ji("../x"), _ji("h3", title=""), _ji("h4", company="", companytext=""), _ji("h5", firstdate="soon")]
    jobs, _ = _run("jobindex", _row("jobindex", "python"), _stub({"page=1&": (200, _stash(bad, hitcount=99)), "page=2&": (200, _stash([]))}))
    assert [j["url"].rsplit("/", 1)[-1] for j in jobs] == ["h1", "h5"] and jobs[1]["posted"] == ""
    http = _stub({"page=1&": (200, _stash([_ji("h1")], hitcount=99)), "page=2&": (429, None)})
    jobs, stats = _run("jobindex", _row("jobindex", "python"), http)
    assert len(jobs) == 1 and stats["ok"] == 1 and len(http.calls) == 2  # a refusal ends the board, what was gathered stays
    jobs, stats = _run("jobindex", _row("jobindex", "python"), _stub({"jobsoegning": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("jobindex", _row("jobindex", "python"), _stub({"jobsoegning": (200, _stash([]))}))
    assert jobs == [] and stats["ok"] == 1
    assert _run("jobindex", _row("jobindex", "  "), _stub({}))[1].get("ok", 0) == 0
    assert _run("jobindex", _row("jobindex", "a\nb"), _stub({}))[1].get("ok", 0) == 0


def test_jobindex_unparseable_or_hostile_stash():
    for page in ("<html>no stash</html>", "var Stash = {not json", "var Stash = " + "[" * 100000 + "]" * 100000, "var Stash = []"):
        assert _run("jobindex", _row("jobindex", "python"), _stub({"jobsoegning": (200, page)}))[1].get("ok", 0) == 0
    t0 = time.monotonic()
    deep = "var Stash = " + '{"a":' * 50000 + "1" + "}" * 50000
    _run("jobindex", _row("jobindex", "python"), _stub({"jobsoegning": (200, deep)}))
    assert time.monotonic() - t0 < 2


def test_jobindex_markup_in_text_is_kept_out_of_urls():
    jobs, _ = _run("jobindex", _row("jobindex", "python"), _stub({"jobsoegning": (200, _stash([_ji("h9", title="<img src=x onerror=1>Dev &amp; Ops", url="https://evil.example/x")]))}))
    assert jobs[0]["title"] == "<img src=x onerror=1>Dev & Ops" and _job_hosts(jobs) == {"www.jobindex.dk"}


# ----------------------------------------------------------------------------- Jobbank.dk
def _rss(*items):
    body = "".join(f"<item><title>{t}</title><description>{d}</description><link>{u}</link><pubDate>{p}</pubDate></item>" for t, d, u, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Jobbank</title><link>https://jobbank.dk/job/</link>{body}</channel></rss>'


_D = "Fuldtidsjob hos Novo, Nordisk A/S, København (Ansøgningsfrist: 01.12.2026)"


def test_jobbank_happy_path():
    xml = _rss(("Data Scientist", "Fuldtidsjob, Graduate/trainee hos Acme A/S, Aarhus (Ansøgningsfrist: løbende)", "https://jobbank.dk/job/1001/acme/data-scientist", "Tue, 20 Oct 2026 10:00:00 +0000"),
               ("Analyst", _D, "https://www.jobbank.dk/job/1002/novo/analyst", ""))
    http = _stub({"/job/rss": (200, xml)})
    jobs, stats = _run("jobbankdk", _row("jobbankdk", "data scientist"), http)
    assert [j["url"] for j in jobs] == ["https://jobbank.dk/job/1001/acme/data-scientist", "https://www.jobbank.dk/job/1002/novo/analyst"]
    assert [j["company"] for j in jobs] == ["Acme A/S", "Novo"] and [j["location"] for j in jobs] == ["Aarhus", "Nordisk A/S, København"]
    assert jobs[0]["posted"] == "2026-10-20" and jobs[1]["posted"] == "" and jobs[0]["extra"]["employment_type"] == "Fuldtidsjob, Graduate/trainee"
    assert http.calls[0][0] == "https://jobbank.dk/job/rss?key=data+scientist" and _hosts(http) == {"jobbank.dk"} and stats["ok"] == 1


def test_jobbank_bad_items_off_host_and_cloudflare():
    xml = _rss(("Ok", _D, "https://jobbank.dk/job/1/a/b", ""), ("Off host", _D, "https://evil.example/job/2/a/b", ""),
               ("Plain http", _D, "http://jobbank.dk/job/3/a/b", ""), ("Lookalike", _D, "https://jobbank.dk.evil.example/job/4/a/b", ""),
               ("Userinfo", _D, "https://jobbank.dk@evil.example/job/5/a/b", ""), ("No id", _D, "https://jobbank.dk/job/", ""),
               ("No company", "Fuldtidsjob", "https://jobbank.dk/job/6/a/b", ""), ("", _D, "https://jobbank.dk/job/7/a/b", ""),
               ("Dup", _D, "https://jobbank.dk/job/1/a/b", ""), ("Later", _D, "https://jobbank.dk/job/8/a/b", "not a date"))
    jobs, _ = _run("jobbankdk", _row("jobbankdk", "x"), _stub({"/job/rss": (200, xml)}))
    assert [j["url"] for j in jobs] == ["https://jobbank.dk/job/1/a/b", "https://jobbank.dk/job/8/a/b"] and _job_hosts(jobs) == {"jobbank.dk"}
    http = _stub({"/job/rss": (403, "<html>Just a moment...</html>")})
    jobs, stats = _run("jobbankdk", _row("jobbankdk", "x"), http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1  # the challenge ends the board, no retry elsewhere


def test_jobbank_hostile_xml():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
    assert _run("jobbankdk", _row("jobbankdk", "x"), _stub({"/job/rss": (200, bomb)}))[0] == []
    assert _run("jobbankdk", _row("jobbankdk", "x"), _stub({"/job/rss": (200, "<rss><item>")}))[0] == []


# ----------------------------------------------------------------------------- hh.ru
def _hhv(vid, name="Python-разработчик", company="Acme", **kw):
    return {"vacancyId": vid, "name": name, "@isAdv": False, "area": {"name": "Москва"}, "company": {"visibleName": company},
            "compensation": {"from": 100000, "to": 200000, "currencyCode": "RUR", "gross": True},
            "employment": {"@type": "FULL"}, "workFormats": [{"workFormatsElement": ["REMOTE"]}],
            "publicationTime": {"$": "2026-10-20T12:00:00.000+03:00"}, "links": {"desktop": "https://evil.example/vacancy/1"}, **kw}


def _hhpage(*vs, escape=True):
    blob = json.dumps({"vacancySearchResult": {"vacancies": list(vs)}}, ensure_ascii=False)
    return f'<html><body><template id="HH-Lux-InitialState">{html.escape(blob) if escape else blob}</template></body></html>'


def test_hh_happy_path_paging_and_fields():
    p0 = _hhpage(_hhv(1001), _hhv(1002, company="HeadHunter::Analytics/Data Science", compensation={"from": None, "to": 90000, "currencyCode": "RUR", "gross": False},
                                    workFormats=[{"workFormatsElement": ["HYBRID", "ON_SITE"]}], employment={"@type": "PART"}))
    p1 = _hhpage(_hhv(1002), _hhv(1003, compensation={"noCompensation": {}}, workFormats=[]), _hhv(1004, **{"@isAdv": True}), _hhv(0), _hhv(1005, company=""),
                 _hhv(1006, name=""), "junk", _hhv(1007, compensation={"from": True}), escape=False)
    http = _stub({"page=0&": (200, p0), "page=1&": (200, p1), "page=2&": (200, _hhpage())})
    jobs, stats = _run("hhru", _row("hhru", "96"), http)
    assert [j["url"] for j in jobs] == [f"https://hh.ru/vacancy/{n}" for n in (1001, 1002, 1003, 1007)] and _job_hosts(jobs) == {"hh.ru"}
    assert [j["company"] for j in jobs][:2] == ["Acme", "HeadHunter"] and jobs[0]["location"] == "Москва" and jobs[0]["posted"] == "2026-10-20"
    assert jobs[0]["description"] == "Зарплата: 100000–200000 RUR (до вычета налогов)" and jobs[1]["description"] == "Зарплата: до 90000 RUR (на руки)"
    assert jobs[2]["description"] == "" and jobs[0]["remote"] is True and jobs[1]["remote"] is None
    assert jobs[0]["extra"]["work_mode"] == "remote" and jobs[1]["extra"] == {"job_id": "1002", "work_mode": "hybrid", "employment_type": "part_time"}
    assert len(http.calls) == 3 and _hosts(http) == {"hh.ru"} and stats["ok"] == 1
    assert "professional_role=96&items_on_page=100&page=0&order_by=publication_time&period=7" in http.calls[0][0]


def test_hh_refusal_page_cap_and_bad_state():
    http = _stub({"page=0&": (200, _hhpage(_hhv(1))), "page=1&": (403, None)})
    jobs, stats = _run("hhru", _row("hhru", "96"), http)
    assert len(jobs) == 1 and stats["ok"] == 1 and len(http.calls) == 2
    assert _run("hhru", _row("hhru", "96"), _stub({"page=0&": (429, None)}))[1].get("ok", 0) == 0
    for page in ("<html>no state</html>", '<template id="HH-Lux-InitialState">{not json</template>', '<template id="HH-Lux-InitialState">[]</template>',
                 '<template id="HH-Lux-InitialState">{"vacancySearchResult": {"vacancies": 5}}</template>'):
        assert _run("hhru", _row("hhru", "96"), _stub({"search/vacancy": (200, page)}))[1].get("ok", 0) == 0
    for role in ("", "9 6", "96&x=1", "abc", "1234567"):
        assert _run("hhru", _row("hhru", role), _stub({}))[1].get("ok", 0) == 0
    counter = {"n": 0}

    def endless(url, headers):
        counter["n"] += 1
        return 200, _hhpage(_hhv(counter["n"]))
    http = _stub({"search/vacancy": endless})
    assert len(_run("hhru", _row("hhru", "96"), http)[0]) == 20 and len(http.calls) == 20  # hh.ru's own 2000-result depth


def test_hh_unclosed_template_and_scaling():
    jobs, _ = _run("hhru", _row("hhru", "96"), _stub({"page=0&": (200, '<template id="HH-Lux-InitialState">' + html.escape(json.dumps({"vacancySearchResult": {"vacancies": [_hhv(5)]}}))), "page=1&": (404, None)}))
    assert len(jobs) == 1
    times = []
    for n in (500, 1000, 2000):
        page = _hhpage(*[_hhv(i) for i in range(1, n + 1)])
        t0 = time.monotonic()
        assert len(_run("hhru", _row("hhru", "96"), _stub({"page=0&": (200, page), "page=1&": (200, _hhpage(*[_hhv(i) for i in range(1, n + 1)]))}))[0]) == n
        times.append(time.monotonic() - t0)
    assert times[-1] < 2.5


# ----------------------------------------------------------------------------- Habr Career
def _hb(vid, title="Python-разработчик", company="Acme", **kw):
    return {"id": vid, "title": title, "remoteWork": True, "publishedDate": {"date": "2026-10-19T09:00:00+03:00"},
            "company": {"title": company}, "locations": [{"title": "Москва"}, {"title": "Москва"}, {"title": "Казань"}], **kw}


def _hbpage(*vs, total=2):
    return 200, {"list": list(vs), "meta": {"currentPage": 1, "totalPages": total}}


def _hbdetail(desc="<p>Описание <b>вакансии</b></p><script>x()</script>"):
    return 200, '<html><script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "T", "description": desc}).replace("</", "<\\/") + "</script></html>"


def test_habr_happy_path_paging_and_detail():
    http = _stub({"/api/frontend/vacancies?type=all&sort=date&page=1": _hbpage(_hb(1001), _hb(1002, remoteWork=False)),
                  "page=2": _hbpage(_hb(1002), _hb(1003, company="Beta")), "/vacancies/1001": _hbdetail(), "/vacancies/1003": (404, None)})
    jobs, stats = _run("habrcareer", _row("habrcareer"), http)
    assert [j["url"] for j in jobs] == [f"https://career.habr.com/vacancies/{n}" for n in (1001, 1002, 1003)] and _job_hosts(jobs) == {"career.habr.com"}
    assert jobs[0]["description"] == "Описание вакансии" and jobs[1]["description"] == "" and jobs[2]["company"] == "Beta"
    assert jobs[0]["location"] == "Москва, Казань" and jobs[0]["posted"] == "2026-10-19" and jobs[0]["remote"] is True and jobs[1]["remote"] is None
    assert _hosts(http) == {"career.habr.com"} and stats["ok"] == 1 and sum("/api/" in u for u, *_ in http.calls) == 2  # totalPages reached
    assert http.calls[0][2]["Referer"] == "https://career.habr.com/vacancies"


def test_habr_title_filter_cap_refusal_and_bad_cards():
    cards = [_hb(n, title=f"Dev {n}") for n in range(1, 41)]
    http = _stub({"page=1": _hbpage(*cards, total=1), "/vacancies/": _hbdetail()})
    jobs, _ = _run("habrcareer", _row("habrcareer"), http)
    assert len(jobs) == 40 and sum("/vacancies/" in u for u, *_ in http.calls) == 25 and sum(bool(j["description"]) for j in jobs) == 25
    http = _stub({"page=1": _hbpage(*cards[:3], total=1), "/vacancies/1": (403, None), "/vacancies/2": _hbdetail(), "/vacancies/3": _hbdetail()})
    jobs, _ = _run("habrcareer", _row("habrcareer"), http)
    assert len(jobs) == 3 and not any(j["description"] for j in jobs) and sum("/vacancies/" in u for u, *_ in http.calls) == 1
    http = _stub({"page=1": _hbpage(*cards[:3], total=1), "/vacancies/": _hbdetail()})
    jobs, _ = _run("habrcareer", _row("habrcareer", title_ok=lambda t: t.endswith("2")), http)
    assert len(jobs) == 3 and [bool(j["description"]) for j in jobs] == [False, True, False]
    bad = [_hb(1), None, "x", {"id": True}, {"id": -3}, _hb(2, title=""), _hb(3, company=""), _hb(4, locations="x", publishedDate=[]), _hb(5, remoteWork="yes")]
    jobs, _ = _run("habrcareer", _row("habrcareer"), _stub({"page=1": _hbpage(*bad, total=1), "/vacancies/": (404, None)}))
    assert [j["url"].rsplit("/", 1)[-1] for j in jobs] == ["1", "4", "5"] and jobs[1]["location"] == "" and jobs[2]["remote"] is None


def test_habr_refusals_end_paging():
    http = _stub({"page=1": _hbpage(_hb(1), total=5), "page=2": (429, None), "/vacancies/": (404, None)})
    jobs, stats = _run("habrcareer", _row("habrcareer"), http)
    assert len(jobs) == 1 and stats["ok"] == 1 and sum("/api/" in u for u, *_ in http.calls) == 2
    assert _run("habrcareer", _row("habrcareer"), _stub({"page=1": (403, None)}))[1].get("ok", 0) == 0
    assert _run("habrcareer", _row("habrcareer"), _stub({"page=1": (200, {"list": "x"})}))[1].get("ok", 0) == 0
    counter = {"n": 0}

    def endless(url, headers):
        counter["n"] += 1
        return _hbpage(_hb(counter["n"]), total=0)
    http = _stub({"/api/": endless, "/vacancies/": (404, None)})
    assert len(_run("habrcareer", _row("habrcareer"), http)[0]) == 50  # the page cap


# ----------------------------------------------------------------------------- Getmatch
def _gm(oid, position="Python Developer", company="Acme", **kw):
    return {"id": oid, "position": position, "url": f"/vacancies/{oid}-python-developer", "published_at": "2026-06-19T12:55:17.948391",
            "offer_description": "<p>Short summary</p>", "company": {"name": company},
            "location_items": [{"label": "Москва", "format": "remote"}, {"label": "Москва", "format": "remote"}, {"label": "Релокация", "format": "relocation_company"}], **kw}


def test_getmatch_happy_path_paging_and_detail():
    p0 = {"meta": {"total": 150}, "offers": [_gm(i) for i in range(1, 101)]}
    p1 = {"meta": {"total": 150}, "offers": [_gm(100), _gm(101, company="Beta", location_items=[{"label": "Минск", "format": "office"}, {"label": "Киев", "format": "remote"}])]}
    http = _stub({"offset=0&": (200, p0), "offset=100&": (200, p1), "/api/offers/1": (200, {"description": "<p>Full <i>body</i></p><script>x()</script>"}),
                  "/api/offers/": (404, None)})
    jobs, stats = _run("getmatch", _row("getmatch"), http)
    assert len(jobs) == 101 and jobs[0]["url"] == "https://getmatch.ru/vacancies/1-python-developer" and _job_hosts(jobs) == {"getmatch.ru"} and _hosts(http) == {"getmatch.ru"}
    assert jobs[0]["description"] == "Full body" and jobs[1]["description"] == "Short summary" and jobs[0]["posted"] == "2026-06-19"
    assert jobs[0]["location"] == "Москва, Релокация" and jobs[0]["remote"] is True and jobs[0]["extra"]["work_mode"] == "remote"
    assert jobs[-1]["company"] == "Beta" and jobs[-1]["remote"] is None and jobs[-1]["extra"]["work_mode"] == ""
    assert sum("offset=" in u for u, *_ in http.calls) == 2 and stats["ok"] == 1  # meta.total reached
    assert sum("/api/offers/" in u for u, *_ in http.calls) == 25  # detail cap


def test_getmatch_bad_cards_off_host_links_and_refusals():
    evil = ["//evil.example/x", "https://evil.example/x", "http://getmatch.ru/x", "/ok\\@evil.example", "x", "", None, "/with space", "/vacancies/ok"]
    offers = [_gm(10 + i, url=u) for i, u in enumerate(evil)] + [None, "x", _gm(True), _gm(-1), _gm(30, position=""), _gm(31, company=""), _gm(32, location_items="x", published_at=5)]
    jobs, _ = _run("getmatch", _row("getmatch"), _stub({"offset=0&": (200, {"meta": {"total": 1}, "offers": offers}), "/api/offers/": (404, None)}))
    assert _job_hosts(jobs) == {"getmatch.ru"} and {j["extra"]["job_id"] for j in jobs} == {"18", "32"}
    assert {j["url"] for j in jobs if j["extra"]["job_id"] == "18"} == {"https://getmatch.ru/vacancies/ok"}
    http = _stub({"offset=0&": (200, {"meta": {"total": 999}, "offers": [_gm(1), _gm(2), _gm(3)]}), "offset=100&": (429, None), "/api/offers/1": (403, None), "/api/offers/": (200, {"description": "B"})})
    jobs, stats = _run("getmatch", _row("getmatch"), http)
    assert len(jobs) == 3 and stats["ok"] == 1 and not any(j["description"] == "B" for j in jobs)  # first detail refusal ends the details
    assert sum("/api/offers/" in u for u, *_ in http.calls) == 1 and sum("offset=" in u for u, *_ in http.calls) == 2
    assert _run("getmatch", _row("getmatch"), _stub({"offset=0&": (403, None)}))[1].get("ok", 0) == 0
    assert _run("getmatch", _row("getmatch"), _stub({"offset=0&": (200, {"offers": "x"})}))[1].get("ok", 0) == 0


def test_getmatch_title_filter_and_page_cap():
    http = _stub({"offset=0&": (200, {"meta": {"total": 3}, "offers": [_gm(1, "Dev one"), _gm(2, "Dev two"), _gm(3, "Dev three")]}), "/api/offers/": (200, {"description": "Body"})})
    jobs, _ = _run("getmatch", _row("getmatch", title_ok=lambda t: t.endswith("two")), http)
    assert [j["description"] for j in jobs] == ["Short summary", "Body", "Short summary"]
    counter = {"n": 0}

    def endless(url, headers):
        counter["n"] += 1
        return 200, {"offers": [_gm(counter["n"])]}
    http = _stub({"offset=": endless, "/api/offers/": (404, None)})
    assert len(_run("getmatch", _row("getmatch"), http)[0]) == 30
