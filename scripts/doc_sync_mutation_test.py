"""Prove every doc_sync_check.py guard can actually go RED.

A green drift report means one of two very different things: "the docs are
correct", or "my regex matched nothing and I am watching an empty room". They
are indistinguishable from the outside, and the second is how a stale number
survives for months behind a passing check -- `SCORER_VERSION` sat at 4 in the
docs while the code said 7, with a green tripwire the whole time.

So each guard is broken ON PURPOSE, one at a time, and must report drift.
Restore is byte-level (read_bytes/write_bytes): a text round-trip rewrites line
endings on Windows and would leave the tree dirty after a "successful" run.

Usage (from repo root):
    python scripts/doc_sync_mutation_test.py

Exit 0 = every guard proven able to fail. Exit 1 = at least one guard is blind.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["JOB360_ROOT"]) if os.environ.get("JOB360_ROOT") else Path(__file__).resolve().parents[1]

# (doc, regex with ONE capture group, replacement that lies, fact name in the report)
CASES: list[tuple[str, str, str, str]] = [
    ("CLAUDE.md", r"SOURCE_REGISTRY`? has (\d+) entries", "SOURCE_REGISTRY` has 999 entries", "registry"),
    ("CLAUDE.md", r"(\d+) unique source classes", "999 unique source classes", "unique-classes"),
    ("CLAUDE.md", r"SCORER_VERSION`?\s*=\s*\*{0,2}(\d+)", "SCORER_VERSION` = **999", "scorer-version"),
    ("CLAUDE.md", r"(\d+) workflows in", "999 workflows in", "workflows"),
    ("ARCHITECTURE.md", r"across (\d+) `?test_\*\.py`? files", "across 999 test_*.py files", "test-files"),
    ("frontend/CLAUDE.md", r"Next\.js (\d+\.\d+\.\d+)", "Next.js 1.2.3", "nextjs-version"),
    ("frontend/CLAUDE.md", r"React (\d+\.\d+\.\d+)", "React 4.5.6", "react-version"),
]


def main() -> int:
    failures: list[str] = []

    for rel, pattern, replacement, fact in CASES:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{fact}: {rel} does not exist")
            continue

        original = path.read_bytes()
        text = original.decode("utf-8", errors="replace")
        mutated, n = re.subn(pattern, replacement, text, count=1)

        if n == 0:
            # The doc no longer makes this claim at all. Either the claim was
            # reworded (so the guard is now blind) or the pattern is wrong.
            failures.append(f"{fact}: no claim matching /{pattern}/ in {rel} — guard watches nothing")
            continue

        try:
            path.write_bytes(mutated.encode("utf-8"))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "doc_sync_check.py")],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            if fact in proc.stdout:
                print(f"PASS  {fact:16s} went RED when {rel} lied")
            else:
                failures.append(f"{fact}: stayed GREEN on a broken {rel} — the guard is blind")
        finally:
            path.write_bytes(original)

    print()
    if failures:
        print("MUTATION TEST FAILED — these guards cannot fail, so they prove nothing:")
        for f in failures:
            print("  " + f)
        return 1

    print(f"All {len(CASES)} guards proven able to go RED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
