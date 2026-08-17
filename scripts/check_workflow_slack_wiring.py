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
ways, and asserts the checker goes red for each.

THE DRILL MUTATES A COPY, NEVER THE REPO
----------------------------------------
The first version of this drill wrote the broken workflows into
`.github/workflows/` itself and restored them in a Python `finally`. `finally`
does not run on SIGKILL, on a CI runner timeout, on a power cut, and on Windows
not reliably on an interrupt either — so an interrupted drill left the repo
holding the exact defect this checker exists to catch, plus a `.drillbak` file.
Measured, not assumed: killed mid-window, `git status --porcelain
.github/workflows/` showed `M uptime.yml` + `?? uptime.yml.drillbak`, and this
checker then reported a real BASH SYNTAX ERROR in a file nobody had edited.
A drill that can damage the thing it protects is worse than no drill.

So the workflows are copied into a temp directory and the COPY is broken. There
is no restore step, because there is nothing to restore: an interrupt at any
instant leaves the repo byte-identical. `_fingerprint()` re-proves that at the
end of every drill rather than trusting it, and a leftover `.drillbak` from an
old interrupted run is detected on startup and refused.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WF = REPO / ".github" / "workflows"

# ${{ ... }} is GitHub templating, not bash. Swap it for a bare word so `bash -n`
# judges the SHELL syntax rather than choking on the expression.
EXPR = re.compile(r"\$\{\{[^}]*\}\}")

LOCAL_ACTION_PREFIX = "./.github/actions/"
SLACK_ACTION = "./.github/actions/slack"


def check(wf_dir: Path = WF) -> tuple[list[str], dict]:
    """Return (failures, stats). Empty failures == wiring is intact.

    `wf_dir` exists so --drill can point this at a throwaway COPY of the
    workflows instead of mutating the real ones.
    """
    failures: list[str] = []
    stats = {"workflows": 0, "bash_blocks": 0, "slack_steps": 0, "channels": {}}

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
                    # timeout is not optional here. This spawns one `bash -n`
                    # per run: block -- 152 of them in this repo today -- and a
                    # single hung child would hang the whole CI job with no
                    # output, which reads exactly like a passing silent check.
                    # (Measured: ~150s total on Windows, seconds on ubuntu. The
                    # spawn cost is the slowness; the missing timeout was the
                    # risk.)
                    try:
                        proc = subprocess.run(
                            ["bash", "-n"],
                            input=EXPR.sub("GHEXPR", run).encode("utf-8"),
                            capture_output=True,
                            timeout=60,
                        )
                    except subprocess.TimeoutExpired:
                        failures.append(
                            f"{where} step {i} ({step.get('name', '?')}): "
                            f"`bash -n` did not return within 60s - refusing to "
                            f"report this block as clean"
                        )
                        continue
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


def _fingerprint(directory: Path) -> str:
    """Content hash of a directory. Used to PROVE the real tree was untouched."""
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stale_backups() -> list[Path]:
    """Leftovers from a drill that was interrupted BEFORE this file was fixed."""
    return sorted(WF.glob("*.drillbak"))


def drill() -> int:
    # A leftover .drillbak means an older drill was killed mid-mutation and the
    # workflow file next to it may still be broken. Refuse to run on top of that
    # — a drill starting from a corrupt tree measures nothing.
    stale = stale_backups()
    if stale:
        print("Refusing to drill: leftover backups from an interrupted OLD drill:")
        for p in stale:
            print(f"  {p}")
        print()
        print("Restore and remove them first, e.g.:")
        for p in stale:
            print(f"  git checkout -- {p.with_suffix('').relative_to(REPO)} && rm {p}")
        return 2

    baseline, _ = check()
    if baseline:
        print("Refusing to drill: the workflows are ALREADY failing the check.")
        return report(baseline, _)

    before = _fingerprint(WF)
    worst = 0
    # THE WHOLE POINT: break a COPY. No try/finally, because there is nothing to
    # undo — an interrupt at any instant leaves .github/workflows byte-identical.
    with tempfile.TemporaryDirectory(prefix="slack-wiring-drill-") as tmp:
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
            target.write_text(original, encoding="utf-8")  # reset the COPY only
            print()

    after = _fingerprint(WF)
    print("=== real tree untouched ===")
    print(f"  .github/workflows fingerprint before drill : {before}")
    print(f"  .github/workflows fingerprint after  drill : {after}")
    if before != after:
        print("  x THE DRILL WROTE TO THE REAL TREE. That is the bug it exists to avoid.")
        worst = 2
    elif stale_backups():
        print("  x the drill left a .drillbak behind")
        worst = 2
    else:
        print("  ok - byte-identical, and no .drillbak left behind")
    print()

    failures, stats = check()
    return max(worst, report(failures, stats))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--drill",
        action="store_true",
        help="break a COPY of the workflows on purpose and prove this checker "
        "goes red (the real tree is never written)",
    )
    args = ap.parse_args()
    if args.drill:
        sys.exit(drill())

    # Even on a plain check: a `.drillbak` next to the workflows is proof that a
    # drill was interrupted, and the file it backs up may still hold the injected
    # defect. Deliberately NOT gitignored — it should be loud in `git status` —
    # and refused here so nobody reads a PASS off a half-broken tree.
    stale = stale_backups()
    if stale:
        print("REFUSING TO RUN - leftover drill backups found:")
        for p in stale:
            print(f"  {p}")
        print()
        print("An interrupted drill may have left the real workflow broken. Restore:")
        for p in stale:
            print(f"  git checkout -- {p.with_suffix('').relative_to(REPO)} && rm {p}")
        sys.exit(2)

    sys.exit(report(*check()))
