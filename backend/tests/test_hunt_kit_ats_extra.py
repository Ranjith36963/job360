"""Offline tests for hunt_kit/kit_ats_extra.py (Avature, Comeet, HiBob).

Fixtures are shaped like the sample data in the references' own tests: freehire internal/ingest/sources/{comeet,hibob}_test.go and career-ops tests/providers/avature.test.mjs (MIT). Nothing touches the network.
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


extra = _load("kit_ats_extra_under_test", "kit_ats_extra.py")
hk = _load("hunt_kit_extra_under_test", "hunt_kit.py")
F = extra.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body, headers)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, **_kw):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, method, body, headers or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls  # type: ignore[attr-defined]
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name]({"name": "Acme", "ats": name, **row}, http, stats)
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == name and j["url"].startswith("https://") and "<" not in j["description"]
        assert j["company"]  # the employer from the row, never the board vendor
    return jobs, stats


def test_registry_and_citations():
    assert set(F) == {"avature", "comeet", "hibob"}
    src = (KIT_DIR / "kit_ats_extra.py").read_text(encoding="utf-8")
    assert src.count("# Borrowed from") >= len(F)  # every reader names where it came from


def test_hunt_kit_wiring():
    assert hk.ATS_MODULES[-1] == "kit_ats_extra"
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES)) >= set(F)  # the run finds every reader
    info = hk.board_from_job_url("https://qogita.careers.hibob.com/jobs/abc")
    assert (info["kind"], info["ats"], info["token"]) == ("ats", "hibob", "qogita")


def test_wiring_modules_sources_and_served_kit_files():
    assert "kit_ats_extra" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    from src.api.routes.hunt_kit import KIT_FILES
    assert "kit_ats_extra.py" in KIT_FILES


def test_parse_extra_keeps_slash_form_tokens():
    rows = hk.parse_extra("comeet:overwolf/B1.001,avature:jobs.ea.com,hibob:qogita")
    assert [(r["ats"], r["token"], r["name"]) for r in rows] == [
        ("comeet", "overwolf/B1.001", "overwolf"), ("avature", "jobs.ea.com", "jobs.ea.com"), ("hibob", "qogita", "qogita")]
    assert all(r["source"] == "user" for r in rows)
    assert hk.parse_extra("comeet:a b/c,avature:,evil:x/y") == []  # junk still dropped


@pytest.mark.parametrize("url,ats,token", [
    ("https://www.comeet.com/jobs/overwolf/B1.001/data-analyst/BC.26F", "comeet", "overwolf/B1.001"),
    ("https://www.comeet.com/jobs/overwolf/B1.001", "comeet", "overwolf/B1.001"),
    ("https://koch.avature.net/careers/JobDetail/Engineer/1234", "avature", "koch.avature.net"),
    ("https://DeloitteCM.avature.net/en_US/careers/SearchJobs", "avature", "deloittecm.avature.net"),
])
def test_job_links_resolve_to_the_extra_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)
    assert hk.parse_extra(f"{ats}:{info['token']}")[0]["token"] == token  # the answer feeds straight back into --companies-extra


@pytest.mark.parametrize("url", [
    "https://www.comeet.com/", "https://www.comeet.com/jobs/overwolf", "https://www.avature.net/", "https://avature.net.evil.example/x",
    "https://notcomeet.com/jobs/a/b", "https://example.com/avature.net/jobs/a/b"])
def test_unrelated_or_tokenless_links_do_not_resolve_a_token(url):
    info = hk.board_from_job_url(url)
    assert info["token"] == "" and info["kind"] in ("other", "unsupported", "ats")
    if info["kind"] == "ats":
        assert info["ats"] == "comeet"  # right vendor, but the link does not show the board token
    assert info["ats"] != "avature"


def test_comeet_and_avature_hosts_are_no_longer_unsupported():
    assert not hk._UNSUPPORTED_HOSTS.search("www.comeet.com") and not hk._UNSUPPORTED_HOSTS.search("koch.avature.net")
    assert hk.board_from_job_url("https://acme.comeet.com/jobs/x")["kind"] == "other"


def test_companies_rows_are_valid_and_cover_every_reader():
    # the rows live in companies.json beside every other board (load_companies maps ``board`` to ``token``)
    rows = [r for r in json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8")) if r.get("ats") in F]
    assert rows and {r["ats"] for r in rows} == set(F)
    assert all(set(r) == {"name", "ats", "board", "source"} and r["source"] in {"freehire", "career-ops"} for r in rows)
    assert len({(r["ats"], r["board"].lower()) for r in rows}) == len(rows)  # no board listed twice


@pytest.mark.parametrize("name", sorted(F))
def test_bad_token_and_dead_board_count_as_not_ok(name):
    for row in ({"token": ""}, {"token": "ok-token"}, {"token": "a/b"}):
        jobs, stats = _run(name, row, _stub({}))
        assert jobs == [] and stats == {"tried": 1, "jobs": 0}  # tried, never ok


@pytest.mark.parametrize("name", sorted(F))
def test_garbage_never_raises(name):
    def boom(url, **kw):
        raise RuntimeError("network down")

    token = {"avature": "jobs.ea.com", "comeet": "overwolf/B1.001", "hibob": "qogita"}[name]
    for garbage in ((200, 12345), (200, None), (200, [1, None, "x"]), (200, {"results": 5, "jobAdDetails": "x"}),
                    (200, "<<<not html>>>"), (500, None), (0, None)):
        _jobs, stats = _run(name, {"token": token}, lambda url, g=garbage, **kw: g)
        assert stats["tried"] == 1
    stats = {}
    assert F[name]({"name": "Acme", "token": token}, boom, stats) == [] and stats == {"tried": 1, "jobs": 0}


# --------------------------------------------------------------------------- Avature
def _card(i: int, loc: str = "") -> str:
    place = f'<span class="list-item-location">{loc}</span>' if loc else ""
    return (f'<article class="article article--result" id="article--{i}"><h3 class="title"><a class="link" '
            f'href="https://acme.avature.net/careers/JobDetail/Role-{i}/{i}">Role {i}</a></h3>{place}</article>')


AVATURE_FRAGMENT = """
    <article class="article article--result" id="article--1">
      <div class="article__header"><div class="article__header__text">
        <h3 class="title"><a class="link" href="https://acme.avature.net/careers/JobDetail/Senior-PLM-Engineer-17304/17304?businessTitle=PLM">Senior PLM Engineer &amp; Architect</a></h3>
        <div class="article__header__text__subtitle"><span class="list-item-jobId">Job ID 17304</span><span class="list-item-posted">Posted 02-May-2026</span></div>
      </div></div>
    </article>
    <article class="article article--result" id="article--2">
      <h3 class="title"><a class="link" href="/en_US/searchjobs/JobDetail/Data-Engineer-900/900">Data Engineer</a></h3>
      <span class="list-item-location">Munich, Germany</span>
    </article>
    <article class="article article--result" id="article--3">
      <h3 class="title"><span>No link here</span></h3>
    </article>"""


def test_avature_listing_happy_path():
    http = _stub({"/careers/SearchJobs": (200, AVATURE_FRAGMENT)})
    jobs, stats = _run("avature", {"token": "acme.avature.net"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert len(http.calls) == 1 and http.calls[0][0] == "https://acme.avature.net/careers/SearchJobs?jobOffset=0"  # 3 cards < 6: last page
    a, b = jobs
    assert a["title"] == "Senior PLM Engineer & Architect" and a["posted"] == "2026-05-02"
    assert a["url"] == "https://acme.avature.net/careers/JobDetail/Senior-PLM-Engineer-17304/17304?businessTitle=PLM"
    assert b["url"] == "https://acme.avature.net/en_US/searchjobs/JobDetail/Data-Engineer-900/900" and b["location"] == "Munich, Germany"
    assert a["company"] == "Acme" and a["board"] == "acme.avature.net"


def test_avature_class_suffix_unclassed_link_and_jobs_cards():
    page = """
    <article class="article article--result 1" id="article--1">
      <h3 class="title"><a class="link" href="https://acme.avature.net/en_US/externaljobs/JobDetail/Head-of-PLM/511918">Head of PLM</a></h3>
    </article>
    <article class="article article--result" id="article--2">
      <h3 class="title"><a href="https://acme.avature.net/en_US/careers/JobDetail/Director-Platform/13672">Director Platform Engineering</a></h3>
    </article>
    <article class="article article--jobs" id="article--3">
      <h3 class="article__header__text__title"><a href="/careers/JobDetail/Engineer/777">Engineer</a></h3>
      <a class="link" href="/careers/JobDetail/Engineer/777">View more</a>
    </article>"""
    jobs, _ = _run("avature", {"token": "acme.avature.net"}, _stub({"SearchJobs": (200, page)}))
    assert [j["title"] for j in jobs] == ["Head of PLM", "Director Platform Engineering", "Engineer"]


def test_avature_pages_by_six_and_stops_on_a_short_page():
    pages = {"jobOffset=0": (200, "".join(_card(i) for i in range(6))), "jobOffset=6": (200, "".join(_card(i) for i in range(6, 8)))}
    http = _stub(pages)
    jobs, stats = _run("avature", {"token": "acme.avature.net"}, http)
    assert len(jobs) == 8 and stats["ok"] == 1 and len(http.calls) == 2


def test_avature_inert_offset_key_heals_to_offset_once():
    http = _stub({
        "jobOffset=0": (200, "".join(_card(i) for i in range(6))),
        "jobOffset=6": (200, "".join(_card(i) for i in range(6))),  # echoes page 0: the key is inert
        "?offset=6": (200, "".join(_card(i) for i in range(6, 9))),
    })
    jobs, _ = _run("avature", {"token": "acme.avature.net"}, http)
    assert len(jobs) == 9 and sum("?offset=" in c[0] for c in http.calls) == 1


def test_avature_stops_on_first_non_200_and_caps_pages():
    http = _stub({})
    jobs, stats = _run("avature", {"token": "acme.avature.net"}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0} and len(http.calls) == 1
    # a failing later page keeps what was read
    http2 = _stub({"jobOffset=0": (200, "".join(_card(i) for i in range(6)))})
    jobs, stats = _run("avature", {"token": "acme.avature.net"}, http2)
    assert len(jobs) == 6 and stats["ok"] == 1 and len(http2.calls) == 2
    # never more than AVATURE_PAGES requests
    n = []

    def endless(url, **kw):
        n.append(url)
        return 200, "".join(_card(len(n) * 10 + i) for i in range(6))

    jobs, _ = _run("avature", {"token": "acme.avature.net"}, endless)
    assert len(n) == extra.AVATURE_PAGES and len(jobs) == 6 * extra.AVATURE_PAGES


def test_avature_empty_board_is_ok_and_bad_path_refused():
    jobs, stats = _run("avature", {"token": "acme.avature.net"}, _stub({"SearchJobs": (200, "<html>no results</html>")}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}
    http = _stub({})
    assert _run("avature", {"token": "acme.avature.net", "path": "/x?y=../SearchJobs"}, http)[1] == {"tried": 1, "jobs": 0} and not http.calls
    assert _run("avature", {"token": "bad host!"}, http)[0] == [] and not http.calls


COMEET_PAGE = ('<html><head><title>Overwolf Careers</title></head><body><div id="comeet"></div>'
               '<script>window.__data = {"company":{"uid":"B1.001"},"token":"NOTAREALCOMEETTOKENNOTAREAL"};</script></body></html>')
COMEET_POSITIONS = [{
    "uid": "BC.26F", "name": "Data Analyst Lead", "company_name": "Overwolf", "url_active_page": "https://careers.overwolf.com/career/BC.26F",
    "workplace_type": "Hybrid", "time_updated": "2026-06-22T18:23:19Z",
    "location": {"name": "HQ", "country": "IL", "city": "Ramat Gan", "state": "Israel"},
    "details": [{"name": "Description", "value": "<p>Lead our data team.</p>", "order": 1},
                {"name": "Requirements", "value": "<ul><li>SQL</li></ul><script>alert(1)</script>", "order": 2}],
}]


def test_comeet_scrapes_token_then_maps_positions():
    http = _stub({"/jobs/overwolf/B1.001": (200, COMEET_PAGE), "/careers-api/2.0/company/B1.001/positions": (200, COMEET_POSITIONS)})
    jobs, stats = _run("comeet", {"token": "overwolf/B1.001", "name": "Overwolf"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    assert http.calls[1][0] == "https://www.comeet.co/careers-api/2.0/company/B1.001/positions?token=NOTAREALCOMEETTOKENNOTAREAL&details=true"
    j = jobs[0]
    assert (j["title"], j["company"], j["url"]) == ("Data Analyst Lead", "Overwolf", "https://careers.overwolf.com/career/BC.26F")
    assert j["location"] == "Ramat Gan, Israel" and j["posted"] == "2026-06-22" and j["remote"] is None
    assert j["extra"] == {"work_mode": "hybrid"}
    assert "Lead our data team." in j["description"] and "Requirements" in j["description"] and "SQL" in j["description"]
    assert "alert" not in j["description"] or "<" not in j["description"]


def test_comeet_location_urls_and_company_fallback():
    positions = [
        {"uid": "A", "name": "Remote Dev", "company_name": "Payload Co", "url_comeet_hosted_page": "https://www.comeet.com/jobs/x/UID.1/a",
         "url_active_page": "http://insecure.example/a", "workplace_type": "Remote", "location": {"name": "Remote Europe", "is_remote": True}},
        {"uid": "B", "name": "Country Only", "url_active_page": "https://x.example/b", "location": {"country": "DE"}},
        {"uid": "C", "name": "Flagged", "url_active_page": "https://x.example/c", "location": {"name": "Berlin", "is_remote": True}},
        {"name": "No UID", "url_active_page": "https://x.example/d"},
        {"uid": "E", "name": "No url"},
    ]
    http = _stub({"/jobs/x/UID.1": (200, COMEET_PAGE), "positions": (200, positions)})
    stats: dict = {}
    jobs = F["comeet"]({"token": "x/UID.1", "ats": "comeet"}, http, stats)  # no row name: the payload names the employer
    assert [j["title"] for j in jobs] == ["Remote Dev", "Country Only", "Flagged"]
    assert jobs[0]["company"] == "Payload Co" and jobs[0]["url"] == "https://www.comeet.com/jobs/x/UID.1/a" and jobs[0]["remote"] is True
    assert jobs[1]["location"] == "DE" and jobs[2]["location"] == "Berlin, Remote" and jobs[2]["remote"] is True


def test_comeet_missing_token_empty_and_non_200():
    jobs, stats = _run("comeet", {"token": "overwolf/B1.001"}, _stub({"/jobs/overwolf": (200, "<html>no token here</html>")}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    http = _stub({"/jobs/overwolf": (200, COMEET_PAGE), "positions": (500, None)})
    assert _run("comeet", {"token": "overwolf/B1.001"}, http)[1] == {"tried": 1, "jobs": 0} and len(http.calls) == 2
    http = _stub({"/jobs/overwolf": (200, COMEET_PAGE), "positions": (200, [])})
    assert _run("comeet", {"token": "overwolf/B1.001"}, http)[1] == {"tried": 1, "ok": 1, "jobs": 0}


# --------------------------------------------------------------------------- HiBob
HIBOB_BODY = {"filterGroups": {"departments": []}, "jobAdDetails": [{
    "id": "04e764ae-fb6c-4303-ba2f-509b917804c2", "title": "Data Scientist, Client Facing", "department": "Customer", "site": "New York",
    "country": "United States", "language": "en", "description": "<p>We build AI &amp; finance tooling.</p><script>alert(1)</script>",
    "requirements": "<ul><li>4+ years as a data scientist</li></ul>", "responsibilities": "<ul><li>Work on-site with clients</li></ul>",
    "benefits": "<ul><li>$140-170k DOE</li></ul>", "sectionLabels": {}, "publishedAt": "2026-04-22T07:20:46.518438210Z",
    "workspaceTypeId": "hybrid", "workspaceType": "Hybrid"}]}


def test_hibob_sends_tenant_referer_and_maps_the_posting():
    http = _stub({"/api/job-ad": (200, HIBOB_BODY)})
    jobs, stats = _run("hibob", {"token": "qogita", "name": "Qogita"}, http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1} and len(http.calls) == 1
    url, _m, _b, headers = http.calls[0]
    assert url == "https://qogita.careers.hibob.com/api/job-ad" and headers["Referer"] == "https://qogita.careers.hibob.com/jobs"
    j = jobs[0]
    assert j["url"] == "https://qogita.careers.hibob.com/jobs/04e764ae-fb6c-4303-ba2f-509b917804c2"
    assert (j["title"], j["company"], j["location"], j["posted"]) == ("Data Scientist, Client Facing", "Qogita", "New York, United States", "2026-04-22")
    assert j["remote"] is None and j["extra"] == {"work_mode": "hybrid"}
    for want in ("We build AI & finance tooling.", "4+ years as a data scientist", "Work on-site with clients", "$140-170k DOE",
                 "Responsibilities", "Requirements", "Benefits"):
        assert want in j["description"]


def test_hibob_remote_idless_and_failures():
    body = {"jobAdDetails": [{"id": "r1", "title": "Remote Dev", "workspaceType": "Remote"}, {"title": "no id"}, {"id": "x", "title": ""}, "junk"]}
    jobs, _ = _run("hibob", {"token": "qogita"}, _stub({"job-ad": (200, body)}))
    assert [j["title"] for j in jobs] == ["Remote Dev"] and jobs[0]["remote"] is True
    assert _run("hibob", {"token": "qogita"}, _stub({"job-ad": (401, None)}))[1] == {"tried": 1, "jobs": 0}
    assert _run("hibob", {"token": "qogita"}, _stub({"job-ad": (200, {"jobAdDetails": []})}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
