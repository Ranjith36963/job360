#!/usr/bin/env python3
"""Guard the ONE property an alerting path must have: it may not change the
outcome of the work it is describing, and it may not go silent when the step it
quotes dies.

WHY THIS EXISTS
---------------
Six guards shipped in this repo in one week that could not fire. Two more were
found INSIDE the branch written to fix the first six. Both new ones were the
same shape as every earlier one: the guard tested something ADJACENT to the
thing that actually breaks. This file tests the thing itself.

    RULE A - AN ALERT MAY NOT FLIP THE VERDICT.
      `.github/actions/slack` fails LOUD on purpose (missing token, bad
      channel, ratelimited -> exit 1). That is right for an alarm. But a
      failing step makes GitHub's `failure()` true for the REST of the job,
      and it makes every later step with an implicit `success()` gate skip.
      So a mid-job Slack step that rate-limits could:
        * turn on "Report back when it could not fix it" - after the repair
          had already opened its PR. The loop then TELLS THE OWNER IT FAILED
          while the fix sits merged-ready. Measured on repair.yml:665/701 and
          pr-repair.yml:544/554 before this checker existed.
        * silence a SECOND, unrelated alert later in the same job, because
          that step's gate is implicitly `success() && ...`.
      The rule, stated as a property: for every step positioned AFTER the
      first alert step in a job, the gate's truth value must NOT depend on
      `failure()` / `success()` for ANY assignment of its other terms. That
      is checked by brute force below, not by pattern-matching a known-bad
      string - a new way of writing the same bug is still caught.

    RULE B - AN ALERT MAY NOT DIE WITH THE STEP IT QUOTES.
      Every detector's Slack branch keys off an output (`announce`, `pushed`,
      `capped`, `channel`) that the producing step only writes if it REACHES
      that line. If that step dies half way - a gh 403, a rate limit, an
      unbound variable - the output never exists, every branch skips, and the
      outage goes unannounced. That is the "alarm rang into an empty room"
      bug this repo has now hit four separate times, and it is always the LAST
      call in the job that hides it. So: any substantial step whose outputs an
      alert depends on must ALSO have a fallback alert gated on
      `always() && steps.<id>.outcome == 'failure'` (or `!= 'success'`).
      "Substantial" = a `uses:` step or a `run:` block over 3 lines. A
      one-line `echo x >> $GITHUB_OUTPUT` cannot realistically die, and
      demanding a fallback for it would be noise that gets ignored.

USAGE
  python scripts/check_alert_paths.py           # check
  python scripts/check_alert_paths.py --drill   # prove it can go RED

--drill mutates a COPY of .github/workflows in a temp dir. It NEVER writes to
the real tree - the earlier version of the sibling checker restored the real
files in a Python `finally`, which a Ctrl-C does not run, so an interrupted
drill left the repo holding the exact defect the checker exists to catch.
"""
from __future__ import annotations

import argparse
import itertools
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WF = REPO / ".github" / "workflows"
ALERT_ACTION = "./.github/actions/slack"

STATUS_FN = re.compile(r"\b(always|success|failure|cancelled)\s*\(\s*\)")
# One comparison, e.g. steps.ship.outcome != 'success'.  Treated as an opaque
# boolean atom: we do not care WHAT it evaluates to, only that its value is
# independent of whether the alert step failed.
COMPARISON = re.compile(
    r"[A-Za-z_][\w.\-\[\]']*\s*(?:==|!=)\s*(?:'[^']*'|\"[^\"]*\"|[A-Za-z_][\w.\-]*)"
)
STEP_REF = re.compile(r"steps\.([A-Za-z_][\w-]*)\.(?:outputs\.[\w-]+|outcome|conclusion)")


def gate_of(step: dict) -> str:
    """The step's real gate, with GitHub's implicit success() made explicit."""
    cond = step.get("if")
    cond = "" if cond is None else str(cond).strip()
    if not cond:
        return "success()"
    if STATUS_FN.search(cond):
        return cond
    # GitHub wraps any `if` that names no status function in success().
    return f"success() && ({cond})"


def is_alert(step: dict) -> bool:
    uses = step.get("uses")
    return isinstance(uses, str) and uses.rstrip("/") == ALERT_ACTION


def substantial(step: dict) -> bool:
    if step.get("uses"):
        return True
    run = str(step.get("run") or "")
    return len([ln for ln in run.splitlines() if ln.strip()]) > 3


def depends_on_job_status(gate: str) -> bool:
    """True if the gate's value can change purely because an earlier step failed.

    Every comparison becomes an independent boolean atom and we brute-force all
    assignments. No world-modelling, no assumptions about what `announce` holds
    - if there EXISTS a state of the world where the alert failing flips this
    gate, that is the bug.
    """
    atoms: list[str] = []

    def _atom(_m: re.Match[str]) -> str:
        atoms.append(_m.group(0))
        return f"A{len(atoms) - 1}"

    expr = COMPARISON.sub(_atom, gate)
    expr = re.sub(r"\balways\s*\(\s*\)", "True", expr)
    expr = re.sub(r"\bcancelled\s*\(\s*\)", "False", expr)
    expr = re.sub(r"\bfailure\s*\(\s*\)", "FAILED", expr)
    expr = re.sub(r"\bsuccess\s*\(\s*\)", "(not FAILED)", expr)
    expr = expr.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    # Anything left that is not a recognised token means we cannot reason about
    # this gate; say so rather than pass it silently.
    leftovers = re.sub(r"\bA\d+\b|True|False|FAILED|and|or|not|[()\s]", "", expr)
    if leftovers:
        raise ValueError(f"ungradeable gate {gate!r} (leftover: {leftovers!r})")

    if len(atoms) > 12:
        raise ValueError(f"gate too wide to brute force: {gate!r}")

    for combo in itertools.product([False, True], repeat=len(atoms)):
        env = {f"A{i}": v for i, v in enumerate(combo)}
        clean = eval(expr, {"__builtins__": {}}, {**env, "FAILED": False})  # noqa: S307
        dirty = eval(expr, {"__builtins__": {}}, {**env, "FAILED": True})  # noqa: S307
        if bool(clean) != bool(dirty):
            return True
    return False


def has_fallback(steps: list, step_id: str) -> bool:
    pat = re.compile(
        rf"steps\.{re.escape(step_id)}\.outcome\s*(?:==\s*'failure'|!=\s*'success')"
    )
    for step in steps:
        if not isinstance(step, dict):
            continue
        cond = str(step.get("if") or "")
        if "always()" in cond and pat.search(cond):
            return True
    return False


def check(wf_dir: Path = WF) -> tuple[list[str], dict]:
    failures: list[str] = []
    stats = {"workflows": 0, "alert_jobs": 0, "alert_steps": 0, "gates_graded": 0}

    for path in sorted(wf_dir.glob("*.yml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.name}: YAML DOES NOT PARSE: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        stats["workflows"] += 1

        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
            alert_idx = [i for i, s in enumerate(steps) if is_alert(s)]
            if not alert_idx:
                continue
            where = f"{path.name}/{job_name}"
            stats["alert_jobs"] += 1
            stats["alert_steps"] += len(alert_idx)

            # ── RULE A ────────────────────────────────────────────────────
            for i in range(alert_idx[0] + 1, len(steps)):
                step = steps[i]
                gate = gate_of(step)
                name = step.get("name", step.get("uses", "?"))
                stats["gates_graded"] += 1
                try:
                    contaminated = depends_on_job_status(gate)
                except ValueError as exc:
                    failures.append(f"RULE A {where} step {i} ({name}): {exc}")
                    continue
                if contaminated:
                    failures.append(
                        f"RULE A {where} step {i} ({name}): its gate depends on the "
                        f"JOB status after an alert step ran, so a Slack outage can "
                        f"flip it. gate = {gate!r}. Fix: gate on the specific step "
                        f"outcome it actually means (steps.<id>.outcome), and lead "
                        f"with always()."
                    )

            # ── RULE B ────────────────────────────────────────────────────
            for i in alert_idx:
                step = steps[i]
                blob = str(step.get("if") or "") + " " + " ".join(
                    str(v) for v in (step.get("with") or {}).values()
                )
                for sid in sorted(set(STEP_REF.findall(blob))):
                    producer = next(
                        (s for s in steps if str(s.get("id") or "") == sid), None
                    )
                    if producer is None or not substantial(producer):
                        continue
                    if has_fallback(steps, sid):
                        continue
                    failures.append(
                        f"RULE B {where} step {i} ({step.get('name', '?')}): depends on "
                        f"`steps.{sid}` but the job has NO fallback alert gated on "
                        f"`always() && steps.{sid}.outcome == 'failure'`. If step "
                        f"`{sid}` dies half way this alert never fires and the finding "
                        f"is lost in silence."
                    )
    return failures, stats


def report(failures: list[str], stats: dict) -> int:
    print(f"workflows parsed  : {stats['workflows']}")
    print(f"jobs with alerts  : {stats['alert_jobs']}")
    print(f"alert steps       : {stats['alert_steps']}")
    print(f"post-alert gates  : {stats['gates_graded']}")
    print()
    if failures:
        print("FAILURES:")
        for f in sorted(set(failures)):
            print("  x", f)
        return 1
    print("OK - no alert step can flip a verdict, and every alert has a fallback")
    return 0


# ── the drill entrance: mutates a COPY, never the repo ──────────────────────
MUTATIONS = [
    (
        "RULE A: a bare failure() gate placed after an alert step",
        "uptime.yml",
        lambda t: t.replace(
            "        if: always() && steps.raise.outputs.announce == 'recovered'",
            "        if: always() && failure()",
            1,
        ),
    ),
    (
        "RULE A: a post-alert step left on GitHub's implicit success() gate",
        "uptime.yml",
        lambda t: t.replace(
            "        if: always() && steps.raise.outcome == 'failure'\n",
            "        if: steps.raise.outcome == 'failure'\n",
            1,
        ),
    ),
    (
        "RULE B: the fallback alert for the producing step is deleted",
        "uptime.yml",
        lambda t: re.sub(
            r"      - name: Slack — the alarm path itself broke\n(?:.*\n)*?"
            r"          slack_bot_token: \$\{\{ secrets\.SLACK_BOT_TOKEN \}\}\n",
            "",
            t,
            count=1,
        ),
    ),
]


def drill() -> int:
    baseline, _ = check()
    if baseline:
        print("Refusing to drill: the workflows are ALREADY failing the check.\n")
        return report(baseline, _)

    before = _fingerprint(WF)
    worst = 0
    with tempfile.TemporaryDirectory(prefix="alert-paths-drill-") as tmp:
        sandbox = Path(tmp) / "workflows"
        shutil.copytree(WF, sandbox)
        print(f"drilling on a COPY at {sandbox} - the real tree is never written\n")
        for label, filename, mutate in MUTATIONS:
            target = sandbox / filename
            original = target.read_text(encoding="utf-8")
            mutated = mutate(original)
            print(f"=== DRILL: {label} ===")
            if mutated == original:
                print("  ! mutation did not apply - the anchor text moved. FIX THE DRILL.")
                worst = 2
                print()
                continue
            target.write_text(mutated, encoding="utf-8")
            failures, _stats = check(sandbox)
            print(
                f"  checker result = "
                f"{'RED (good)' if failures else 'GREEN - THE GUARD IS BLIND'}"
            )
            for f in failures:
                print("    x", f)
            if not failures:
                worst = 2
            target.write_text(original, encoding="utf-8")
            print()

    after = _fingerprint(WF)
    print("=== real tree untouched ===")
    print(f"  .github/workflows fingerprint before drill : {before}")
    print(f"  .github/workflows fingerprint after  drill : {after}")
    if before != after:
        print("  x THE DRILL WROTE TO THE REAL TREE. That is the bug it exists to avoid.")
        worst = 2
    else:
        print("  ok - byte-identical")
    print()

    failures, stats = check()
    return max(worst, report(failures, stats))


def _fingerprint(directory: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--drill",
        action="store_true",
        help="break a COPY of the workflows on purpose and prove this checker goes red",
    )
    ap.add_argument(
        "--from",
        dest="from_dir",
        metavar="DIR",
        help="check a directory of workflow files other than .github/workflows "
        "(e.g. an old revision exported with `git archive`) — how you show a "
        "before/after without touching the tree",
    )
    args = ap.parse_args()
    if args.drill:
        sys.exit(drill())
    sys.exit(report(*check(Path(args.from_dir) if args.from_dir else WF)))
