#!/usr/bin/env python3
"""DEAD-MAN'S SWITCH — who watches the watchers? (issue #144)

A workflow that STOPS RUNNING looks exactly like a workflow that passes: no red
X, no failure email, nothing. GitHub also auto-disables scheduled workflows
after ~60 days of repository inactivity — silently. So the whole monitoring
layer can switch itself off and every dashboard stays green.

This is the same blind spot as scripts/absence_check.py, one level up: that one
asks "did the PRODUCT stop?", this one asks "did the WATCHERS stop?". Absence of
signal is the signal.

Each scheduled workflow declares how long it may go without reporting a run.
Anything overdue — or that has never run at all — fails this check.

TWO QUESTIONS SINCE 2026-09-11 (harness simplification slice 4), NOT ONE:

  1. STOPPED  — did the watcher stop RUNNING?          (exit 1, the original)
  2. RED      — is the watcher running but FAILING?    (reported, never exit 1)

The second exists because two of the three production watchers had been
silent-red for a month (synthetic-live since 2026-08-09, external-health since
2026-08-06) with every dashboard green: each opened its own issue, the issue
went to triage, triage emailed, and nothing reached the channel the owner
actually reads. This file already looks at every scheduled watcher once a day,
so it is the one place that can say "these N are red, since when, last green
on" in ONE message — and absence.yml turns that into a Slack transition
(green->red announces, red->red is silent, red->green announces).

RED is deliberately NOT an exit-1: a watcher that is red because the OWNER has
not set a secret would otherwise make THIS watcher red every day, and a
permanently red watchdog is a dead watchdog. The stopped/red split keeps the
exit code meaning "the harness itself is broken" and nothing else.

No LLM. Read-only (gh run list). Exit 0 = all watchers alive, 1 = one or more
stopped, 2 = the checker itself broke (fail LOUD).

Run: python scripts/watchdog_check.py            (needs gh authed; GH_TOKEN in CI)
     python scripts/watchdog_check.py --json out.json   (machine-readable verdict)
     python scripts/watchdog_check.py --drill     (the pure classifier can go red)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# workflow file -> (max hours between runs, why that number)
# Generous by ~1.5x the real cadence: this must catch "stopped", not "slipped".
# GitHub's own cron is best-effort and can drift by tens of minutes.
EXPECTED: dict[str, tuple[float, str]] = {
    "uptime.yml": (3, "every 10 min"),
    # Gained its cron in #525 (2026-09-08) and drifted out of this roster until
    # slice 4 (2026-09-11) ran the roster check — the exact blind spot below.
    "auto-merge.yml": (2, "every 20 min"),
    "synthetic-live.yml": (14, "every 6h"),
    "db-backup.yml": (36, "daily 02:17"),
    "ci-offline.yml": (36, "daily 06:00"),
    "finding-watch.yml": (2, "every 30 min"),
    "absence.yml": (36, "daily 08:00"),
    "security-watch.yml": (36, "daily 08:20"),
    "external-health.yml": (36, "daily 07:10"),
    "dependabot-auto.yml": (36, "daily 09:30"),
    "pr-shepherd.yml": (36, "daily 09:45"),
    "security.yml": (9 * 24, "weekly Mon 04:00"),
    "codeql.yml": (9 * 24, "weekly Mon 05:00"),
    "revert-main.yml": (32 * 24, "monthly, 1st 06:00"),
    # ci.yml is event-triggered only — silence is normal, so it is
    # deliberately NOT watched here. Watching it would produce a permanent
    # false alarm, and a permanent alarm is how a loop dies.
}

# Event-triggered or PR-only workflows: silence is CORRECT for these, so they
# are excluded from the roster-drift check below rather than watched.
# doc-sync.yml moved here 2026-09-09: it dropped its daily cron for a
# pull_request-only PR gate (owner decision — drift is fixed in the PR that
# caused it, not by a nightly bot), so a quiet weekend with no PRs is normal
# silence, not a stopped watcher. Watching it here would fire "STOPPED" every
# quiet weekend — a permanent false alarm.
# branch-reaper.yml moved here 2026-09-10 (slice 2): it dropped its weekly
# cron for a push-to-main trigger, so it runs exactly when a merge lands and
# is silent otherwise. Its drill still fires on every PR via ci.yml.
NOT_SCHEDULED: set[str] = {
    "ci.yml", "pr-repair.yml", "triage.yml", "doc-sync.yml", "branch-reaper.yml",
}

# Not scheduled, so never STOPPED — but its conclusion on main IS the health of
# production's gate, so it joins the RED question. `ci.yml` red on main means
# the last thing that shipped did not pass its own tests.
RED_ONLY: dict[str, str] = {"ci.yml": "push to main"}

# How many consecutive failing runs before a watcher counts as RED. One red run
# is a flake (measured 2026-08-17: Security scanning flapped 1.9 failures/week);
# two in a row is a condition. The streak is counted over COMPLETED runs only —
# a cancelled or in-progress run says nothing about the condition.
RED_STREAK = 2
RUNS_TO_FETCH = 8


@dataclass
class Verdict:
    """The pure result for one watcher. Everything absence.yml needs is here."""
    workflow: str
    cadence: str
    last_run_age_h: float | None  # None = never ran
    stopped: bool
    red: bool
    streak: int                    # consecutive failures at the head
    last_green: str | None         # ISO date of the newest success in the window
    note: str = ""
    conclusions: list[str] = field(default_factory=list)


def assess(runs: list[dict], now: datetime, max_h: float | None, workflow: str,
           cadence: str) -> Verdict:
    """Classify one watcher from its recent runs. PURE — this is what the drill
    breaks. `runs` is newest-first, each with createdAt/status/conclusion.

    max_h None means the STOPPED question is not asked (RED_ONLY workflows).
    """
    if not runs:
        return Verdict(workflow, cadence, None, stopped=max_h is not None, red=False,
                       streak=0, last_green=None, note="never ran")
    newest = datetime.fromisoformat(runs[0]["createdAt"].replace("Z", "+00:00"))
    age_h = (now - newest).total_seconds() / 3600
    stopped = max_h is not None and age_h > max_h

    completed = [r for r in runs if r.get("status") == "completed"
                 and r.get("conclusion") in ("success", "failure")]
    conclusions = [r["conclusion"] for r in completed]
    streak = 0
    for c in conclusions:
        if c != "failure":
            break
        streak += 1
    last_green = next((r["createdAt"][:10] for r in completed if r["conclusion"] == "success"), None)
    red = streak >= RED_STREAK
    note = ""
    if red and last_green is None:
        note = f"no green run in the last {len(completed)} completed"
    return Verdict(workflow, cadence, age_h, stopped, red, streak, last_green, note, conclusions)


def roster_drift(workflow_dir: str = ".github/workflows") -> list[str]:
    """Every scheduled workflow on disk must appear in EXPECTED.

    THE ROSTER IS THE WEAK POINT. EXPECTED is hand-written, so the failure mode
    is not a wrong number — it is a loop that was never added at all, which is
    invisible precisely because an unwatched watcher produces no signal. Six
    workflows had drifted out of it before this check existed.

    Returns human-readable drift lines; empty list means the roster is complete.
    """
    import pathlib
    import re

    drift: list[str] = []
    d = pathlib.Path(workflow_dir)
    if not d.is_dir():
        return [f"cannot read {workflow_dir} — roster drift is UNVERIFIED"]
    on_disk = set()
    for f in sorted(d.glob("*.yml")):
        text = f.read_text(encoding="utf-8", errors="replace")
        # A workflow is "scheduled" if it declares a cron. Deliberately a dumb
        # regex, not a YAML parse: this must keep working on a malformed file.
        if re.search(r"^\s*-\s*cron:", text, re.M):
            on_disk.add(f.name)
    for wf in sorted(on_disk - set(EXPECTED) - NOT_SCHEDULED):
        drift.append(f"`{wf}` runs on a schedule but is NOT watched — add it to EXPECTED")
    for wf in sorted(set(EXPECTED) - on_disk):
        drift.append(f"`{wf}` is watched but no longer exists on disk — remove it from EXPECTED")
    return drift


# Sentinel: the workflow file is not on the default branch yet (e.g. it only
# exists in an open PR). That is NOT a stopped watcher and must not raise.
NOT_DEPLOYED = "not-deployed"


def recent_runs(workflow: str, branch: str | None = None) -> list[dict] | str:
    """Newest-first recent runs, [] if never run, NOT_DEPLOYED if absent.

    A workflow living only in an open PR would otherwise crash the checker —
    found by running this for the first time against absence.yml (PR #147).
    """
    cmd = ["gh", "run", "list", "--workflow", workflow, "--limit", str(RUNS_TO_FETCH),
           "--json", "createdAt,status,conclusion"]
    if branch:
        cmd += ["--branch", branch]
    out = subprocess.run(
        cmd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        # A DETECTOR THAT HANGS REPORTS NOTHING, AND NOTHING READS AS FINE.
        # Both sibling detectors already bound this (checker_scorecard.py uses
        # timeout=120, chain_check.py uses timeout=60); this one and
        # doc_clutter_check.sh did not, so a stalled `gh` made them look slow
        # rather than broken. TimeoutExpired is deliberately NOT caught: the
        # watchdog's whole job is to be noticed when it cannot answer.
        timeout=60,
    )
    if out.returncode != 0:
        err = out.stderr.strip()
        if "not found" in err.lower() or "HTTP 404" in err:
            return NOT_DEPLOYED
        raise RuntimeError(f"gh failed for {workflow}: {err[:200]}")
    return json.loads(out.stdout or "[]")


def _drill() -> int:
    """Break the pure classifier on purpose. Needs no gh and no network."""
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    def run(hours_ago: float, conclusion: str | None, status: str = "completed") -> dict:
        ts = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        from datetime import timedelta
        return {"createdAt": (ts - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": status, "conclusion": conclusion}

    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"   got={got!r} want={want!r}"))

    print("watchdog_check.py --drill")
    # NEGATIVE CONTROLS FIRST: it must be able to say "fine".
    v = assess([run(1, "success"), run(7, "success")], now, 14, "x.yml", "6h")
    check("fresh + green is neither stopped nor red", (v.stopped, v.red), (False, False))

    # STOPPED — the original question.
    v = assess([run(40, "success")], now, 14, "x.yml", "6h")
    check("40h since a 6h watcher ran is STOPPED", v.stopped, True)
    check("...and stopped is not red", v.red, False)
    v = assess([], now, 14, "x.yml", "6h")
    check("never ran is STOPPED", v.stopped, True)
    v = assess([], now, None, "ci.yml", "push")
    check("a RED_ONLY watcher that never ran is NOT stopped", v.stopped, False)

    # RED — the new question.
    v = assess([run(1, "failure"), run(7, "failure"), run(13, "success")], now, 14, "x.yml", "6h")
    check("two failures in a row is RED", v.red, True)
    check("...with streak 2", v.streak, 2)
    check("...and it remembers the last green day", v.last_green, "2026-09-10")
    v = assess([run(1, "failure"), run(7, "success")], now, 14, "x.yml", "6h")
    check("ONE failure is a flake, not red", v.red, False)
    v = assess([run(1, "failure"), run(4, None, "in_progress"), run(7, "failure")], now, 14, "x.yml", "6h")
    check("an in-progress run does not break the streak", v.red, True)
    v = assess([run(1, "failure"), run(4, "cancelled"), run(7, "failure")], now, 14, "x.yml", "6h")
    check("a cancelled run does not break the streak", v.red, True)
    v = assess([run(1, "failure"), run(4, "success"), run(7, "failure")], now, 14, "x.yml", "6h")
    check("a success in between DOES break the streak", v.red, False)
    v = assess([run(1, "failure")] * 8, now, 14, "x.yml", "6h")
    check("all-red window says so in the note", "no green run" in v.note, True)
    v = assess([run(1, "failure"), run(2, "failure")], now, None, "ci.yml", "push")
    check("RED_ONLY watcher can be red", v.red, True)

    # Both at once: a watcher can be stopped AND have been red before it stopped.
    v = assess([run(40, "failure"), run(46, "failure")], now, 14, "x.yml", "6h")
    check("stopped and red are independent", (v.stopped, v.red), (True, True))

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    if passed != len(cases):
        print("DRILL FAILED — the watchdog classifier no longer behaves as documented.")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Are the watchers still running, and are they green?")
    ap.add_argument("--json", metavar="FILE", help="write the machine-readable verdict here")
    ap.add_argument("--drill", action="store_true", help="prove the classifier can go red")
    args = ap.parse_args(argv)
    if args.drill:
        return _drill()

    now = datetime.now(timezone.utc)
    stopped: list[str] = []
    red: list[Verdict] = []
    rows: list[tuple[str, str, str, str]] = []

    targets: list[tuple[str, float | None, str, str | None]] = [
        (wf, max_h, cadence, None) for wf, (max_h, cadence) in sorted(EXPECTED.items())
    ] + [(wf, None, why, "main") for wf, why in sorted(RED_ONLY.items())]

    for wf, max_h, cadence, branch in targets:
        runs = recent_runs(wf, branch)
        if runs == NOT_DEPLOYED:
            # Declared here but not yet on the default branch — informational.
            rows.append((wf, "not on main yet", "—", "—"))
            continue
        v = assess(runs, now, max_h, wf, cadence)  # type: ignore[arg-type]
        if v.last_run_age_h is None:
            age = "never"
        else:
            age = f"{v.last_run_age_h:.1f}h ago"
        health = "ok"
        if v.red:
            health = f"**RED** x{v.streak}" + (f" (last green {v.last_green})" if v.last_green else " (no green in window)")
            red.append(v)
        verdict = "**STOPPED**" if v.stopped else ("—" if max_h is None else "ok")
        rows.append((wf, age, verdict, health))
        if v.stopped:
            if v.last_run_age_h is None:
                stopped.append(f"`{wf}` has NEVER run ({cadence})")
            else:
                stopped.append(
                    f"`{wf}` last ran {v.last_run_age_h:.1f}h ago — expected {cadence} "
                    f"(limit {max_h:.0f}h). It is not running."
                )

    print("# Watchdog — are the watchers still running, and are they green?\n")
    print("| workflow | last run | running? | passing? |")
    print("|---|---|---|---|")
    for wf, age, verdict, health in rows:
        print(f"| `{wf}` | {age} | {verdict} | {health} |")

    # The roster itself is a watcher, and nothing was watching IT.
    drift = roster_drift()
    if drift:
        print("\n## The watchdog's own roster has drifted\n")
        for d in drift:
            print(f"- {d}")
        print(
            "\nA scheduled loop missing from EXPECTED is completely unwatched: "
            "it can stop for months and every dashboard stays green. This is the "
            "same blind spot the watchdog exists to close, one level up again."
        )
        stopped.extend(drift)

    if red:
        print("\n## Watchers that are running but RED\n")
        for v in red:
            since = f"last green {v.last_green}" if v.last_green else v.note
            print(f"- `{v.workflow}` — {v.streak} failing run(s) in a row, {since}")
        print(
            "\nEach of these has its own issue and its own diagnosis; this is the "
            "roll-up so the owner sees them in ONE place. A watcher that is red "
            "for a missing secret stays red until the secret exists — that is an "
            "owner action, not a code fix."
        )

    if stopped:
        print("\n## Watchers have stopped\n")
        for s in stopped:
            print(f"- {s}")
        print(
            "\nA workflow that stops running looks identical to one that passes. "
            "GitHub also auto-disables schedules after ~60 days of repo inactivity. "
            "Re-enable under Actions, or fix the schedule."
        )

    if not stopped:
        print(f"\nAll {len(rows)} watchers reported within their window"
              + (f"; {len(red)} of them are red." if red else " and are green."))

    if args.json:
        payload = {
            "stopped": bool(stopped),
            "stopped_list": stopped,
            "red_count": len(red),
            "red_list": [v.workflow for v in red],
            "red": [
                {"workflow": v.workflow, "streak": v.streak, "last_green": v.last_green,
                 "note": v.note} for v in red
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    return 1 if stopped else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — must fail LOUD, never silently green
        print(f"watchdog_check crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
