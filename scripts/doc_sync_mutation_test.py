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

# The drift report and these findings quote doc text full of arrows and
# em-dashes. A Windows console defaults to cp1252 and this script died mid-report
# on a single "→" -- a guard that crashes has not failed safe, it has stopped
# answering, which is the very thing this file exists to prevent.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# (doc, regex with ONE capture group, replacement that lies, fact name in the report)
CASES: list[tuple[str, str, str, str]] = [
    ("CLAUDE.md", r"SOURCE_REGISTRY`? has (\d+) entries", "SOURCE_REGISTRY` has 999 entries", "registry"),
    ("CLAUDE.md", r"(\d+) unique source classes", "999 unique source classes", "unique-classes"),
    ("CLAUDE.md", r"SCORER_VERSION`?\s*=\s*\*{0,2}(\d+)", "SCORER_VERSION` = **999", "scorer-version"),
    ("CLAUDE.md", r"(\d+) workflows in", "999 workflows in", "workflows"),
    ("ARCHITECTURE.md", r"across (\d+) `?test_\*\.py`? files", "across 999 test_*.py files", "test-files"),
    ("frontend/CLAUDE.md", r"Next\.js (\d+\.\d+\.\d+)", "Next.js 1.2.3", "nextjs-version"),
    ("frontend/CLAUDE.md", r"React (\d+\.\d+\.\d+)", "React 4.5.6", "react-version"),
    # Second promotion batch, 2026-08-24.
    ("docs/product/pillars/03-job-providers.md",
     r"checking all (\d+) subclasses", "checking all 999 subclasses", "subclasses"),
    ("docs/product/pillars/glossary.md",
     r"`RATE_LIMITS` dict in `settings\.py` \((\d+) entries",
     "`RATE_LIMITS` dict in `settings.py` (999 entries", "rate-limits"),
    # Third batch, 2026-08-24 — the glossary's remaining countable facts.
    ("docs/product/pillars/glossary.md",
     r"\((\d+) slugs across \d+ platforms\)", "(999 slugs across 11 platforms)", "ats-slugs"),
    ("docs/product/pillars/glossary.md",
     r"\(\d+ slugs across (\d+) platforms\)", "(302 slugs across 99 platforms)", "ats-platforms"),
    ("docs/product/pillars/glossary.md",
     r"every source must produce: (\d+) fields", "every source must produce: 999 fields", "job-fields"),
    ("docs/product/pillars/glossary.md",
     r"shape with (\d+) strict-typed fields", "shape with 999 strict-typed fields",
     "enrichment-fields"),
    ("docs/product/pillars/glossary.md",
     r"strict-typed fields, (\d+) enums", "strict-typed fields, 99 enums", "enrichment-enums"),
    # The only CODE file guarded here. It shipped "47 Job Sources" to every
    # visitor of job360.uk for a week after the roster dropped to 41.
    ("frontend/src/app/page.tsx",
     r"title: \"(\d+) Job Sources\"", 'title: "999 Job Sources"', "landing-source-count"),
]


def unwatched_claims() -> list[str]:
    """Find docs that state a guarded fact but are NOT in LIVING_DOCS.

    The other half of this drill, and the one the registry specifically asks
    for: "a doc it is not watching". The checker has shipped blind exactly this
    way before -- frontend/CLAUDE.md carried a stale "The 28 hard rules" while
    being absent from LIVING_DOCS, so the one file with the wrong number was
    the one file the guard could not see.

    Planting a lie only proves the docs on the LIST are scanned. It says
    nothing about a doc that should be on the list and isn't. This does.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import doc_sync_check as dsc  # noqa: PLC0415

    watched = {w.replace("\\", "/") for w in dsc.LIVING_DOCS}
    patterns = [p for _, _, p in dsc.build_checks()[0]]

    # graphify-out/ holds dated, machine-generated graph snapshots. They are
    # frozen records of what the repo looked like on a day, so a "stale" number
    # in one is correct, not drift. Archives are excluded for the same reason.
    # graphify-out/ holds dated machine-generated graph snapshots; docs/harness/
    # fable/ and reviews/ hold dated audit and review records. All three are
    # FROZEN accounts of what was true on a day. A "stale" number in a dated
    # record is correct — rewriting it would falsify the record. Only LIVING
    # docs owe agreement with today's code.
    skip_dirs = (
        "node_modules", ".git", "_archive", "archive",
        "graphify-out", "fable", "reviews",
    )

    findings: list[str] = []
    for path in ROOT.rglob("*.md"):
        parts = path.parts
        if any(skip in parts for skip in skip_dirs):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in watched:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                findings.append(f"{rel} claims /{pat}/ -> {m.group(0)!r} but is not in LIVING_DOCS")
                break
    return findings


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
                # Explicit utf-8, never text=True: that decodes with the
                # machine's locale (cp1252 on Windows), and the drift report
                # is full of em-dashes. Caught by scripts/encoding_guard.py --
                # this file's first draft printed "MUTATION TEST FAILED <?>".
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT),
            )
            if fact in (proc.stdout or ""):
                print(f"PASS  {fact:16s} went RED when {rel} lied")
            else:
                failures.append(f"{fact}: stayed GREEN on a broken {rel} — the guard is blind")
        finally:
            path.write_bytes(original)

    # Second half of the drill: docs that make a guarded claim while sitting
    # outside LIVING_DOCS. Planting a lie proves the LISTED docs are scanned;
    # this proves nothing that matters is missing from the list.
    for finding in unwatched_claims():
        failures.append(f"unwatched-claim: {finding}")

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
