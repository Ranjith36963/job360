"""Offline tests for the hunt kit's run log, failed-sources list, skipped counters, atomic history and run lock.

Borrowed from career-ops scan.mjs (scan-runs.tsv row per run incl. failure-path rows, errors list, scan-history
skipped_* statuses, atomicWriteFile, pipeline-lock.mjs). HTTP is faked at the kit's own ``http_get_json`` door.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_runlog_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()


class Net:
    def __init__(self, tokens: list[str], monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
        self.calls: list[str] = []
        self.hooks: dict[str, Any] = {}
        self.out = tmp / "found.jsonl"
        self.stream = Path(str(self.out) + ".stream")
        self.runs = tmp / "runs.jsonl"
        self.hist = tmp / "seen.json"
        monkeypatch.setattr(
            hk, "load_companies",
            lambda p: [{"name": t.upper(), "ats": "greenhouse", "token": t, "source": "user"} for t in tokens])
        monkeypatch.setattr(hk, "http_get_json", self._get)

    def _get(self, url: str, params: Any = None, retries: int = 0, data: Any = None) -> tuple[int, Any]:
        token = url.split("/boards/")[1].split("/")[0]
        self.calls.append(token)
        if token in self.hooks:
            self.hooks[token]()
        return 200, {"jobs": [{
            "title": "AI Engineer", "absolute_url": "https://boards.example/%s/0" % token,
            "location": {"name": "London, United Kingdom"}, "content": "<p>Agents.</p>",
            "company_name": token.upper(),
        }]}

    def argv(self, *extra: str) -> list[str]:
        return [
            "--sources", "greenhouse", "--no-universe", "--work-countries", "GB", "--titles", "ai engineer",
            "--history", str(self.hist), "--workers", "1", "--out", str(self.out),
            "--run-log", str(self.runs), *extra,
        ]

    def rows(self) -> list[dict[str, Any]]:
        if not self.runs.exists():
            return []
        return [json.loads(x) for x in self.runs.read_text(encoding="utf-8").splitlines() if x.strip()]


def _raise(exc: BaseException) -> Any:
    def f() -> None:
        raise exc
    return f


def test_every_run_appends_a_row_with_start_end_status_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    assert hk.main(net.argv()) == 0
    assert hk.main(net.argv("--all")) == 0
    first, second = net.rows()
    assert first["status"] == "completed" and second["status"] == "completed"
    assert first["start"] <= first["end"]
    assert (first["found"], first["kept"], first["failed"], first["skipped_seen"]) == (2, 2, 0, 0)
    assert first["failed_sources"] == [] and first["sources_done"] == 2
    for k in ("skipped_seen", "skipped_company", "skipped_bad_url", "skipped_dead", "duration_s", "stop_reason"):
        assert k in first


def test_interrupted_run_still_leaves_a_row_and_keeps_history_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    net.hooks["b"] = _raise(KeyboardInterrupt())
    assert hk.main(net.argv()) == 130
    (row,) = net.rows()
    assert row["status"] == "interrupted"
    assert row["kept"] == 1 and row["sources_done"] == 1 and row["found"] >= 1
    assert not net.hist.exists()


def test_fatal_error_writes_a_failed_row_then_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a"], monkeypatch, tmp_path)

    def boom(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise RuntimeError("sort exploded")

    monkeypatch.setattr(hk, "sort_newest", boom)
    with pytest.raises(RuntimeError, match="sort exploded"):
        hk.main(net.argv())
    (row,) = net.rows()
    assert row["status"] == "failed" and "sort exploded" in row["error"]
    assert row["found"] == 1 and row["kept"] == 1  # what the sweep had before it died
    assert not net.hist.exists()


def test_a_row_is_written_once_even_if_two_signals_race(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = hk.RunLog(str(tmp_path / "r.jsonl"))
    assert log.write("interrupted") is True
    assert log.write("failed") is False
    assert len((tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert hk.RunLog("none").write("failed") is False  # disabled


def test_failed_sources_are_listed_with_their_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a", "b", "c"], monkeypatch, tmp_path)
    net.hooks["b"] = _raise(RuntimeError("503 from b"))
    assert hk.main(net.argv()) == 0
    err = capsys.readouterr().err
    assert "Failed sources (1):" in err and "greenhouse:b: RuntimeError: 503 from b" in err
    (row,) = net.rows()
    assert row["failed"] == 1
    assert row["failed_sources"] == [{"source": "greenhouse:b", "error": "RuntimeError: 503 from b"}]
    assert "greenhouse:b" not in Path(str(net.stream) + ".progress").read_text(encoding="utf-8").split()


def test_module_task_failure_is_recorded_with_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    col = hk.Collector(hk.build_parser().parse_args(["--titles", "x"]))

    def call(st: dict[str, int]) -> list[dict[str, Any]]:
        raise ValueError("bad xml")

    hk._module_task(col, "workable", call, "workable:acme")
    assert col.failed == [{"source": "workable:acme", "error": "ValueError: bad xml"}]


def test_skipped_counts_reach_the_run_log_and_the_history_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    net = Net(["a", "b", "c"], monkeypatch, tmp_path)
    net.hist.write_text(json.dumps({"urls": ["https://boards.example/a/0"], "keys": []}), encoding="utf-8")
    assert hk.main(net.argv("--skip-companies", "c")) == 0  # c is dropped at the board list
    (row,) = net.rows()
    assert row["skipped_seen"] == 1 and row["kept"] == 1
    saved = json.loads(net.hist.read_text(encoding="utf-8"))
    assert saved["urls"] == ["https://boards.example/a/0", "https://boards.example/b/0"]  # old format still read
    (hrun,) = saved["runs"]
    assert hrun["skipped_seen"] == 1 and hrun["kept"] == 1 and "skipped_company" in hrun
    assert hk.main(net.argv("--all")) == 0
    assert len(json.loads(net.hist.read_text(encoding="utf-8"))["runs"]) == 2  # runs accumulate


def test_skipped_company_and_bad_url_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    col = hk.Collector(hk.build_parser().parse_args(["--titles", "ai engineer", "--skip-companies", "acme"]))
    col.offer("s", {"title": "AI Engineer", "url": "javascript:x", "company": "z"})
    col.offer("s", {"title": "AI Engineer", "url": "https://x.example/1", "company": "ACME Inc"})
    assert col.stats["s"]["skipped_bad_url"] == 1 and col.stats["s"]["skipped_company"] == 1


def test_history_save_is_atomic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hist = tmp_path / "seen.json"
    hist.write_text(json.dumps({"urls": ["old"], "keys": []}), encoding="utf-8")

    def dead_replace(src: str, dst: str) -> None:
        raise OSError("disk pulled")

    monkeypatch.setattr(hk.os, "replace", dead_replace)
    with pytest.raises(OSError):
        hk.save_history(str(hist), {"old"}, set(), [{"url": "new", "company": "c", "title": "t"}])
    assert json.loads(hist.read_text(encoding="utf-8"))["urls"] == ["old"]  # old file whole
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]  # temp file cleaned up


def test_history_is_not_saved_when_the_output_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a"], monkeypatch, tmp_path)
    argv = net.argv()
    argv[argv.index("--out") + 1] = str(tmp_path)  # a directory: opening it for writing fails
    with pytest.raises(OSError):
        hk.main(argv)
    assert not net.hist.exists()
    (row,) = net.rows()
    assert row["status"] == "failed"


def test_second_run_on_the_same_stream_is_refused_and_lock_is_released(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a"], monkeypatch, tmp_path)
    lock_dir = Path(str(net.stream) + ".lock")
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": os.getpid(), "token": "x", "started_at": "t"}))
    assert hk.main(net.argv()) == 3  # holder is alive (this very process)
    assert net.calls == [] and not net.stream.exists()
    assert "another run is writing" in capsys.readouterr().err
    assert lock_dir.exists()  # a refused run never removes the holder's lock

    (lock_dir / "owner.json").unlink()
    lock_dir.rmdir()
    assert hk.main(net.argv()) == 0
    assert not lock_dir.exists()  # released on exit

    net.hooks["a"] = _raise(KeyboardInterrupt())
    assert hk.main(net.argv()) == 130
    assert not lock_dir.exists()  # and after an interrupt


def test_a_stale_lock_from_a_dead_process_is_reclaimed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a"], monkeypatch, tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lock_dir = Path(str(net.stream) + ".lock")
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": proc.pid, "token": "x", "started_at": "t"}))
    assert hk.main(net.argv()) == 0
    assert net.calls == ["a"] and not lock_dir.exists()


def test_release_never_removes_someone_elses_lock(tmp_path: Path) -> None:
    mine = hk.RunLock(str(tmp_path / "s"))
    assert mine.acquire() == ""
    owner = Path(mine.dir) / "owner.json"
    owner.write_text(json.dumps({"pid": os.getpid(), "token": "someone-else"}))
    mine.release()
    assert Path(mine.dir).exists()


def test_two_reclaimers_race_on_a_dead_lock_and_only_one_wins(tmp_path: Path) -> None:
    import threading

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    stream = str(tmp_path / "s")
    lock_dir = Path(stream + ".lock")
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": proc.pid, "token": "dead", "started_at": "t"}))
    locks = [hk.RunLock(stream) for _ in range(8)]
    barrier = threading.Barrier(len(locks))
    results: list[str] = [None] * len(locks)  # type: ignore[list-item]

    def go(i: int) -> None:
        barrier.wait()
        results[i] = locks[i].acquire()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(locks))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    winners = [i for i, r in enumerate(results) if r == ""]
    assert len(winners) == 1  # the others see a live holder; nobody deleted the winner's fresh lock
    assert json.loads((lock_dir / "owner.json").read_text())["token"] == locks[winners[0]].token
    assert not Path(stream + ".lock.recover").exists()  # guard always cleaned up


def test_an_abandoned_recover_guard_does_not_block_reclaim_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    stream = str(tmp_path / "s")
    Path(stream + ".lock").mkdir()
    (Path(stream + ".lock") / "owner.json").write_text(json.dumps({"pid": proc.pid, "token": "d"}))
    guard = Path(stream + ".lock.recover")
    guard.mkdir()
    old = guard.stat().st_mtime - 3600
    os.utime(guard, (old, old))  # a guard left by a process killed mid-reclaim
    assert hk.RunLock(stream).acquire() == ""
