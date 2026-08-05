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

No LLM. Read-only (gh run list). Exit 0 = all watchers alive, 1 = one or more
stopped, 2 = the checker itself broke (fail LOUD).

Run: python scripts/watchdog_check.py     (needs gh authed; GH_TOKEN in CI)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# workflow file -> (max hours between runs, why that number)
# Generous by ~1.5x the real cadence: this must catch "stopped", not "slipped".
# GitHub's own cron is best-effort and can drift by tens of minutes.
EXPECTED: dict[str, tuple[float, str]] = {
    "uptime.yml": (3, "every 10 min"),
    "synthetic-live.yml": (14, "every 6h"),
    "db-backup.yml": (36, "daily 02:17"),
    "live-e2e.yml": (36, "daily 03:00"),
    "ci-offline.yml": (36, "daily 06:00"),
    "doc-sync.yml": (36, "daily 06:30"),
    "absence.yml": (36, "daily 08:00"),
    # THE PRODUCT + QUEUE LOOPS. Added 2026-08-03 after an audit found them
    # UNWATCHED: every loop written after this file was created had been
    # silently omitted, because the roster is hand-maintained and nothing
    # checked it against the directory. That included the two loops that
    # actually look at production data (product-health, user-journey), both
    # loops that manage the PR queue, and the judge-of-the-judge.
    "journey.yml": (36, "daily 05:40"),
    "external-health.yml": (36, "daily 07:10"),
    "product-health.yml": (36, "daily 08:30"),
    "user-journey.yml": (36, "daily 09:00"),
    "data-invariants.yml": (14, "every 6h"),
    "dependabot-auto.yml": (36, "daily 09:30"),
    "pr-shepherd.yml": (36, "daily 09:45"),
    "checker-scorecard.yml": (9 * 24, "weekly Mon 10:00"),
    "security.yml": (9 * 24, "weekly Mon 04:00"),
    "codeql.yml": (9 * 24, "weekly Mon 05:00"),
    # ci.yml and repair.yml are event-triggered only — silence is normal, so
    # they are deliberately NOT watched here. Watching them would produce a
    # permanent false alarm, and a permanent alarm is how a loop dies.
}

# Event-triggered or PR-only workflows: silence is CORRECT for these, so they
# are excluded from the roster-drift check below rather than watched.
NOT_SCHEDULED: set[str] = {"ci.yml", "repair.yml", "pr-repair.yml", "triage.yml"}


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


def last_run_iso(workflow: str) -> str | None:
    """Most recent run timestamp, None if never run, NOT_DEPLOYED if absent.

    A workflow living only in an open PR would otherwise crash the checker —
    found by running this for the first time against absence.yml (PR #147).
    """
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", workflow, "--limit", "1",
         "--json", "createdAt"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        err = out.stderr.strip()
        if "not found" in err.lower() or "HTTP 404" in err:
            return NOT_DEPLOYED
        raise RuntimeError(f"gh failed for {workflow}: {err[:200]}")
    data = json.loads(out.stdout or "[]")
    return data[0]["createdAt"] if data else None


def main() -> int:
    now = datetime.now(timezone.utc)
    stopped: list[str] = []
    rows: list[tuple[str, str, str]] = []

    for wf, (max_h, cadence) in sorted(EXPECTED.items()):
        iso = last_run_iso(wf)
        if iso == NOT_DEPLOYED:
            # Declared here but not yet on the default branch — informational.
            rows.append((wf, "not on main yet", "—"))
            continue
        if iso is None:
            stopped.append(f"`{wf}` has NEVER run ({cadence})")
            rows.append((wf, "never", "**STOPPED**"))
            continue
        age_h = (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
        overdue = age_h > max_h
        rows.append((wf, f"{age_h:.1f}h ago", "**STOPPED**" if overdue else "ok"))
        if overdue:
            stopped.append(
                f"`{wf}` last ran {age_h:.1f}h ago — expected {cadence} "
                f"(limit {max_h:.0f}h). It is not running."
            )

    print("# Watchdog — are the watchers still running?\n")
    print("| workflow | last run | verdict |")
    print("|---|---|---|")
    for wf, age, verdict in rows:
        print(f"| `{wf}` | {age} | {verdict} |")

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

    if stopped:
        print("\n## Watchers have stopped\n")
        for s in stopped:
            print(f"- {s}")
        print(
            "\nA workflow that stops running looks identical to one that passes. "
            "GitHub also auto-disables schedules after ~60 days of repo inactivity. "
            "Re-enable under Actions, or fix the schedule."
        )
        return 1

    print(f"\nAll {len(rows)} scheduled watchers reported within their window.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — must fail LOUD, never silently green
        print(f"watchdog_check crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
