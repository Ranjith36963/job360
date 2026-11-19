"""Two-pass hunt (2026-11-19): pass 1 lists distinct titles and opens no ad; pass 2 opens full ads for kept titles.

The 19 Nov run's --every-title single pass returned ~101,000 ads with full text. The run could not read them, so the
title hint became the filter again. Pass 1 gives the assistant every distinct title cheaply; it keeps titles; pass 2
fetches detail only for those.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load():
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_two_pass", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()

BASE = ["--titles", "AI Engineer", "--work-countries", "GB", "--days", "3650", "--history", "none"]


def _args(*argv: str):
    return hk.build_parser().parse_args([*BASE, *argv])


def _raw(i: int, title: str, calls: list[str]):
    def detail() -> dict[str, str]:
        calls.append(title)
        return {"desc": "Full ad text for %s. You have 8+ years of experience." % title}

    return {"title": title, "url": "https://jobs.example/%d" % i, "loc": "London, UK", "desc": "",
            "company": "Acme", "board": "acme", "detail": detail}


LISTINGS = ["Senior AI Engineer", "senior ai-engineer", "Accountant", "Forward Deployed Engineer"]


def test_pass_one_lists_distinct_titles_with_counts_and_never_opens_an_ad(tmp_path):
    calls: list[str] = []
    col = hk.Collector(_args("--titles-only", str(tmp_path / "t.jsonl")))
    col.tl.tid = "greenhouse:acme"
    for i, t in enumerate(LISTINGS):
        col.offer("greenhouse", _raw(i, t, calls))
    col.offer_job("feed", {"title": "Accountant", "company": "Bean Co", "url": "https://jobs.example/9",
                           "location": "Leeds, UK", "description": "A listing text", "source": "feed"})
    assert calls == []  # the detail callable is never invoked
    assert all("description" not in r for r in col.results)
    assert {r["task"] for r in col.results} == {"greenhouse:acme"}
    titles = hk.distinct_titles(col.results, col.target_phrases)
    assert [(t["title"], t["count"]) for t in titles] == [
        ("Accountant", 2), ("Senior AI Engineer", 2), ("Forward Deployed Engineer", 1)]
    assert titles[1]["hint"] == {"matched_target_phrase": True, "level_words": ["senior"]}
    assert titles[1]["sample"] == {"company": "Acme", "place": "London, UK"}


def test_a_title_with_every_hint_negative_is_in_the_pass_one_file():
    col = hk.Collector(_args("--titles-only", "t.jsonl"))
    col.offer("greenhouse", _raw(1, "Accountant", []))
    (entry,) = hk.distinct_titles(col.results, col.target_phrases)
    assert entry["title"] == "Accountant" and entry["hint"] == {"matched_target_phrase": False, "level_words": []}


def test_pass_one_reader_gate_lists_the_title_and_spends_no_detail_request():
    col = hk.Collector(_args("--titles-only", "t.jsonl"))
    col.tl.tid = "icims:acme"
    assert col.gate("Data Scientist") is False  # a reader asking "may I open this card?" is told no
    assert col.results == [{"gate": True, "title": "Data Scientist", "task": "icims:acme"}]
    assert [t["count"] for t in hk.distinct_titles(col.results, col.target_phrases)] == [1]
    plain = hk.Collector(_args("--every-title"))
    assert plain.gate("Data Scientist") is True and plain.results == []  # the single pass is unchanged


def test_pass_two_opens_full_ads_only_for_kept_titles(tmp_path):
    kept = tmp_path / "kept.txt"
    kept.write_text(json.dumps({"title": "Senior AI Engineer", "count": 2}) + "\nForward Deployed Engineer\n")
    args = _args("--keep-titles", str(kept), "--rows", "r.jsonl")
    args.keep_set = hk.load_keep_titles(str(kept))
    col = hk.Collector(args)
    calls: list[str] = []
    for i, t in enumerate(LISTINGS):
        col.offer("greenhouse", _raw(i, t, calls))
    assert calls == ["Senior AI Engineer", "senior ai-engineer", "Forward Deployed Engineer"]
    out = {r["title"]: r for r in col.results}
    assert sorted(out) == sorted(calls)
    assert out["Senior AI Engineer"]["description"].startswith("Full ad text")  # full text, as the usual --out
    assert out["Senior AI Engineer"]["title_hint"]["guessed_min_years"] == 8  # years only a hint, never a drop
    assert col.gate("Accountant") is False and col.gate("Forward  Deployed Engineer") is True


def test_pass_two_reruns_only_the_sources_holding_a_kept_title(tmp_path):
    rows = tmp_path / "t.jsonl.rows.jsonl"
    rows.write_text("\n".join(json.dumps(r) for r in [
        {"title": "AI Engineer", "task": "lever:a", "url": "https://x/1"},
        {"gate": True, "title": "AI Engineer", "task": "icims:b"},
        {"title": "Accountant", "task": "lever:c", "url": "https://x/2"},
    ]) + "\n")
    assert hk.keep_task_ids(str(rows), {"ai engineer"}) == {"lever:a", "icims:b"}
    rows.write_text(json.dumps({"title": "AI Engineer", "url": "https://x/1"}) + "\n")
    assert hk.keep_task_ids(str(rows), {"ai engineer"}) is None  # no task id: re-run every source, never lose it


def test_main_pass_one_writes_the_titles_file_and_rows_and_no_history(tmp_path, monkeypatch):
    out = tmp_path / "titles.jsonl"
    seen: dict[str, object] = {}

    def fake_run(args):
        col = hk.Collector(args)
        seen["every_title"] = col.every_title
        col.tl.tid = "lever:acme"
        col.offer("lever", _raw(1, "Applied AI Engineer", []))
        col.offer("lever", _raw(2, "Applied AI Engineer", []))
        return col

    monkeypatch.setattr(hk, "run", fake_run)
    hist = tmp_path / "seen.json"
    assert hk.main([*BASE[:-2], "--history", str(hist), "--titles-only", str(out), "--run-log", "none"]) == 0
    assert seen["every_title"] is True  # --titles-only implies --every-title
    (line,) = out.read_text().splitlines()
    assert json.loads(line)["title"] == "Applied AI Engineer" and json.loads(line)["count"] == 2
    rows = (tmp_path / "titles.jsonl.rows.jsonl").read_text().splitlines()
    assert len(rows) == 2 and all("description" not in json.loads(r) for r in rows)
    assert not hist.exists()  # pass 1 marks nothing seen and moves no cursor


@pytest.mark.parametrize("extra", [["--keep-titles", "k.txt"], ["--keep-titles", "k.txt", "--titles-only", "t"]])
def test_main_refuses_a_half_two_pass_command(extra):
    assert hk.main([*BASE, *extra]) == 2


def test_the_two_pass_flags_are_part_of_the_run_fingerprint():
    plain = hk.run_fingerprint(_args("--every-title"))
    assert plain == hk.run_fingerprint(_args("--every-title"))
    assert hk.run_fingerprint(_args("--every-title", "--titles-only", "t.jsonl")) != plain
    a, b = _args("--keep-titles", "k", "--rows", "r"), _args("--keep-titles", "k", "--rows", "r")
    a.keep_set, b.keep_set = {"ai engineer"}, {"ai engineer", "llm engineer"}
    assert hk.run_fingerprint(a) != hk.run_fingerprint(b)  # other kept titles = another search


# Owner, 2026-11-19 (after the overnight runs): the RECIPE goes back to one kit pass. The two-pass flags stay in the
# kit (the tests above), but the recipe no longer gives them; it keeps the full-read rule.
def test_recipe_gives_one_pass_and_keeps_the_full_read_rule():
    from src.api.routes.recipes import load_recipe

    flat = " ".join(load_recipe("hunt").text.lower().split())
    gives = flat.replace("never pass `--every-title`, `--titles-only` or `--keep-titles`", "")  # the one ban line
    assert "--titles-only" not in gives and "--keep-titles" not in gives
    assert "titles.jsonl" not in flat and "kept.txt" not in flat
    assert "titles_seen" not in flat and "titles_kept" not in flat
    assert "never only its first 3,000 characters" in flat
    assert "never brought" in flat


# 19 Nov 03:11 UTC: pass 1 hung past --max-minutes (a running source kept going) and was killed, so no titles file.
def _pass_one_with_a_stuck_source(hk_args, release):
    """A fake run: one source lists two rows at once, the other never returns until released."""
    import concurrent.futures as cf

    col = hk.Collector(hk_args)

    def fast():
        col.tl.tid = "lever:fast"
        col.offer("lever", _raw(1, "Applied AI Engineer", []))
        col.offer("lever", _raw(2, "Applied AI Engineer", []))

    def stuck():
        col.tl.tid = "feed:freehire"
        release.wait(30)

    ex = cf.ThreadPoolExecutor(max_workers=2)
    futs = {ex.submit(fn): src for src, fn in (("lever", fast), ("freehire", stuck))}
    abandoned = hk._wait_sources(futs, col.sink, None, hard=True)
    ex.shutdown(wait=False, cancel_futures=True)
    return col, abandoned


@pytest.mark.real_sleep
def test_pass_one_ends_at_its_deadline_with_a_source_still_running(tmp_path, monkeypatch):
    import threading
    import time

    out = tmp_path / "titles.jsonl"
    release = threading.Event()
    seen: dict[str, object] = {}

    def fake_run(args):
        col, seen["abandoned"] = _pass_one_with_a_stuck_source(args, release)
        return col

    monkeypatch.setattr(hk, "run", fake_run)
    t0 = time.monotonic()
    try:
        rc = hk.main([*BASE, "--titles-only", str(out), "--run-log", "none", "--max-minutes", "0.01"])
    finally:
        release.set()
    assert rc == 0 and time.monotonic() - t0 < 10  # 0.6 s deadline; the stuck source would hold it 30 s
    assert seen["abandoned"] == 1
    (line,) = out.read_text().splitlines()
    assert json.loads(line) == {**json.loads(line), "title": "Applied AI Engineer", "count": 2}


@pytest.mark.parametrize("stop", ["sigint", "sigterm"])
def test_a_stopped_pass_one_still_writes_its_titles_file(tmp_path, monkeypatch, stop):
    import os
    import signal

    out = tmp_path / "titles.jsonl"

    def fake_run(args):
        col = hk.Collector(args)
        col.tl.tid = "lever:acme"
        col.offer("lever", _raw(1, "Applied AI Engineer", []))
        if stop == "sigterm":
            os.kill(os.getpid(), signal.SIGTERM)  # the handler turns it into the stop path
        raise KeyboardInterrupt

    monkeypatch.setattr(hk, "run", fake_run)
    assert hk.main([*BASE, "--titles-only", str(out), "--run-log", "none"]) == 130
    (line,) = out.read_text().splitlines()
    assert json.loads(line)["title"] == "Applied AI Engineer"
    assert signal.getsignal(signal.SIGTERM) is not hk._sigterm_to_interrupt  # handler restored


def test_titles_from_rows_rebuilds_the_titles_file_offline(tmp_path, monkeypatch):
    rows = tmp_path / "old.rows.jsonl"
    rows.write_text("".join(json.dumps({"title": t, "url": "https://jobs.example/%d" % i, "company": "Acme",
                                        "location": "London"}) + "\n"
                            for i, t in enumerate(["ML Engineer", "ML Engineer", "Accountant"])))
    monkeypatch.setattr(hk, "run", lambda args: pytest.fail("the rebuild must not run any source"))
    out = tmp_path / "titles.jsonl"
    assert hk.main([*BASE, "--titles-from-rows", str(rows), "--titles-only", str(out)]) == 0
    assert [(json.loads(x)["title"], json.loads(x)["count"]) for x in out.read_text().splitlines()] == [
        ("ML Engineer", 2), ("Accountant", 1)]


def test_a_stopped_pass_one_marks_the_run_abandoned_so_the_cli_does_not_join_a_stuck_source(monkeypatch):
    """reviewer-bugs on #1702: Ctrl-C / SIGTERM in pass 1 wrote the titles file but the CLI still joined the stuck
    source thread at exit. The stop path now sets the same flag the deadline sets."""
    monkeypatch.setattr(hk, "ABANDONED", False)
    hk._mark_abandoned()
    assert hk.ABANDONED is True
