#!/usr/bin/env python3
"""deploy_settle.py - wait for THIS commit's deploy to be live, then let it settle.

WHY THIS EXISTS (FC-008, 2026-10-03).

post-merge-watch.yml's old "wait" polled /api/readyz until it answered 200. That
answers "is SOME backend up", not "is MY backend up": during a Railway deploy the OLD
container keeps answering 200 until the new one is healthy, so the wait returned in
0 seconds in all three runs of the 2026-10-03 incident (`[0 s] GET .../api/readyz -> 200`)
and the 15-minute watch window then started on the OLD code. The container swap came
later and its shutdown noise (`ClientDisconnect`, `event loop blocked`) landed inside
the window as brand-new Sentry issues.

So: ask Railway which deployment carries THIS sha, wait for it to report SUCCESS, then
wait SETTLE_MINUTES more so the swap's shutdown noise is over before anything is
sampled. Railway reports no "became SUCCESS at" time, so the settle clock starts when we
first SEE it succeed, or - if it was already SUCCESS on the first look (a queued run) -
at `createdAt + DEPLOY_BUILD_MINUTES`. Both are estimates; the workflow header says so.

NEVER A TRIP. This script only waits and reports. If Railway cannot be asked (no token,
CLI missing, API error, deploy not found in time) it says so LOUDLY in `deploy_state`
and the workflow degrades to a plain SETTLE_MINUTES sleep; the readyz probe and the
Sentry rules stay the only things that can trip a rollback.

Prints `key=value` lines on stdout (append to $GITHUB_OUTPUT); logs go to stderr.

    DEPLOY_SERVICE        Railway service to look at              (default backend)
    DEPLOY_WAIT_MINUTES   max wait for SUCCESS                    (default 10)
    SETTLE_MINUTES        quiet time after SUCCESS                (default 5)
    DEPLOY_BUILD_MINUTES  build-time estimate, see above          (default 4)
    DEPLOY_POLL_SECONDS   poll cadence                            (default 15)

    python scripts/deploy_settle.py --sha <sha>
    python scripts/deploy_settle.py --drill            # offline
    python scripts/deploy_settle.py --drill --blind    # negative control: must exit 1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

PENDING = {"INITIALIZING", "BUILDING", "DEPLOYING", "QUEUED", "WAITING", "NEEDS_APPROVAL"}
GONE = {"REMOVED", "REMOVING"}
FAILED = {"FAILED", "CRASHED"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _meta_sha(dep: dict) -> str:
    meta = dep.get("meta")
    return str(meta.get("commitHash", "")) if isinstance(meta, dict) else ""


def assess(deployments: list[dict], sha: str) -> tuple[str, dict | None]:
    """(state, deployment) for `sha`. Pure. Deployments are newest first (Railway's order).

    states: not_found | pending | live | superseded | failed | skipped
    """
    sha = sha.strip().lower()
    mine = [d for d in deployments if sha and _meta_sha(d).lower().startswith(sha[:12])]
    if not mine:
        return "not_found", None
    dep = mine[0]
    status = str(dep.get("status", "")).upper()
    if status == "SUCCESS":
        return "live", dep
    if status in PENDING:
        return "pending", dep
    if status in FAILED:
        return "failed", dep
    if status in GONE:
        # Railway marks an old deployment REMOVED once a newer one replaced it.
        return ("superseded" if deployments and deployments[0] is not dep else "failed"), dep
    return "skipped", dep  # SKIPPED or a status this script does not know: say so, don't guess


def parse_instant(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def settle_remaining(now: float, created_at: float | None, seen_success_at: float | None,
                     settle_s: float, build_s: float) -> float:
    """Seconds still to wait so `settle_s` of quiet follows the (estimated) SUCCESS instant."""
    if seen_success_at is not None:
        success = seen_success_at
    elif created_at is not None:
        success = created_at + build_s
    else:
        success = now  # unknown age: assume it just went live - wait the full settle
    return max(0.0, success + settle_s - now)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def wait_for_deploy(sha: str, service: str, wait_s: float, settle_s: float, build_s: float,
                    poll_s: float) -> dict[str, str]:
    """Poll Railway; return the outputs. Never raises on a Railway failure."""
    import rollback_gear as rg  # same Railway door the undo uses

    deadline = time.time() + wait_s
    seen_pending = False
    state, dep = "not_found", None
    while True:
        try:
            state, dep = assess(rg.list_deployments(service), sha)
        except (rg.RailwayCliMissingError, rg.RailwayApiMalformedError, rg.RailwayApiError) as exc:
            log(f"::warning::cannot ask Railway about {service}: {exc}")
            return {"deploy_state": "unverifiable", "deploy_created_at": ""}
        log(f"[deploy] {service} sha={sha[:8]} -> {state}")
        if state in ("pending", "not_found"):
            seen_pending = True
        if state not in ("pending", "not_found") or time.time() >= deadline:
            break
        time.sleep(poll_s)

    created = ""
    if dep is not None:
        created = str(dep.get("createdAt") or "")
    if state != "live":
        if state in ("pending", "not_found"):
            state = "timeout"
        log(f"::warning::deploy of {sha[:8]} is '{state}' - not waiting for a settle.")
        return {"deploy_state": state, "deploy_created_at": created}

    created_dt = parse_instant(created)
    now = time.time()
    remaining = settle_remaining(
        now,
        created_dt.timestamp() if created_dt else None,
        now if seen_pending else None,
        settle_s,
        build_s,
    )
    if remaining > 0:
        log(f"[deploy] live; settling {remaining:.0f}s so the container swap's shutdown noise ends")
        time.sleep(remaining)
    return {"deploy_state": "live", "deploy_created_at": created}


def drill(blind: bool = False) -> int:
    """Offline self-test of the pure functions. `blind` makes assess() say 'live' always."""
    sha = "41504c58755579fc1686cd23a578f8291a2086f7"
    other = "62f9a919" + "0" * 32

    def dep(status: str, commit: str) -> dict:
        return {"id": "d-" + status, "status": status, "createdAt": "2026-10-03T14:49:36.382Z",
                "meta": {"commitHash": commit}}

    def a(deps: list[dict]) -> str:
        return "live" if blind else assess(deps, sha)[0]

    cases = [
        ("commit's deploy still BUILDING -> pending (the old container's readyz 200 is not enough)",
         a([dep("BUILDING", sha), dep("SUCCESS", other)]) == "pending"),
        ("no deployment for this sha yet -> not_found, keep waiting",
         a([dep("SUCCESS", other)]) == "not_found"),
        ("SUCCESS for this sha -> live", a([dep("SUCCESS", sha), dep("REMOVED", other)]) == "live"),
        ("REMOVED while a newer deploy is live -> superseded",
         a([dep("SUCCESS", other), dep("REMOVED", sha)]) == "superseded"),
        ("FAILED -> failed", a([dep("FAILED", sha), dep("SUCCESS", other)]) == "failed"),
        ("settle: SUCCESS seen 1 min ago, settle 5 -> 4 min left",
         settle_remaining(1000.0, None, 940.0, 300.0, 240.0) == 240.0),
        ("settle: created 30 min ago (queued run) -> no wait",
         settle_remaining(10000.0, 8200.0, None, 300.0, 240.0) == 0.0),
        ("settle: created 1 min ago, already SUCCESS -> assume build ended 4 min after create: 8 min left",
         settle_remaining(10000.0, 9940.0, None, 300.0, 240.0) == 480.0),
    ]
    print("DRILL - deploy_settle must wait for THIS sha's deploy, not any 200."
          + ("  [BLIND: assess() always says live]" if blind else ""))
    for name, ok in cases:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    failed = [n for n, ok in cases if not ok]
    print(f"DRILL RESULT: {len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sha", help="commit under watch (default $GITHUB_SHA)")
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--blind", action="store_true", help="with --drill: negative control")
    args = ap.parse_args(argv)
    if args.drill:
        return drill(args.blind)
    sha = args.sha or os.getenv("GITHUB_SHA", "")
    if not sha:
        print("::error::no commit sha: pass --sha or set GITHUB_SHA", file=sys.stderr)
        return 2
    try:
        out = wait_for_deploy(
            sha,
            os.getenv("DEPLOY_SERVICE", "backend"),
            _env_float("DEPLOY_WAIT_MINUTES", 10) * 60,
            _env_float("SETTLE_MINUTES", 5) * 60,
            _env_float("DEPLOY_BUILD_MINUTES", 4) * 60,
            _env_float("DEPLOY_POLL_SECONDS", 15),
        )
    except ValueError as exc:
        print(f"::error::bad deploy_settle configuration: {exc}", file=sys.stderr)
        return 2
    for key, value in out.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
