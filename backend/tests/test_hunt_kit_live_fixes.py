"""Offline regressions for the first live run on the owner's PC (2026-10-18): Workday junk rows, a time
budget starved by the board sweep, silent EURES refusals, Taleo's inline list, Eightfold's PCSX fallback."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hunt_kit"


def _load(name: str, file: str) -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load("hunt_kit_live_fixes", "hunt_kit.py")
kit_ats = _load("kit_ats_live_fixes", "kit_ats.py")
kit_gov = _load("kit_gov_live_fixes", "kit_gov.py")


# ---------------------------------------------------------------- 1. Workday junk rows
def test_workday_rows_whose_tenant_is_a_host_label_are_dropped() -> None:
    dropped = [0]
    rows = hk.universe_rows(
        "workday", ["wd5|wd1|rydercareers", "ryder|wd5|rydercareers", "wd12|wd3|x", "wdco|wd5|site"], dropped)
    assert [r["tenant"] for r in rows] == ["ryder", "wdco"]  # a real tenant starting "wd" stays
    assert dropped == [2]


def test_load_universe_says_how_many_junk_rows_it_dropped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "workday_companies.json").write_text(json.dumps(["wd5|wd1|a", "wd5|wd1|b", "ryder|wd5|c"]))
    rows = hk.load_universe(["workday"], tmp_path)
    assert [r["tenant"] for r in rows] == ["ryder"]
    assert "dropped 2 junk Workday rows" in capsys.readouterr().err


def test_shipped_companies_json_has_no_host_label_workday_tenants() -> None:
    data = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    bad = [r for r in data if r.get("ats") == "workday" and hk.WD_HOST_TENANT.fullmatch(str(r.get("tenant", "")))]
    assert bad == []  # counted 2026-10-18: 0 of 580 workday rows


# ---------------------------------------------------------------- 2. run order under a time budget
def test_order_tasks_user_boards_then_feeds_then_round_robin() -> None:
    def w() -> None:
        return None

    own = [("ashby", "ashby:mine", w, True)]
    feeds = [("freehire", "freehire", w), ("vdab", "feed:vdab", w)]
    sweep = [("greenhouse", f"greenhouse:g{i}", w, False) for i in range(3)]
    sweep += [("ashby", f"ashby:a{i}", w, False) for i in range(2)] + [("lever", "lever:l0", w, False)]
    ids = [t[1] for t in hk.order_tasks(own, feeds, sweep)]
    assert ids == ["ashby:mine", "freehire", "feed:vdab",
                   "greenhouse:g0", "ashby:a0", "lever:l0", "greenhouse:g1", "ashby:a1", "greenhouse:g2"]


def test_run_starts_feeds_before_the_board_sweep_and_interleaves_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []
    boards = ([{"name": f"g{i}", "ats": "greenhouse", "token": f"g{i}", "source": "career-ops"} for i in range(3)]
              + [{"name": f"l{i}", "ats": "lever", "token": f"l{i}", "source": "career-ops"} for i in range(3)])
    monkeypatch.setattr(hk, "load_companies", lambda p: boards)
    for ats in ("greenhouse", "lever"):
        monkeypatch.setitem(hk._BOARD_FN, ats, lambda col, b: order.append(b["token"]))
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"vdab": lambda q, h, st, **kw: order.append("feed") or []}
                        if attr == "FEEDS" else {})
    out = tmp_path / "found.jsonl"
    argv = ["--sources", "greenhouse,lever,vdab", "--no-universe", "--work-countries", "NL", "--titles", "engineer",
            "--history", "none", "--workers", "1", "--out", str(out), "--companies-extra", "greenhouse:mine"]
    assert hk.main(argv) == 0
    assert order == ["mine", "feed", "g0", "l0", "g1", "l1", "g2", "l2"]


# ---------------------------------------------------------------- 3. EURES
def test_eures_logs_the_status_of_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[dict[str, Any]] = []

    def http(url: str, *, method: str = "GET", data: Any = None, headers: Any = None, as_json: bool = True):
        seen.append({"url": url, "method": method, "headers": headers or {}, "body": json.loads(data)})
        return 403, None

    stats: dict[str, int] = {}
    assert kit_gov.fetch_eures(["python"], http, stats, days=30, countries=["DE"]) == []
    assert stats["tried"] == 1 and not stats.get("ok")
    assert "eures answered HTTP 403" in capsys.readouterr().err
    h = seen[0]["headers"]
    assert seen[0]["method"] == "POST" and h["Content-Type"] == "application/json" and h["Accept"] == "application/json"
    assert seen[0]["body"]["locationCodes"] == ["de"]
    # freehire eures.go:115-134: every key of the request schema is present, arrays empty not null
    assert set(seen[0]["body"]) == {
        "resultsPerPage", "page", "sortSearch", "keywords", "publicationPeriod", "occupationUris", "skillUris",
        "requiredExperienceCodes", "positionScheduleCodes", "sectorCodes", "educationAndQualificationLevelCodes",
        "positionOfferingCodes", "locationCodes", "euresFlagCodes", "otherBenefitsCodes", "requiredLanguages",
        "sessionId", "requestLanguage"}


# ---------------------------------------------------------------- 4. Taleo inline list
def test_taleo_reads_the_inline_first_page_when_the_shell_has_no_portal_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = (FIXTURES / "taleo_joblist.html").read_text(encoding="utf-8")
    calls: list[str] = []

    def http(url: str, *, method: str = "GET", data: Any = None, headers: Any = None, as_json: bool = True):
        calls.append(url)
        return 200, page

    stats: dict[str, int] = {}
    row = {"name": "kearney", "ats": "taleo", "tenant": "kearney", "section": "01"}
    jobs = kit_ats.fetch_taleo(row, http, stats)
    assert [j["title"] for j in jobs[:2]] == ["Kearney Activate Data and AI Solution Architect",
                                              "PERLab Associate- Consumer"]
    assert len(jobs) == 3 and calls == ["https://kearney.taleo.net/careersection/01/jobsearch.ftl?lang=en"]
    j = jobs[0]
    assert j["url"] == "https://kearney.taleo.net/careersection/01/jobdetail.ftl?job=006FB&lang=en"
    assert j["location"] == "Americas-United States-Boston" and j["posted"] == "2026-10-13"
    assert "Americas-Canada-Toronto" in jobs[2]["location"] and jobs[2]["posted"] == "2026-10-07"  # more-locations row
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    assert "read 3 of 49 jobs" in capsys.readouterr().err


def test_taleo_with_no_portal_and_no_inline_list_still_answers_nothing() -> None:
    http = lambda url, **kw: (200, "<html>nothing here</html>")  # noqa: E731
    stats: dict[str, int] = {}
    assert kit_ats.fetch_taleo({"name": "x", "tenant": "x", "section": "1"}, http, stats) == []
    assert stats["tried"] == 1 and not stats.get("ok")


# ---------------------------------------------------------------- 5. Eightfold PCSX fallback
def test_eightfold_pcsx_probe_after_a_403_uses_career_ops_urls() -> None:
    urls: list[str] = []
    pos = {"id": 7, "name": "Engineer", "canonicalPositionUrl": "https://careers.q.com/job/7", "postedTs": 1760000000}

    def http(url: str, *, method: str = "GET", data: Any = None, headers: Any = None, as_json: bool = True):
        urls.append(url)
        if "/api/apply/v2/jobs" in url:
            return 403, None
        return 200, {"status": 200, "data": {"count": 1, "positions": [pos]}}

    jobs = kit_ats.fetch_eightfold({"name": "Q", "tenant": "qualcomm", "domain": "qualcomm.com"}, http, {})
    # career-ops eightfold.mjs:buildApiUrl / buildPcsxUrl: domain, start (, num on v2 only)
    assert urls == ["https://qualcomm.eightfold.ai/api/apply/v2/jobs?domain=qualcomm.com&start=0&num=10",
                    "https://qualcomm.eightfold.ai/api/pcsx/search?domain=qualcomm.com&start=0"]
    assert len(jobs) == 1 and jobs[0]["extra"]["api"] == "pcsx" and jobs[0]["posted"].startswith("2025-")


# ---------------------------------------------------------------- CodeRabbit round on #879
kit_feeds = _load("kit_feeds_live_fixes", "kit_feeds.py")


def test_himalayas_search_keeps_paging_after_a_short_page() -> None:
    pages = {1: 3, 2: 5, 3: 0}  # search pages can be short before the end; only an empty page ends it
    calls: list[str] = []

    def http(url: str, **kw: Any):
        calls.append(url)
        n = int(url.rsplit("page=", 1)[1])
        jobs = [{"title": "Data Engineer", "companyName": "Acme", "applicationLink": f"https://h.app/{n}/{i}",
                 "pubDate": 0} for i in range(pages.get(n, 0))]
        return 200, {"jobs": jobs}

    kit_feeds.fetch_himalayas(["Data Engineer"], http, {}, days=0, countries=[])
    assert [c.rsplit("page=", 1)[1] for c in calls] == ["1", "2", "3"]


def test_himalayas_feed_follows_the_cursor_until_it_is_gone() -> None:
    bodies = [{"jobs": [{"title": "x", "applicationLink": "https://h.app/a"}], "nextCursor": "c2"},
              {"jobs": [{"title": "y", "applicationLink": "https://h.app/b"}]}]
    calls: list[str] = []

    def http(url: str, **kw: Any):
        calls.append(url)
        return 200, bodies[len(calls) - 1]

    kit_feeds.fetch_himalayas([], http, {}, days=0, countries=[])
    assert calls == ["https://himalayas.app/jobs/api", "https://himalayas.app/jobs/api?cursor=c2"]


def test_eures_never_places_an_unlocated_ad_in_every_country_for_star() -> None:
    body = {"jvs": [{"id": "1", "title": "Python Developer"}], "numberRecords": 1}
    http = lambda url, **kw: (200, body)  # noqa: E731
    star = kit_gov.fetch_eures(["python"], http, {}, days=0, countries=["DE", "*"])
    assert star and "Germany" not in star[0]["location"]
    own = kit_gov.fetch_eures(["python"], http, {}, days=0, countries=["DE"])
    assert own and own[0]["location"].endswith("Germany")  # the user's own single country still places it


def test_taleo_inline_jobs_get_details_when_asked() -> None:
    page = (FIXTURES / "taleo_joblist.html").read_text(encoding="utf-8")
    detail = ('<script type="application/ld+json">{"@type":"JobPosting","description":"Build data products."}'
              "</script>")

    def http(url: str, **kw: Any):
        return 200, detail if "jobdetail.ftl" in url else page

    row = {"name": "kearney", "tenant": "kearney", "section": "01", "fetch_details": True, "detail_limit": 2}
    jobs = kit_ats.fetch_taleo(row, http, {})
    assert len(jobs) == 3
    assert [bool(j["description"]) for j in jobs] == [True, True, False]
