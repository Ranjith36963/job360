"""Chain wires fails a PR only for wires the PR itself breaks (owner, 2026-10-22).

`chain_check.py --baseline BASE_DIR` runs the base's own checker on the base
tree and fails only on broken wires the base does not have. Pinned here:
inherited -> pass (even when the line moved), new -> fail, base not
comparable -> fail as before (fail closed), clean -> pass.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import chain_check as cc  # noqa: E402

_WF = """name: planted
on:
  workflow_dispatch: {{}}
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
{pad}{steps}"""


def _broken(tag: str) -> str:
    """A W2 broken wire: a gate reading a step id that does not exist."""
    return f"      - name: gate {tag}\n        if: steps.nope_{tag}.outputs.go == 'true'\n        run: echo hi\n"


def _tree(root: Path, tags: list[str], pad: int = 0, checker: bool = True) -> Path:
    (root / ".github" / "workflows").mkdir(parents=True)
    steps = "".join(_broken(t) for t in tags) or "      - run: echo ok\n"
    pads = "      - run: echo pad\n" * pad
    (root / ".github" / "workflows" / "planted.yml").write_text(_WF.format(pad=pads, steps=steps), encoding="utf-8")
    if checker:
        (root / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "chain_check.py", root / "scripts" / "chain_check.py")
    return root


@pytest.mark.parametrize(
    ("base_tags", "head_tags", "pad", "want"),
    [
        (["main"], ["main"], 0, 0),          # main's broken wire, carried: inherited, green
        (["main"], ["main"], 3, 0),          # ...and the PR moved its line: still inherited
        ([], ["pr"], 0, 1),                  # only the PR breaks it: red
        (["main"], ["main", "pr"], 0, 1),    # main's plus the PR's own: red for the PR's
        ([], [], 0, 0),                      # clean
    ],
)
def test_only_the_prs_own_broken_wires_fail(tmp_path, monkeypatch, capsys, base_tags, head_tags, pad, want):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    base = _tree(tmp_path / "base", base_tags)
    head = _tree(tmp_path / "head", head_tags, pad=pad, checker=False)
    assert cc.main(["--root", str(head), "--no-map", "--baseline", str(base)]) == want
    out = capsys.readouterr().out
    if base_tags and head_tags:
        assert "inherited from main (not this PR's): 1" in out
        assert "::warning title=broken wire inherited from main" in out
    if want:
        assert "::error title=broken wire added by this PR::" in out and "nope_pr" in out


def test_a_base_with_nothing_comparable_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    base = _tree(tmp_path / "base", ["main"], checker=False)  # no checker to run
    head = _tree(tmp_path / "head", ["main"], checker=False)
    assert cc.main(["--root", str(head), "--no-map", "--baseline", str(base)]) == 1
    assert "chain baseline unavailable" in capsys.readouterr().out


def test_the_verdict_reaches_the_job_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    base = _tree(tmp_path / "base", ["main"])
    head = _tree(tmp_path / "head", ["main"], checker=False)
    assert cc.main(["--root", str(head), "--no-map", "--baseline", str(base)]) == 0
    assert "inherited from main (not this PR's)" in summary.read_text(encoding="utf-8")


def test_without_baseline_a_broken_wire_is_still_red(tmp_path):
    """Push to main runs without --baseline: a broken wire there is still red."""
    assert cc.main(["--root", str(_tree(tmp_path / "main", ["main"])), "--no-map"]) == 1


def test_line_numbers_are_masked():
    assert cc._comparable("W2|a.yml|j: x -> y|line 12 at b.yml:40") == "W2|a.yml|j: x -> y|line N at b.yml:N"
