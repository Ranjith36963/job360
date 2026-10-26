"""Offline tests for hunt_kit/kit_w4c.py (CleverStaff, FriendWork, joinup.ch).

Fixtures are shaped like the data in freehire's internal/ingest/sources/cleverstaff_test.go and friendwork_test.go (+ testdata/
friendwork_itgeo.json) (MIT, bed5ec9) and career-ops providers/joinup.mjs (MIT, 8f48928), written fresh. Nothing touches the network.
Eluta and Talent.com have no reader (the reference holds only a search URL), so their hosts are checked as unsupported.
"""
from __future__ import annotations

import importlib.util
import json
import sys
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


kw = _load("kit_w4c_under_test", "kit_w4c.py")
hk = _load("hunt_kit_w4c_under_test", "hunt_kit.py")
F = kw.ATS_FETCHERS
NAMES = ("cleverstaff", "friendwork", "joinup")
TOKEN = "c656f1c111eb44075d21d77f57216c72"


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


def _row(ats: str, token: str, **extra) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **extra}


def _hosts(http) -> set:
    return {urlsplit(c[0]).hostname for c in http.calls}


def _https(jobs) -> bool:
    return all(urlsplit(j["url"]).scheme == "https" for j in jobs)


def test_every_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_w4c" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_w4c.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_w4c.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://cleverstaff.net/i/vacancy-KIFCf6", "cleverstaff", ""),
    (f"https://jobs.friend.work/{TOKEN}/6080", "friendwork", TOKEN),
    ("https://jobs.friend.work/", "friendwork", ""),
    ("https://joinup.ch/job/some-role-1234", "joinup", ""),
    ("https://www.joinup.ch/job/some-role-1234", "joinup", ""),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


@pytest.mark.parametrize("url", ["https://www.eluta.ca/search?q=dev&l=Toronto", "https://ca.talent.com/jobs?k=dev",
                                 "https://www.talent.com/view?id=1"])
def test_eluta_and_talent_com_are_unsupported_not_unknown(url):
    info = hk.board_from_job_url(url)
    assert info["kind"] == "unsupported" and info["ats"] == ""


def test_lookalike_hosts_are_not_these_boards():
    for url in ("https://cleverstaff.net.evil.com/i/vacancy-1", "https://jobs.friend.work.evil.com/x/1", "https://joinup.ch.evil.com/job/x"):
        assert hk.board_from_job_url(url)["ats"] == ""


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra(f"cleverstaff:doit-software1,friendwork:{TOKEN},joinup:all")
    assert [r["ats"] for r in rows] == ["cleverstaff", "friendwork", "joinup"]


# --------------------------------------------------------------------------- CleverStaff
def _cs(**kw_):
    base = {"vacancyId": "c3fdb13932974023abdf822201f48752", "localId": "KIFCf6", "position": "Founding Engineer",
            "descr": "<p>Build the <b>product</b></p><script>alert(1)</script>", "employmentType": "fullEmployment",
            "workCondition": "remote", "status": "inwork", "clientName": "Real Employer", "dc": 1760000000000}
    base.update(kw_)
    return base


def test_cleverstaff_happy_path_one_request_whole_list():
    http = _stub({"getAllOpenVacancy": (200, {"status": "ok", "objects": [_cs(), _cs(vacancyId="v2", localId="Zz9", position="Designer", workCondition="office")]})})
    jobs, stats = _run("cleverstaff", _row("cleverstaff", "doit-software1", name="DOIT Software"), http)
    assert [j["title"] for j in jobs] == ["Founding Engineer", "Designer"]
    j = jobs[0]
    assert j["url"] == "https://cleverstaff.net/i/vacancy-KIFCf6" and j["company"] == "DOIT Software"
    assert "Build the product" in j["description"] and "alert" not in j["description"]
    assert j["remote"] is True and j["extra"]["work_mode"] == "remote" and j["extra"]["employment_type"] == "full_time"
    assert j["posted"] == "2025-10-09" and j["extra"]["job_id"] == "c3fdb13932974023abdf822201f48752"
    assert jobs[1]["remote"] is None and jobs[1]["extra"]["work_mode"] == "onsite"
    assert stats["ok"] == 1 and stats["jobs"] == 2
    assert len(http.calls) == 1 and http.calls[0][0].endswith("alias=doit-software1")
    assert _hosts(http) == {"cleverstaff.net"} and _https(jobs)


def test_cleverstaff_drops_bad_closed_and_duplicate_objects_and_keeps_the_rest():
    objs = [_cs(vacancyId=""), _cs(vacancyId="a", localId=""), _cs(vacancyId="b", position=""), _cs(vacancyId="c", status="closed"),
            _cs(vacancyId="d", status="cancelled"), "junk", None, 7, _cs(vacancyId="e", localId="../../evil"),
            _cs(vacancyId="f", localId="https://evil.com/"), _cs(vacancyId=["x"]), _cs(vacancyId="g", localId="Good1", dc="soon", descr=None),
            _cs(vacancyId="g", localId="Dup2")]
    jobs, _ = _run("cleverstaff", _row("cleverstaff", "t1"), _stub({"alias=": (200, {"status": "ok", "objects": objs})}))
    assert [j["url"] for j in jobs] == ["https://cleverstaff.net/i/vacancy-Good1"]
    assert jobs[0]["posted"] == "" and jobs[0]["description"] == ""
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"cleverstaff.net"}


def test_cleverstaff_failures_end_the_board_and_a_hub_tenant_uses_the_client_name():
    for resp in ((200, {"status": "error", "message": "down"}), (429, None), (403, None), (200, {"objects": []}), (200, {"status": "ok", "objects": "x"}), (200, "text")):
        jobs, stats = _run("cleverstaff", _row("cleverstaff", "t1"), _stub({"alias=": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0
    for bad in ("", "a b", "a/b", "../x", "x" * 90, "a&alias=b"):
        http = _stub({"alias=": (200, {"status": "ok", "objects": [_cs()]})})
        assert _run("cleverstaff", _row("cleverstaff", bad), http)[0] == [] and not http.calls
    hub, _ = _run("cleverstaff", _row("cleverstaff", "t1", hub=True), _stub({"alias=": (200, {"status": "ok", "objects": [_cs()]})}))
    assert hub[0]["company"] == "Real Employer"


# --------------------------------------------------------------------------- FriendWork
def _fw(**kw_):
    base = {"publishedJobId": 6080, "title": "Middle backend developer (.NET)", "description": "<p>Мы ищем</p><style>x{}</style>",
            "salaryFrom": 150000, "salaryTo": 170000, "currency": 0, "city": "Москва", "address": None, "expYearsMin": 2,
            "employmentType": "Fulltime", "workplaceType": "Office", "publishDateTime": "2019-10-23T14:39:25.15",
            "dateCreated": "2019-10-22T10:00:00", "externalPublicationUrl": f"/{TOKEN}/6080"}
    base.update(kw_)
    return base


def test_friendwork_happy_path_one_unpaged_request():
    http = _stub({"CareerPortal": (200, {"company": {"name": "IT Geo"}, "publications": [_fw(), _fw(publishedJobId=6077, title="Frontend", salaryFrom=0, salaryTo=0, workplaceType="Remote", employmentType="Internship", externalPublicationUrl=None)]})})
    jobs, stats = _run("friendwork", _row("friendwork", TOKEN, name="IT Geo"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["6080", "6077"]
    a, b = jobs
    assert a["url"] == f"https://jobs.friend.work/{TOKEN}/6080" and a["company"] == "IT Geo" and a["location"] == "Москва"
    assert a["posted"] == "2019-10-23" and "Мы ищем" in a["description"] and "x{}" not in a["description"]
    assert {k: a["extra"][k] for k in ("salary_min", "salary_max", "salary_currency", "salary_period")} == {
        "salary_min": 150000, "salary_max": 170000, "salary_currency": "RUB", "salary_period": "month"}
    assert a["extra"]["employment_type"] == "full_time" and a["extra"]["work_mode"] == "onsite"
    assert b["url"] == f"https://jobs.friend.work/{TOKEN}/6077"  # no externalPublicationUrl: the token URL
    assert b["remote"] is True and "salary_min" not in b["extra"] and b["extra"]["employment_type"] == "internship"
    assert stats["ok"] == 1 and len(http.calls) == 1 and http.calls[0][0].endswith("token=" + TOKEN)
    assert _hosts(http) == {"api.friend.work"} and _https(jobs)


def test_friendwork_address_fallback_company_fallback_and_date_fallback():
    http = _stub({"CareerPortal": (200, {"company": {"name": "Tenant X"}, "publications": [_fw(city="", address="Казань, ул. Баумана 1", publishDateTime=None, workplaceType="Hybrid", currency=99)]})})
    (j,), _ = _run("friendwork", {"ats": "friendwork", "token": "acme"}, http)
    assert j["location"] == "Казань, ул. Баумана 1" and j["posted"] == "2019-10-22" and j["company"] == "Tenant X"
    assert j["extra"]["work_mode"] == "hybrid" and "salary_currency" not in j["extra"]


def test_friendwork_off_host_or_hostile_publication_urls_never_leave_the_portal():
    pubs = [_fw(publishedJobId=1, externalPublicationUrl="//evil.example/x"), _fw(publishedJobId=2, externalPublicationUrl="https://evil.example/x"),
            _fw(publishedJobId=3, externalPublicationUrl="@evil.example/x"), _fw(publishedJobId=4, externalPublicationUrl="/\\evil.example/x"),
            _fw(publishedJobId=5, externalPublicationUrl="/ok/5"), _fw(publishedJobId=6, externalPublicationUrl=["x"])]
    http = _stub({"CareerPortal": (200, {"publications": pubs})})
    jobs, _ = _run("friendwork", _row("friendwork", "acme"), http)
    assert len(jobs) == 6
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"jobs.friend.work"} and _https(jobs)
    assert [j["url"] for j in jobs if j["extra"]["job_id"] == "5"] == ["https://jobs.friend.work/ok/5"]
    assert [j["url"] for j in jobs if j["extra"]["job_id"] == "1"] == ["https://jobs.friend.work/acme/1"]
    assert _hosts(http) == {"api.friend.work"}


def test_friendwork_one_bad_publication_costs_one_and_a_refusal_ends_the_board():
    pubs = [_fw(publishedJobId=0), _fw(publishedJobId=True), _fw(publishedJobId="9"), _fw(title=""), "junk", None, _fw(publishedJobId=8, salaryFrom="x", salaryTo=None, currency="RUB"),
            _fw(publishedJobId=8, title="Dup"), _fw(publishedJobId=9, description=None, publishDateTime=12345, dateCreated={"a": 1})]
    jobs, _ = _run("friendwork", _row("friendwork", "acme"), _stub({"CareerPortal": (200, {"company": [], "publications": pubs})}))
    assert [j["extra"]["job_id"] for j in jobs] == ["8", "9"] and jobs[1]["posted"] == ""
    for resp in ((429, None), (403, None), (404, None), (200, {"publications": None}), (200, [])):
        out, stats = _run("friendwork", _row("friendwork", "acme"), _stub({"CareerPortal": resp}))
        assert out == [] and stats.get("ok", 0) == 0
    for bad in ("", "a/b", "../x", "a b", "a&token=b", "x" * 70):
        http = _stub({"CareerPortal": (200, {"publications": [_fw()]})})
        assert _run("friendwork", _row("friendwork", bad), http)[0] == [] and not http.calls


# --------------------------------------------------------------------------- joinup.ch
def _page(hits, extra_before: str = "") -> str:
    data = {"props": {"pageProps": {"serverState": {"initialResults": {"jobs": {"results": [{"hits": hits}]}}}}}}
    return f'<html><head>{extra_before}</head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def test_joinup_happy_path_reads_iso_and_epoch_dates_and_title_headline_location_shapes():
    hits = [{"title": "Backend Engineer", "startup": "Acme AG", "slug": "backend-engineer-acme-1", "location": "Zurich", "created": "2026-10-20T08:00:00.000Z"},
            {"headline": "Data Lead", "startup": "Beta SA", "slug": "data-lead-2", "location": {"name": "Remote, Switzerland"}, "created": 1760000000},
            {"title": "Ops", "slug": "ops-3", "location": {"city": "Bern"}, "created": 0}]
    http = _stub({"joinup.ch/browse/jobs": (200, _page(hits))})
    jobs, stats = _run("joinup", _row("joinup", "all", name="joinup.ch"), http)
    assert [j["title"] for j in jobs] == ["Backend Engineer", "Data Lead", "Ops"]
    a, b, c = jobs
    assert a["url"] == "https://joinup.ch/job/backend-engineer-acme-1" and a["company"] == "Acme AG" and a["posted"] == "2026-10-20"
    assert b["posted"] == "2025-10-09" and b["location"] == "Remote, Switzerland" and b["remote"] is True
    assert c["company"] == "joinup.ch" and c["location"] == "Bern" and c["posted"] == ""
    assert stats["ok"] == 1 and len(http.calls) == 1 and _hosts(http) == {"joinup.ch"} and _https(jobs)


def test_joinup_hostile_slugs_and_one_bad_hit_do_not_lose_the_board():
    hits = [{"title": "A", "slug": "../../admin"}, {"title": "B", "slug": "https://evil.example/x"}, {"title": "C", "slug": "a/b"},
            {"title": "D", "slug": ""}, {"title": "E"}, {"slug": "no-title"}, "junk", None, 5, {"title": "F", "slug": ["x"]},
            {"title": "G", "slug": "good-1", "created": {"x": 1}, "startup": ["no"], "location": 7},
            {"title": "Dup", "slug": "good-1"}]
    http = _stub({"joinup.ch": (200, _page(hits))})
    jobs, _ = _run("joinup", _row("joinup", "all"), http)
    assert [j["url"] for j in jobs] == ["https://joinup.ch/job/good-1"]
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"joinup.ch"} and _hosts(http) == {"joinup.ch"}


def test_joinup_missing_or_broken_next_data_fails_the_board_instead_of_reporting_it_empty():
    bad = ["<html>no data</html>", '<script id="__NEXT_DATA__">{not json</script>', '<script id="__NEXT_DATA__">{"props":{}}</script>',
           '<script id="__NEXT_DATA__">[1,2]</script>', '<script id="__NEXT_DATA__">{"a":1}', _page("notalist"),
           '<script id="__NEXT_DATA__">' + "[" * 200_000 + "</script>"]
    for page in bad:
        jobs, stats = _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": (200, page)}))
        assert jobs == [] and stats.get("ok", 0) == 0, page[:60]
    for resp in ((403, None), (429, None), (0, None), (200, None)):
        assert _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": resp}))[0] == []
    ok_empty, stats = _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": (200, _page([]))}))
    assert ok_empty == [] and stats["ok"] == 1


def test_joinup_hostile_markup_is_linear_and_loses_nothing():
    hit = {"title": "Real", "slug": "real-1", "startup": "S"}
    junk = "<script " * 40_000 + "<!--" * 40_000 + "<div " * 40_000
    (jobs, _), elapsed = best_of(lambda: _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": (200, _page([hit], junk))})))
    assert [j["title"] for j in jobs] == ["Real"] and elapsed < 1.0
    # a doubling of the junk costs about a doubling of the time (no super-linear scan)
    def cost(n: int) -> float:
        page = _page([hit], "<script " * n + "<a " * n)
        return best_of(lambda: _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": (200, page)})))[1]
    cost(1000)
    small, big = cost(20_000), cost(80_000)
    assert big < max(small * 12, 0.5)


def test_joinup_repeated_next_data_ids_in_one_unclosed_tag_are_linear():
    def cost(n: int) -> float:
        page = "<html><script " + 'id="__NEXT_DATA__" ' * n

        def once():
            jobs, _ = _run("joinup", _row("joinup", "all"), _stub({"joinup.ch": (200, page)}))
            assert jobs == []
        return best_of(once)[1]
    cost(1000)
    small, big = cost(25_000), cost(100_000)  # a span regex retried at every id costs ~16x for 4x the ids
    assert big < 1.0 and big < max(small * 8, 0.3)


def test_http_that_raises_never_raises_out_of_a_fetcher():
    def boom(url, **_):
        raise RuntimeError("network down")

    for name in NAMES:
        jobs, stats = _run(name, _row(name, "x1"), boom)
        assert jobs == [] and stats["tried"] == 1 and stats.get("ok", 0) == 0
