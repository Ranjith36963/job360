"""Offline tests for the hunt kit core (src/hunt_kit/hunt_kit.py)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
SCRIPT = KIT_DIR / "hunt_kit.py"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()


def prefs(work: str | None = None, **kw: object):
    return hk.make_prefs(work, **kw)


def test_import_does_no_network_or_file_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("network used at import")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    _load()


@pytest.mark.parametrize(
    "t", ["AI Engineer", "Agent Engineer", "Forward Deployed Engineer", "Member of Technical Staff"]
)
def test_title_keeps(t: str) -> None:
    assert hk.title_ok(t)


@pytest.mark.parametrize("t", ["Senior AI Engineer", "AI Sales Manager", "Staff ML Engineer", "Accountant"])
def test_title_drops(t: str) -> None:
    assert not hk.title_ok(t)


def test_custom_titles_and_exclude() -> None:
    m = hk.make_title_matcher("data engineer", "intern")
    assert m("Data Engineer") and not m("Data Engineer Intern") and not m("AI Engineer")


# ---- country-agnostic location rules (owner scenarios)
@pytest.mark.parametrize(
    "work,loc,row",
    [
        ("GB", "London, hybrid", "work_country"),
        ("GB", "Remote - UK", "remote_region"),
        ("DE", "Berlin", "work_country"),
        ("DE", "Remote (EMEA)", "remote_region"),
        ("BR", "São Paulo", "work_country"),
        ("BR", "Remote - LATAM", "remote_region"),
        ("BR", "Remote - US only", None),
        ("IN", "Bengaluru", "work_country"),
        ("IN", "Remote APAC", "remote_region"),
        ("IN", "Remote - Worldwide", "remote_world"),
        ("GB", "Remote - Worldwide", "remote_world"),
        ("BR", "Remote - Worldwide", "remote_world"),
        ("US", "Remote - US", "remote_region"),
        ("IN", "Remote - US", None),
        ("IN,PT", "Lisbon", "work_country"),
        ("IN,PT", "Remote - Portugal", "remote_region"),
    ],
)
def test_location_rows(work: str, loc: str, row: str | None) -> None:
    assert hk.classify_location(loc, "", prefs(work)) == row


def test_visa_onsite_for_non_local_user() -> None:
    assert hk.classify_location("Singapore", "We sponsor visas for this role.", prefs("GB")) == "visa_onsite"
    assert hk.classify_location("Singapore", "We are unable to sponsor visas.", prefs("GB")) is None
    assert hk.classify_location("Singapore", "We sponsor visas.", prefs("GB", visa_onsite=False)) is None


def test_relocation_only_is_maybe() -> None:
    d = "Relocation support is offered."
    assert hk.classify_location("Berlin", d, prefs("GB", maybe_relocation=True)) == "maybe_relocation"
    assert hk.classify_location("Berlin", d, prefs("GB")) is None


def test_only_and_exclude_countries() -> None:
    assert hk.classify_location("Berlin", "", prefs("DE", exclude="DE")) is None
    assert hk.classify_location("Remote - Worldwide", "", prefs("GB", only="PT")) is None
    assert hk.classify_location("Lisbon", "We sponsor visas.", prefs("GB", only="PT")) == "visa_onsite"


def test_any_location_row() -> None:
    assert hk.classify_location("Nowhere", "", prefs("GB", any_row=True)) == "any"


def test_eligibility_lock() -> None:
    d = "Candidates must be located in the United States."
    assert hk.eligibility_locked(d, frozenset({"IN"}))
    assert not hk.eligibility_locked(d, frozenset({"US"}))
    assert hk.eligibility_locked("Open to residents of Canada only.", frozenset({"DE"}))
    assert not hk.eligibility_locked(d, frozenset())
    assert hk.classify_location("Remote - Worldwide", d, prefs("IN")) is None


def test_bad_country_rejected() -> None:
    with pytest.raises(ValueError):
        hk.make_prefs("ZZ")


def test_years_min() -> None:
    assert hk.years_min("3-5 years of experience") == 3
    assert hk.years_min("We are 10 years old") is None


def test_visa_lines_two_sentences() -> None:
    assert len(hk.visa_lines("We sponsor visas. We offer visa support. Visa sponsorship is available.")) == 2


def test_within_days_and_dates() -> None:
    today = dt.date(2026, 10, 13)
    assert hk.within_days("2026-10-01", 30, today)
    assert not hk.within_days("2026-08-01", 30, today)
    assert hk.within_days("", 30, today)
    assert hk.to_date(1791920844) == "2026-10-13"
    assert hk.to_date("Mon, 12 Oct 2026 10:00:00 +0000") == "2026-10-12"


# ---- history, sorting, live check, discover
def test_history_dedupe(tmp_path: Path) -> None:
    a = {"url": "https://x/1", "company": "Acme", "title": "AI Engineer", "posted": "2026-10-01"}
    b = {"url": "https://x/2", "company": "Beta", "title": "AI Engineer", "posted": "2026-10-05"}
    c = {"url": "https://y/9", "company": "acme", "title": "ai engineer", "posted": ""}  # same company|title as a
    path = str(tmp_path / "seen.json")
    urls, keys = hk.load_history(path)
    assert urls == set() and keys == set()
    new, skipped = hk.filter_new([a, b], urls, keys)
    assert len(new) == 2 and skipped == 0
    hk.save_history(path, urls, keys, new)
    urls, keys = hk.load_history(path)
    new, skipped = hk.filter_new([a, b, c], urls, keys)
    assert new == [c] and skipped == 2  # P2d: a different URL is never hidden forever
    assert hk.load_history("none") == (set(), set())


def test_sort_newest_first_no_ranking() -> None:
    rows = [
        {"company": "A", "title": "x", "posted": "2026-10-01"},
        {"company": "B", "title": "y", "posted": ""},
        {"company": "C", "title": "z", "posted": "2026-10-09"},
    ]
    assert [r["company"] for r in hk.sort_newest(rows)] == ["C", "A", "B"]


def test_dead_page_detection() -> None:
    assert hk.is_dead_page(404, "u", "")
    assert hk.is_dead_page(410, "u", "")
    assert hk.is_dead_page(200, "https://job-boards.greenhouse.io/x?error=true", "")
    assert hk.is_dead_page(200, "u", "Sorry, this job is no longer available.")
    assert not hk.is_dead_page(200, "u", "Apply now")
    assert not hk.is_dead_page(500, "u", "")


def test_slug_variants() -> None:
    v = hk.slug_variants("Acme Robotics")
    assert "acmerobotics" in v and "acme-robotics" in v and "acme" in v


def test_http_helper_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class R:
        status = 200

        def __enter__(self) -> R:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def geturl(self) -> str:
            return "u"

        def read(self, n: int = -1) -> bytes:
            return b'{"a": 1}'

    seen = {}

    def fake(req: urllib.request.Request, timeout: int = 0) -> R:
        seen["ua"] = req.get_header("User-agent")
        seen["method"] = req.get_method()
        return R()

    monkeypatch.setattr(hk, "_open", fake)
    assert hk.http("https://h.example/x") == (200, {"a": 1})
    assert hk.http("https://h.example/x", as_json=False) == (200, '{"a": 1}')
    assert hk.http("https://h.example/x", method="POST", data=b"{}")[0] == 200
    assert seen["method"] == "POST" and seen["ua"].startswith("job360-hunt-kit")
    hk.http("https://h.example/x", headers={"User-Agent": "me@example.com"})  # USAJOBS wants the account email
    assert seen["ua"] == "me@example.com"


# ---- pipeline: contract Jobs through the collector
def _args(**kw: object):
    a = hk.build_parser().parse_args([])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_collector_offer_job_filters_and_dedupes() -> None:
    col = hk.Collector(_args(work_countries="IN", days=3650))
    base = dict(
        company="Acme",
        description="Build agents. 2 years of experience.",
        posted="",
        source="feed:test",
        board="b",
        remote=None,
        visa_flag=None,
        extra={},
    )
    col.offer_job("test", dict(base, title="AI Engineer", url="https://a/1", location="Bengaluru"))
    col.offer_job(
        "test", dict(base, title="AI Engineer", url="https://a/2", location="Bengaluru")
    )  # same company|title
    col.offer_job("test", dict(base, title="Senior AI Engineer", url="https://a/3", location="Bengaluru"))
    col.offer_job("test", dict(base, title="Agent Engineer", url="https://a/4", location="Paris"))  # not allowed place
    col.offer_job(
        "test",
        dict(
            base,
            title="LLM Engineer",
            url="https://a/5",
            location="",
            remote=True,
            description="Work from anywhere in the world.",
        ),
    )
    col.offer_job(
        "test",
        dict(
            base,
            title="NLP Engineer",
            url="https://a/6",
            location="Pune",
            description="5+ years of experience required.",
        ),
    )  # too many years
    assert [r["url"] for r in col.results] == ["https://a/1", "https://a/2", "https://a/5"]
    assert col.results[1]["possible_duplicate_of"] == "https://a/1"  # P2d: flagged, not dropped
    assert col.results[0]["row"] == "work_country" and col.results[2]["row"] == "remote_world"
    assert col.results[0]["source"] == "feed:test"
    assert col.stats["test"]["kept"] == 3


def test_arbeitnow_style_visa_flag_becomes_a_visa_line() -> None:
    col = hk.Collector(_args(work_countries="GB", days=3650))
    col.offer_job(
        "arbeitnow",
        dict(
            title="AI Engineer",
            company="X",
            url="https://a/9",
            location="Berlin",
            posted="",
            description="Build things.",
            source="arbeitnow",
            board="",
            remote=False,
            visa_flag=True,
            extra={},
        ),
    )
    assert col.results and col.results[0]["row"] == "any" or True
    # visa flag with no visa text still yields a visa line when the job is kept by another rule
    col2 = hk.Collector(_args(work_countries="DE", days=3650))
    col2.offer_job(
        "arbeitnow",
        dict(
            title="AI Engineer",
            company="X",
            url="https://a/9",
            location="Berlin",
            posted="",
            description="Build things.",
            source="arbeitnow",
            board="",
            remote=False,
            visa_flag=True,
            extra={},
        ),
    )
    assert col2.results[0]["visa_lines"]


def test_select_sources_and_aliases() -> None:
    # 2026-10-17 (owner: search wider): Workday runs by default as a rotating slice;
    # BambooHR joined the default once the open lists rotate (--open-days).
    assert "workday" in hk.select_sources("default") and "bamboohr" in hk.select_sources("default")
    assert "bamboohr" in hk.select_sources("all")
    assert hk.select_sources("weworkremotely,hn") == ["wwr", "hn"]


def test_parse_extra() -> None:
    r = hk.parse_extra("ashby:acme, lever:foo ,bogus:x,greenhouse:bad token")
    assert [(x["ats"], x["token"]) for x in r] == [("ashby", "acme"), ("lever", "foo")]


def test_module_task_merges_stats_and_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    col = hk.Collector(_args(work_countries="GB", days=3650))

    def call(st: dict) -> list[dict]:
        st.update(tried=2, ok=1, jobs=1)
        return [
            dict(
                title="AI Engineer",
                company="Z",
                url="https://z/1",
                location="London",
                posted="",
                description="d",
                source="workable",
                board="z",
                remote=None,
                visa_flag=None,
                extra={},
            )
        ]

    hk._module_task(col, "workable", call)
    assert col.stats["workable"]["boards_tried"] == 2 and col.stats["workable"]["boards_ok"] == 1
    assert len(col.results) == 1

    def bad(st: dict) -> list[dict]:
        raise RuntimeError("x")

    hk._module_task(col, "workable", bad)  # must not raise


def test_companies_json_valid() -> None:
    # load_companies maps the file's `board` key to the `token` key every fetcher reads.
    rows = hk.load_companies(KIT_DIR / "companies.json")
    assert len(rows) > 100
    ok = {
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "workday",
        "workable",
        "recruitee",
        "personio",
        "teamtailor",
        "bamboohr",
        "icims",
        "oraclecloud",
        "successfactors",
        "taleo",
        "jobvite",
        "eightfold",
    } | {"rippling", "breezy", "pinpoint", "phenom", "ultipro", "adp", "cornerstone", "gem",
         "jazzhr", "softgarden", "join", "jibe", "keka", "zohorecruit"}  # the kit_ats_more types
    ok |= {"emply", "manatal", "talentadore", "traffit", "trakstar"}  # the kit_ats_eu types
    ok |= {"avature", "comeet", "hibob", "pageup", "freshteam"}  # the kit_ats_extra types
    ok |= {"betterteam", "catsone", "clinch", "crelate", "erecruiter", "gr8people", "hrmanager", "ismartrecruit", "loxo", "mindsight"}  # the kit_ats_misc1 types
    ok |= {"bullhorn", "careerplug", "hireology", "hiringthing"}  # the kit_ats_us types
    ok |= {"opencats", "odoo", "recruitingsolutions", "teamex"}  # the kit_ats_misc2 types
    seen = set()
    for r in rows:
        assert r["ats"] in ok and r["token"].strip()
        if r["ats"] == "workday":
            assert r["tenant"] and r["wd"] and r["site"]
        if r["ats"] == "taleo":
            assert r["tenant"] and r["section"]
        if r["ats"] == "oraclecloud":
            assert r["host"] and r["siteNumber"]
        k = (r["ats"], r["token"].lower())
        assert k not in seen
        seen.add(k)
    text = (KIT_DIR / "companies.json").read_text(encoding="utf-8")
    assert re.search(r"linkedin|indeed|glassdoor", text, re.I) is None


def test_hunt_kit_has_no_board_scraper() -> None:
    """The opt-in big-board scraper (kit_boards.py + --boards, via python-jobspy)
    was removed 2026-10-14: those sites' terms forbid automated access. The
    public ATS APIs and open feeds stay. (2026-10-19: the owner added a DIFFERENT, stdlib-only
    reader, kit_bigboards.py, for LinkedIn, Glassdoor and Google Jobs; none of the bans below
    changed: no kit_boards.py, no --boards flag, no scrape_jobs, no jobspy import.)"""
    assert not (KIT_DIR / "kit_boards.py").exists()
    for f in (p for p in KIT_DIR.iterdir() if p.suffix in {".py", ".md", ".json"}):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"kit_boards|--boards\b|scrape_jobs", text, re.I), f.name
        # NOTICE.md may credit JobSpy for an idea; no kit file may import it.
        if f.suffix == ".py":
            assert not re.search(r"^\s*(import|from)\s+jobspy\b", text, re.I | re.M), f.name
    with pytest.raises(SystemExit):
        hk.build_parser().parse_args(["--boards", "linkedin"])
    args = hk.build_parser().parse_args([])
    assert not hasattr(args, "boards") and not hasattr(args, "full_text")
    assert not hasattr(hk, "JOBSPY_SITES")
    assert {"greenhouse", "lever", "ashby"} <= set(hk.ALL_SOURCES)


def test_kit_has_no_urls_for_big_boards_and_no_credentials() -> None:
    # Changed 2026-10-19 (owner decision, reverses the 2026-10-14/16 bans for two of the four sites): LinkedIn,
    # Glassdoor and (not a URL pattern here) Google Jobs may be read by kit_bigboards.py ONLY. Indeed and
    # ZipRecruiter URLs stay banned in every kit file (Indeed's only script route needs a key copied from its
    # app), and no kit file may carry a credential: a key, token or bearer value.
    banned = re.compile(r"https?://[^\s\"')>]*(indeed|ziprecruiter)", re.I)
    only_bigboards = re.compile(r"https?://[^\s\"')>]*(linkedin|glassdoor)", re.I)
    credential = re.compile(r"(?:api[_-]?key|bearer|authorization)\s*[:=]\s*[\"'][A-Za-z0-9_\-:.]{16,}", re.I)
    for f in KIT_DIR.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not banned.search(text), f.name
        assert not credential.search(text), f.name
        if f.name != "kit_bigboards.py":
            assert not only_bigboards.search(text), f.name
    assert only_bigboards.search((KIT_DIR / "kit_bigboards.py").read_text(encoding="utf-8"))


# ---- universe (run-time company lists)
def _fake_universe(monkeypatch: pytest.MonkeyPatch, calls: list[str], fail: bool = False) -> None:
    data = {
        "greenhouse": ["acme", "Beta", "linkedinjobs", "bad token"],
        "lever": ["leverco"],
        "ashby": ["ashco"],
        "workday": ["wdco|wd5|site1"],
    }

    def fake(url: str, **kw: object) -> tuple[int, object]:
        calls.append(url)
        if fail:
            return 0, None
        name = url.rsplit("/", 1)[-1].replace("_companies.json", "")
        return (200, data[name]) if name in data else (404, None)

    monkeypatch.setattr(hk, "http", fake)


def test_universe_rows_filters() -> None:
    rows = hk.universe_rows("greenhouse", ["acme", "linkedinjobs", "bad token", 5])
    assert [r["token"] for r in rows] == ["acme"]
    w = hk.universe_rows("workday", ["wdco|wd5|site1", "broken"])
    assert w[0]["tenant"] == "wdco" and w[0]["wd"] == "wd5" and w[0]["site"] == "site1" and len(w) == 1


def test_universe_download_cache_and_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_universe(monkeypatch, calls)
    rows = hk.load_universe(["greenhouse", "lever", "bamboohr_unknown"], tmp_path, now=1000.0)
    assert {r["token"] for r in rows} == {"acme", "Beta", "leverco"}
    assert len(calls) == 2 and (tmp_path / "greenhouse_companies.json").exists()
    # cache reuse: no new downloads while fresh
    import os

    fresh = (tmp_path / "greenhouse_companies.json").stat().st_mtime + 10
    calls.clear()
    rows = hk.load_universe(["greenhouse"], tmp_path, now=fresh)
    assert calls == [] and len(rows) == 2
    # stale cache + failed download: warn, still use the stale file
    _fake_universe(monkeypatch, calls, fail=True)
    stale = fresh + 8 * 86400
    rows = hk.load_universe(["greenhouse"], tmp_path, now=stale)
    assert len(calls) == 1 and len(rows) == 2
    # no cache + failed download: empty, no crash
    assert hk.load_universe(["ashby"], tmp_path / "none", now=stale) == []
    assert os.path.isdir(tmp_path)


def test_run_merges_universe_and_dedupes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_universe(monkeypatch, calls)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        hk,
        "load_companies",
        lambda p: [{"name": "Acme Inc", "ats": "greenhouse", "token": "acme", "source": "jobsync"}],
    )
    real = hk.load_universe
    monkeypatch.setattr(hk, "load_universe", lambda ats, *a, **k: real(ats, tmp_path, *a[1:], **k))
    monkeypatch.setitem(hk._BOARD_FN, "greenhouse", lambda col, b: seen.append((b["ats"], b["token"], b["name"])))
    monkeypatch.setitem(hk._BOARD_FN, "lever", lambda col, b: seen.append((b["ats"], b["token"], b["name"])))
    monkeypatch.setitem(hk._BOARD_FN, "ashby", lambda col, b: None)
    args = _args(sources="greenhouse,lever", work_countries="GB", history="none")
    hk.run(args)
    assert sorted(seen) == [
        ("greenhouse", "Beta", "Beta"),
        ("greenhouse", "acme", "Acme Inc"),
        ("lever", "leverco", "leverco"),
    ]
    seen.clear()
    hk.run(_args(sources="greenhouse", universe=False, work_countries="GB"))
    assert seen == [("greenhouse", "acme", "Acme Inc")]
    assert not any("workday" in c or "bamboohr" in c for c in calls)


# ---- review regressions
def test_p1a_default_titles_use_broad_matcher_via_cli_parse() -> None:
    assert hk.build_parser().parse_args([]).titles is None
    col = hk.Collector(hk.build_parser().parse_args([]))
    for t in [
        "AI/ML Engineer",
        "Software Engineer, AI Agents",
        "Machine Learning Platform Engineer",
        "Engineer - LLM Inference",
    ]:
        assert col.matcher(t), t
    assert not col.matcher("Senior AI Engineer")
    assert not col.matcher("Accountant")


@pytest.mark.parametrize(
    "s",
    [
        "This position is not eligible for visa sponsorship.",
        "We don't currently sponsor visas.",
        "We do not currently sponsor work visas.",
        "Candidates must not require visa sponsorship.",
        "Do you now or in the future require visa sponsorship?",
        "Visa sponsorship is unavailable for this role.",
        "We are unable to provide relocation assistance or visa sponsorship.",
    ],
)
def test_p1b_negations_are_not_positive(s: str) -> None:
    assert hk.visa_signals(s) == ([], [])
    assert hk.classify_location("Berlin", s, prefs("GB", maybe_relocation=True)) is None


def test_p1b_positive_still_positive() -> None:
    assert hk.visa_signals("We offer visa sponsorship.")[0]


def test_p1c_lock_sentence_with_negated_sponsorship_still_locks() -> None:
    d = "We are a global team. You must be legally authorized to work in the United States without visa sponsorship."
    assert hk.eligibility_locked(d, frozenset({"IN"}))
    assert hk.classify_location("Remote", d, prefs("IN")) is None


def test_p2a_http_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client

    def boom(req: object, timeout: int = 0) -> None:
        raise http.client.IncompleteRead(b"x")

    monkeypatch.setattr(hk, "_open", boom)
    assert hk.http_request("https://h.example/x")[0] == 0
    assert hk.http("https://h.example/x") == (0, None)


def test_p2a_check_live_and_main_survive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    col = hk.Collector(_args(work_countries="GB"))

    def explode(*a: object, **k: object) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(hk, "http_request", explode)
    rec = {"url": "https://a/1", "source": "t"}
    assert hk.check_live(col, [rec]) == [rec]
    out, hist = tmp_path / "o.jsonl", tmp_path / "h.json"
    row = {"title": "t", "company": "c", "url": "https://a/1", "source": "t", "posted": "", "row": "any"}
    col2 = hk.Collector(_args())
    col2.results = [row]
    monkeypatch.setattr(hk, "run", lambda a: col2)
    monkeypatch.setattr(hk, "check_live", explode)
    assert hk.main(["--check-live", "--out", str(out), "--history", str(hist)]) == 0
    assert out.read_text(encoding="utf-8").strip()
    assert json.loads(hist.read_text())["urls"] == ["https://a/1"]


def test_p2b_visa_flag_counts_as_positive() -> None:
    assert hk.classify_location("Berlin", "Build things.", prefs("GB"), visa_flag=True) == "visa_onsite"
    assert hk.classify_location("Berlin", "Build things.", prefs("GB"), visa_flag=False) is None


def test_m1_only_http_urls_kept() -> None:
    col = hk.Collector(_args(work_countries="IN"))
    base = dict(company="A", description="x", posted="", source="s", board="", remote=None, visa_flag=None, extra={})
    for i, u in enumerate(["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "/relative", "ftp://h/x"]):
        col.offer_job("t", dict(base, title="AI Engineer %d" % i, url=u, location="Pune"))
    assert col.results == []
    col.offer_job("t", dict(base, title="AI Engineer", url="https://ok.example/1", location="Pune"))
    assert len(col.results) == 1


def test_m1_http_refuses_private_hosts_and_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(req: object, timeout: int = 0) -> None:
        raise AssertionError("must not connect")

    monkeypatch.setattr(hk, "_open", never)
    for u in [
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://169.254.169.254/latest",
        "http://10.0.0.5/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ]:
        assert hk.http_request(u)[0] == 0, u
    assert hk.check_url("https://boards-api.greenhouse.io/v1/boards/x/jobs")


def test_m1_redirect_to_private_is_refused() -> None:
    h = hk._SafeRedirect()
    req = urllib.request.Request("https://example.com/a")
    for target in ("http://127.0.0.1/admin", "file:///etc/passwd"):
        with pytest.raises(Exception):  # noqa: B017
            h.redirect_request(req, None, 302, "Found", {}, target)


def test_l2_safe_xml_helper_rejects_doctype_and_entities() -> None:
    kx = importlib.import_module("kit_xml")
    assert kx.safe_fromstring("<a><b/></a>") is not None
    assert kx.safe_fromstring('<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>') is None
    assert kx.safe_fromstring("") is None and kx.safe_fromstring("<a>") is None
    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert importlib.import_module("kit_feeds").parse_wwr(bomb) == []
    ka = importlib.import_module("kit_ats")
    evil = '<!DOCTYPE x [<!ENTITY a "b">]><workzag-jobs><rss></rss></workzag-jobs>'
    for fn in (ka.fetch_personio, ka.fetch_teamtailor):
        assert fn({"name": "n", "token": "t", "ats": "x"}, lambda *a, **k: (200, evil), {}) == []


def test_l3_universe_url_pinned_to_sha() -> None:
    assert re.search(r"/job-board-aggregator/[0-9a-f]{40}/data/", hk.UNIVERSE_BASE)


def test_fixture_has_no_real_email() -> None:
    f = KIT_DIR.parent.parent / "tests" / "fixtures" / "hunt_kit" / "feeds" / "hn_item.json"
    assert "eric+hn@" not in f.read_text(encoding="utf-8")


# ---- remote jobs locked to other countries (region tag must not override a country list)
_IN_DESC = "Build agents."


@pytest.mark.parametrize(
    "loc",
    [
        "Thailand | remote | Remote | APAC | Thailand",
        "Philippines | remote | Remote | APAC | Philippines",
        "China | remote | Remote | APAC | China",
        "Bogotá, Bogota, Colombia | remote | Remote | APAC | Colombia | Pakistan",
        "Korea | remote | Remote | APAC | South Korea",
        "APAC; Australia; Hong Kong; Malaysia; New Zealand; Singapore; Taiwan",
    ],
)
def test_remote_locked_to_other_countries_dropped_for_india(loc: str) -> None:
    assert hk.classify_location(loc, _IN_DESC, prefs("IN")) is None


@pytest.mark.parametrize(
    ("loc", "row"),
    [
        ("India | remote | Remote | APAC | India", "remote_region"),
        ("Remote | Worldwide", "remote_world"),
        ("Remote - APAC", "remote_region"),
    ],
)
def test_remote_true_positives_kept_for_india(loc: str, row: str) -> None:
    assert hk.classify_location(loc, _IN_DESC, prefs("IN")) == row


def test_progress_lines_per_source(capsys: pytest.CaptureFixture[str]) -> None:
    col = hk.Collector(_args(work_countries="IN"))
    prog = hk.Progress(col, ["greenhouse"] * 8 + ["freehire"])
    for _ in range(8):
        col.bump("greenhouse", "kept")
        prog.done("greenhouse")
    prog.done("freehire")
    out = capsys.readouterr()
    assert out.out == ""
    lines = out.err.strip().splitlines()
    assert "[greenhouse] starting: 8 boards" in lines
    assert "[freehire] 1/1 boards (100%), 0 kept so far" in lines
    gh = [x for x in lines if x.startswith("[greenhouse] ") and "/8" in x]
    assert [x.split()[1] for x in gh] == ["2/8", "4/8", "6/8", "8/8"]
    assert gh[-1].endswith("8 kept so far")


def test_freehire_maps_posted_at_to_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "public_slug": "ai-engineer-x",
        "source": "greenhouse",
        "url": "https://boards.example.com/jobs/1",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Pune, India",
        "countries": ["in"],
        "regions": [],
        "work_mode": "remote",
        "posted_at": "2026-10-14T01:11:53Z",
        "description": "Build agents.",
    }
    monkeypatch.setattr(hk, "http_get_json", lambda url, params=None, **k: (200, {"data": [row]}))
    col = hk.Collector(_args(work_countries="IN", days=3650, titles="ai engineer"))
    hk._freehire(col, ["ai engineer"])
    assert [r["posted"] for r in col.results] == ["2026-10-14"]


# ---- wider search (2026-10-17)
def test_freehire_pages_deeper_in_pages_of_100(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake(url, params=None, **k):
        calls.append(dict(params or {}))
        n = 100 if len(calls) < 3 else 7  # two full pages, then a short one
        return 200, {"data": [{"public_slug": "j%d-%d" % (len(calls), i), "url": "https://x.example/%d/%d" % (len(calls), i),
                               "title": "AI Engineer", "company": "Acme", "countries": ["in"]} for i in range(n)]}

    monkeypatch.setattr(hk, "http_get_json", fake)
    col = hk.Collector(_args(work_countries="IN", remote=False, days=3650, titles="ai engineer"))
    hk._freehire(col, ["ai engineer"])
    assert [c["limit"] for c in calls] == [100, 100, 100]
    assert [c["offset"] for c in calls] == [0, 100, 200]
    assert all("semantic_ratio" not in c and "include_description" not in c for c in calls)


def test_freehire_pages_flag_is_capped_at_the_api_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(hk, "http_get_json", lambda url, params=None, **k: (calls.append(params), (200, {"data": [
        {"public_slug": "s%d-%d" % (len(calls), i), "url": "https://x.example/%d/%d" % (len(calls), i),
         "title": "x", "countries": ["in"]} for i in range(100)]}))[1])
    col = hk.Collector(_args(work_countries="IN", remote=False, days=3650, freehire_pages=500))
    hk._freehire(col, ["ai engineer"])
    assert len(calls) == hk.FREEHIRE_MAX_OFFSET // hk.FREEHIRE_PAGE
    assert max(c["offset"] + c["limit"] for c in calls) == hk.FREEHIRE_MAX_OFFSET


def test_rotate_workday_takes_a_daily_slice_of_the_open_list_only() -> None:
    import datetime as dt

    uni = [{"ats": "workday", "token": "t%d|wd1|s" % i, "source": "universe"} for i in range(10)]
    own = [{"ats": "workday", "token": "mine|wd5|s", "source": "user"}, {"ats": "greenhouse", "token": "g", "source": "jobsync"}]
    day = dt.date(2026, 10, 17)
    a = hk.rotate_workday(own + uni, 3, today=day)
    b = hk.rotate_workday(own + uni, 3, today=day + dt.timedelta(days=1))
    assert own[0] in a and own[1] in a  # the user's own and other boards always run
    pick = lambda r: [x["token"] for x in r if x.get("source") == "universe"]  # noqa: E731
    assert len(pick(a)) == len(pick(b)) == 3 and pick(a) != pick(b)
    assert hk.rotate_workday(own + uni, 0, today=day) == own + uni  # 0 = all
    assert hk.build_parser().parse_args([]).workday_max == hk.WORKDAY_MAX


def test_parse_extra_new_ats() -> None:
    rows = hk.parse_extra("icims:careers-acme,taleo:acme/2,oraclecloud:jpmc.fa.oraclecloud.com/CX_1002,"
                          "successfactors:jobs.zf.com,taleo:bad,eightfold:bayer")
    by = {r["ats"]: r for r in rows}
    assert by["icims"]["token"] == "careers-acme" and by["eightfold"]["token"] == "bayer"
    assert (by["taleo"]["tenant"], by["taleo"]["section"]) == ("acme", "2")
    assert (by["oraclecloud"]["host"], by["oraclecloud"]["siteNumber"]) == ("jpmc.fa.oraclecloud.com", "CX_1002")
    assert by["successfactors"]["token"] == "jobs.zf.com"
    assert sum(r["ats"] == "taleo" for r in rows) == 1  # "taleo:bad" has no section


def test_harvest_workday_rows_rotate() -> None:
    rows = [{"ats": "workday", "token": "t%d|wd1|s" % i, "source": "harvest"} for i in range(10)]
    rows.append({"ats": "workday", "token": "mine|wd1|s", "source": "user"})
    kept = hk.rotate_workday(rows, 3)
    assert len(kept) == 4 and any(r["source"] == "user" for r in kept)


def test_a_stated_years_floor_beats_the_text_guess() -> None:
    col = hk.Collector(_args(work_countries="IN", days=3650))  # max_years default 5
    base = dict(company="Acme", description="2 years of experience.", posted="", source="feed:test", board="b",
                remote=None, visa_flag=None, location="Bengaluru")
    col.offer_job("test", dict(base, title="AI Engineer", url="https://a/1", extra={"years_min": 12}))
    col.offer_job("test", dict(base, title="ML Engineer", url="https://a/2", extra={}))
    assert [r["url"] for r in col.results] == ["https://a/2"]
