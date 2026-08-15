#!/usr/bin/env python3
"""DRILL: prove a failing Slack step cannot change what the repair loops REPORT.

This is the behavioural half of scripts/check_alert_paths.py. That checker
proves a structural property over every workflow; this one answers the single
concrete question the finding was about, out loud, in the two worlds:

    WORLD 1  the repair worked: it opened a PR / pushed a fix, and every step
             including the Slack alert succeeded.
    WORLD 2  IDENTICAL work, but the mid-job Slack step failed (rate-limited,
             revoked token, bad channel). Nothing about the repair changed.

The steps that TELL A HUMAN what happened must read the same in both worlds. If
they do not, the alerting path is deciding the verdict of the work it is only
supposed to be describing.

    python scripts/drill_slack_cannot_lie.py                  # current tree
    python scripts/drill_slack_cannot_lie.py --from DIR       # an older export

Exit 0 = the reported outcome is identical in both worlds.
Exit 1 = a Slack outage changes what the loop says it did.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from check_alert_paths import WF, gate_of  # same parsing, no second opinion

# The steps whose whole job is to tell a human what the loop did. If a Slack
# outage flips one of these, the loop lies.
REPORTING_STEPS = {
    "repair.yml": ["Report back when it could not fix it", "could not fix it"],
    "pr-repair.yml": ["Report back when it could not fix it", "could not finish"],
}

# Two worlds, because "the alert must not turn this ON" is only half the test.
# A step that never fires at all would pass that half — and would be a far worse
# bug. So the same gates are also checked in a world where the repair GENUINELY
# failed: there they MUST fire, Slack working or not.
WORLD_SHIPPED = {
    "name": "the repair SUCCEEDED — a PR was opened / a fix was pushed",
    "must_fire": False,
    "steps.token.outputs.have": "true",
    "steps.ship.outcome": "success",
    "steps.cage.outcome": "success",
    "steps.cage.outputs.changed": "backend/src/services/skill_matcher.py",
    "steps.verify.outcome": "success",
    "steps.verdict.outcome": "success",
    "steps.verdict.outputs.capped": "",
    "steps.issue.outputs.num": "999",
}
WORLD_FAILED = {
    "name": "the repair GENUINELY FAILED — independent verification stayed red",
    "must_fire": True,
    "steps.token.outputs.have": "true",
    "steps.ship.outcome": "skipped",  # gated off by the red verify above it
    "steps.cage.outcome": "success",
    "steps.cage.outputs.changed": "backend/src/services/skill_matcher.py",
    "steps.verify.outcome": "failure",
    "steps.verdict.outcome": "skipped",
    "steps.verdict.outputs.capped": "",
    "steps.issue.outputs.num": "999",
}
WORLDS = [WORLD_SHIPPED, WORLD_FAILED]
WORLD: dict = WORLD_SHIPPED

REF = re.compile(r"steps\.[A-Za-z_][\w-]*\.(?:outputs\.[\w-]+|outcome|conclusion)")


def evaluate(gate: str, slack_broke: bool) -> bool:
    expr = REF.sub(lambda m: repr(WORLD.get(m.group(0), "")), gate)
    expr = re.sub(r"\balways\s*\(\s*\)", "True", expr)
    expr = re.sub(r"\bcancelled\s*\(\s*\)", "False", expr)
    expr = re.sub(r"\bfailure\s*\(\s*\)", repr(slack_broke), expr)
    expr = re.sub(r"\bsuccess\s*\(\s*\)", repr(not slack_broke), expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307


def main(wf_dir: Path) -> int:
    global WORLD
    bad = 0
    for world in WORLDS:
        WORLD = world
        print("=" * 72)
        print(f"WORLD: {world['name']}")
        for k, v in world.items():
            if k.startswith("steps."):
                print(f"   {k:38} = {v!r}")
        print(f"   the reporting steps MUST fire here : {world['must_fire']}")
        print()
        for filename, needles in REPORTING_STEPS.items():
            doc = yaml.safe_load((wf_dir / filename).read_text(encoding="utf-8"))
            for job_name, job in (doc.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    name = str(step.get("name") or "")
                    if not any(n.lower() in name.lower() for n in needles):
                        continue
                    gate = gate_of(step)
                    ok_world = evaluate(gate, slack_broke=False)
                    bad_world = evaluate(gate, slack_broke=True)
                    print(f"{filename}/{job_name}: {name}")
                    print(f"   gate                        : {gate}")
                    print(f"   slack OK  -> step fires?    : {ok_world}")
                    print(f"   slack BROKE -> step fires?  : {bad_world}")
                    if ok_world != bad_world:
                        bad += 1
                        print("   => CHANGED — IT LIES")
                        print(
                            "      A Slack outage alone flips what this loop reports "
                            "about work it already finished."
                        )
                    elif ok_world != world["must_fire"]:
                        bad += 1
                        print(
                            f"   => WRONG VERDICT: fires={ok_world}, "
                            f"should be {world['must_fire']} in this world"
                        )
                    else:
                        print("   => SAME, and correct for this world (good)")
                    print()
    if bad:
        print(f"FAIL: {bad} reporting-step verdict(s) are wrong.")
        return 1
    print("OK: the loops report the same thing whether or not Slack answered,")
    print("    and they still speak up when the repair really did fail.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="from_dir", metavar="DIR")
    args = ap.parse_args()
    sys.exit(main(Path(args.from_dir) if args.from_dir else WF))
