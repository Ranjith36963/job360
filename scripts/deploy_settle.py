#!/usr/bin/env python3
"""deploy_settle.py - wait until production SERVES this commit, or say loudly that it never did.

WHY THIS EXISTS (FC-009; first designed in PR #728, rebuilt 2026-10-20).

post-merge-watch.yml used to "wait for the deploy" by polling /api/readyz until it
answered 200. The OLD container answers 200 until the swap, so the wait returned in
0 s on every run (`[0 s] GET .../api/readyz -> 200`, runs 37131014974 and 39397244330),
the 15-minute watch then sampled the old code, and a build that FAILED still ended in
"merged and live". /api/health cannot help: it returns a hardcoded "1.0.0".

So: poll GET /api/version (backend/src/api/routes/health.py, which serves Railway's
RAILWAY_GIT_COMMIT_SHA) until it reports THIS sha. URLs are tried in order: the custom
domain first (job360.uk proxies /api to the backend, frontend/next.config.ts, so that
also proves DNS + TLS + the proxy), then the backend's own URL, so a frontend outage
alone cannot read as "the backend never deployed".

States (stdout `key=value` lines, append to $GITHUB_OUTPUT; logs go to stderr):

    live         /api/version == this sha. `live_at` is when we first saw it; the
                 Sentry filter starts there, so the old container's errors are not ours.
    superseded   a NEWER commit (a descendant of this one) is already serving, or, at
                 the timeout, Railway says a newer deployment replaced this one. Its own
                 run watches it; this run must not judge or roll back someone else's deploy.
                 A served sha the clone lacks (merged during the wait) is fetched first.
    skipped      never served, but Railway reports this commit's deployment SKIPPED
                 (it chose not to deploy it). Nothing new is live; nothing to watch.
    never_live   timeout. The OLD sha (or no answer, or "unknown") was still being
                 served. exit 1: the deploy did not land, and nothing pretends it did.

Railway is asked only to EXPLAIN a never_live (FAILED / BUILDING / not found), via the
same door the undo uses (rollback_gear.list_deployments). It cannot turn a timeout green
except by reporting SKIPPED or superseded.

    DEPLOY_WAIT_MINUTES   max wait for the sha              (default 12)
    DEPLOY_POLL_SECONDS   poll cadence                      (default 15)
    DEPLOY_SERVICE        Railway service for the diagnosis (default backend)

    python scripts/deploy_settle.py --sha <sha> --url https://job360.uk/api/version [--url ...]
    python scripts/deploy_settle.py --drill            # offline fixture worlds
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


def same_sha(a: str, b: str) -> bool:
    """Full or abbreviated (>= 7 hex) SHAs naming the same commit."""
    a, b = a.strip().lower(), b.strip().lower()
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def judge(served: str | None, sha: str, is_ancestor: Callable[[str, str], bool]) -> str:
    """One poll's verdict. Pure. `served` is what /api/version said (None = no answer).

    -> live | superseded | waiting
    """
    if not served or served == "unknown":
        return "waiting"  # old container without the route, a blip, or no SHA set
    if same_sha(served, sha):
        return "live"
    if is_ancestor(sha, served):
        return "superseded"
    return "waiting"  # the OLD sha (or one we cannot place) is still serving


def assess(deployments: list[dict], sha: str) -> str:
    """Railway's own word on this sha's deployment (from PR #728). Pure. Newest first.

    -> not_found | pending | live | superseded | failed | skipped
    """
    def commit(d: dict) -> str:
        meta = d.get("meta")
        return str(meta.get("commitHash", "")) if isinstance(meta, dict) else ""

    mine = [d for d in deployments if same_sha(commit(d), sha)]
    if not mine:
        return "not_found"
    dep = mine[0]
    status = str(dep.get("status", "")).upper()
    if status == "SUCCESS":
        return "live"
    if status in PENDING:
        return "pending"
    if status in FAILED:
        return "failed"
    if status in GONE:
        return "superseded" if deployments and deployments[0] is not dep else "failed"
    return "skipped" if status == "SKIPPED" else f"unknown status {status!r}"


def fetch_version(url: str, timeout: float = 15.0) -> str | None:
    """The sha /api/version serves at `url`, or None on any failure (logged)."""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "post-merge-watch"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URLs from the workflow
            body = json.loads(r.read().decode("utf-8") or "{}")
    except Exception as exc:
        log(f"  {url} -> {type(exc).__name__}: {exc}")
        return None
    served = str(body.get("sha", "") if isinstance(body, dict) else "").strip().lower()
    # Only a hex sha or "unknown" is ever passed on: these values reach $GITHUB_OUTPUT.
    if served == "unknown" or (7 <= len(served) <= 40 and all(c in "0123456789abcdef" for c in served)):
        return served
    return None


def _git(args: list[str], cwd: str | None) -> int:
    """Exit code of one git command (-1 if git could not run). Output is discarded."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60,
                              check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


_FETCHED: set[tuple[str | None, str]] = set()  # (clone, sha) already fetched for: once each


def git_is_ancestor(older: str, newer: str, *, cwd: str | None = None) -> bool:
    """True when `older` is an ancestor of `newer`, fetching `newer` if the clone lacks it.

    merge-base exits 0 (yes), 1 (no), or 128 when an object is not in the clone. The
    clone is the one checkout made when the run STARTED, so a commit merged during the
    12-minute wait is missing from it. 128 therefore means "fetch, then ask again",
    never "not a descendant" (2026-10-20: otherwise a deploy that a newer merge
    legitimately replaced reads as "deploy never went live").
    """
    ask = ["merge-base", "--is-ancestor", older, newer]
    rc = _git(ask, cwd)
    if rc in (0, 1) or (cwd, newer) in _FETCHED:
        return rc == 0
    _FETCHED.add((cwd, newer))
    log(f"  {newer[:12]} is not in this clone; fetching origin main")
    _git(["fetch", "--quiet", "origin", "main"], cwd)
    rc = _git(ask, cwd)
    if rc not in (0, 1):  # main moved again, or it is on another ref: ask for it by name
        _git(["fetch", "--quiet", "origin", newer], cwd)
        rc = _git(ask, cwd)
    return rc == 0


def railway_state(sha: str, service: str) -> str:
    """Ask Railway why the sha never served. Never raises."""
    if not os.getenv("RAILWAY_TOKEN", "").strip():
        return "not asked (RAILWAY_TOKEN missing)"
    try:
        import rollback_gear as rg  # same Railway door the undo uses

        return assess(rg.list_deployments(service), sha)
    except Exception as exc:
        return f"unreadable ({type(exc).__name__}: {exc})"


def wait_for_sha(
    sha: str,
    urls: list[str],
    wait_s: float,
    poll_s: float,
    *,
    fetch: Callable[[str], str | None] = fetch_version,
    is_ancestor: Callable[[str, str], bool] = git_is_ancestor,
    diagnose: Callable[[], str] = lambda: "not asked",
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Poll until `sha` serves, a newer commit serves, or the deadline passes."""
    start = now()
    deadline = start + wait_s
    last: dict[str, str | None] = {}
    not_yet: float | None = None  # the last poll that still saw something else

    def iso(t: float | None) -> str:
        return "" if t is None else datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="seconds")

    while True:
        for url in urls:
            served = fetch(url)
            last[url] = served
            verdict = judge(served, sha, is_ancestor)
            log(f"[{now() - start:5.0f} s] {url} -> {served or 'no answer'} ({verdict})")
            if verdict != "waiting":
                # `since` bounds the swap from BELOW: the new code cannot have raised
                # anything before the last poll that still saw the old answer. Empty
                # when the first poll already matched (a queued run); the workflow then
                # uses the commit time, which is earlier still, so the net only widens.
                return {"deploy_state": verdict, "live_sha": served or "", "live_at": iso(now()),
                        "since": iso(not_yet), "live_url": url, "detail": f"{url} served {served}"}
        not_yet = now()
        if now() >= deadline:
            break
        sleep(poll_s)
    seen = ", ".join(f"{u} -> {v or 'no answer'}" for u, v in last.items())
    state = diagnose()
    if state == "skipped":
        return {"deploy_state": "skipped", "live_sha": "", "live_at": "", "since": "", "live_url": "",
                "detail": f"Railway SKIPPED this commit's deployment; still serving: {seen}"}
    if state == "superseded":
        # Railway removed this deployment for a newer one (merges 70 s apart do this,
        # DEPLOY.md). Like SKIPPED: not this run's deploy to judge (2026-10-20).
        return {"deploy_state": "superseded", "live_sha": "", "live_at": "", "since": "", "live_url": "",
                "detail": f"Railway says a newer deployment superseded this one; still serving: {seen}"}
    return {"deploy_state": "never_live", "live_sha": "", "live_at": "", "since": "", "live_url": "",
            "detail": (f"deploy never went live: after {wait_s / 60:.0f} min still serving: {seen}; "
                       f"Railway says: {state}")}


WORKFLOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows",
                        "post-merge-watch.yml")


def run_decide_step(env: dict[str, str]) -> dict[str, str]:
    """Execute post-merge-watch.yml's REAL `decide` step with `env`; return its outputs.

    The drill reads the step out of the workflow, so an edit there is what gets
    tested, not a copy of it. Runs the way Actions does: bash -eo pipefail.
    """
    import tempfile

    import yaml

    with open(WORKFLOW, encoding="utf-8") as f:
        wf = yaml.safe_load(f)
    step = next(st for st in wf["jobs"]["watch"]["steps"] if st.get("id") == "decide")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        script = os.path.join(tmp, "decide.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(step["run"])
        full = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": out,
                "POLICY_SERVICES": "backend,frontend", **env}
        subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", script],
                       env=full, check=True, capture_output=True, timeout=30)
        with open(out, encoding="utf-8") as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)


def decide_worlds() -> list[tuple[str, bool]]:
    """Fixture worlds for the workflow's verdict: what rolls back, what only alarms."""
    def d(**kw: str) -> dict[str, str]:
        return run_decide_step({k.upper(): v for k, v in kw.items()})

    never = d(deploy_state="never_live", deploy_detail="deploy never went live: old sha served")
    died = d()
    clean = d(deploy_state="live", probe_tripped="false", sentry_tripped="false")
    sup = d(deploy_state="superseded", deploy_detail="newer commit serving")
    dom = d(deploy_state="live", domain_tripped="true", domain_reason="job360.uk: 2 failures")
    fe = d(deploy_state="live", sentry_tripped="true", sentry_reason="new issue", sentry_services="frontend")
    rz = d(deploy_state="live", probe_tripped="true", probe_reason="readyz: 2", probe_services="backend")
    bare = d(deploy_state="live", sentry_tripped="true", sentry_reason="new issue", sentry_services="")
    return [
        ("decide: old sha still live (never_live) -> RED, nothing rolled back",
         never["alarm"] == "true" and never["tripped"] == "false" and "never went live" in never["trip_reason"]),
        ("decide: Step 1 died (no state) -> RED, nothing rolled back",
         died["alarm"] == "true" and died["tripped"] == "false"),
        ("decide: live and clean -> green, 'merged and live' may post",
         clean["alarm"] == "false" and clean["tripped"] == "false" and clean["live"] == "true"),
        ("decide: superseded -> green, no rollback, no 'merged and live'",
         sup["alarm"] == "false" and sup["tripped"] == "false" and sup["live"] == "false"),
        ("decide: job360.uk down, Railway URLs fine -> RED, nothing rolled back",
         dom["alarm"] == "true" and dom["tripped"] == "false"),
        ("decide: new frontend Sentry error -> roll back frontend ONLY",
         fe["tripped"] == "true" and fe["services"] == "frontend"),
        ("decide: readyz trip -> roll back backend", rz["tripped"] == "true" and rz["services"] == "backend"),
        ("decide: a trip with no service named -> the policy's services, never nothing",
         bare["tripped"] == "true" and bare["services"] == "backend,frontend"),
    ]


def wait_worlds(blind: bool = False) -> list[tuple[str, bool]]:
    """Fixture worlds through the real wait loop with a fake clock, fake HTTP and fake git.

    `blind` restores the OLD rule (any answer counts as live), so the "old sha still
    live" world must then FAIL - the proof this drill can still go red.
    """
    import contextlib
    import io

    sha = "a" * 40
    old = "b" * 40
    newer = "c" * 40
    dom, be = "https://job360.uk/api/version", "https://backend.example/api/version"

    def run(script: dict[str, list[str | None]], railway: str = "failed",
            ancestors: frozenset[tuple[str, str]] = frozenset()) -> dict[str, str]:
        clock = [0.0]
        calls = {u: 0 for u in script}

        def fetch(url: str) -> str | None:
            seq = script.get(url, [None])
            i = min(calls.get(url, 0), len(seq) - 1)
            calls[url] = calls.get(url, 0) + 1
            served = seq[i]
            if blind and served is not None:
                return sha  # the old rule: "it answered, so it is live"
            return served

        def sleep(s: float) -> None:
            clock[0] += s

        with contextlib.redirect_stderr(io.StringIO()):  # the poll log; the verdict is the test
            return wait_for_sha(sha, [dom, be], 600, 60, fetch=fetch,
                                is_ancestor=lambda a, b: (a, b) in ancestors,
                                diagnose=lambda: railway, now=lambda: clock[0], sleep=sleep)

    worlds = [
        ("old sha still live for the whole wait -> never_live (red), Railway's reason quoted",
         lambda r: r["deploy_state"] == "never_live" and "failed" in r["detail"],
         run({dom: [old], be: [old]})),
        ("old container has no /api/version (404) -> never_live, not live",
         lambda r: r["deploy_state"] == "never_live", run({dom: [None], be: [None]})),
        ("backend reports sha=unknown -> never_live (cannot verify is not verified)",
         lambda r: r["deploy_state"] == "never_live", run({dom: ["unknown"], be: ["unknown"]})),
        ("old, old, then this sha -> live; Sentry `since` = the last poll that saw the OLD sha",
         lambda r: (r["deploy_state"] == "live" and r["live_at"].startswith("1970-01-01T00:02:00")
                    and r["since"].startswith("1970-01-01T00:01:00")),
         run({dom: [old, old, sha], be: [old]})),
        ("domain down (frontend outage) but backend serves the sha -> live via backend URL",
         lambda r: r["deploy_state"] == "live" and r["live_url"] == be, run({dom: [None], be: [sha]})),
        ("a newer descendant commit is serving -> superseded (never judge its deploy)",
         lambda r: r["deploy_state"] == "superseded",
         run({dom: [newer], be: [newer]}, ancestors=frozenset({(sha, newer)}))),
        ("an unrelated sha that is NOT a descendant -> never_live",
         lambda r: r["deploy_state"] == "never_live", run({dom: [newer], be: [newer]})),
        ("Railway SKIPPED this commit -> skipped, not a red",
         lambda r: r["deploy_state"] == "skipped", run({dom: [old], be: [old]}, railway="skipped")),
        ("never served, Railway says a newer deployment superseded it -> superseded, not a red",
         lambda r: r["deploy_state"] == "superseded" and "superseded" in r["detail"],
         run({dom: [old], be: [old]}, railway="superseded")),
        ("abbreviated sha 7+ hex matches; 6 does not",
         lambda r: same_sha(sha[:7], sha) and not same_sha(sha[:6], sha), {}),
        ("Railway assess(): BUILDING -> pending, SKIPPED -> skipped, FAILED -> failed",
         lambda r: (assess([{"status": "BUILDING", "meta": {"commitHash": sha}}], sha) == "pending"
                    and assess([{"status": "SKIPPED", "meta": {"commitHash": sha}}], sha) == "skipped"
                    and assess([{"status": "FAILED", "meta": {"commitHash": sha}}], sha) == "failed"
                    and assess([{"status": "SUCCESS", "meta": {"commitHash": old}}], sha) == "not_found"),
         {}),
    ]
    return [(name, bool(check(result))) for name, check, result in worlds]


def git_worlds() -> list[tuple[str, bool]]:
    """The real git_is_ancestor on throwaway repos: a clone made BEFORE a newer merge.

    `upstream` gets commit A; `clone` is cloned from it (the run's checkout); then B
    (a child of A) lands on upstream main, as a merge during the 12-minute wait does.
    """
    import contextlib
    import io
    import tempfile

    cfg = ["-c", "user.name=drill", "-c", "user.email=drill@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]

    def git(cwd: str, *args: str) -> str:
        return subprocess.run(["git", *cfg, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              check=True, timeout=60).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
        up, clone = os.path.join(tmp, "upstream"), os.path.join(tmp, "clone")
        os.mkdir(up)
        git(up, "init", "-q", "-b", "main")
        git(up, "commit", "-q", "--allow-empty", "-m", "A")
        a = git(up, "rev-parse", "HEAD")
        git(tmp, "clone", "-q", up, clone)
        git(up, "commit", "-q", "--allow-empty", "-m", "B")
        b = git(up, "rev-parse", "HEAD")
        nowhere = "d" * 40
        missing_before = _git(["merge-base", "--is-ancestor", a, b], clone)
        clock = [0.0]
        r = wait_for_sha(a, ["https://job360.uk/api/version"], 120, 60, fetch=lambda u: b,
                         is_ancestor=lambda x, y: git_is_ancestor(x, y, cwd=clone),
                         diagnose=lambda: "failed", now=lambda: clock[0],
                         sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        stranger = git_is_ancestor(a, nowhere, cwd=clone)
    return [
        ("git: a newer main commit merged during the wait (missing from the clone, merge-base "
         "128) -> fetched, then superseded, not red",
         missing_before == 128 and r["deploy_state"] == "superseded" and r["live_sha"] == b),
        ("git: a sha that no fetch can find -> not a descendant (the wait stays red)", stranger is False),
    ]


def drill(blind: bool = False) -> int:
    """wait_worlds(), git_worlds() and the workflow's own decide step (decide_worlds, needs pyyaml)."""
    results = wait_worlds(blind) + git_worlds() + decide_worlds()
    print("DRILL - deploy_settle must wait for THIS sha to SERVE, not any answer."
          + ("  [BLIND: any answer counts as live]" if blind else ""))
    failed = []
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failed.append(name)
    print(f"DRILL RESULT: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--sha", help="commit under watch (default $GITHUB_SHA)")
    ap.add_argument("--url", action="append", default=[], help="a /api/version URL; repeat, tried in order")
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--blind", action="store_true", help="with --drill: negative control")
    args = ap.parse_args(argv)
    if args.drill:
        return drill(args.blind)
    sha = (args.sha or os.getenv("GITHUB_SHA", "")).strip().lower()
    if not (7 <= len(sha) <= 40 and all(c in "0123456789abcdef" for c in sha)) or not args.url:
        print("::error::deploy_settle needs a hex --sha (or GITHUB_SHA) and at least one --url",
              file=sys.stderr)
        return 2
    try:
        wait_s = _env_float("DEPLOY_WAIT_MINUTES", 12) * 60
        poll_s = _env_float("DEPLOY_POLL_SECONDS", 15)
    except ValueError as exc:
        print(f"::error::bad deploy_settle configuration: {exc}", file=sys.stderr)
        return 2
    service = os.getenv("DEPLOY_SERVICE", "backend")
    out = wait_for_sha(sha, args.url, wait_s, poll_s, diagnose=lambda: railway_state(sha, service))
    for key, value in out.items():
        print(f"{key}={value}")
    if out["deploy_state"] == "never_live":
        print(f"::error::{out['detail']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
