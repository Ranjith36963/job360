"""A fresh container has no rotation cursor file: the slow-ATS slice must start at the calendar day, not row 0."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_fresh_cursor_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()
POOL = 14  # open_days 7 -> slices of 2
DAY1 = dt.date(2026, 10, 19)
DAY2 = dt.date(2026, 10, 20)


def _freeze(monkeypatch: pytest.MonkeyPatch, day: dt.date) -> None:
    class FakeDate(dt.date):
        @classmethod
        def today(cls) -> FakeDate:
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr(hk, "_dt", SimpleNamespace(date=FakeDate, datetime=dt.datetime, timedelta=dt.timedelta,
                                                   timezone=dt.timezone))


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hist: str, day: dt.date) -> tuple[Any, list[str]]:
    _freeze(monkeypatch, day)
    seen: list[str] = []

    def fetch(row: dict, http: Any, st: dict) -> list:
        seen.append(row["token"])
        return []

    uni = [{"name": "I%d" % i, "ats": "icims", "token": "i%02d" % i, "source": "universe"} for i in range(POOL)]
    monkeypatch.setattr(hk, "load_companies", lambda p: [])
    monkeypatch.setattr(hk, "load_universe", lambda ats_list, *a, **k: uni)
    monkeypatch.setattr(hk, "_merged", lambda attr, mods: {"icims": fetch})
    args = hk.build_parser().parse_args(["--sources", "icims", "--history", hist, "--workers", "1",
                                         "--stream", str(tmp_path / ("s-%s.jsonl" % Path(hist).name)),
                                         "--run-log", "none"])
    col = hk.run(args)
    return col, sorted(seen)


def _calendar_start(day: dt.date) -> int:
    return (day.toordinal() * 2) % POOL


def _tokens(start: int) -> list[str]:
    return sorted("i%02d" % ((start + k) % POOL) for k in range(2))


def test_fresh_paths_on_different_days_pick_different_slices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _calendar_start(DAY1) != _calendar_start(DAY2)
    _, s1 = _run(monkeypatch, tmp_path, str(tmp_path / "a" / "h.json"), DAY1)
    _, s2 = _run(monkeypatch, tmp_path, str(tmp_path / "b" / "h.json"), DAY2)
    assert s1 == _tokens(_calendar_start(DAY1))
    assert s2 == _tokens(_calendar_start(DAY2))
    assert s1 != s2


def test_fresh_paths_on_the_same_day_pick_the_same_slice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, s1 = _run(monkeypatch, tmp_path, str(tmp_path / "a" / "h.json"), DAY1)
    _, s2 = _run(monkeypatch, tmp_path, str(tmp_path / "b" / "h.json"), DAY1)
    assert s1 == s2 == _tokens(_calendar_start(DAY1))


def test_existing_cursor_file_still_sets_the_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hist = str(tmp_path / "h.json")
    hk.save_rotation_cursor(hist, {"icims": 6})
    col, seen = _run(monkeypatch, tmp_path, hist, DAY1)
    assert seen == ["i06", "i07"] and col.cursor == {"icims": 8}


def test_fresh_run_saves_calendar_start_plus_finished(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    col, seen = _run(monkeypatch, tmp_path, str(tmp_path / "h.json"), DAY1)
    assert col.cursor == {"icims": (_calendar_start(DAY1) + len(seen)) % POOL}


def test_history_none_still_uses_the_calendar_slice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    col, seen = _run(monkeypatch, tmp_path, "none", DAY2)
    assert seen == _tokens(_calendar_start(DAY2))
    assert not list(tmp_path.rglob("*.rotation.json"))
    assert col.cursor == {"icims": 2}  # in-memory only, unchanged behaviour


def test_calendar_offsets_matches_rotate_open_none_branch() -> None:
    pool = [{"ats": "icims", "token": "t%d" % i, "source": "universe"} for i in range(POOL)]
    start = hk.calendar_offsets(pool, {}, DAY1)["icims"]
    assert [b["token"] for b in hk.rotate_open(pool, {}, DAY1)] == [b["token"] for b in hk.rotate_open(pool, {}, DAY1, offsets={"icims": start})]
