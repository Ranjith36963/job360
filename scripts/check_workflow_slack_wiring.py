#!/usr/bin/env python3
"""Guard the harness's VOICE: every Slack alert path must still be wired.

WHY THIS EXISTS
---------------
`.github/actions/slack` is the only way the loops reach a human. Before it, the
harness spoke solely on GitHub and 7 `triage:needs-human` issues sat open — 6 of
them with ZERO human comments — for 3-8 days. So the wiring itself is now
load-bearing, and load-bearing things get a machine check.

The three failures this catches are all INVISIBLE to a careful read and to YAML
linting, because each produces a perfectly valid workflow file:

  1. A stray `fi`/`done`/quote left in a `run:` block by an edit. Valid YAML —
     it is just text inside a block scalar — and it explodes only at runtime, in
     the alarm path, during a real incident. (Made exactly this mistake in
     doc-sync.yml while wiring Slack; only `bash -n` found it.)
  2. A `uses: ./.github/actions/slack` step in a job that never runs
     actions/checkout. A local composite action is just files in the repo, so
     without a checkout it cannot resolve and the ALERT step is what errors.
  3. A Slack step that forgets `slack_bot_token`. The action fails loud by
     design, but loud-at-3am is worse than caught-at-commit.

USAGE
  python scripts/check_workflow_slack_wiring.py           # check
  python scripts/check_workflow_slack_wiring.py --drill   # prove it can go RED

--drill is not decoration. Six guards shipped in this repo in one week that
could not fire; a checker that has only ever printed PASS is indistinguishable
from one that always prints PASS. --drill breaks the workflows on purpose, three
ways, asserts the checker goes red for each, and restores the originals.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WF = REPO / ".github" / "workflows"

# ${{ ... }} is GitHub templating, not bash. Swap it for a bare word so `bash -n`
# judges the SHELL syntax rather than choking on the expression.
EXPR = re.compile(r"\$\{\{[^}]*\}\}")

LOCAL_ACTION_PREFIX = "./.github/actions/"
SLACK_ACTION = "./.github/actions/slack"


def check() -> tuple[list[str], dict]:
    """Return (failures, stats). Empty failures == wiring is intact."""
    failures: list[str] = []
    stats = {"workflows": 0, "bash_blocks": 0, "slack_steps": 0, "channels": {}}

    for path in sorted(WF.glob("*.yml")):
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
            steps = job.get("steps") or []
            has_checkout = any(
                isinstance(s.get("uses"), str)
                and s["uses"].startswith("actions/checkout")
                for s in steps
                if isinstance(s, dict)
            )
            where = f"{path.name}/{job_name}"

            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                if isinstance(uses, str) and uses.startswith(LOCAL_ACTION_PREFIX):
                    if not has_checkout:
                        failures.append(
                            f"{where} step {i}: uses local action {uses} but the job "
                            f"never runs actions/checkout - it cannot resolve at runtime"
                        )
                    if uses.rstrip("/") == SLACK_ACTION:
                        stats["slack_steps"] += 1
                        with_ = step.get("with") or {}
                        channel = str(with_.get("channel", "<missing>"))
                        stats["channels"][channel] = (
                            stats["channels"].get(channel, 0) + 1
                        )
                        if "SLACK_BOT_TOKEN" not in str(with_.get("slack_bot_token", "")):
                            failures.append(
                                f"{where} step {i}: slack step does not pass "
                                f"slack_bot_token: secrets.SLACK_BOT_TOKEN"
                            )
                        if not str(with_.get("title", "")).strip():
                            failures.append(
                                f"{where} step {i}: slack step has no title - a "
                                f"message with no headline is noise"
                            )

                run = step.get("run")
                if isinstance(run, str) and run.strip():
                    shell = step.get("shell") or (
                        (job.get("defaults") or {}).get("run") or {}
                    ).get("shell", "bash")
                    if shell not in ("bash", "sh"):
                        continue
                    stats["bash_blocks"] += 1
                    proc = subprocess.run(
                        ["bash", "-n"],
                        input=EXPR.sub("GHEXPR", run).encode("utf-8"),
                        capture_output=True,
                    )
                    if proc.returncode != 0:
                        err = proc.stderr.decode("utf-8", "replace").strip()
                        failures.append(
                            f"{where} step {i} ({step.get('name', '?')}): "
                            f"BASH SYNTAX ERROR: {err}"
                        )
    return failures, stats


def report(failures: list[str], stats: dict) -> int:
    print(f"workflows parsed  : {stats['workflows']}")
    print(f"bash blocks -n'd  : {stats['bash_blocks']}")
    print(f"slack alert steps : {stats['slack_steps']}")
    if stats["channels"]:
        print("routing:")
        for channel, n in sorted(stats["channels"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {channel}")
    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  x", f)
        return 1
    print("OK - every Slack alert path is wired and every run: block parses")
    return 0


# ── the drill entrance ──────────────────────────────────────────────────────
MUTATIONS = [
    (
        "stray `fi` in a run: block (valid YAML, broken bash)",
        "uptime.yml",
        lambda t: t.replace(
            '          title="Loop RED: uptime probe is failing"',
            '          title="Loop RED: uptime probe is failing"\n          fi',
            1,
        ),
    ),
    (
        "slack step in a job with no actions/checkout",
        "uptime.yml",
        lambda t: t.replace("      - uses: actions/checkout@v7\n", "", 1),
    ),
    (
        "slack step missing slack_bot_token",
        "uptime.yml",
        lambda t: t.replace(
            "          slack_bot_token: ${{ secrets.SLACK_BOT_TOKEN }}\n", "", 1
        ),
    ),
]


def drill() -> int:
    baseline, _ = check()
    if baseline:
        print("Refusing to drill: the workflows are ALREADY failing the check.")
        return report(baseline, _)

    worst = 0
    for label, filename, mutate in MUTATIONS:
        target = WF / filename
        backup = target.with_suffix(".yml.drillbak")
        shutil.copy(target, backup)
        try:
            original = target.read_text(encoding="utf-8")
            mutated = mutate(original)
            if mutated == original:
                print(f"=== DRILL: {label} ===")
                print("  ! mutation did not apply - the anchor text moved. FIX THE DRILL.")
                worst = 2
                continue
            target.write_text(mutated, encoding="utf-8")
            failures, _stats = check()
            print(f"=== DRILL: {label} ===")
            print(
                f"  checker result = {'RED (good)' if failures else 'GREEN - THE GUARD IS BLIND'}"
            )
            for f in failures:
                print("    x", f)
            if not failures:
                worst = 2
        finally:
            shutil.copy(backup, target)
            backup.unlink()
        print()

    failures, stats = check()
    print("=== restored ===")
    rc = report(failures, stats)
    return max(worst, rc)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--drill",
        action="store_true",
        help="break the workflows on purpose and prove this checker goes red",
    )
    args = ap.parse_args()
    sys.exit(drill() if args.drill else report(*check()))
