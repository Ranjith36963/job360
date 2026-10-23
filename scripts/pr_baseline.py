"""A PR is red only for findings it adds; main's own stay red on main (owner, 2026-10-22).

Shared by `ruleset_gate.py --check` and `drill_registry.py --run-drills`. On a
pull_request, CI checks the PR's base commit out into a directory and passes it
as `--baseline DIR`. When the PR's own run has findings, the BASE commit's own
checker runs on the base tree, and only findings the base does not have fail.
Shared ones print as "inherited from main (not this PR's)". Push to main passes
no `--baseline`, so main's own findings still fail there.

Fail closed: if the base run gives nothing comparable (no checker, a crash, an
output this parser does not recognise), every finding counts as this PR's.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

BASE_TIMEOUT_S = 300


def comparable(text: str) -> str:
    """A finding without line numbers, temp dirs or layout: none of them is the finding."""
    text = re.sub(r"(?<=:)\d+|(?<=line )\d+", "N", text)
    text = re.sub(r"(/tmp|/var/folders|\\Temp)[/\\][^\s'\"]+", "<tmp>", text)
    return " ".join(text.split())


def run_base(base: Path, script: str, args: list[str]) -> tuple[int, str] | None:
    """Run the BASE commit's own copy of `script` against the base tree."""
    path = base / "scripts" / script
    if not path.is_file():
        return None
    try:
        p = subprocess.run([sys.executable, str(path), *args], cwd=base, capture_output=True,  # noqa: S603
                           check=False, encoding="utf-8", errors="replace", timeout=BASE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode, p.stdout.replace(str(base), "<root>")


def _annotate(level: str, title: str, text: str) -> None:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{text}")


def judge(head: list[str], base_findings: set[str] | None, what: str) -> int:
    """0 if every head finding is inherited from the base, else 1. Prints the verdict."""
    lines = ["", f"## Baseline ({what}): the PR's base commit", ""]
    new = [f for f in head if base_findings is None or comparable(f) not in base_findings]
    inherited = [f for f in head if f not in new]
    if base_findings is None:
        _annotate("warning", f"{what}: baseline unavailable", "the base run gave nothing "
                  "comparable; every finding is judged as this PR's (fail closed)")
        lines.append("The base run gave nothing comparable, so every finding above is this PR's.")
    if inherited:
        lines.append(f"**inherited from main (not this PR's): {len(inherited)}.** Fix these on main.")
        lines += [f"- {f.splitlines()[0]}" for f in inherited]
        for f in inherited:
            _annotate("warning", f"{what}: inherited from main (not this PR's)", f.splitlines()[0])
    if new:
        lines.append(f"**this PR's own: {len(new)}.** Fix them in this PR.")
        lines += [f"- {f.splitlines()[0]}" for f in new]
        for f in new:
            _annotate("error", f"{what}: added by this PR", f.splitlines()[0])
    else:
        lines.append("Nothing of this PR's own. OK")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 1 if new else 0


def against_base(head: list[str], base: Path, script: str, args: list[str],
                 parse: Callable[[int, str], set[str] | None], what: str, head_root: Path) -> int:
    """Run the base's own `script args`, parse its findings, and judge `head` against them.

    Each tree's own absolute path reads as `<root>`, so a path in a message matches.
    """
    ran = run_base(base, script, args)
    found = parse(*ran) if ran is not None else None
    return judge([f.replace(str(head_root), "<root>") for f in head], found, what)
