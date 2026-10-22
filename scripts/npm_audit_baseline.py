#!/usr/bin/env python3
"""Fail a PR's npm audit only for advisories the PR itself brings in.

WHY THIS EXISTS
---------------
`npm audit (frontend deps)` is a required check. It audits the PR's lockfile
against TODAY's advisory database, so a new advisory published against a
package main already ships turned every open PR red at once (38 PRs stale-red
on 2026-10-21), and people merged main into PRs that had changed nothing.

On a pull_request the workflow now audits twice, with the same flags:
the PR's lockfile (head) and the base commit's lockfile (base). This script
compares the two `npm audit --json` reports:

  * an advisory at or above the gate severity on head and NOT on base -> RED
    (the PR's own change introduced it);
  * one present on both -> a ::warning:: and a job-summary line,
    "inherited from main (not this PR's)". Main's own push and scheduled runs
    still run the plain blocking audit, so main's problems stay red on main.
  * the base report missing or unusable (no lockfile at base, npm error)
    -> today's behaviour: every gated advisory on head is RED (fail closed).
  * the head report unusable -> RED.

An advisory is keyed by (package name, advisory url, or source id if no url).

Usage:
  python scripts/npm_audit_baseline.py --head H.json [--base B.json] [--level high]
  python scripts/npm_audit_baseline.py --drill [--break-checker NEW]
Writes to $GITHUB_STEP_SUMMARY when it is set. Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LEVELS = ["info", "low", "moderate", "high", "critical"]

Key = tuple[str, str]


def load(path: str | None) -> dict | None:
    """The parsed report, or None when it is missing or npm did not audit."""
    if not path or not Path(path).is_file():
        return None
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or "error" in report:
        return None
    if not isinstance(report.get("vulnerabilities"), dict):
        return None
    return report


def advisories(report: dict, level: str) -> dict[Key, str]:
    """{(package, advisory id): title} for every advisory at or above `level`.

    Only the advisory objects in `via` are counted; a string in `via` just
    points at another package whose own entry carries the advisory.
    """
    floor = LEVELS.index(level)
    found: dict[Key, str] = {}
    for vuln in report["vulnerabilities"].values():
        for via in vuln.get("via", []):
            if not isinstance(via, dict):
                continue
            # an unknown severity counts as gated: fail closed, never open
            severity = via.get("severity")
            if severity in LEVELS and LEVELS.index(severity) < floor:
                continue
            ident = str(via.get("url") or via.get("source") or via.get("title"))
            name = str(via.get("name") or vuln.get("name"))
            found[(name, ident)] = f"[{via.get('severity')}] {via.get('title', '')}"
    return found


def compare(head: dict | None, base: dict | None, level: str,
            disabled: frozenset[str] = frozenset()) -> tuple[int, list[str]]:
    """(exit code, report lines). `disabled` exists only for the drill."""
    if head is None:
        return 1, ["::error::the PR's npm audit produced no usable report (npm error?) -- failing closed"]
    head_adv = advisories(head, level)
    if base is None:
        lines = ["::warning::no usable npm audit for the base commit -- every advisory counts as this PR's"]
        new = head_adv
        inherited: dict[Key, str] = {}
    else:
        base_adv = advisories(base, level)
        new = {k: v for k, v in head_adv.items() if k not in base_adv}
        inherited = {k: v for k, v in head_adv.items() if k in base_adv}
        lines = []
    if "NEW" in disabled:  # drill only: the guard goes blind to new advisories
        inherited, new = {**inherited, **new}, {}
    for (name, ident), title in sorted(inherited.items()):
        lines.append(f"::warning::inherited from main (not this PR's): {name} {title} {ident}")
    for (name, ident), title in sorted(new.items()):
        lines.append(f"::error::introduced by this PR: {name} {title} {ident}")
    lines.append(f"npm audit (>= {level}): {len(new)} introduced by this PR, {len(inherited)} inherited from main")
    return (1 if new else 0), lines


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    out = ["### npm audit -- production dependencies, PR vs base", ""]
    for line in lines:
        text = line.split("::", 2)[-1] if line.startswith("::") else line
        out.append(f"- {text}")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


# ── the drill: the comparison watched going red on fixture reports ───────────

def _report(*advs: tuple[str, str, str]) -> dict:
    vulns: dict[str, dict] = {}
    for name, url, severity in advs:
        vulns.setdefault(name, {"name": name, "severity": severity, "via": []})["via"].append(
            {"source": 1, "name": name, "url": url, "severity": severity, "title": f"{name} bug"})
    return {"auditReportVersion": 2, "vulnerabilities": vulns}


def drill(disabled: frozenset[str] = frozenset()) -> int:
    old = ("next", "https://github.com/advisories/GHSA-old", "high")
    new = ("braces", "https://github.com/advisories/GHSA-new", "critical")
    low = ("hono", "https://github.com/advisories/GHSA-low", "moderate")
    cases = [  # (what, head, base, expected exit)
        ("inherited only -> pass", _report(old, low), _report(old), 0),
        ("new advisory -> fail", _report(old, new), _report(old), 1),
        ("new advisory below the gate -> pass", _report(old, low), _report(old), 0),
        ("same package, new advisory id -> fail",
         _report(old, ("next", "https://github.com/advisories/GHSA-other", "high")), _report(old), 1),
        ("no base report -> fail closed", _report(old), None, 1),
        ("no head report -> fail closed", None, _report(), 1),
        ("clean head, no base -> pass", _report(), None, 0),
    ]
    fails = 0
    for what, head, base, want in cases:
        got, _ = compare(head, base, "high", disabled)
        ok = got == want
        fails += not ok
        print(f"[drill] {'ok ' if ok else 'FAILED'} {what} (exit {got}, want {want})")
    print(f"[drill] {len(cases) - fails}/{len(cases)} cases passed")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    def opt(flag: str, default: str | None = None) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else default

    disabled = frozenset([opt("--break-checker")]) if "--break-checker" in argv else frozenset()
    if "--drill" in argv:
        return drill(disabled)
    head_path = opt("--head")
    if not head_path:
        print(__doc__)
        return 2
    level = opt("--level", "high") or "high"
    code, lines = compare(load(head_path), load(opt("--base")), level)
    print("\n".join(lines))
    write_summary(lines)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
