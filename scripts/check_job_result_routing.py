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
one fix attempt already spent.

HOW IT READS SHELL (FC-005 — learned the hard way)
--------------------------------------------------
The first versions read physical LINES. Shell puts many statements on one line, and
an adversarial run found 15 of 21 real bugs missed and 7 of 12 correct scripts
flagged. So the script is first turned into STATEMENTS the way bash sees them:
`${{ needs.X.result }}` written straight into the script becomes a variable, `\\`
continuations are joined, heredoc bodies are skipped, quotes / escapes / comments
are honoured, and a leading `then` / `do` / `else` is peeled off. Every check runs
on those statements.

WHAT IT CHECKS, for every variable that carries a result (step/job `env:`,
`${{ needs.X.result }}` in the script, and anything assigned from those)
  1. CASE   `case "$VAR"` needs a `*)` arm (or arms for all four values).
  2. ELIF   an if/elif chain routing on 2+ values needs an `else` -- unless a
            condition is purely `!=` (that IS a catch-all), or every branch
            exits/returns (the code after `fi` is then the catch-all).
  3. LONE   separate else-less `if`s or `[ .. ] && x=..` lines that SET the same
            variable for `success` and another value, in a step with no complete
            router over that result. (`echo`-only status lines are not routing.)

NOT COVERED (documented, see fixtures/owed_*): a `case` inside `$( )`, `else if`
nesting, and non-bash steps (`actions/github-script` JavaScript).

THE FIXTURES ARE THE SPEC
-------------------------
scripts/fixtures/job_result_routing/: every `bad_*` must be flagged, every `good_*`
must pass, `owed_*` are known gaps (reported, not failed). Every shape any reviewer
ever found lives there forever. New finding -> new fixture FIRST, then the fix.

USAGE
  python scripts/check_job_result_routing.py            # check .github/workflows
  python scripts/check_job_result_routing.py --root DIR # check another directory
  python scripts/check_job_result_routing.py --drill    # run the fixtures
  python scripts/check_job_result_routing.py --drill --break-checker CASE|IF
      # negative control: blinds one check, so the drill MUST exit non-zero
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
FIXTURES = ROOT / "scripts" / "fixtures" / "job_result_routing"

_NEEDS = r"needs(?:\.([A-Za-z0-9_-]+)|\[\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\])\.result"
_RESULT_EXPR = re.compile(r"^\$\{\{\s*" + _NEEDS + r"\s*\}\}$")
_RESULT_INTERP = re.compile(r"\$\{\{\s*" + _NEEDS + r"\s*\}\}")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGN = re.compile(r"^(?:(?:local|export|readonly|declare(?:\s+-\w+)*)\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_EXITS = re.compile(r"^(exit|return)\b")
ALL_RESULTS = {"success", "failure", "cancelled", "skipped"}

# Checks a negative control may switch off (--break-checker).
CHECKS = ("CASE", "IF")


def _result_vars(env: object) -> list[str]:
    """Names of env vars that carry a job result."""
    if not isinstance(env, dict):
        return []
    return [k for k, v in env.items() if isinstance(v, str) and _RESULT_EXPR.match(v.strip())]


def _interp_name(m: re.Match[str]) -> str:
    """The made-up shell variable standing in for a `${{ needs.X.result }}`."""
    return "__needs_" + re.sub(r"\W", "_", m.group(1) or m.group(2))


def _statements(text: str) -> tuple[list[tuple[int, str]], list[str]]:
    """Turn a `run:` block into (statements with 1-based line numbers, interp vars)."""
    interp: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name = _interp_name(m)
        interp.append(name)
        return "${" + name + "}"

    raw = _RESULT_INTERP.sub(sub, text).splitlines()
    # Join `\` continuations, keeping the first line's number.
    lines: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, line in enumerate(raw, 1):
        if not buf:
            start = n
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1] + " "
            continue
        lines.append((start, buf + line))
        buf = ""
    if buf:
        lines.append((start, buf))

    out: list[tuple[int, str]] = []
    heredoc_end = ""
    for n, line in lines:
        if heredoc_end:
            if line.strip() == heredoc_end:
                heredoc_end = ""
            continue  # a heredoc BODY is data, never shell
        pieces, cur, quote, i = [], "", "", 0
        while i < len(line):
            ch = line[i]
            if quote == "'":
                cur += ch
                if ch == "'":
                    quote = ""
            elif ch == "\\" and i + 1 < len(line):
                cur += ch + line[i + 1]
                i += 1
            elif quote == '"':
                cur += ch
                if ch == '"':
                    quote = ""
            elif ch in "'\"":
                quote = ch
                cur += ch
            elif ch == "#" and (not cur or cur[-1].isspace()):
                break
            elif ch == ";":
                pieces.append(cur)
                cur = ""
            else:
                cur += ch
            i += 1
        pieces.append(cur)
        for piece in pieces:
            t = piece.strip()
            while True:  # peel `then` / `do` (dropped) and `else` (its own statement)
                m = re.match(r"^(then|do|else)(\s+|$)", t)
                if not m:
                    break
                if m.group(1) == "else":
                    out.append((n, "else"))
                t = t[m.end():].strip()
            if t:
                out.append((n, t))
        hd = _HEREDOC.search(line)
        if hd:
            heredoc_end = hd.group(2)
    return out, interp


def _aliases(stmts: list[tuple[int, str]], names: list[str]) -> list[str]:
    """Add every variable assigned straight from a result variable (r="$R")."""
    found = list(dict.fromkeys(names))
    changed = True
    while changed:
        changed = False
        for _, s in stmts:
            m = _ASSIGN.match(s)
            if not m:
                continue
            val = m.group(2).strip().strip("'\"")
            src = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", val)
            if src and src.group(1) in found and m.group(1) not in found:
                found.append(m.group(1))
                changed = True
    return found


class _Var:
    """The comparisons one result variable takes part in."""

    def __init__(self, var: str) -> None:
        v = r'"?\$\{?' + re.escape(var) + r'\}?"?'
        w = r'["\']?([A-Za-z_]+)["\']?'
        self.pos = [re.compile(v + r"\s*==?\s*" + w), re.compile(w + r"\s*==?\s*" + v)]
        self.neg = [re.compile(v + r"\s*!=\s*" + w), re.compile(w + r"\s*!=\s*" + v)]
        self.case = re.compile(r"^case\s+" + v + r"\s+in\b(.*)$")

    def positives(self, text: str) -> set[str]:
        return {x for p in self.pos for x in p.findall(text)}

    def negatives(self, text: str) -> set[str]:
        return {x for p in self.neg for x in p.findall(text)}


def _arm_patterns(text: str) -> list[str] | None:
    """`success|failure) body` -> ['success', 'failure']; None if not an arm."""
    m = re.match(r"^\(?\s*([^()]*?)\s*\)", text)
    if not m or "=" in m.group(1) or "$(" in text[: m.end()]:
        return None
    return [p.strip() for p in m.group(1).split("|")]


def _check_case(stmts: list[tuple[int, str]], var: str) -> tuple[list[int], bool]:
    """(lines of `case "$VAR"` with no catch-all, whether a complete case exists)."""
    bad, complete_seen, cv = [], False, _Var(var)
    for i, (n, s0) in enumerate(stmts):
        m = cv.case.match(s0)
        if not m:
            continue
        arms: list[list[str]] = []
        first = _arm_patterns(m.group(1).strip())
        if first:
            arms.append(first)
        depth = 0
        for _, s in stmts[i + 1:]:
            if re.match(r"^case\s", s):
                depth += 1
            elif re.match(r"^esac\b", s):
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0:
                pats = _arm_patterns(s)
                if pats:
                    arms.append(pats)
        flat = [p for a in arms for p in a]
        literal = {p.strip("'\"") for p in flat}
        if "*" in flat or ALL_RESULTS <= literal:
            complete_seen = True
        else:
            bad.append(n)
    return bad, complete_seen


def _chain(stmts: list[tuple[int, str]], i: int) -> tuple[list[str], list[list[str]], bool, int]:
    """Walk an if-chain at stmts[i]: (conditions, branch bodies, has_else, end index)."""
    conds, bodies, has_else, depth = [stmts[i][1][2:].strip()], [[]], False, 0
    j = i + 1
    while j < len(stmts):
        t = stmts[j][1]
        if re.match(r"^if\s", t):
            depth += 1
        elif re.match(r"^fi\b", t):
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and re.match(r"^elif\s", t):
            conds.append(t[4:].strip())
            bodies.append([])
            j += 1
            continue
        elif depth == 0 and t == "else":
            has_else = True
            bodies.append([])
            j += 1
            continue
        bodies[-1].append(t)
        j += 1
    return conds, bodies, has_else, j


def _check_if(stmts: list[tuple[int, str]], var: str, case_complete: bool) -> list[tuple[int, str]]:
    """Return (line, message) for unrouted if/elif chains and lone branches over VAR."""
    cv, bad = _Var(var), []
    routed = case_complete
    lone: list[tuple[int, set[str], set[str]]] = []  # (line, values, assigned names)

    for i, (n, s) in enumerate(stmts):
        if re.match(r"^(\[\[?|test)\s", s) and "&&" in s:
            t = re.sub(r"\|\|\s*(true|:)\s*$", "", s).strip()  # `|| true` is not routing
            if "||" in t:
                continue  # `... && a || b`: b is its catch-all
            parts = [p.strip() for p in t.split("&&")]
            vals = set().union(*(cv.positives(p) for p in parts[:-1]))
            a = _ASSIGN.match(parts[-1])
            if vals and a:
                lone.append((n, vals, {a.group(1)}))
            continue
        if not re.match(r"^if\s", s):
            continue
        conds, bodies, has_else, _ = _chain(stmts, i)
        if not any(cv.positives(c) or cv.negatives(c) for c in conds):
            continue  # this chain is not about VAR
        pure_neg = any(cv.negatives(c) and not cv.positives(c) for c in conds)
        values = set().union(*(cv.positives(c) for c in conds))
        if has_else or pure_neg:
            routed = True
            continue
        if len(conds) >= 2:  # an if/elif router
            all_exit = all(b and _EXITS.match(b[-1]) for b in bodies)
            if len(values) >= 2 and not all_exit:
                bad.append((n, f"if/elif chain over ${var} has no `else`"))
            continue
        assigned = {m.group(1) for b in bodies for m in [_ASSIGN.match(x) for x in b] if m}
        if values and assigned:
            lone.append((n, values, assigned))

    if not routed:
        for name in sorted({a for _, _, names in lone for a in names}):
            hits = [(ln, vals) for ln, vals, names in lone if name in names]
            covered = set().union(*(vals for _, vals in hits))
            if len(hits) >= 2 and "success" in covered and len(covered) >= 2:
                bad.append((hits[0][0], f"separate branches set ${name} for {sorted(covered)} with no catch-all"))
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
                stmts, interp = _statements(step["run"])
                names = _aliases(stmts, job_vars + _result_vars(step.get("env")) + interp)
                label = step.get("name") or step.get("id") or "?"
                where = f"{wf.name}: job `{job_id}` step `{label}`"
                for var in names:
                    case_bad, complete = _check_case(stmts, var)
                    if broken != "CASE":
                        for ln in case_bad:
                            problems.append(f'{where} run-line {ln}: `case "${var}"` has no `*)` arm '
                                            "-- cancelled/skipped fall through silently")
                    if broken != "IF":
                        for ln, msg in _check_if(stmts, var, complete):
                            problems.append(f"{where} run-line {ln}: {msg} -- cancelled/skipped fall through silently")
    return problems


def drill(broken: str = "") -> int:
    """Run every fixture: bad_* flagged, good_* clean, owed_* reported. 0 = pass."""
    files = sorted(FIXTURES.glob("*.yml"))
    if not files:
        print(f"DRILL FAIL: no fixtures in {FIXTURES}")
        return 1
    wrong, owed_caught = 0, []
    for f in files:
        kind = f.name.split("_", 1)[0]
        # check() reads a directory: a temp one holding just this file (never the repo).
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "w.yml").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            got = check(Path(tmp), broken)
        if kind == "owed":
            if got:
                owed_caught.append(f.name)
            continue
        ok = bool(got) == (kind == "bad")
        if not ok:
            wrong += 1
            print(f"  WRONG {f.name}: {len(got)} finding(s), expected {'some' if kind == 'bad' else 'none'}")
    n_owed = sum(1 for f in files if f.name.startswith("owed_"))
    print(f"  {len(files) - n_owed} fixtures judged, {n_owed} owed (known gaps)")
    for name in owed_caught:
        print(f"  NOTE {name} is now caught -- rename it bad_*")
    print("DRILL PASS" if not wrong else f"DRILL FAIL ({wrong} fixture(s) wrong)")
    return 1 if wrong else 0


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
