"""Offline tests for the hunt kit's job-closed check, dead-board memory and --why-missed."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_liveness_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()

JOB_PAGE = "<html><body><h1>AI Engineer</h1><p>" + "You will build agents and evaluation tools. " * 12 + "</p></body></html>"


def _args(**kw: object):
    a = hk.build_parser().parse_args([])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------- liveness
def _state(status: int, text: str, final: str = "https://x.example/job/12345678", req: str = "") -> tuple[str, str]:
    return tuple(hk.classify_liveness(status, req or final, final, text))  # type: ignore[return-value]


@pytest.mark.parametrize("status", [404, 410])
def test_gone_status_is_closed(status: int) -> None:
    assert _state(status, "") == ("closed", "http_gone")


def test_error_redirect_is_closed() -> None:
    assert _state(200, JOB_PAGE, final="https://job-boards.greenhouse.io/acme?error=true") == ("closed", "expired_url")


@pytest.mark.parametrize(
    "phrase",
    [
        "Sorry, this job is no longer available.",
        "This position has been filled.",
        "This role is closed.",
        "Applications closed on 15 December 2025.",
        "Diese Stelle ist bereits besetzt.",  # German
        "Deze vacature is niet meer gepubliceerd",  # Dutch
        "Oferta cerrada el 15/12/2025",  # Spanish
        "Cette offre n’est plus disponible.",  # French, typographic apostrophe
        "Offre expirée",  # French, accent
        "Ce poste n'est plus ouvert",
        "Вакансия в архиве",  # Russian
    ],
)
def test_closed_phrases_in_many_languages(phrase: str) -> None:
    page = "<html><body><nav>Jobs</nav><div>%s</div>%s</body></html>" % (phrase, "<p>Menu item</p>" * 3)
    assert _state(200, page) == ("closed", "expired_body")


@pytest.mark.parametrize(
    "prose",
    [
        "Applications will be accepted until the position has been filled.",  # a promise, not a banner
        "We review every job posting until the role has been filled by the right person.",
        "We will contact shortlisted candidates once applications have closed.",
        "This role is closed-loop control of the platform.",
        "Your job application form has been filled from your CV; check it before you send.",  # form lookbehind
    ],
)
def test_live_posting_copy_is_not_read_as_closed(prose: str) -> None:
    page = JOB_PAGE.replace("</body>", "<p>%s</p></body>" % prose)
    assert _state(200, page) == ("alive", "no_death_signal")


def test_alive_page() -> None:
    assert _state(200, JOB_PAGE) == ("alive", "no_death_signal")


def test_js_shell_stays_unknown() -> None:
    shell = '<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
    assert _state(200, shell) == ("unknown", "js_shell")


@pytest.mark.parametrize(
    ("status", "code"),
    [(0, "fetch_failed"), (403, "status_403"), (429, "status_429"), (500, "status_500"), (503, "status_503")],
)
def test_blocked_or_failed_fetch_is_unknown(status: int, code: str) -> None:
    assert _state(status, "") == ("unknown", code)


def test_bot_wall_is_unknown_not_closed() -> None:
    assert _state(200, "<html><body>Just a moment...</body></html>") == ("unknown", "bot_challenge")


def test_redirect_that_lost_the_job_id_is_unknown() -> None:
    res = _state(200, JOB_PAGE, final="https://careers.example/search", req="https://careers.example/job/99887766")
    assert res == ("unknown", "redirected_off_posting")


def test_listing_page_and_near_empty_page_are_closed() -> None:
    assert _state(200, JOB_PAGE.replace("</body>", "<p>42 jobs found</p></body>")) == ("closed", "listing_page")
    assert _state(200, "<html><body><p>Careers</p></body></html>") == ("closed", "insufficient_content")
    assert _state(200, "Apply now") == ("unknown", "short_page_with_apply")


def test_is_dead_page_bool_api_kept() -> None:
    assert hk.is_dead_page(404, "u", "") and not hk.is_dead_page(200, "u", JOB_PAGE)


def test_check_live_drops_only_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "https://a/closed": (200, "https://a/closed", b"This job is no longer available."),
        "https://a/gone": (404, "https://a/gone", b""),
        "https://a/open": (200, "https://a/open", JOB_PAGE.encode()),
        "https://a/blocked": (429, "https://a/blocked", b""),
        "https://a/down": (0, "https://a/down", b""),
    }
    monkeypatch.setattr(hk, "http_request", lambda url, **k: pages[url])
    col = hk.Collector(_args())
    recs = [{"url": u, "source": "t"} for u in pages]
    kept = hk.check_live(col, recs)
    assert sorted(r["url"] for r in kept) == ["https://a/blocked", "https://a/down", "https://a/open"]
    assert col.stats["t"]["live_dead"] == 2 and col.stats["t"]["live_unknown"] == 2


# ---------------------------------------------------------------- dead boards
D0 = dt.date(2026, 10, 1)


def test_three_404s_in_a_row_mark_a_board_dead() -> None:
    boards: dict = {}
    for i in range(2):
        boards = hk.update_dead_boards(boards, {"greenhouse:gone": "404"}, D0 + dt.timedelta(days=i))
        assert not hk.is_dead_board(boards["greenhouse:gone"], D0 + dt.timedelta(days=i))
    boards = hk.update_dead_boards(boards, {"greenhouse:gone": "404"}, D0 + dt.timedelta(days=2))
    assert boards["greenhouse:gone"]["misses"] == 3
    assert hk.is_dead_board(boards["greenhouse:gone"], D0 + dt.timedelta(days=3))
    # a dead board is looked at again after 30 days
    assert not hk.is_dead_board(boards["greenhouse:gone"], D0 + dt.timedelta(days=2 + hk.DEAD_BOARD_RECHECK_DAYS))


def test_success_resets_and_unknown_does_not_count() -> None:
    boards = hk.update_dead_boards({}, {"lever:x": "404"}, D0)
    boards = hk.update_dead_boards(boards, {"lever:x": "404"}, D0)
    assert boards["lever:x"]["misses"] == 2
    assert hk.update_dead_boards(boards, {"lever:x": ""}, D0) == boards  # throttled / transport error: unchanged
    assert hk.update_dead_boards(boards, {}, D0) == boards  # not fetched this run: unchanged
    assert hk.update_dead_boards(boards, {"lever:x": "ok"}, D0) == {}


def test_board_outcome_only_real_404_counts() -> None:
    assert hk.board_outcome([404]) == "404"
    assert hk.board_outcome([404, 404]) == "404"
    assert hk.board_outcome([404, 200]) == "ok"  # lever: US region 404, EU region answers
    assert hk.board_outcome([0]) == ""  # transport error
    assert hk.board_outcome([404, 0]) == ""
    assert hk.board_outcome([429]) == "" and hk.board_outcome([503]) == "" and hk.board_outcome([]) == ""


def test_old_and_damaged_history_files_load(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"urls": ["u"], "keys": ["k"], "runs": []}), encoding="utf-8")
    assert hk.load_dead_boards(str(old)) == {}
    assert hk.load_history(str(old)) == ({"u"}, {"k"})
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"boards": {"a:b": {"misses": "x"}, "c:d": 5, "e:f": {"misses": 3, "last": "2026-10-01"}}}))
    assert hk.load_dead_boards(str(bad)) == {"e:f": {"misses": 3, "last": "2026-10-01"}}
    assert hk.load_dead_boards(str(tmp_path / "missing.json")) == {} and hk.load_dead_boards("none") == {}
    hk.save_history(str(old), {"u"}, {"k"}, [])  # an old file gains the field without losing anything
    assert json.loads(old.read_text())["boards"] == {}


def _fake_statuses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Greenhouse boards named gone* answer 404, flaky* answer 503, others 200 with no jobs."""
    asked: list[str] = []

    def fake(url: str, *a: object, **k: object):
        asked.append(url)
        if "/gone" in url:
            return 404, url, b""
        if "/flaky" in url:
            return 503, url, b""
        return 200, url, b'{"jobs": []}'

    monkeypatch.setattr(hk, "_http_request", fake)
    return asked


def _main(tmp_path: Path, n: int, *extra: str) -> int:
    (tmp_path / "none.json").write_text("[]")
    return hk.main([
        "--sources", "greenhouse", "--no-universe", "--companies", str(tmp_path / "none.json"),
        "--companies-extra", "greenhouse:gone,greenhouse:flaky,greenhouse:fine", "--any-location",
        "--history", str(tmp_path / "seen.json"), "--out", str(tmp_path / ("o%d.jsonl" % n)),
        "--stream", str(tmp_path / ("s%d.jsonl" % n)), "--run-log", "none", *extra,
    ])


def test_dead_board_skipped_after_three_runs_and_retry_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _fake_statuses(monkeypatch)
    for n in range(3):
        assert _main(tmp_path, n) == 0
    assert sum("/gone" in u for u in asked) == 3 and sum("/flaky" in u for u in asked) == 3
    boards = json.loads((tmp_path / "seen.json").read_text())["boards"]
    assert boards["greenhouse:gone"]["misses"] == 3
    assert "greenhouse:flaky" not in boards and "greenhouse:fine" not in boards  # 503 and 200 leave no counter
    asked.clear()
    capsys.readouterr()
    assert _main(tmp_path, 3) == 0
    assert not any("/gone" in u for u in asked) and any("/flaky" in u for u in asked)
    assert "skipped 1 board as dead" in capsys.readouterr().err
    asked.clear()
    assert _main(tmp_path, 4, "--retry-dead") == 0
    assert any("/gone" in u for u in asked)


def test_success_after_404s_clears_the_counter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hist = tmp_path / "seen.json"
    hist.write_text(json.dumps({"urls": [], "keys": [], "boards": {"greenhouse:gone": {"misses": 2, "last": "2026-10-01"}}}))
    monkeypatch.setattr(hk, "_http_request", lambda url, *a, **k: (200, url, b'{"jobs": []}'))
    assert _main(tmp_path, 0) == 0
    assert "greenhouse:gone" not in json.loads(hist.read_text())["boards"]


# ---------------------------------------------------------------- why-missed
@pytest.fixture
def why_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("--why-missed must not use the network")

    monkeypatch.setattr(hk, "_open", boom)
    monkeypatch.setattr(hk, "http_request", boom)
    companies = tmp_path / "companies.json"
    companies.write_text(json.dumps([{"name": "Known", "ats": "greenhouse", "board": "known", "source": "t"},
                                     {"name": "Acme Corp", "ats": "greenhouse", "board": "acx", "source": "t"}]))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "lever_companies.json").write_text(json.dumps(["inuniverse"]))
    (cache / "greenhouse_companies.json").write_text(json.dumps(["othergh"]))
    real = hk.cached_universe
    monkeypatch.setattr(hk, "cached_universe", lambda ats, cache_dir=None: real(ats, cache))

    def ask(url: str, **kw: object) -> dict:
        a = _args(companies=str(companies), history=str(tmp_path / "seen.json"), **kw)
        return hk.why_missed(a, url, D0)

    return ask


def test_why_missed_verdicts(why_setup, tmp_path: Path) -> None:
    ask = why_setup
    r = ask("https://job-boards.greenhouse.io/newco/jobs/123")
    assert (r["verdict"], r["board"]) == ("board_not_in_list", "greenhouse:newco")
    assert 'add --companies-extra "greenhouse:newco"' in r["change"]
    assert ask("https://boards.greenhouse.io/known/jobs/1")["verdict"] == "board_covered"
    assert ask("https://boards.greenhouse.io/newco/jobs/1", companies_extra="greenhouse:newco")["verdict"] == "board_covered"
    r = ask("https://jobs.lever.co/inuniverse/abc-123")
    assert r["verdict"] == "board_covered" and "cached open company list" in r["evidence"][0]
    assert ask("https://jobs.lever.co/inuniverse/abc-123", universe=False)["verdict"] == "board_not_in_list"
    r = ask("https://jobs.ashbyhq.com/someone/1")  # ashby universe file is not cached
    assert r["verdict"] == "universe_not_cached" and "companies-extra" in r["change"]
    r = ask("https://acme.bamboohr.com/careers/5", sources="lever")  # bamboohr runs by default now; not in this list
    assert r["verdict"] == "board_in_list_source_skipped" and r["change"] == "--sources lever,bamboohr"
    r = ask("https://boards.greenhouse.io/known/jobs/1", sources="lever")
    assert r["verdict"] == "board_in_list_source_skipped" and r["change"] == "--sources lever,greenhouse"
    assert ask("https://boards.greenhouse.io/known/jobs/1", skip_companies="known")["verdict"] == "skipped_by_option"
    # the run lowercases the skip words and also matches the row's company name
    assert ask("https://boards.greenhouse.io/known/jobs/1", skip_companies="KNOWN")["verdict"] == "skipped_by_option"
    assert ask("https://boards.greenhouse.io/acx/jobs/1")["verdict"] == "board_covered"
    assert ask("https://boards.greenhouse.io/acx/jobs/1", skip_companies="Acme")["verdict"] == "skipped_by_option"


def test_why_missed_other_verdicts(why_setup, tmp_path: Path) -> None:
    ask = why_setup
    # Owner decision 2026-10-19: the kit reads LinkedIn and Glassdoor itself (kit_bigboards), so their links are
    # "feed_covered" now (they were "not_a_board_url" under the 2026-10-16 rule). Indeed is still never read.
    r = ask("https://www.linkedin.com/jobs/view/123456")
    assert (r["verdict"], r["board"]) == ("feed_covered", "feed:linkedin")
    r = ask("https://www.glassdoor.co.uk/job-listing/j?jl=123")
    assert (r["verdict"], r["board"]) == ("feed_covered", "feed:glassdoor")
    r = ask("https://www.linkedin.com/jobs/view/123456", sources="greenhouse")
    assert r["verdict"] == "board_in_list_source_skipped" and r["change"] == "--sources greenhouse,linkedin"
    assert ask("https://uk.indeed.com/viewjob?jk=abc")["verdict"] == "not_a_board_url"
    assert ask("https://acme.comeet.com/jobs/abc")["verdict"] == "ats_not_supported"  # breezy is read since kit_ats_more
    assert ask("https://careers.example.com/jobs/1")["verdict"] == "ats_not_supported"
    r = ask("https://careers.example.com/jobs/1?gh_jid=555")  # Greenhouse embedded on a company domain
    assert r["verdict"] == "ats_not_supported" and "greenhouse" in r["change"]
    assert ask("not a url")["verdict"] == "invalid_url"
    # job sites the kit reads as a feed are not "aggregators the kit never reads"
    r = ask("https://www.reed.co.uk/jobs/data-engineer/123")
    assert (r["verdict"], r["board"]) == ("feed_covered", "feed:reed")
    assert ask("https://my.jobstreet.com/job/88")["verdict"] == "feed_covered"
    r = ask("https://www.seek.com.au/job/92996157", sources="greenhouse")
    assert r["verdict"] == "board_in_list_source_skipped" and r["change"] == "--sources greenhouse,jobstreet"
    (tmp_path / "seen.json").write_text(json.dumps({"boards": {"greenhouse:known": {"misses": 3, "last": "2026-09-25"}}}))
    r = ask("https://boards.greenhouse.io/known/jobs/1")
    assert r["verdict"] == "board_marked_dead" and "--retry-dead" in r["change"]
    assert ask("https://boards.greenhouse.io/known/jobs/1", retry_dead=True)["verdict"] == "board_covered"


@pytest.mark.parametrize(
    ("url", "ats", "token"),
    [
        ("https://job-boards.greenhouse.io/Acme/jobs/1", "greenhouse", "acme"),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=1", "greenhouse", "acme"),
        ("https://jobs.eu.lever.co/acme/uuid", "lever", "acme"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby", "acme"),
        ("https://apply.workable.com/acme/j/ABC/", "workable", "acme"),
        ("https://acme.recruitee.com/o/engineer", "recruitee", "acme"),
        ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/London/AI-Eng_R1", "workday", "acme|wd5|careers"),
        ("https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=1", "taleo", "acme/ex"),
        ("https://careers-acme.icims.com/jobs/1/x/job", "icims", "careers-acme"),
    ],
)
def test_board_from_job_url(url: str, ats: str, token: str) -> None:
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


def test_why_missed_cli_prints_and_exits_zero(
    why_setup, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = hk.main(["--why-missed", "https://jobs.lever.co/newco/1", "--no-universe",
                  "--companies", str(tmp_path / "companies.json"), "--history", str(tmp_path / "seen.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "Verdict: board_not_in_list" in out and "Change: add --companies-extra" in out
    assert not (tmp_path / "seen.json").exists()  # read-only


# --------------------------------------------------------------------------- regex scaling (hostile markup)
def _grows_linearly(fn, make, n=6000):
    """Time fn(make(n)) and fn(make(4n)): linear stays within 8x (+50 ms of noise room), quadratic takes ~16x."""
    import time

    def run(k):
        s, best = make(k), 9e9
        for _ in range(2):
            t0 = time.perf_counter()
            fn(s)
            best = min(best, time.perf_counter() - t0)
        return best

    t1, t4 = run(n), run(4 * n)
    assert t4 < 8 * t1 + 0.05, (t1, t4)


def test_visible_text_is_linear_on_unclosed_blocks():
    assert hk._visible_text("<style>a{}</style><p>Hi <b>there</b></p><!-- c --><script>x()</script>end") == "Hi there end"
    assert hk._visible_text("a<script>never closed") == "a never closed"  # no closer: only the tag goes
    for evil in ("<script>", "<!--", "<svg", '<a href="', "<"):
        _grows_linearly(hk._visible_text, lambda k, e=evil: e * k)
