#!/usr/bin/env python3
"""Guard: a shell branch over a job RESULT must have a catch-all arm.

FAILURE CLASS FC-001 (docs/harness/FAILURE_CATALOG.md) — "unrouted job result".

WHY THIS EXISTS
---------------
`needs.<job>.result` has FOUR values: success, failure, cancelled, skipped. A job
that hits its own `timeout-minutes` is `cancelled`, not `failure`. PR #679 shipped

    mode=none
    case "$VERIFY_RESULT" in
      success) mode=ship ;;
      failure) mode=verify-failed ;;
    esac

so a timed-out verify left `mode=none`, every later step was gated on another mode,
and the job ended GREEN having done nothing — no PR, no comment, no relabel, with the
one fix attempt already spent. The bug reviewer re-found it on four consecutive
commits before it was fixed. A careful read misses it because the YAML is valid and
the happy paths are all correct; only the value nobody wrote down is wrong.

WHAT IT CHECKS
--------------
For every step whose `env:` maps a variable to `${{ needs.<job>.result }}`:
  1. every `case "$VAR" in ... esac` over that variable has a `*)` arm;
  2. every `if/elif` chain that compares that variable against TWO OR MORE
     distinct values has an `else` (one single `if [ "$X" = failure ]` is a
     deliberate check, not a router, and is left alone).

USAGE
  python scripts/check_job_result_routing.py            # check .github/workflows
  python scripts/check_job_result_routing.py --root DIR # check another directory
  python scripts/check_job_result_routing.py --drill    # prove it can go RED
  python scripts/check_job_result_routing.py --drill --break-checker CASE
      # negative control: blinds check 1, so the drill MUST exit non-zero
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

_RESULT_EXPR = re.compile(r"^\$\{\{\s*needs\.[A-Za-z0-9_-]+\.result\s*\}\}$")
_KEYWORD = re.compile(r"^(if|elif|else|fi)\b")

# Checks a negative control may switch off (--break-checker).
CHECKS = ("CASE", "IF")


def _result_vars(env: object) -> list[str]:
    """Names of env vars that carry a job result."""
    if not isinstance(env, dict):
        return []
    return [k for k, v in env.items() if isinstance(v, str) and _RESULT_EXPR.match(v.strip())]


def _check_case(lines: list[str], var: str) -> list[int]:
    """Return 1-based line numbers of `case "$VAR"` blocks with no `*)` arm."""
    bad: list[int] = []
    opener = re.compile(r'^case\s+"?\$\{?' + re.escape(var) + r'\}?"?\s+in\b')
    for i, raw in enumerate(lines):
        if not opener.match(raw.strip()):
            continue
        depth, has_default = 0, False
        for line in lines[i + 1:]:
            s = line.strip()
            if re.match(r"^case\b", s):
                depth += 1
            if re.match(r"^esac\b", s):
                if depth == 0:
                    break
                depth -= 1
            if depth == 0 and re.match(r"^\*\s*\)", s):
                has_default = True
        if not has_default:
            bad.append(i + 1)
    return bad


def _check_if(lines: list[str], var: str) -> list[int]:
    """Return line numbers of if/elif chains routing on VAR (2+ values) with no else."""
    bad: list[int] = []
    cmp_ = re.compile(r'"\$\{?' + re.escape(var) + r'\}?"\s*!?==?\s*"?([A-Za-z_]+)"?')
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s.startswith("if ") or re.search(r"\bfi\s*$", s):
            continue  # not a chain opener, or a one-line if
        values = set(cmp_.findall(s))
        depth, has_else = 0, False
        for line in lines[i + 1:]:
            t = line.strip()
            m = _KEYWORD.match(t)
            if not m:
                continue
            kw = m.group(1)
            if kw == "if" and not re.search(r"\bfi\s*$", t):
                depth += 1
            elif kw == "fi":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and kw == "elif":
                values |= set(cmp_.findall(t))
            elif depth == 0 and kw == "else":
                has_else = True
        if len(values) >= 2 and not has_else:
            bad.append(i + 1)
    return bad


def check(root: Path, broken: str = "") -> list[str]:
    """Return one human-readable problem per unrouted branch under ROOT."""
    problems: list[str] = []
    for wf in sorted(list(root.glob("*.yml")) + list(root.glob("*.yaml"))):
        try:
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{wf.name}: does not parse ({exc.__class__.__name__})")
            continue
        jobs = (doc or {}).get("jobs") or {}
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_vars = _result_vars(job.get("env"))
            for step in job.get("steps") or []:
                if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                    continue
                names = job_vars + _result_vars(step.get("env"))
                if not names:
                    continue
                lines = step["run"].splitlines()
                label = step.get("name") or step.get("id") or "?"
                for var in names:
                    if broken != "CASE":
                        for ln in _check_case(lines, var):
                            problems.append(
                                f"{wf.name}: job `{job_id}` step `{label}` run-line {ln}: "
                                f'`case "${var}"` has no `*)` arm -- cancelled/skipped fall through silently'
                            )
                    if broken != "IF":
                        for ln in _check_if(lines, var):
                            problems.append(
                                f"{wf.name}: job `{job_id}` step `{label}` run-line {ln}: "
                                f"if/elif chain over ${var} has no `else` -- cancelled/skipped fall through silently"
                            )
    return problems


_DRILL_BAD_CASE = """\
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: route
        env:
          VERIFY_RESULT: ${{ needs.verify.result }}
        run: |
          mode=none
          case "$VERIFY_RESULT" in
            success) mode=ship ;;
            failure) mode=verify-failed ;;
          esac
"""

_DRILL_BAD_IF = """\
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    env:
      FIX_RESULT: ${{ needs.fix.result }}
    steps:
      - name: route
        run: |
          if [ "$FIX_RESULT" = "success" ] && [ "$X" = "true" ]; then
            mode=ship
          elif [ "$FIX_RESULT" = "failure" ]; then
            mode=fix-failed
          fi
"""

_DRILL_GOOD = """\
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: route
        env:
          VERIFY_RESULT: ${{ needs.verify.result }}
          FIX_RESULT: ${{ needs.fix.result }}
        run: |
          case "$VERIFY_RESULT" in
            success) mode=ship ;;
            *) mode=incomplete ;;
          esac
          if [ "$FIX_RESULT" = "success" ]; then
            mode=park
          elif [ "$FIX_RESULT" = "failure" ]; then
            mode=fix-failed
          else
            mode=incomplete
          fi
          if [ "$FIX_RESULT" = "failure" ]; then echo one-line-is-fine; fi
"""


def drill(broken: str = "") -> int:
    """Each defect must go RED, the clean file must stay GREEN. 0 = drill passed."""
    cases = [("bad-case", _DRILL_BAD_CASE, True), ("bad-if", _DRILL_BAD_IF, True), ("good", _DRILL_GOOD, False)]
    failed = 0
    for name, text, must_fail in cases:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "w.yml").write_text(text, encoding="utf-8")
            got = check(Path(tmp), broken)
        ok = bool(got) == must_fail
        print(f"  drill {name}: {'ok' if ok else 'WRONG'} ({len(got)} finding(s), expected {'some' if must_fail else 'none'})")
        failed += not ok
    print("DRILL PASS" if not failed else f"DRILL FAIL ({failed} case(s) wrong)")
    return 1 if failed else 0


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=WORKFLOWS)
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--break-checker", choices=CHECKS, default="")
    args = ap.parse_args()
    if args.drill:
        return drill(args.break_checker)
    problems = check(args.root, args.break_checker)
    for p in problems:
        print(f"::error::{p}")
    print(f"{len(problems)} unrouted job-result branch(es) [FC-001]" if problems else "OK: every job-result branch has a catch-all [FC-001]")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
