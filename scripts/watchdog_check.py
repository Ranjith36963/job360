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
    # THE PRODUCT-QUALITY DETECTORS. Added 2026-08-03 after an audit found them
    # UNWATCHED: product-health and user-journey are two of the three loops that
    # look at production data, and either could have stopped running weeks ago
    # without anything noticing. A watchdog that watches the infrastructure loops
    # but not the loops that actually check the product is watching the wrong
    # things — the loops most worth having are the ones nobody would miss.
    "product-health.yml": (36, "daily 08:30"),
    "user-journey.yml": (36, "daily 09:00"),
    "data-invariants.yml": (14, "every 6h"),
    "security.yml": (9 * 24, "weekly Mon 04:00"),
    "codeql.yml": (9 * 24, "weekly Mon 05:00"),
    # ci.yml and repair.yml are event-triggered only — silence is normal, so
    # they are deliberately NOT watched here. Watching them would produce a
    # permanent false alarm, and a permanent alarm is how a loop dies.
}


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
