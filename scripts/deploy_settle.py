#!/usr/bin/env python3
"""deploy_settle.py - wait for THIS commit's deploy to be live, then let it settle.

WHY THIS EXISTS (FC-009, 2026-10-03).

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

THE SERVED SHA (2026-10-20, PR #997). Railway's SUCCESS says the new deployment is
healthy; it does not say the domain serves it. With `--version-url` (repeatable; the
workflow passes https://job360.uk/api/version, then the backend's own URL) the script
also polls GET /api/version (backend/src/api/routes/health.py, which serves Railway's
RAILWAY_GIT_COMMIT_SHA) until it reports THIS sha, and the settle clock then starts
from when it first saw the new sha. `version_state`:

    live         a URL served this sha.
    superseded   a NEWER commit (a descendant; fetched if the clone lacks it) serves.
                 Its own run watches it; `judge=false`, so this run neither samples
                 nor rolls back someone else's deploy.
    never_live   the wait ran out while the OLD sha was served (or nothing answered).
                 `judge=false`: sampling now would judge the old code, and a rollback
                 would undo the deployment that is still good. The workflow goes RED.
    unknown      every answer was "unknown" (RAILWAY_GIT_COMMIT_SHA unset): the route
                 cannot tell, so the run degrades to the Railway-only behaviour above.
    not_asked    no --version-url given.

Still never a rollback: never_live is an alarm the workflow raises, not a trip.

Prints `key=value` lines on stdout (append to $GITHUB_OUTPUT); logs go to stderr.

    DEPLOY_SERVICE        Railway service to look at              (default backend)
    DEPLOY_WAIT_MINUTES   max wait for SUCCESS                    (default 10)
    DEPLOY_VERSION_WAIT_MINUTES  max wait for /api/version = sha  (default 5)
    SETTLE_MINUTES        quiet time after SUCCESS                (default 5)
    DEPLOY_BUILD_MINUTES  build-time estimate, see above          (default 4)
    DEPLOY_POLL_SECONDS   poll cadence                            (default 15)

    python scripts/deploy_settle.py --sha <sha> [--version-url URL ...]
    python scripts/deploy_settle.py --drill            # offline
    python scripts/deploy_settle.py --drill --blind    # negative control: must exit 1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
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


def same_sha(a: str, b: str) -> bool:
    """Full or abbreviated (>= 7 hex) SHAs naming the same commit."""
    a, b = a.strip().lower(), b.strip().lower()
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def version_state(served: str | None, sha: str, is_ancestor: Callable[[str, str], bool]) -> str:
    """One /api/version answer judged against `sha`. Pure. None = no answer.

    -> live | superseded | unknown | waiting
    """
    if not served:
        return "waiting"  # the old container without the route, or a blip
    if served == "unknown":
        return "unknown"
    if same_sha(served, sha):
        return "live"
    if is_ancestor(sha, served):
        return "superseded"
    return "waiting"  # the OLD sha (or one we cannot place) is still serving


def fetch_version(url: str, timeout: float = 15.0) -> str | None:
    """The sha `url` serves, "unknown", or None on any failure (logged)."""
    headers = {"Cache-Control": "no-cache", "User-Agent": "post-merge-watch"}
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed https URLs from the workflow
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URLs from the workflow
            body = json.loads(r.read().decode("utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001 - any failure is "no answer", never a crash
        log(f"  {url} -> {type(exc).__name__}: {exc}")
        return None
    served = str(body.get("sha", "") if isinstance(body, dict) else "").strip().lower()
    # Only a hex sha or "unknown" is passed on: these values reach $GITHUB_OUTPUT.
    if served == "unknown" or (7 <= len(served) <= 40 and all(c in "0123456789abcdef" for c in served)):
        return served
    return None


def _git(args: list[str]) -> int:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, timeout=60, check=False)  # noqa: S603, S607
        return proc.returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def git_is_ancestor(older: str, newer: str) -> bool:
    """True when `older` is an ancestor of `newer`. The run's clone predates any merge
    made during the wait, so a missing object (exit 128) means fetch and ask again,
    never "not a descendant"."""
    ask = ["merge-base", "--is-ancestor", older, newer]
    rc = _git(ask)
    if rc not in (0, 1):
        log(f"  {newer[:12]} is not in this clone; fetching it")
        _git(["fetch", "--quiet", "origin", "main"])
        rc = _git(ask)
        if rc not in (0, 1):
            _git(["fetch", "--quiet", "origin", newer])
            rc = _git(ask)
    return rc == 0


def wait_for_version(
    sha: str,
    urls: list[str],
    wait_s: float,
    poll_s: float,
    *,
    fetch: Callable[[str], str | None] = fetch_version,
    is_ancestor: Callable[[str, str], bool] = git_is_ancestor,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str, float | None]:
    """Poll the URLs until one serves `sha` (or a descendant), or `wait_s` passes.

    -> (version_state, what was last served, when the new sha was first seen - None
    when the very first poll already saw it, i.e. no swap was watched).
    """
    if not urls:
        return "not_asked", "", None
    deadline = now() + wait_s
    saw_other = False
    last = ""
    all_unknown = True
    while True:
        for url in urls:
            served = fetch(url)
            verdict = version_state(served, sha, is_ancestor)
            last = served or "no answer"
            log(f"[version] {url} -> {last} ({verdict})")
            if verdict in ("live", "superseded"):
                return verdict, last, (now() if saw_other else None)
            if verdict != "unknown":
                all_unknown = False
        saw_other = True
        if now() >= deadline:
            return ("unknown" if all_unknown else "never_live"), last, None
        sleep(poll_s)


def wait_for_deploy(sha: str, service: str, wait_s: float, settle_s: float, build_s: float,
                    poll_s: float, version_urls: list[str] | None = None,
                    version_wait_s: float = 300.0) -> dict[str, str]:
    """Poll Railway, then /api/version; return the outputs. Never raises on a Railway failure."""
    import rollback_gear as rg  # same Railway door the undo uses

    deadline = time.time() + wait_s
    seen_pending = False
    state, dep = "not_found", None
    while True:
        try:
            state, dep = assess(rg.list_deployments(service), sha)
        except (rg.RailwayCliMissingError, rg.RailwayApiMalformedError, rg.RailwayApiError) as exc:
            log(f"::warning::cannot ask Railway about {service}: {exc}")
            state, dep = "unverifiable", None
            break
        log(f"[deploy] {service} sha={sha[:8]} -> {state}")
        if state in ("pending", "not_found"):
            seen_pending = True
        if state not in ("pending", "not_found") or time.time() >= deadline:
            break
        time.sleep(poll_s)

    created = ""
    if dep is not None:
        created = str(dep.get("createdAt") or "")
    if state in ("pending", "not_found"):
        state = "timeout"

    # Railway already said this commit will not serve: one look, not a full wait.
    v_wait = 0.0 if state in ("failed", "skipped") else version_wait_s
    v_state, v_served, v_seen = wait_for_version(sha, version_urls or [], v_wait, poll_s,
                                                 fetch=fetch_version, is_ancestor=git_is_ancestor)
    out = {"deploy_state": state, "deploy_created_at": created, "version_state": v_state,
           "version_served": v_served, "judge": "true", "settled": "false"}
    if v_state in ("never_live", "superseded"):
        out["judge"] = "false"
        log(f"::warning::/api/version: {v_state} (last served {v_served}) - this run will not judge the deploy.")
        return out
    if state != "live" and v_state != "live":
        log(f"::warning::deploy of {sha[:8]} is '{state}' - not waiting for a settle.")
        return out

    created_dt = parse_instant(created)
    now = time.time()
    seen = v_seen if v_seen is not None else (now if seen_pending and state == "live" else None)
    remaining = settle_remaining(now, created_dt.timestamp() if created_dt else None, seen, settle_s, build_s)
    if remaining > 0:
        log(f"[deploy] live; settling {remaining:.0f}s so the container swap's shutdown noise ends")
        time.sleep(remaining)
    out["settled"] = "true"
    return out


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

    def v(answers: list[str | None], descendants: frozenset[str] = frozenset()) -> tuple[str, str, float | None]:
        """wait_for_version on a fake clock: one answer per poll, the last one repeats."""
        clock, calls = [0.0], [0]

        def fetch(_url: str) -> str | None:
            served = answers[min(calls[0], len(answers) - 1)]
            calls[0] += 1
            return sha if blind and served else served  # BLIND: "it answered, so it is live"

        def sleep(s: float) -> None:
            clock[0] += s

        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            return wait_for_version(sha, ["https://job360.uk/api/version"], 300, 60, fetch=fetch,
                                    is_ancestor=lambda _a, b: b in descendants,
                                    now=lambda: clock[0], sleep=sleep)

    newer = "c" * 40
    cases += [
        ("version: old sha served the whole wait -> never_live (the old container's 200 is not a deploy)",
         v([other])[0] == "never_live"),
        ("version: old sha, then this sha -> live, swap time recorded",
         v([other, other, sha])[0] == "live" and v([other, other, sha])[2] == 120.0),
        ("version: this sha on the first look (queued run) -> live, no swap time",
         v([sha]) == ("live", sha, None)),
        ("version: abbreviated sha still names this commit", v([sha[:12]])[0] == "live"),
        ("version: a newer descendant serves -> superseded, not judged",
         v([newer], frozenset({newer}))[0] == "superseded"),
        ("version: an unrelated newer-looking sha is NOT a descendant -> never_live",
         v([newer])[0] == "never_live"),
        ("version: route missing (no answer) all wait -> never_live", v([None])[0] == "never_live"),
        ("version: 'unknown' every time -> unknown (route cannot tell, degrade)",
         v(["unknown"])[0] == "unknown"),
        ("version: no URL given -> not_asked", wait_for_version(sha, [], 0, 1)[0] == "not_asked"),
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
    ap.add_argument("--version-url", action="append", default=[],
                    help="GET /api/version URL to poll for the served sha (repeatable, tried in order)")
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
            args.version_url,
            _env_float("DEPLOY_VERSION_WAIT_MINUTES", 5) * 60,
        )
    except ValueError as exc:
        print(f"::error::bad deploy_settle configuration: {exc}", file=sys.stderr)
        return 2
    for key, value in out.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
