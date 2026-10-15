"""Offline tests for the hunt kit's streaming output, --resume and run budget.

A full run once took 25+ minutes, wrote its output only at the end and was killed for low memory,
losing everything. Jobs now go to disk as each source finishes; these tests pin that.
HTTP is faked at the kit's own ``http_get_json`` door, so nothing touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_stream_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()


def _board(token: str) -> dict[str, str]:
    return {"name": token.upper(), "ats": "greenhouse", "token": token, "source": "user"}


def _payload(token: str, n: int = 1) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "title": "AI Engineer",
                "absolute_url": "https://boards.example/%s/%d" % (token, i),
                "location": {"name": "London, United Kingdom"},
                "content": "<p>Build agents for %s.</p>" % token,
                "company_name": token.upper(),
            }
            for i in range(n)
        ]
    }


class Net:
    """Fake HTTP: one payload per board token, a log of every board fetched, optional hooks."""

    def __init__(self, tokens: list[str], monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
        self.calls: list[str] = []
        self.hooks: dict[str, Any] = {}
        self.tokens = tokens
        self.out = tmp / "found.jsonl"
        self.stream = Path(str(self.out) + ".stream")
        monkeypatch.setattr(hk, "load_companies", lambda p: [_board(t) for t in tokens])
        monkeypatch.setattr(hk, "http_get_json", self._get)

    def _get(self, url: str, params: Any = None, retries: int = 0, data: Any = None) -> tuple[int, Any]:
        token = url.split("/boards/")[1].split("/")[0]
        self.calls.append(token)
        if token in self.hooks:
            self.hooks[token]()
        return 200, _payload(token)

    def argv(self, *extra: str) -> list[str]:
        return [
            "--sources", "greenhouse", "--no-universe", "--work-countries", "GB", "--titles", "ai engineer",
            "--history", "none", "--workers", "1", "--out", str(self.out), *extra,
        ]

    def urls(self, path: Path) -> list[str]:
        return [json.loads(x)["url"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_jobs_are_on_disk_before_the_next_source_is_fetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    seen_when_b_starts: list[list[str]] = []

    def crash_in_b() -> None:
        seen_when_b_starts.append(net.urls(net.stream))  # what a SIGKILL right here would leave behind
        raise KeyboardInterrupt

    net.hooks["b"] = crash_in_b
    assert hk.main(net.argv()) == 130  # interrupted, not a traceback
    assert seen_when_b_starts == [["https://boards.example/a/0"]]
    assert net.urls(net.stream) == ["https://boards.example/a/0"]
    assert not net.out.exists()  # the final file is built at the end, from the stream
    progress = Path(str(net.stream) + ".progress").read_text(encoding="utf-8").split()
    assert "greenhouse:a" in progress and "greenhouse:b" not in progress and "#complete" not in progress
    err = capsys.readouterr().err
    assert "[greenhouse:a] 1 job," in err  # one progress line per source: name, jobs, elapsed
    assert "--resume" in err


def test_resume_skips_finished_sources_and_final_file_is_the_union(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a", "b", "c"], monkeypatch, tmp_path)

    def crash_in_b() -> None:
        raise KeyboardInterrupt

    net.hooks["b"] = crash_in_b
    assert hk.main(net.argv()) == 130
    assert net.calls == ["a", "b"]

    net.hooks.clear()
    net.calls.clear()
    assert hk.main(net.argv("--resume")) == 0
    assert net.calls == ["b", "c"]  # "a" was not fetched again
    union = ["https://boards.example/%s/0" % t for t in "abc"]
    assert sorted(net.urls(net.out)) == union  # final file == everything found across both runs
    assert sorted(net.urls(net.stream)) == union  # and no job was written twice
    assert Path(str(net.stream) + ".progress").read_text(encoding="utf-8").split()[-1] == "#complete"
    assert "resuming: 1 of 3 sources already done" in capsys.readouterr().err

    net.calls.clear()  # a finished run resumed again does nothing and says so
    assert hk.main(net.argv("--resume")) == 0
    assert net.calls == []
    assert "already finished" in capsys.readouterr().err


def test_resume_survives_a_half_written_last_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    net.hooks["b"] = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    assert hk.main(net.argv()) == 130
    with open(net.stream, "a", encoding="utf-8") as f:
        f.write('{"title": "cut off mid-wr')  # what a kill during a write leaves
    net.hooks.clear()
    assert hk.main(net.argv("--resume")) == 0
    assert sorted(net.urls(net.out)) == ["https://boards.example/a/0", "https://boards.example/b/0"]


def test_source_budget_stops_cleanly_writes_partial_and_prints_resume_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a", "b", "c"], monkeypatch, tmp_path)
    hist = tmp_path / "seen.json"
    argv = net.argv("--max-sources", "2")
    argv[argv.index("--history") + 1] = str(hist)
    assert hk.main(argv) == 0
    assert net.calls == ["a", "b"]
    assert sorted(net.urls(net.out)) == ["https://boards.example/a/0", "https://boards.example/b/0"]  # partial written
    err = capsys.readouterr().err
    assert "Stopped early: source budget reached (2)" in err
    assert "rerun with --resume" in err and "--max-sources 2" in err and "--resume" in err.splitlines()[-1]
    assert not hist.exists()  # a partial run must not mark its jobs "seen"
    progress = Path(str(net.stream) + ".progress").read_text(encoding="utf-8").split()
    assert "#complete" not in progress

    net.calls.clear()
    assert hk.main(argv[:-2] + ["--resume"]) == 0  # --max-sources differs: still the same search
    assert net.calls == ["c"]
    assert len(net.urls(net.out)) == 3
    assert hist.exists()  # history is saved once the run is whole


def test_time_budget_stops_starting_new_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(hk, "_now", lambda: clock[0])

    def slow_a() -> None:
        clock[0] += 3 * 60  # source "a" takes three minutes

    net.hooks["a"] = slow_a
    assert hk.main(net.argv("--max-minutes", "2")) == 0
    assert net.calls == ["a"]  # "b" never started
    assert net.urls(net.out) == ["https://boards.example/a/0"]
    err = capsys.readouterr().err
    assert "Stopped early: time budget reached" in err and "--resume" in err


def test_a_fresh_run_never_destroys_an_unfinished_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a", "b"], monkeypatch, tmp_path)
    net.hooks["b"] = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    assert hk.main(net.argv()) == 130
    net.hooks.clear()
    assert hk.main(net.argv("--days", "10")) == 0  # forgot --resume, and the search differs anyway
    assert net.urls(Path(str(net.stream) + ".prev")) == ["https://boards.example/a/0"]
    assert hk.main(net.argv("--days", "11", "--resume")) == 0  # different search: ignored, starts fresh
    assert len(net.urls(net.out)) == 2


def test_parse_chunks_are_small_and_lose_nothing() -> None:
    d = _payload("big", 120)
    chunks = list(hk._parse_chunks(hk.parse_greenhouse, d, _board("big")))
    assert [len(c) for c in chunks] == [50, 50, 20]
    assert [r["url"] for c in chunks for r in c] == [r["url"] for r in hk.parse_greenhouse(d, _board("big"))]
    lst = list(range(7))
    assert list(hk._parse_chunks(lambda part, b: part, lst, {})) == [lst]


def test_stream_mode_keeps_no_job_records_in_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = Net(["a"], monkeypatch, tmp_path)
    args = hk.build_parser().parse_args(net.argv())
    sink = hk.Stream(str(net.stream), "fp")
    sink.open(False)
    args.sink = sink
    col = hk.run(args)
    sink.close()
    assert col.results == [] and sink.count == 1  # the description lives on disk, not in the list
    assert "Build agents for a." in net.stream.read_text(encoding="utf-8")
