#!/usr/bin/env python
"""mypy ratchet — make the type-checker BLOCKING without a 803-error rewrite.

The problem this solves
-----------------------
`[tool.mypy] strict = true` over a codebase written before strict mode was on
produces 803 errors in 115 files. ci.yml therefore ran mypy with
`continue-on-error: true` and a comment claiming "strict mode is active for new
code; existing errors are grandfathered".

There was no grandfathering mechanism. `continue-on-error` does not check new
code against anything — it discards the result entirely, so new code could add
type errors freely and nobody would know. The comment described an intent that
no code implemented.

How the ratchet works
---------------------
`mypy_baseline.txt` records the errors that existed when the ratchet was
introduced. This script runs mypy and compares:

  * a NEW error (a signature not in the baseline, or more occurrences of a
    baselined signature than the baseline allows) -> exit 1, build fails
  * a FIXED error (in the baseline, no longer reported) -> reported as progress

So the count can only go down. That is what "grandfathered" was supposed to
mean, and it makes mypy blocking today rather than after a 803-error refactor.

Why signatures and not line numbers
-----------------------------------
A baseline keyed on `file:line` is worthless: inserting one line at the top of
a module shifts every error below it, so the whole file reads as "all errors
fixed, all errors new". Signatures are `(file, error-code, message)` with the
line number deliberately discarded, which survives ordinary edits. The trade-off
is honest and worth stating: two identical errors in one file are
indistinguishable, so we compare COUNTS per signature — three `type-arg` errors
in a file may not silently become four.

Usage
-----
    python scripts/mypy_ratchet.py            # check (CI mode); exit 1 on new errors
    python scripts/mypy_ratchet.py --update   # regenerate the baseline after fixing

Run --update only to record FIXED errors. If you run it to bury a new error you
have defeated the entire mechanism — fix the error or add a targeted
`# type: ignore[code]` with a comment saying why.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Tuple

BACKEND = pathlib.Path(__file__).resolve().parent.parent
BASELINE = BACKEND / "mypy_baseline.txt"

# "src\foo.py:66: error: Function is missing a return type annotation  [no-untyped-def]"
ERROR_RE = re.compile(
    r"^(?P<file>[^:]+?):(?P<line>\d+):(?:\d+:)?\s*error:\s*(?P<msg>.*?)\s*\[(?P<code>[a-z][a-z0-9-]+)\]\s*$"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

Signature = Tuple[str, str, str]  # (file, code, message)


def run_mypy() -> str:
    """Run mypy and return its raw output (mypy exits non-zero when it finds errors)."""
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "mypy", "src/", "--no-color-output", "--no-error-summary"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    return proc.stdout + proc.stderr


def parse(output: str) -> Dict[Signature, int]:
    """Parse mypy output into {signature: occurrence_count}.

    The line number is intentionally dropped — see the module docstring.
    Paths are normalised to forward slashes so a baseline generated on Windows
    matches a CI run on Linux.
    """
    counts: Dict[Signature, int] = collections.Counter()
    for raw in output.splitlines():
        line = ANSI_RE.sub("", raw)
        m = ERROR_RE.match(line.strip())
        if not m:
            continue  # notes, summaries, config warnings
        path = m.group("file").replace("\\", "/").strip()
        # Only OUR source. mypy also reports errors inside third-party stubs,
        # which depend on the machine, not on this repo: CI hit
        #   numpy/__init__.pyi: Type statement is only supported in Python 3.12+
        # from site-packages. Those are unfixable here and differ per
        # environment, so a baseline containing them could never match twice.
        if "site-packages" in path or "hostedtoolcache" in path or path.startswith("/"):
            continue
        counts[(path, m.group("code"), m.group("msg"))] += 1
    return counts


def load_baseline() -> Dict[Signature, int]:
    if not BASELINE.exists():
        return {}
    counts: Dict[Signature, int] = collections.Counter()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # format: <count>\t<file>\t<code>\t<message>
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        n, path, code, msg = parts
        counts[(path, code, msg)] = int(n)
    return counts


def write_baseline(counts: Dict[Signature, int]) -> None:
    total = sum(counts.values())
    lines = [
        "# mypy ratchet baseline — DO NOT hand-edit to silence a new error.",
        "# Regenerate with: python scripts/mypy_ratchet.py --update",
        "# Only run --update to record errors you FIXED. See scripts/mypy_ratchet.py.",
        f"# total errors: {total}",
        "",
    ]
    for (path, code, msg), n in sorted(counts.items()):
        lines.append(f"{n}\t{path}\t{code}\t{msg}")
    BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true", help="regenerate the baseline (only to record FIXES)")
    args = ap.parse_args()

    current = parse(run_mypy())
    total = sum(current.values())

    if args.update:
        old = sum(load_baseline().values())
        write_baseline(current)
        delta = old - total if old else 0
        print(f"baseline updated: {total} errors" + (f" ({delta:+d} vs previous {old})" if old else ""))
        return 0

    baseline = load_baseline()
    if not baseline:
        print("no baseline found — create one with: python scripts/mypy_ratchet.py --update", file=sys.stderr)
        return 1

    new: List[Tuple[Signature, int]] = []
    for sig, n in sorted(current.items()):
        allowed = baseline.get(sig, 0)
        if n > allowed:
            new.append((sig, n - allowed))

    fixed = sum(max(0, n - current.get(sig, 0)) for sig, n in baseline.items())
    base_total = sum(baseline.values())

    if new:
        print(f"mypy ratchet FAILED — {sum(c for _, c in new)} new type error(s):\n", file=sys.stderr)
        for (path, code, msg), count in new:
            suffix = f"  (x{count})" if count > 1 else ""
            print(f"  {path}: {msg}  [{code}]{suffix}", file=sys.stderr)
        print(
            "\nFix them, or add a targeted `# type: ignore[code]` with a comment saying why."
            "\nDo NOT run --update to bury them — that defeats the ratchet.",
            file=sys.stderr,
        )
        return 1

    msg = f"mypy ratchet OK — {total} errors (baseline {base_total}), no new type errors."
    if fixed:
        msg += f" {fixed} fixed — run `python scripts/mypy_ratchet.py --update` to bank the progress."
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
