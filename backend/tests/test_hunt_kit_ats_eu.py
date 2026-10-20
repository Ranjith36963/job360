"""Offline tests for hunt_kit/kit_ats_eu.py (part one: Emply, Manatal, TalentAdore, Traffit, Trakstar).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) for each ATS here
(Emply: the job.paychex.eu / molslinjen captures; Manatal: manatal_test.go:35-44; TalentAdore: talentadore_test.go:19-47;
Traffit: traffit_test.go:34-52; Trakstar: trakstar_test.go:18-42). Nothing touches the network.
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


eu = _load("kit_ats_eu_under_test", "kit_ats_eu.py")
hk = _load("hunt_kit_ats_eu_under_test", "hunt_kit.py")
F = eu.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
NAMES = ("emply", "manatal", "talentadore", "traffit", "trakstar")


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
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
    jobs = F[name](row, http, stats)
    return jobs, stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def test_every_reader_is_registered():
    assert set(F) == set(NAMES)


def test_hunt_kit_wiring():
    assert "kit_ats_eu" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    src = (KIT_DIR / "kit_ats_eu.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)  # every reader names the reference file and line it follows
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_ats_eu.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://acme.career.emply.com/ad/engineer/abc123", "emply", "acme"),
    ("https://acme.traffit.com/public/an/list/", "traffit", "acme"),
    ("https://acme.hire.trakstar.com/jobs/xyz", "trakstar", "acme"),
    ("https://www.careers-page.com/acme/job/ABC123", "manatal", "acme"),
    ("https://acme.careers-page.com/jobs/1", "manatal", "acme"),
    ("https://davidjoseph-co.careers-page.com/jobs/1", "manatal", "davidjoseph-co"),
    ("https://careers-page.com/acme/job/ABC123", "manatal", "acme"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


# --------------------------------------------------------------------------- Emply
def _emply_vac(short, title, *, talent=False, loc="Østerbro, København", published="2026-08-21T07:00:00Z", trs=None, ext=None, slug=None):
    return {"id": "id-" + short, "shortId": short, "title": title, "titleAsUrl": slug or title.lower().replace(" ", "-"),
            "talentPool": talent, "location": loc, "published": published, "externalCseAdLink": ext,
            "translations": trs if trs is not None else [{"title": title, "content": "<p>Body of " + title + "</p>", "languageKey": "en-GB"}]}


EMPLY_P0 = {"count": 6, "vacancies": [
    _emply_vac("moxy5t", "Unsolicited applications", talent=True, loc=""),
    _emply_vac("hmds58", "Lead Integration Developer", slug="lead-integration-developer"),
    _emply_vac("t8sgwl", "Account Executive", trs=[
        {"title": "Account Manager", "content": "<p>Deine Aufgaben und mehr</p>", "languageKey": "de-DE"},
        {"title": "Account Executive", "content": "<p>Your tasks</p>", "languageKey": "en-GB"}]),
    _emply_vac("tzfpvg", "Nurse", loc=""),
]}
EMPLY_P1 = {"count": 6, "vacancies": [
    _emply_vac("2exc7k", "Soger kok", trs=[{"title": "ØRESUNDSLINJEN søger kok", "content": "<p>Kok</p>", "languageKey": "da-DK"}], slug="oresundslinjen-soger-kok"),
    _emply_vac("ext001", "External role", ext="https://careers.example.com/role/1"),
]}


def _emply_http(pages=None, status=200):
    pages = pages if pages is not None else {0: EMPLY_P0, 4: EMPLY_P1}

    def handler(url, method, body):
        return (status, pages.get(body["offset"], {"count": 6, "vacancies": []})) if status == 200 else (status, None)
    return _stub({"/api/integration/vacancy/get-page": handler})


def test_emply_happy_path_pages_by_offset_and_maps():
    http = _emply_http()
    jobs, stats = _run("emply", _row("emply", "job.paychex.eu", name="Paychex"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 5}
    assert [c[2]["offset"] for c in http.calls] == [0, 4]
    assert all(c[1] == "POST" and c[2]["filters"] == [] and c[2]["count"] == 100 for c in http.calls)
    assert all(c[0] == "https://job.paychex.eu/api/integration/vacancy/get-page" for c in http.calls)
    by = {j["extra"]["short_id"]: j for j in jobs}
    assert "moxy5t" not in by  # the talent-pool entry is not a vacancy
    assert set(by) == {"hmds58", "t8sgwl", "tzfpvg", "2exc7k", "ext001"}
    lead = by["hmds58"]
    assert set(lead) == JOB_KEYS and lead["source"] == "emply" and lead["company"] == "Paychex"
    assert lead["url"] == "https://job.paychex.eu/ad/lead-integration-developer/hmds58/en"
    assert lead["posted"] == "2026-08-21" and "Body of Lead" in lead["description"] and lead["location"] == "Østerbro, København"
    assert by["t8sgwl"]["title"] == "Account Executive" and "Your tasks" in by["t8sgwl"]["description"]  # English text wins
    assert by["t8sgwl"]["url"].endswith("/t8sgwl/en")
    assert by["tzfpvg"]["location"] == ""
    assert by["2exc7k"]["url"] == "https://job.paychex.eu/ad/oresundslinjen-soger-kok/2exc7k/da"  # first language when no English
    assert by["ext001"]["url"] == "https://careers.example.com/role/1"  # the vacancy's own external ad link


def test_emply_slug_token_builds_the_career_emply_host_and_bad_tokens_send_nothing():
    http = _emply_http()
    _run("emply", _row("emply", "molslinjen"), http)
    assert http.calls[0][0] == "https://molslinjen.career.emply.com/api/integration/vacancy/get-page"
    for bad in ("", "-x", "a b", "https://x.dk", "x/../y"):
        h = _emply_http()
        jobs, stats = _run("emply", _row("emply", bad), h)
        assert jobs == [] and stats["ok"] == 0 if "ok" in stats else True
        assert h.calls == []


def test_emply_empty_non200_gone_tenant_and_later_page_failure():
    jobs, stats = _run("emply", _row("emply", "x"), _emply_http({0: {"count": 0, "vacancies": []}}))
    assert jobs == [] and stats["ok"] == 1  # an empty board is a successful board
    jobs, stats = _run("emply", _row("emply", "x"), _emply_http(status=500))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("emply", _row("emply", "x"), _stub({"get-page": (0, None)}))  # a gone tenant answers the marketing page (not JSON)
    assert jobs == [] and stats.get("ok", 0) == 0

    def flaky(url, method, body):
        return (200, EMPLY_P0) if body["offset"] == 0 else (503, None)
    jobs, stats = _run("emply", _row("emply", "x"), _stub({"get-page": flaky}))
    assert len(jobs) == 3 and stats["ok"] == 1  # a hiccup after page 0 keeps what we have


# --------------------------------------------------------------------------- Manatal
MANATAL_P1 = {"count": 3, "next": "https://open.api.manatal.com/open/v3/career-page/acme/jobs/?page=2", "previous": None, "results": [
    {"id": 1, "hash": "AB12CD34", "organization_name": "Engineering", "position_name": "Backend Engineer", "description": "<p>Build &amp; ship.</p>",
     "country": "Thailand", "state": "Bangkok", "city": "Bangkok", "location_display": "Bangkok, Thailand", "is_remote": True, "contract_details": "full_time"},
    {"id": 2, "hash": "", "position_name": "skip me", "description": "x", "location_display": "Remote", "is_remote": None}]}
MANATAL_P2 = {"count": 3, "next": None, "previous": "x", "results": [
    {"id": 3, "hash": "ZZ99", "position_name": "QA Intern", "description": "<p>Test.</p>", "country": "Germany", "state": "", "city": "Berlin",
     "location_display": "", "is_remote": None, "contract_details": "internship"}]}


def test_manatal_follows_next_and_maps():
    http = _stub({"page=2": (200, MANATAL_P2), "career-page/acme/jobs": (200, MANATAL_P1)})
    jobs, stats = _run("manatal", _row("manatal", "acme", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert [c[0] for c in http.calls][0] == "https://open.api.manatal.com/open/v3/career-page/acme/jobs/"
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme" and a["source"] == "manatal"  # organization_name is a department, never the employer
    assert a["url"] == "https://www.careers-page.com/acme/job/AB12CD34" and a["description"] == "Build & ship."
    assert a["remote"] is True and a["location"] == "Bangkok, Thailand" and a["extra"]["employment_type"] == "full_time"
    assert b["location"] == "Berlin, Germany" and b["remote"] is None and b["posted"] == ""


def test_manatal_next_link_is_host_locked_and_failures():
    page = dict(MANATAL_P1, next="https://evil.example.com/steal?page=2")
    http = _stub({"career-page/acme/jobs": (200, page)})
    jobs, _ = _run("manatal", _row("manatal", "acme"), http)
    assert len(jobs) == 1 and len(http.calls) == 1
    jobs, stats = _run("manatal", _row("manatal", "acme"), _stub({"career-page": (200, {"count": 0, "next": None, "results": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("manatal", _row("manatal", "acme"), _stub({"career-page": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    loop = {"count": 1, "next": "https://open.api.manatal.com/open/v3/career-page/acme/jobs/?page=2", "results": []}
    http = _stub({"career-page": (200, loop)})
    _run("manatal", _row("manatal", "acme"), http)
    assert len(http.calls) == eu.MAX_PAGES  # a feed that never ends is capped


# --------------------------------------------------------------------------- TalentAdore
TA_FEED = {"company": "Acme", "jobs": [
    {"id": "yjMOo", "job_token": "mWXEgr", "name": "AI Engineer", "link": "https://ats.talentadore.com/apply/ai-engineer/mWXEgr",
     "description_html": "<p>Build models.</p>", "description_text": "Build models.", "start_date": "2026-07-10T12:16:20Z",
     "updated": "2026-07-11T09:00:00Z", "city": "Helsinki", "county": "", "country": "Finland", "location": "Pasilankatu 2 A"},
    {"id": "RyGxO", "job_token": "m5n9e1", "name": "Remote Designer", "link": "https://ats.talentadore.com/apply/remote-designer/m5n9e1",
     "description_html": "", "description_text": "Design things.", "start_date": "", "updated": "2026-07-09T08:00:00Z", "city": "", "county": "", "country": "Remote"},
    {"id": "x", "job_token": "", "name": "No token", "link": "https://ats.talentadore.com/apply/x"}]}


def test_talentadore_happy_path():
    http = _stub({"/positions/9wmfASE/json": (200, TA_FEED)})
    jobs, stats = _run("talentadore", _row("talentadore", "9wmfASE", name="Acme Oy"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert http.calls[0][0] == "https://ats.talentadore.com/positions/9wmfASE/json?v=2"  # v=2 keeps the dates RFC3339
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme Oy" and a["location"] == "Helsinki, Finland" and a["posted"] == "2026-07-10"
    assert a["url"] == "https://ats.talentadore.com/apply/ai-engineer/mWXEgr" and a["extra"]["job_token"] == "mWXEgr" and a["description"] == "Build models."
    assert b["description"] == "Design things." and b["remote"] is True and b["posted"] == "2026-07-09"


def test_talentadore_row_without_a_real_name_takes_the_feed_company_and_failures():
    jobs, _ = _run("talentadore", {"name": "9wmfASE", "ats": "talentadore", "token": "9wmfASE"}, _stub({"positions": (200, TA_FEED)}))
    assert {j["company"] for j in jobs} == {"Acme"}
    jobs, stats = _run("talentadore", _row("talentadore", "t"), _stub({"positions": (200, {"jobs": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("talentadore", _row("talentadore", "t"), _stub({"positions": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0


# --------------------------------------------------------------------------- Traffit
def _tr_page(start, n, count):
    return {"count": count, "items": [{"advertId": start + i, "title": "Role", "name": "Role", "url": f"https://acme.traffit.com/public/an/x{start + i}"} for i in range(n)]}


TR_LAST = {"count": 103, "items": [
    {"advertId": 2001, "title": "  Senior Java Engineer  ", "name": "Senior Java Engineer", "url": "https://acme.traffit.com/public/an/abc", "validStart": 1770284174,
     "description": "<p>Build things</p>", "geolocation": '{"iso":"pl","locality":"Warszawa","region1":"Mazowieckie","country":"Polska"}'},
    {"advertId": 2002, "title": "", "name": "Remote Fallback Role", "url": "https://acme.traffit.com/public/an/def", "geolocation": None},
    {"advertId": 0, "advertPublishId": 0, "title": "No Id", "url": "https://acme.traffit.com/public/an/ghi"}]}


def test_traffit_pages_until_count_and_maps():
    http = _stub({"offset=0": (200, _tr_page(1000, 100, 103)), "offset=100": (200, TR_LAST)})
    jobs, stats = _run("traffit", _row("traffit", "acme", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 102}  # 100 + 2; the id-less posting is dropped
    assert [c[0] for c in http.calls] == ["https://acme.traffit.com/public/an/list/?limit=100&offset=0", "https://acme.traffit.com/public/an/list/?limit=100&offset=100"]
    j = jobs[100]
    assert set(j) == JOB_KEYS and j["title"] == "Senior Java Engineer" and j["company"] == "Acme"
    assert j["location"] == "Warszawa, Mazowieckie, Polska" and j["posted"] == "2026-02-05" and j["description"] == "Build things"
    assert j["url"] == "https://acme.traffit.com/public/an/abc" and j["extra"]["advert_id"] == "2001"
    assert jobs[101]["title"] == "Remote Fallback Role" and jobs[101]["location"] == ""  # name when the title is blank; null geolocation


def test_traffit_empty_non200_garbage_geolocation():
    jobs, stats = _run("traffit", _row("traffit", "empty"), _stub({"traffit": (200, {"count": 0, "items": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("traffit", _row("traffit", "x"), _stub({"traffit": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    page = {"count": 1, "items": [{"advertId": 5, "title": "T", "url": "https://x.traffit.com/public/an/t", "geolocation": "{not json", "validStart": "soon"}]}
    jobs, _ = _run("traffit", _row("traffit", "x"), _stub({"traffit": (200, page)}))
    assert len(jobs) == 1 and jobs[0]["location"] == "" and jobs[0]["posted"] == ""
    http = _stub({"traffit": (200, _tr_page(1, 100, 10**6))})  # a feed that never reaches count is capped
    _run("traffit", _row("traffit", "x"), http)
    assert len(http.calls) == eu.MAX_PAGES


# --------------------------------------------------------------------------- Trakstar
TK_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:job="https://recruiterbox.com/rss/job/"><channel><title>Cheesecake Labs Jobs</title>
<item><title>Senior Go Engineer</title><link>http://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb</link>
<guid>http://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb</guid><pubDate>Thu, 18 Jun 2026 00:00:00 +0530</pubDate>
<description>&lt;p&gt;Build the &lt;strong&gt;pipeline&lt;/strong&gt;.&lt;/p&gt;</description>
<job:locationCity>Florian&#243;polis</job:locationCity><job:locationState>SC</job:locationState><job:locationCountry>Brazil</job:locationCountry></item>
<item><title>Remote Platform Engineer</title><link>http://cheesecakelabs.hire.trakstar.com/jobs/ab1cdef</link>
<pubDate>Thu, 18 Jun 2026 00:00:00 +0530</pubDate><description>&lt;p&gt;Work from anywhere.&lt;/p&gt;</description>
<job:locationCity>Remote</job:locationCity><job:locationCountry>Brazil</job:locationCountry></item>
<item><title></title><link>http://cheesecakelabs.hire.trakstar.com/jobs/zzz</link></item></channel></rss>"""


def test_trakstar_rss_happy_path():
    http = _stub({"/jobfeeds/cheesecakelabs": (200, TK_FEED)})
    jobs, stats = _run("trakstar", _row("trakstar", "cheesecakelabs", name="Cheesecake Labs"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert http.calls[0][0] == "https://cheesecakelabs.hire.trakstar.com/jobfeeds/cheesecakelabs"
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Cheesecake Labs" and a["title"] == "Senior Go Engineer"
    assert a["url"] == "https://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb" and a["extra"]["job_id"] == "fk0zjzb"  # http upgraded to https
    assert a["location"] == "Florianópolis, SC, Brazil" and a["posted"] == "2026-06-18" and a["remote"] is None
    assert "Build the" in a["description"] and "pipeline" in a["description"] and "<p>" not in a["description"]
    assert b["remote"] is True and b["location"] == "Remote, Brazil"


def test_trakstar_empty_non200_and_garbage():
    empty = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    jobs, stats = _run("trakstar", _row("trakstar", "way2"), _stub({"jobfeeds": (200, empty)}))
    assert jobs == [] and stats["ok"] == 1
    for resp in ((404, None), (200, "<html>not rss</html>"), (200, "<rss><channel><item>"), (200, '<!DOCTYPE r [<!ENTITY a "b">]><rss/>'), (200, "")):
        jobs, stats = _run("trakstar", _row("trakstar", "way2"), _stub({"jobfeeds": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0, resp


# --------------------------------------------------------------------------- all readers
GARBAGE = [(0, None), (200, None), (200, []), (200, {}), (200, "text"), (200, 7), (200, [1, None, "x"]), (200, {"jobs": "x", "items": {}, "results": 3, "vacancies": [None]}),
           (500, None), (403, None), (429, None)]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("resp", GARBAGE, ids=lambda r: str(r)[:30])
def test_never_raises_on_garbage(name, resp):
    jobs, stats = _run(name, _row(name, "acme.factorial.it" if name == "factorial" else "acme"), _stub({"": resp}))
    assert isinstance(jobs, list) and stats["tried"] == 1


@pytest.mark.parametrize("name", NAMES)
def test_http_that_raises_is_survived(name):
    def boom(*a, **k):
        raise RuntimeError("network down")
    jobs, stats = _run(name, _row(name, "acme.factorial.it" if name == "factorial" else "acme"), boom)
    assert jobs == [] and stats["tried"] == 1 and stats.get("ok", 0) == 0


@pytest.mark.parametrize("name", [n for n in NAMES if n != "jobylon"])
@pytest.mark.parametrize("token", ["", "../x", "a b", "x?y=1"])
def test_bad_tokens_send_no_request(name, token):
    http = _stub({})
    assert _run(name, _row(name, token), http)[0] == []
    assert http.calls == []


def test_companies_json_rows_for_these_boards_are_valid():
    raw = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    rows = [r for r in raw if r["ats"] in NAMES]
    assert rows and all(set(r) == {"name", "ats", "board", "source"} for r in rows)
    assert {r["ats"] for r in rows} == set(NAMES) - {"jobylon"}  # jobylon has one global sitemap, no per-company board
    assert len({(r["ats"], r["board"].lower()) for r in rows}) == len(rows)
    assert all(r["name"].strip() and r["board"].strip() for r in rows)


# --------------------------------------------------------------------------- review findings
def test_body_strip_is_linear_and_still_strips_pairs():
    import time
    evil = "<script>" * 50_000  # 400KB, never closed: the old lazy regex was quadratic on this
    t0 = time.perf_counter()
    assert eu._body(evil) == ""
    assert eu._body("<style>" * 50_000 + "x") == ""
    assert time.perf_counter() - t0 < 1.0
    assert eu._body("<p>Hello</p><script type='x'>alert(1)</script><p>World</p>").split() == ["Hello", "World"]
    assert eu._body("<style>p{}</style>Keep<SCRIPT>bad()</SCRIPT >me<scripts>kept</scripts>").split() == ["Keepmekept"]
    assert eu._body("Before<script>never closed " + "a" * 50) == "Before"
    assert eu._body("") == "" and eu._body(None) == ""


def test_body_strip_is_linear_on_fake_tags_and_bare_angle_brackets():
    import time
    t0 = time.perf_counter()
    for evil in ("<styles" * 28_000, "<scripts" * 25_000, "<" * 200_000, "<script " * 8_000 + "</script" * 8_000):
        eu._body(evil)
    assert time.perf_counter() - t0 < 1.5
    assert eu._body("a<styles>b</styles>c<b") == "abc"
    assert eu._body("keep<scripts>x</scripts>") == "keepx"


def test_trakstar_uses_the_shared_safe_xml_parser():
    assert eu.safe_fromstring.__module__.endswith("kit_xml") and not hasattr(eu, "_xml")


def test_manatal_host_is_read_not_unsupported():
    assert hk.board_from_job_url("https://careers-page.com/acme/job/X")["kind"] == "ats"


def test_feed_job_links_must_sit_on_the_boards_own_host():
    foreign = "https://evil.example/apply"
    page = {"count": 1, "items": [{"advertId": 7, "title": "T", "url": foreign}]}
    jobs, _ = _run("traffit", _row("traffit", "acme"), _stub({"traffit": (200, page)}))
    assert jobs == []
    ta = {"company": "A", "jobs": [{"job_token": "t1", "name": "N", "link": foreign}]}
    jobs, _ = _run("talentadore", _row("talentadore", "tok"), _stub({"positions": (200, ta)}))
    assert jobs == []
    rss = f"<rss><channel><item><title>T</title><link>{foreign}</link></item></channel></rss>"
    jobs, _ = _run("trakstar", _row("trakstar", "acme"), _stub({"jobfeeds": (200, rss)}))
    assert jobs == []
