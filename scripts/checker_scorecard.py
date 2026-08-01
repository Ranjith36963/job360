#!/usr/bin/env python3
"""Measure the blind checker: is our judge any good?

HOLE #2. The blind checker shipped and immediately started labelling PRs
`checker:approved|rejected|uncertain`. Nobody has ever measured whether those
labels mean anything. An unmeasured judge is an opinion wearing a badge.

Why this matters, measured elsewhere:
  * a SINGLE-TRIAL LLM judge flips its verdict 13.6% of the time
    (arXiv 2606.13685) — one run is not a verdict, it is a sample
  * real reviewers span a huge range: Greptile 82% catch / 11 false positives
    vs CodeRabbit 44% / 2 (same benchmark). "It found something" tells you
    nothing about which end of that range you are on.

WHAT THIS MEASURES (only what the data can honestly support):

  1. VERDICT MIX      — how often approve / reject / uncertain.
                        A checker that approves 100% is a rubber stamp; one
                        that rejects 100% is noise. Either is useless.
  2. AGREEMENT        — of the PRs the checker judged, how many did the human
                        MERGE. checker:rejected + merged = a false alarm the
                        human overruled. checker:approved + closed unmerged =
                        a miss the human caught. This is the only ground truth
                        we have, and it is DELAYED and NOISY — a human may
                        merge despite a fair rejection. Reported as agreement,
                        never as "accuracy".
  3. COVERAGE         — how many loop PRs got a verdict at all. The checker is
                        continue-on-error; silent skipping would look identical
                        to approval.

WHAT IT DELIBERATELY DOES NOT CLAIM: a catch rate. Measuring catch rate needs
bugs we deliberately planted and know the answer to. Until that exists, this
file reports agreement and says so. Inventing a catch-rate number here would
be exactly the failure the Promise skill exists to prevent.

Usage:
    python scripts/checker_scorecard.py                 # markdown to stdout
    python scripts/checker_scorecard.py --json          # machine-readable
    python scripts/checker_scorecard.py --days 30
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

VERDICT_LABELS = ("checker:approved", "checker:rejected", "checker:uncertain")
LOOP_BRANCH_PREFIXES = ("repair/issue-", "fix/")


# Windows consoles default to cp1252 and would crash on the warning glyphs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def gh_json(args: list[str]) -> list[dict]:
    """Run a gh command and parse JSON. Never raises: no data is a finding."""
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=120, check=False
        )
        if out.returncode != 0:
            print(f"(gh failed: {out.stderr.strip()[:200]})", file=sys.stderr)
            return []
        return json.loads(out.stdout or "[]")
    except Exception as exc:  # noqa: BLE001 — a measurement tool must not crash a run
        print(f"(gh error: {exc})", file=sys.stderr)
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    prs = gh_json([
        "pr", "list", "--state", "all", "--limit", "200",
        "--json", "number,title,labels,state,createdAt,mergedAt,headRefName,author",
    ])

    recent = []
    for p in prs:
        try:
            created = datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if created >= cutoff:
            recent.append(p)

    def labels_of(p: dict) -> set[str]:
        return {lab.get("name", "") for lab in p.get("labels", [])}

    def is_loop_pr(p: dict) -> bool:
        """A PR the harness opened — by branch shape or by bot authorship."""
        branch = p.get("headRefName", "")
        author = (p.get("author") or {}).get("login", "")
        return branch.startswith("repair/issue-") or "github-actions" in author

    loop_prs = [p for p in recent if is_loop_pr(p)]
    judged = [p for p in recent if labels_of(p) & set(VERDICT_LABELS)]

    mix = Counter()
    for p in judged:
        for lab in labels_of(p) & set(VERDICT_LABELS):
            mix[lab] += 1

    # Agreement, on judged PRs that have actually resolved.
    resolved = [p for p in judged if p.get("state") in ("MERGED", "CLOSED")]
    agree = disagree = 0
    disagreements: list[str] = []
    for p in resolved:
        labs = labels_of(p)
        merged = p.get("state") == "MERGED"
        if "checker:approved" in labs:
            (agree, disagree) = (agree + 1, disagree) if merged else (agree, disagree + 1)
            if not merged:
                disagreements.append(f"#{p['number']} approved but NOT merged — possible miss")
        elif "checker:rejected" in labs:
            (agree, disagree) = (agree + 1, disagree) if not merged else (agree, disagree + 1)
            if merged:
                disagreements.append(f"#{p['number']} rejected but merged — possible false alarm")

    coverage = (len(judged) / len(loop_prs) * 100) if loop_prs else 0.0
    agreement = (agree / (agree + disagree) * 100) if (agree + disagree) else None

    data = {
        "window_days": args.days,
        "loop_prs": len(loop_prs),
        "judged": len(judged),
        "coverage_pct": round(coverage, 1),
        "verdict_mix": dict(mix),
        "resolved_judged": len(resolved),
        "agreement_pct": round(agreement, 1) if agreement is not None else None,
        "disagreements": disagreements,
    }

    if args.as_json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"# Blind-checker scorecard — last {args.days} days\n")
    if not loop_prs:
        print("**No loop-opened PRs in the window.** Nothing to measure yet — "
              "this is an honest empty result, not a pass.\n")
        return 0

    print(f"- Loop PRs opened: **{len(loop_prs)}**")
    print(f"- Got a checker verdict: **{len(judged)}** ({coverage:.0f}% coverage)")
    if coverage < 90 and loop_prs:
        print("  - ⚠️ Under 90%: the checker is `continue-on-error`, so a silent "
              "skip looks exactly like an approval. Investigate the gap.")
    print()

    if mix:
        print("## Verdict mix\n")
        total = sum(mix.values())
        for lab in VERDICT_LABELS:
            n = mix.get(lab, 0)
            print(f"- `{lab}`: **{n}** ({n / total * 100:.0f}%)")
        appr = mix.get("checker:approved", 0)
        if total >= 5 and appr == total:
            print("\n  - ⚠️ **Approves everything so far.** A judge that never "
                  "objects carries no information. Feed it a deliberately bad "
                  "diff and confirm it can say reject.")
    print()

    print("## Agreement with the human merge decision\n")
    if agreement is None:
        print("Not enough resolved judged PRs yet. **No number is better than a "
              "made-up one.**")
    else:
        print(f"- Resolved judged PRs: **{len(resolved)}**")
        print(f"- Agreement: **{agreement:.0f}%** ({agree} agree / {disagree} disagree)")
        print("\n> This is AGREEMENT, not accuracy. A human may merge despite a "
              "fair rejection, and both can be right. It is the only ground "
              "truth we have until we plant known bugs on purpose.")
    if disagreements:
        print("\n### Where checker and human disagreed\n")
        for d in disagreements[:10]:
            print(f"- {d}")

    print("\n---\n**Not measured here:** catch rate (needs planted bugs with "
          "known answers) and verdict stability (needs the same diff judged 3+ "
          "times — single-trial LLM judges flip 13.6% of the time).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
