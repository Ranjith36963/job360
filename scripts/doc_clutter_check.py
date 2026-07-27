#!/usr/bin/env python3
"""Loop 3, second gear — CURATE: is the doc estate CLEAN? (report-only, read-only)

doc_sync_check.py asks "do the docs match the code?" (TRUTH).
This asks "is what the agent reads actually usable?" (HYGIENE).

They are different failures. A doc can be perfectly true and still be a 1,577-line
file nobody links to — which is worse than missing, because an agent will read it,
trust it, and answer confidently from a corner of it. Measured on this repo
2026-07-26: 136 tracked .md / 33,708 lines, 55% untouched >30 days, half of them
orphaned. Stale docs have already produced confidently-wrong answers here (the
fable audit recorded findings M2 and M10 as "open" months after the code fixed
them).

WHY A RATCHET, NOT A TARGET.
The gap to "ideal" is large and will not close this month. A check that screams
about the same gap every run is a nag, and a nag gets ignored — that is exactly
how STATUS-DAILY.md died. So this fires ONLY when the estate gets WORSE than the
committed BUDGET below (same shape as the mypy ratchet this repo already trusts).
The report always shows the distance to TARGET so the aspiration stays visible,
but distance alone never fails the run.

To tighten: improve the estate, re-run, lower the BUDGET numbers to the new
values, commit. The budget may only ever go DOWN.

Exit 0 = no regression. Exit 1 = regression (something got worse). Exit 2 = broke.
Read-only: it reports, it never edits (loop1_safe_reenable.md — "verify=read is safe").
Run: python scripts/doc_clutter_check.py   (from the repo root)
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections import defaultdict

# The report contains arrows/box characters. Windows consoles default to cp1252
# and a UnicodeEncodeError would make the checker exit 2 ("checker broke") on a
# dev machine while passing in CI — a portability trap that would train people to
# distrust the loop. Force UTF-8 on our own stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── The ratchet. Measured 2026-07-26 — these ARE today's real numbers, so any
# regression fires immediately. May only ever go DOWN. ───────────────────────
BUDGET = {
    "orphan_pct": 61,        # docs nothing links to
    "stale_pct": 51,         # untouched > STALE_DAYS
    "max_file_lines": 1578,  # largest single living doc (IMPLEMENTATION_LOG.md)
    "dupe_clusters": 7,      # same-topic doc pairs with no cross-reference
    "root_loose": 10,        # stray .md sitting in the repo root
}

# Where we actually want to be. Shown, never enforced (see the docstring).
TARGET = {
    "orphan_pct": 10,
    "stale_pct": 25,
    "max_file_lines": 400,
    "dupe_clusters": 0,
    "root_loose": 3,
}

STALE_DAYS = 30
# Archives and decision logs are SUPPOSED to sit still — ageing is not rot.
EXEMPT_FROM_STALE = ("docs/_archive/", "docs/fable/AUDIT-", "docs/plans/", "CHANGELOG")
ROOT_ALLOWED = {"README.md", "CLAUDE.md", "ARCHITECTURE.md", "CONTRIBUTING.md",
                "STATUS.md", "SECURITY.md"}


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def tracked_docs() -> list[str]:
    return [f for f in sh("git", "ls-files", "*.md").split() if f]


def last_touched() -> dict[str, float]:
    """One git pass for every file's last commit time (per-file git log is O(n) slow)."""
    out = sh("git", "log", "--format=@%ct", "--name-only", "--no-merges")
    seen: dict[str, float] = {}
    ts = 0.0
    for line in out.splitlines():
        if line.startswith("@"):
            ts = float(line[1:] or 0)
        elif line.strip().endswith(".md") and line.strip() not in seen:
            seen[line.strip()] = ts
    return seen


def main() -> int:
    files = tracked_docs()
    if not files:
        print("no tracked markdown found — nothing to check")
        return 0

    text: dict[str, str] = {}
    lines: dict[str, int] = {}
    for f in files:
        try:
            t = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            t = ""
        text[f] = t
        lines[f] = t.count("\n") + 1

    # ── orphans: nothing else links to them ────────────────────────────────
    inbound: dict[str, int] = defaultdict(int)
    basename = {f.split("/")[-1]: f for f in files}
    for src, t in text.items():
        for target in re.findall(r"\]\(([^)]+\.md)[^)]*\)", t):
            leaf = target.split("#")[0].split("/")[-1]
            dst = basename.get(leaf)
            if dst and dst != src:
                inbound[dst] += 1
    orphans = [f for f in files if inbound[f] == 0]
    orphan_pct = round(100 * len(orphans) / len(files))

    # ── staleness ──────────────────────────────────────────────────────────
    touched = last_touched()
    now = time.time()
    considered = [f for f in files if not f.startswith(EXEMPT_FROM_STALE)]
    stale = [f for f in considered
             if (now - touched.get(f, now)) / 86400 > STALE_DAYS]
    stale_pct = round(100 * len(stale) / max(len(considered), 1))

    # ── oversized living docs ──────────────────────────────────────────────
    living = {f: n for f, n in lines.items() if not f.startswith("docs/_archive/")}
    biggest = max(living, key=lambda f: living[f]) if living else ""
    max_lines = living.get(biggest, 0)

    # ── duplicate topics: same first-3 title words, no cross-reference ─────
    titles: dict[str, list[str]] = defaultdict(list)
    for f, t in text.items():
        head = next((ln for ln in t.splitlines() if ln.startswith("#")), "")
        key = " ".join(re.findall(r"[a-z]+", head.lower())[:3])
        if key:
            titles[key].append(f)
    dupes = {k: v for k, v in titles.items() if len(v) > 1}

    # ── loose files in the repo root ───────────────────────────────────────
    root_loose = [f for f in files if "/" not in f and f not in ROOT_ALLOWED]

    actual = {
        "orphan_pct": orphan_pct,
        "stale_pct": stale_pct,
        "max_file_lines": max_lines,
        "dupe_clusters": len(dupes),
        "root_loose": len(root_loose),
    }

    regressions = {k: v for k, v in actual.items() if v > BUDGET[k]}

    # ── report (GitHub-issue ready) ────────────────────────────────────────
    print("# Doc estate — clutter check (Loop 3, CURATE gear)\n")
    print(f"{len(files)} tracked markdown files · {sum(lines.values()):,} lines\n")
    print("| metric | now | budget | target |")
    print("|---|---:|---:|---:|")
    for k in BUDGET:
        flag = " ⚠️" if k in regressions else ""
        print(f"| {k} | {actual[k]}{flag} | {BUDGET[k]} | {TARGET[k]} |")

    print("\n<details><summary>Worst offenders</summary>\n")
    print(f"\n**Orphans ({len(orphans)})** — nothing links to them; biggest first:\n")
    for f in sorted(orphans, key=lambda f: -lines[f])[:10]:
        print(f"- `{f}` ({lines[f]} lines)")
    if biggest:
        print(f"\n**Largest living doc:** `{biggest}` ({max_lines} lines)\n")
    if dupes:
        print(f"\n**Duplicate topics ({len(dupes)})** — same subject, separate files:\n")
        for k, v in sorted(dupes.items(), key=lambda x: -len(x[1]))[:6]:
            print(f"- *{k}* → {', '.join('`' + p.split('/')[-1] + '`' for p in v[:4])}")
    if root_loose:
        print(f"\n**Loose in repo root ({len(root_loose)}):** "
              + ", ".join(f"`{f}`" for f in sorted(root_loose)[:12]))
    print("\n</details>\n")

    if regressions:
        print("## Regression — the estate got worse\n")
        for k, v in regressions.items():
            print(f"- **{k}**: {v} exceeds the committed budget of {BUDGET[k]}")
        print("\nFix the regression, or (if the growth is deliberate and healthy) "
              "raise that one number in `scripts/doc_clutter_check.py` in the same PR "
              "that causes it — so the loosening is reviewed, never silent.")
        return 1

    print("No regression: every metric is within its committed budget. "
          "(The gap to *target* is shown above and is aspiration, not a failure.)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — the checker must fail LOUD, not silently green
        print(f"doc_clutter_check crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
