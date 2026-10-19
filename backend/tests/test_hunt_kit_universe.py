"""Offline tests for the hunt kit's open company lists: row parsing, the 7-day rotation, the per-host limit."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_universe_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()
GUID = "73b6525a-3273-48b3-b9d8-d6479233c61c"
DAY = dt.date(2026, 10, 19)


def _rows(ats: str, n: int, source: str = "universe") -> list[dict[str, Any]]:
    return [{"ats": ats, "token": "%s%d" % (ats[:2], i), "source": source} for i in range(n)]


def test_pinned_commit_and_file_names() -> None:
    assert "/ba8cfa9dbc92aab1c2c253aab29367e0e01ac986/data/" in hk.UNIVERSE_BASE
    assert {"icims", "paylocity"} <= set(hk.UNIVERSE_ATS)
    assert hk.universe_file("paylocity") == "paylocity_companies_clean.json"
    assert hk.universe_file("icims") == "icims_companies.json"
    assert hk.UNIVERSE_BASE % hk.universe_file("lever") == hk.UNIVERSE_BASE.replace("%s", "lever_companies.json")


def test_icims_tokens_must_look_like_a_host_label() -> None:
    rows = hk.universe_rows("icims", ["-careers-harrowcouncil", "121restaurants", "careers-acme", "Bad Token", "x_y"])
    assert [r["token"] for r in rows] == ["121restaurants", "careers-acme"]
    assert all(r["source"] == "universe" and r["ats"] == "icims" for r in rows)


def test_paylocity_rows_come_from_objects() -> None:
    data = [
        {"guid": GUID, "name": "10 Design", "jobs": 0},
        {"guid": GUID.upper(), "name": "Upper Co", "jobs": 1},
        {"guid": "not-a-guid", "name": "Bad", "jobs": 3},
        {"guid": "03950a95-b278-4adf-9e56-296ee2c0058a", "name": "", "jobs": 2},
        {"name": "No guid"},
        "73b6525a-3273-48b3-b9d8-d6479233c61c",
        {"guid": "11111111-2222-3333-4444-555555555555", "name": "Indeed Staffing"},
    ]
    rows = hk.universe_rows("paylocity", data)
    assert [(r["name"], r["token"]) for r in rows] == [
        ("10 Design", GUID),
        ("Upper Co", GUID.upper()),
        ("03950a95-b278-4adf-9e56-296ee2c0058a", "03950a95-b278-4adf-9e56-296ee2c0058a"),
    ]
    assert all(r["ats"] == "paylocity" and r["source"] == "universe" for r in rows)


def test_cached_universe_reads_the_per_ats_files(tmp_path: Path) -> None:
    (tmp_path / "paylocity_companies_clean.json").write_text(json.dumps([{"guid": GUID, "name": "P"}]))
    (tmp_path / "icims_companies.json").write_text(json.dumps(["-bad", "good"]))
    rows, missing = hk.cached_universe(["paylocity", "icims", "lever"], tmp_path)
    assert sorted((r["ats"], r["token"]) for r in rows) == [("icims", "good"), ("paylocity", GUID)]
    assert missing == ["lever"]


def test_load_universe_downloads_the_paylocity_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    urls: list[str] = []

    def fake_http(url: str, *a: Any, **k: Any) -> tuple[int, Any]:
        urls.append(url)
        return 200, [{"guid": GUID, "name": "P"}]

    monkeypatch.setattr(hk, "http", fake_http)
    rows = hk.load_universe(["paylocity"], tmp_path)
    assert urls == [hk.UNIVERSE_BASE % "paylocity_companies_clean.json"]
    assert rows[0]["token"] == GUID and (tmp_path / "paylocity_companies_clean.json").exists()


def test_paylocity_is_a_registered_board_source() -> None:
    assert "paylocity" in hk.BOARD_SOURCES and "paylocity" in hk.ALL_SOURCES
    assert hk.parse_extra("paylocity:%s" % GUID)[0]["ats"] == "paylocity"


@pytest.mark.parametrize("ats,n", [("workday", 21), ("icims", 21), ("bamboohr", 35), ("paylocity", 14)])
def test_rotate_open_covers_every_pool_row_once_in_seven_days(ats: str, n: int) -> None:
    vetted = [{"ats": ats, "token": "mine", "source": "user"}, {"ats": "greenhouse", "token": "g", "source": "universe"}]
    pool = _rows(ats, n)
    seen: list[str] = []
    for d in range(hk.OPEN_CYCLE_DAYS):
        got = hk.rotate_open(vetted + pool, {}, DAY + dt.timedelta(days=d))
        assert vetted[0] in got and vetted[1] in got  # vetted rows and other ATS always run
        seen += [b["token"] for b in got if b.get("source") == "universe" and b["ats"] == ats]
    assert sorted(seen) == sorted(b["token"] for b in pool)  # exactly once: n divides by 7


def test_rotate_open_covers_everything_when_the_pool_does_not_divide_by_seven() -> None:
    pool = _rows("workday", 20)
    seen: set[str] = set()
    for d in range(hk.OPEN_CYCLE_DAYS):
        seen |= {b["token"] for b in hk.rotate_open(pool, {}, DAY + dt.timedelta(days=d))}
    assert seen == {b["token"] for b in pool}
    assert len(hk.rotate_open(pool, {}, DAY)) == 3  # ceil(20 / 7)


def test_rotate_open_leaves_fast_ats_and_vetted_rows_alone() -> None:
    fast = _rows("greenhouse", 50) + _rows("lever", 50, "harvest")
    vetted = _rows("workday", 30, "user") + _rows("icims", 30, "jobsync")
    pool = _rows("icims", 14)
    got = hk.rotate_open(fast + vetted + pool, {}, DAY)
    assert all(b in got for b in fast + vetted)
    assert sum(b in got for b in pool) == 2
    assert [b for b in got if b not in pool] == fast + vetted  # order kept


def test_rotate_open_harvest_rows_rotate_too() -> None:
    pool = _rows("bamboohr", 14, "harvest")
    assert len(hk.rotate_open(pool, {}, DAY)) == 2


def test_open_days_zero_keeps_everything() -> None:
    boards = _rows("workday", 30) + _rows("icims", 30) + _rows("bamboohr", 30) + _rows("paylocity", 30)
    assert hk.rotate_open(boards, {}, DAY, open_days=0) == boards
    assert hk.build_parser().parse_args([]).open_days == 7
    assert hk.build_parser().parse_args(["--open-days", "0"]).open_days == 0


def test_explicit_workday_limit_overrides_the_default_slice() -> None:
    boards = _rows("workday", 30) + _rows("icims", 14)
    got = hk.rotate_open(boards, {"workday": 5}, DAY)
    assert sum(b["ats"] == "workday" for b in got) == 5 and sum(b["ats"] == "icims" for b in got) == 2
    assert sum(b["ats"] == "workday" for b in hk.rotate_open(boards, {"workday": 0}, DAY)) == 30


def test_default_sources_include_bamboohr_and_paylocity() -> None:
    d = hk.select_sources("default")
    assert "bamboohr" in d and "paylocity" in d and "icims" in d and d == hk.select_sources("all")


def test_single_host_ats_never_run_more_than_six_at_once() -> None:
    sems = {a: threading.BoundedSemaphore(hk.SINGLE_HOST_CONCURRENCY) for a in hk.SINGLE_HOST_ATS}
    lock = threading.Lock()
    live = {"greenhouse": 0, "workday": 0}
    peak = {"greenhouse": 0, "workday": 0}

    def work(ats: str) -> None:
        with lock:
            live[ats] += 1
            peak[ats] = max(peak[ats], live[ats])
        time.sleep(0.02)
        with lock:
            live[ats] -= 1

    threads = [threading.Thread(target=hk.with_slot, args=(sems.get(a), work, a))
               for a in ["greenhouse"] * 30 + ["workday"] * 14]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hk.MAX_WORKERS == 16 and hk.SINGLE_HOST_CONCURRENCY == 6
    assert peak["greenhouse"] <= 6 and peak["greenhouse"] > 1
    assert peak["workday"] > 6  # per-tenant hosts are not limited


def test_run_log_and_stats_carry_board_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gh = [{"name": "G%d" % i, "ats": "greenhouse", "token": "g%d" % i, "source": "user"} for i in range(3)]
    uni = [{"name": "I%d" % i, "ats": "icims", "token": "i%d" % i, "source": "universe"} for i in range(14)]
    monkeypatch.setattr(hk, "load_companies", lambda p: gh)
    monkeypatch.setattr(hk, "load_universe", lambda ats_list, *a, **k: uni)
    monkeypatch.setattr(hk, "http_get_json", lambda *a, **k: (404, None))
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"icims": lambda row, http, st: []})
    runs = tmp_path / "runs.jsonl"
    rc = hk.main(["--sources", "greenhouse,icims", "--titles", "ai engineer", "--out", str(tmp_path / "o.jsonl"),
                  "--history", "none", "--run-log", str(runs), "--stats", "--limit-boards", "0"])
    assert rc == 0
    row = json.loads(runs.read_text().splitlines()[-1])
    assert row["boards_listed"] == {"greenhouse": 3, "icims": 14}
    assert row["boards_this_run"]["greenhouse"] == 3 and row["boards_this_run"]["icims"] == 2
    assert all(isinstance(v, int) for v in row["boards_listed"].values())


def test_limit_boards_applies_after_rotation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uni = [{"name": "I%d" % i, "ats": "icims", "token": "i%d" % i, "source": "universe"} for i in range(70)]
    monkeypatch.setattr(hk, "load_companies", lambda p: [])
    monkeypatch.setattr(hk, "load_universe", lambda ats_list, *a, **k: uni)
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"icims": lambda row, http, st: []})
    args = hk.build_parser().parse_args(["--sources", "icims", "--limit-boards", "4", "--history", "none",
                                         "--stream", str(tmp_path / "s.jsonl"), "--run-log", "none"])
    col = hk.run(args)
    assert col.boards_listed == {"icims": 70} and col.boards_this_run == {"icims": 4}
    assert "boards_listed: icims=70" in hk.format_stats(col) and "boards_this_run: icims=4" in hk.format_stats(col)


# ------------------------------------------------------------ progress-based rotation cursor
def _cursor_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: list[str], n: int = 14,
                fetch: Any = None, hist: str | None = None) -> Any:
    uni = [{"name": "I%d" % i, "ats": "icims", "token": "i%02d" % i, "source": "universe"} for i in range(n)]
    monkeypatch.setattr(hk, "load_companies", lambda p: [])
    monkeypatch.setattr(hk, "load_universe", lambda ats_list, *a, **k: uni)
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"icims": fetch or (lambda row, http, st: [])})
    args = hk.build_parser().parse_args(
        ["--sources", "icims", "--history", hist or str(tmp_path / "h.json"), "--workers", "1",
         "--stream", str(tmp_path / "s.jsonl"), "--run-log", "none"] + extra)
    return hk.run(args)


def test_rotate_open_takes_the_slice_from_the_offset_and_wraps() -> None:
    pool = _rows("icims", 14)  # slice of 2
    assert [b["token"] for b in hk.rotate_open(pool, {}, DAY, offsets={"icims": 4})] == ["ic4", "ic5"]
    assert [b["token"] for b in hk.rotate_open(pool, {}, DAY, offsets={"icims": 13})] == ["ic0", "ic13"]  # wraps (pool order kept)
    assert [b["token"] for b in hk.rotate_open(pool, {}, DAY, offsets={})] == ["ic0", "ic1"]
    assert [b["token"] for b in hk.rotate_open(pool, {}, DAY, offsets={"icims": 30})] == ["ic2", "ic3"]  # 30 % 14


def test_cursor_advances_only_by_finished_boards_and_a_stopped_run_leaves_the_rest(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def fetch(row: dict, http: Any, st: dict) -> list:
        seen.append(row["token"])
        return []

    hist = str(tmp_path / "h.json")
    # first run: the budget lets only one of the two slice boards start
    state = {"n": 0}
    orig_admit = hk.Stream.admit

    def admit(self: Any) -> bool:
        state["n"] += 1
        if state["n"] > 1:
            with self.lock:
                self.stop_reason = "test budget"
            return False
        return orig_admit(self)

    monkeypatch.setattr(hk.Stream, "admit", admit)
    sink = hk.Stream(str(tmp_path / "s.jsonl"), "fp")
    sink.open(False)
    col = _cursor_run_with_sink(monkeypatch, tmp_path, sink, fetch, hist)
    assert seen == ["i00"] and col.cursor == {"icims": 1}
    hk.save_rotation_cursor(hist, col.cursor)
    assert hk.load_rotation_cursor(hist) == {"icims": 1}
    sink.close()
    # second run (no budget): starts at the board the first run never reached
    monkeypatch.setattr(hk.Stream, "admit", orig_admit)
    seen.clear()
    col2 = _cursor_run(monkeypatch, tmp_path, [], fetch=fetch, hist=hist)
    assert seen == ["i01", "i02"] and col2.cursor == {"icims": 3}


def _cursor_run_with_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sink: Any, fetch: Any, hist: str) -> Any:
    uni = [{"name": "I%d" % i, "ats": "icims", "token": "i%02d" % i, "source": "universe"} for i in range(14)]
    monkeypatch.setattr(hk, "load_companies", lambda p: [])
    monkeypatch.setattr(hk, "load_universe", lambda ats_list, *a, **k: uni)
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"icims": fetch})
    args = hk.build_parser().parse_args(["--sources", "icims", "--history", hist, "--workers", "1",
                                         "--run-log", "none"])
    args.sink = sink
    return hk.run(args)


def test_a_board_that_raises_still_counts_as_visited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fetch(row: dict, http: Any, st: dict) -> list:
        raise RuntimeError("boom")

    col = _cursor_run(monkeypatch, tmp_path, [], fetch=fetch)
    assert col.cursor == {"icims": 2} and len(col.failed) == 2


def test_resume_does_not_double_advance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hist = str(tmp_path / "h.json")
    sink = hk.Stream(str(tmp_path / "s.jsonl"), "fp")
    sink.open(False)
    sink.mark_done("icims:i00")  # a board the stopped run already finished
    # the stopped run saved its cursor (one board finished): the resumed slice starts after it
    hk.save_rotation_cursor(hist, {"icims": 1})
    seen: list[str] = []

    def fetch(row: dict, http: Any, st: dict) -> list:
        seen.append(row["token"])
        return []

    col = _cursor_run_with_sink(monkeypatch, tmp_path, sink, fetch, hist)
    sink.close()
    assert seen == ["i01", "i02"] and col.cursor == {"icims": 3}  # 1 + the two boards run now, i00 not recounted


def test_resume_counts_slice_boards_already_done_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hist = str(tmp_path / "h.json")
    sink = hk.Stream(str(tmp_path / "s.jsonl"), "fp")
    sink.open(False)
    sink.mark_done("icims:i01")  # done earlier, and inside the slice that starts at offset 1
    hk.save_rotation_cursor(hist, {"icims": 1})
    seen: list[str] = []
    col = _cursor_run_with_sink(monkeypatch, tmp_path, sink, lambda r, h, s: seen.append(r["token"]) or [], hist)
    sink.close()
    assert seen == ["i02"] and col.cursor == {"icims": 3}


def test_pool_shrink_clamps_the_offset_with_modulo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hist = str(tmp_path / "h.json")
    hk.save_rotation_cursor(hist, {"icims": 40})  # the list used to be bigger
    seen: list[str] = []
    col = _cursor_run(monkeypatch, tmp_path, [], n=14, fetch=lambda r, h, s: seen.append(r["token"]) or [], hist=hist)
    assert seen == ["i12", "i13"] and col.cursor == {"icims": 0}  # 40 % 14 = 12, then 14 wraps to 0


def test_history_none_keeps_the_cursor_in_memory_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    col = _cursor_run(monkeypatch, tmp_path, [], hist="none")
    assert col.cursor == {"icims": 2}
    assert hk.load_rotation_cursor("none") == {} and hk.load_rotation_cursor(None) == {}
    hk.save_rotation_cursor("none", {"icims": 5})
    assert not list(tmp_path.glob("*.rotation.json")) and not (tmp_path / "none.rotation.json").exists()


def test_missing_or_broken_cursor_file_starts_at_zero(tmp_path: Path) -> None:
    hist = str(tmp_path / "h.json")
    assert hk.load_rotation_cursor(hist) == {}
    Path(hist + hk.CURSOR_SUFFIX).write_text("{not json", encoding="utf-8")
    assert hk.load_rotation_cursor(hist) == {}
    Path(hist + hk.CURSOR_SUFFIX).write_text('{"icims": "x", "workday": -1, "paylocity": 3}', encoding="utf-8")
    assert hk.load_rotation_cursor(hist) == {"paylocity": 3}


def test_open_days_zero_leaves_the_cursor_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    col = _cursor_run(monkeypatch, tmp_path, ["--open-days", "0"])
    assert col.rotation == {} and col.cursor == {}


def test_run_gives_every_board_row_the_collectors_title_test(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows: list[dict] = []
    _cursor_run(monkeypatch, tmp_path, ["--titles", "ai engineer", "--exclude-title-words", "intern"],
                fetch=lambda r, h, s: rows.append(r) or [])
    ok = rows[0]["title_ok"]
    assert ok("Senior AI Engineer") and not ok("Chef") and not ok("AI Engineer Intern")
