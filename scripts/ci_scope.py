#!/usr/bin/env python3
"""Decide how much of CI a run needs: `docs`, `frontend`, `full` -- or `tested`.

WHY THIS EXISTS (harness simplification slice 3, owner decision 2026-09-08)
--------------------------------------------------------------------------
Measured on the last green run of main (2026-09-10): the backend job spends
347s of its 414s running the full pytest suite, and ci-offline.yml runs the
SAME suite again in parallel. A pull request that changes one React component
therefore waits seven minutes for tests it cannot affect. The owner asked for a
fast lane: frontend-only or docs-only diffs get lint + type-check + unit tests
(about two minutes), and anything touching the backend, the schema or the
harness gets the full gate exactly as before.

WHAT THIS IS NOT
----------------
It is NOT the merge lane. `scripts/lane.py` answers "who may merge this and
what must be true first"; this file answers "which steps are worth running".
Both are read by CI; neither reads the other. A frontend-only PR still takes
whatever merge lane its files put it in.

THE ONE RULE: MOST RESTRICTIVE WINS, UNKNOWN IS NOT SAFE
--------------------------------------------------------
    full  >  frontend  >  docs

One backend file in a forty-file docs PR makes the whole PR `full`. A path this
file has no rule for is `full`, and the reason names it. An empty changeset is
`full`. A push to main is `full` unless it PROVES `tested` (below) -- main is
production. Every refusal here costs a few minutes of compute; a wrong `docs`
costs a broken deploy, and #503 already paid that once.

`tested`: THE SAME TREE ALREADY PASSED (CI audit, 2026-10-19)
--------------------------------------------------------------
Measured over 32 merges: every push to main re-ran ci-offline (575 min), ci
(356 min) on a commit whose PR had just run the same gate. `tested` lets a push
(or the 06:00 schedule) skip the heavy STEPS -- the jobs still run and report --
but only on PROOF, never on a guess. Each workflow proves its own steps:

  push to main   the pushed commit is the merge commit of exactly one merged PR
                 into main; the pushed tree == that PR head's tree; every
                 required status check of the live branch rules is `success` on
                 that head (a check run, or -- for a context no check run
                 carries, e.g. `merge-cage` -- a commit status); AND a run of
                 THIS workflow on that head concluded
                 `success` and carries the artifact `ci-tested-tree-<tree>`.
  schedule       a `push` run of THIS workflow on the same sha, under 24 h old,
                 concluded `success` and carries `ci-tested-tree-<tree>`.

The artifact is uploaded by the `scope` job ONLY when it decided `full`, and it
is named after `git rev-parse HEAD^{tree}` of what that run checked out. So it
proves the heavy steps really ran on exactly this tree. (A pull_request run
checks out the PR's MERGE ref, not its head: head-tree equality alone would
not prove what was tested. The artifact does.) A run that was itself `tested`,
`frontend` or `docs` uploads nothing, so savings never chain off a skip.

Anything else -- no PR, two PRs, tree differs (main moved; strict up-to-date is
OFF in the ruleset), a required check red / missing / still running, no
matching artifact, a stale push run, any API or git error, a dispatch -- is
`full`, and the reason says which.

DEPLOY-SHAPED FRONTEND FILES ARE FULL
-------------------------------------
`frontend/**` is the fast lane EXCEPT the files that decide how the app is built
and shipped: the Dockerfile and .dockerignore (the image Railway builds),
package.json and the lockfile (what gets installed), next.config.* (output
mode, the thing the Dockerfile copies), railway.json, and any .env file. Those
are the files that broke production in #503, and the fast lane skips the exact
step -- the Docker image build -- that would have caught them.

JOB NAMES NEVER CHANGE
----------------------
The ruleset on main requires checks BY NAME (`Backend (Python 3.12)`,
`Frontend (Node 20)`, `offline-suite`, ...), and so does scripts/merge_cage.py.
A job that does not run is a missing required check, and a missing check blocks
the merge. So the fast lane skips STEPS inside those jobs, never the jobs: a
skipped job never reports, a job whose steps were skipped reports success.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import PurePosixPath

SCOPES: tuple[str, ...] = ("full", "frontend", "docs")  # most restrictive first

# `tested` is not a diff scope: it is only ever PROVED, by prove_tested() below.
TESTED = "tested"
TESTED_ARTIFACT_PREFIX = "ci-tested-tree-"
MAIN_REF = "refs/heads/main"
# The 06:00 run is the nightly catch for flakes and date-dependent tests: it may
# lean on a push run only if that run is fresh. Older than this -> run it all.
SCHEDULE_MAX_AGE_H = 24

# Top-level files under frontend/ that change how the app is BUILT or SHIPPED,
# not what it does. Any of these -> full, because the fast lane skips the Docker
# image build and that build is the only step that exercises them.
_DEPLOY_SHAPED_FRONTEND: frozenset[str] = frozenset({
    ".dockerignore",
    "package.json",
    "package-lock.json",
    "railway.json",
})
_DEPLOY_SHAPED_PREFIXES: tuple[str, ...] = ("Dockerfile", "next.config.")

# MARKDOWN THAT IS NOT PROSE (CI audit, 2026-10-20). `*.md` used to be `docs`
# at any depth, so a PR touching only backend/src/recipes/*.md -- served to
# users' agents by MCP `get_recipe`, guarded by tests/test_recipes.py -- ran no
# test at all, and the first run was on main, after the deploy. Under these
# trees a `.md` is product, agent instructions or harness: it takes the lane of
# its tree (backend/.claude/.github -> full, frontend/src -> frontend).
_MD_IS_CODE_PREFIXES: tuple[str, ...] = ("backend/", "frontend/src/", ".claude/", ".github/")

# Files OUTSIDE backend/ that backend/tests read by exact path, so an edit to
# one can turn offline-suite red although the diff looks like docs or frontend.
# Found by grepping backend/tests for reads (2026-10-20); a new such read must
# be added here. Broad scans (test_sourcing_era_deleted over frontend/src and
# frontend/tests, test_conflict_marker_guard over frontend/) are NOT listed:
# listing them would make every frontend diff `full`.
_READ_BY_BACKEND_TESTS: frozenset[str] = frozenset({
    "ARCHITECTURE.md",                          # test_sourcing_era_deleted
    "CLAUDE.md",                                # test_sourcing_era_deleted
    "STATUS.md",                                # test_sourcing_era_deleted
    "CONTRIBUTING.md",                          # test_suite_floor
    "frontend/README.md",                       # test_sourcing_era_deleted
    "frontend/src/lib/event-types.json",        # test_event_types_json_matches_settings
    "frontend/src/lib/event-labels.ts",         # test_bulk_queue_preflight
    "frontend/src/app/trust/page.tsx",          # test_trust_page
    "frontend/src/instrumentation-client.ts",   # test_trust_page
    "frontend/tests/synthetic/live-smoke.mjs",  # test_slice5_fixes
})


def kind_of(path: str) -> str:
    """Return `docs`, `frontend` or `full` for ONE path. Pure."""
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        return "full"  # not a repo-relative path -> refuse to guess
    p = PurePosixPath(path)
    if p.name.startswith(".env"):
        return "full"  # an env file anywhere is configuration, never prose
    if path in _READ_BY_BACKEND_TESTS:
        return "full"  # a backend test reads it: only offline-suite can judge it
    if path.startswith("docs/"):
        return "docs"
    if p.suffix == ".md" and not path.startswith(_MD_IS_CODE_PREFIXES):
        return "docs"
    if path.startswith("frontend/"):
        rel = path[len("frontend/"):]
        top_level = "/" not in rel
        if top_level and (rel in _DEPLOY_SHAPED_FRONTEND
                          or rel.startswith(_DEPLOY_SHAPED_PREFIXES)):
            return "full"
        return "frontend"
    return "full"


def classify(files: list[str]) -> dict:
    """Classify a whole changeset. Always says WHICH files decided it."""
    if not files:
        return {"scope": "full", "why": ["no files in the changeset -- refusing to guess"],
                "by_kind": {}}
    by_kind: dict[str, list[str]] = {}
    for f in files:
        by_kind.setdefault(kind_of(f), []).append(f)
    for scope in SCOPES:
        hits = sorted(by_kind.get(scope) or [])
        if hits:
            shown = ", ".join(hits[:4]) + (" ..." if len(hits) > 4 else "")
            return {
                "scope": scope,
                "why": [f"scope `{scope}` decided by {len(hits)} file(s): {shown}"],
                "by_kind": by_kind,
            }
    raise AssertionError("unreachable: every file has a kind")


def changed_files(base: str, head: str) -> list[str] | None:
    """`git diff --name-only base...head`, or None if git could not answer."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# `tested`: proof that THIS tree already passed THIS workflow's full gate.
# Pure apart from the two injected callables, so the drill can feed it shapes.
# ─────────────────────────────────────────────────────────────────────────────

class ProofError(Exception):
    """Git or the API could not answer. Always means `full`."""


def tested_marker(tree: str) -> str:
    return f"{TESTED_ARTIFACT_PREFIX}{tree}"


def _full(why: str, **matched: object) -> dict:
    return {"scope": "full", "why": [why], "by_kind": {}, "matched": matched}


def _ts(value: str) -> float:
    import datetime as _dt
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _required_contexts(repo: str, gh) -> list[str]:
    rules = gh(f"repos/{repo}/rules/branches/main")
    names: list[str] = []
    for rule in rules if isinstance(rules, list) else []:
        if rule.get("type") == "required_status_checks":
            for c in (rule.get("parameters") or {}).get("required_status_checks") or []:
                if c.get("context"):
                    names.append(c["context"])
    return sorted(set(names))


def _latest_check_runs(repo: str, sha: str, gh) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for page in range(1, 6):
        data = gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        runs = data.get("check_runs") or []
        for r in runs:
            name = r.get("name", "")
            if name not in latest or int(r.get("id", 0)) > int(latest[name].get("id", 0)):
                latest[name] = r
        if len(runs) < 100:
            return latest
    raise ProofError(f"more than 500 check runs on {sha[:12]} -- refusing to read a partial list")


def _commit_statuses(repo: str, sha: str, gh) -> dict[str, dict]:
    """Commit STATUSES by context. A required context can be a status, not a
    check run: the ruleset added `merge-cage` (posted by auto-merge.yml) on
    2026-10-20, and reading check runs alone called it `missing` on every head,
    so every push to main ran `full`. The combined status already holds only
    the newest status per context."""
    latest: dict[str, dict] = {}
    for page in range(1, 6):
        data = gh(f"repos/{repo}/commits/{sha}/status?per_page=100&page={page}")
        statuses = (data or {}).get("statuses") or []
        for s in statuses:
            if s.get("context"):
                latest.setdefault(s["context"], s)
        if len(statuses) < 100:
            return latest
    raise ProofError(f"more than 500 statuses on {sha[:12]} -- refusing to read a partial list")


def _evidence_run(repo: str, workflow: str, sha: str, event: str, tree: str, gh,
                  *, now: float | None = None) -> dict | None:
    """A completed, successful run of `workflow` on `sha` (by `event`) that
    uploaded the full-scope marker for `tree`. Newest first."""
    data = gh(f"repos/{repo}/actions/workflows/{workflow}/runs"
              f"?head_sha={sha}&event={event}&per_page=30")
    runs = sorted(data.get("workflow_runs") or [], key=lambda r: int(r.get("id", 0)), reverse=True)
    for run in runs:
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        if run.get("head_sha") != sha:
            continue
        if now is not None and now - _ts(run.get("updated_at") or run.get("created_at")) \
                > SCHEDULE_MAX_AGE_H * 3600:
            continue
        arts = gh(f"repos/{repo}/actions/runs/{run['id']}/artifacts?name={tested_marker(tree)}")
        if any(a.get("name") == tested_marker(tree) and not a.get("expired")
               for a in arts.get("artifacts") or []):
            return run
    return None


def prove_tested(*, event: str, sha: str, ref: str, repo: str, workflow: str,
                 git, gh, now: float) -> dict:
    """`tested` ONLY with proof; every other road -- including any exception -- is `full`."""
    try:
        return _prove(event=event, sha=sha, ref=ref, repo=repo, workflow=workflow,
                      git=git, gh=gh, now=now)
    except Exception as exc:  # noqa: BLE001 -- ANY doubt is the full gate
        return _full(f"could not prove `tested` ({type(exc).__name__}: {exc}) -- "
                     "refusing to guess, running the full gate", sha=sha)


def _prove(*, event, sha, ref, repo, workflow, git, gh, now) -> dict:
    if event not in ("push", "schedule"):
        return _full(f"event `{event}` never skips on main -- a dispatch is a deliberate "
                     "full run", sha=sha)
    if ref != MAIN_REF:
        return _full(f"ref `{ref}` is not main -- only main runs are ever `tested`", sha=sha)
    if not (sha and workflow and repo):
        return _full("missing --sha / --workflow / --repo -- cannot look anything up", sha=sha)
    tree = git(["rev-parse", f"{sha}^{{tree}}"]).strip()
    if len(tree) != 40:
        raise ProofError(f"git rev-parse {sha[:12]}^{{tree}} gave {tree!r}")

    if event == "schedule":
        run = _evidence_run(repo, workflow, sha, "push", tree, gh, now=now)
        if run is None:
            return _full(f"no successful full-scope `push` run of {workflow} on {sha[:12]} "
                         f"in the last {SCHEDULE_MAX_AGE_H}h -- the nightly runs everything",
                         sha=sha, tree=tree)
        return {"scope": TESTED, "by_kind": {},
                "why": [f"push run {run['id']} of {workflow} already ran the full gate on "
                        f"{sha[:12]} (tree {tree[:12]}) at {run.get('updated_at')}"],
                "matched": {"sha": sha, "tree": tree, "run": run["id"]}}

    # event == push
    pulls = gh(f"repos/{repo}/commits/{sha}/pulls")
    merged = [p for p in (pulls if isinstance(pulls, list) else [])
              if p.get("merged_at") and (p.get("base") or {}).get("ref") == "main"
              and p.get("merge_commit_sha") == sha]
    if len(merged) != 1:
        return _full(f"{len(merged)} merged PR(s) into main have {sha[:12]} as their merge "
                     "commit (need exactly one) -- a direct push or an ambiguous merge",
                     sha=sha, tree=tree)
    pr = merged[0]
    number, head = pr.get("number"), (pr.get("head") or {}).get("sha", "")
    head_tree = ((gh(f"repos/{repo}/git/commits/{head}") or {}).get("tree") or {}).get("sha", "")
    if head_tree != tree:
        return _full(f"tree differs: main {sha[:12]} has tree {tree[:12]}, PR #{number} head "
                     f"{head[:12]} has tree {head_tree[:12] or '?'} -- main moved under the PR",
                     pr=number, head=head, sha=sha)

    required = _required_contexts(repo, gh)
    if not required:
        return _full("the branch rules name no required status checks -- nothing to prove "
                     "green against", pr=number, head=head, sha=sha)
    latest = _latest_check_runs(repo, head, gh)
    # Statuses are read only when a required context is not a check run.
    statuses = (_commit_statuses(repo, head, gh)
                if any(name not in latest for name in required) else {})
    bad = []
    for name in required:
        r = latest.get(name)
        if r is None:
            s = statuses.get(name)
            if s is None:
                bad.append(f"{name}=missing")
            elif s.get("state") != "success":
                bad.append(f"{name}={s.get('state') or 'unknown'}")
        elif r.get("status") != "completed" or r.get("conclusion") != "success":
            bad.append(f"{name}={r.get('conclusion') or r.get('status')}")
    if bad:
        return _full(f"PR #{number} head {head[:12]}: required check(s) not green: "
                     + ", ".join(bad[:5]), pr=number, head=head, sha=sha)

    run = _evidence_run(repo, workflow, head, "pull_request", tree, gh)
    if run is None:
        return _full(f"no successful full-scope run of {workflow} on PR #{number} head "
                     f"{head[:12]} tested tree {tree[:12]} (the PR ran a fast lane, or "
                     "tested a different merge tree)", pr=number, head=head, sha=sha)
    return {"scope": TESTED, "by_kind": {},
            "why": [f"PR #{number} head {head[:12]} has the same tree {tree[:12]} as main "
                    f"{sha[:12]}; all {len(required)} required checks green; run {run['id']} "
                    f"of {workflow} ran the full gate on exactly this tree"],
            "matched": {"pr": number, "head": head, "sha": sha, "tree": tree, "run": run["id"]}}


def _git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              encoding="utf-8", errors="replace").stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ProofError(f"git {' '.join(args)} failed") from exc


def _gh(path: str) -> object:
    try:
        out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                             capture_output=True, text=True, check=True,
                             encoding="utf-8", errors="replace").stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise ProofError(f"gh api {path.split('?')[0]} failed") from exc


def decision_line(verdict: dict) -> str:
    """ONE line per decision: decision, reason, what it matched."""
    m = verdict.get("matched") or {}
    bits = []
    if m.get("pr"):
        bits.append(f"PR #{m['pr']} head {str(m.get('head', ''))[:12]}")
    if m.get("sha"):
        bits.append(f"sha {str(m['sha'])[:12]}")
    if m.get("run"):
        bits.append(f"run {m['run']}")
    return (f"decision: `{verdict['scope']}` | reason: {'; '.join(verdict['why'])} | "
            f"matched: {', '.join(bits) or 'none'}")


def _emit(verdict: dict, print_scope: bool, summary: bool) -> None:
    """Report the verdict.

    The JSON goes to STDERR so the run log always shows the reasoning, and with
    `--print-scope` STDOUT carries the bare scope word and nothing else, so the
    workflow step can do `scope=$(python ...)` and write `$GITHUB_OUTPUT`
    itself. The write is deliberately in the workflow's own `run:` line and not
    in here: scripts/chain_check.py reads workflow text to prove every
    `steps.X.outputs.Y` a job consumes is actually written by step X, and a
    write hidden inside a Python file is a wire it cannot see.
    """
    print(json.dumps(verdict, indent=2, sort_keys=True), file=sys.stderr)
    if print_scope:
        print(verdict["scope"])
    if summary:
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"### CI scope: `{verdict['scope']}`\n\n")
                fh.write(f"{decision_line(verdict)}\n\n")
                fh.write("`full` runs everything; `frontend` skips backend tests and the "
                         "Docker image build; `docs` runs only the doc gates; `tested` skips "
                         "the heavy steps because this exact tree already passed them "
                         "(matched run named above).\n")


def _drill() -> int:
    """Break the classifier on purpose. A guard nobody has watched go red is a claim."""
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"   got={got!r} want={want!r}"))

    print("ci_scope.py --drill")
    # NEGATIVE CONTROLS FIRST. A classifier that answers `full` to everything
    # passes every refusal below and is exactly as useful as no classifier.
    check("docs-only diff takes the docs lane", classify(["docs/harness/x.md"])["scope"], "docs")
    check("frontend-only diff takes the frontend lane",
          classify(["frontend/src/components/JobCard.tsx"])["scope"], "frontend")

    # PRECEDENCE -- one restrictive file beats any number of safe ones.
    many_docs = [f"docs/note{i}.md" for i in range(40)]
    check("40 docs + 1 backend file is FULL",
          classify([*many_docs, "backend/src/main.py"])["scope"], "full")
    check("docs + frontend is FRONTEND (the wider of the two)",
          classify(["README.md", "frontend/src/lib/api.ts"])["scope"], "frontend")
    check("frontend + one workflow is FULL",
          classify(["frontend/src/app/page.tsx", ".github/workflows/ci.yml"])["scope"], "full")
    check("frontend + one script is FULL",
          classify(["frontend/src/app/page.tsx", "scripts/ci_scope.py"])["scope"], "full")

    # DEPLOY-SHAPED FRONTEND FILES -- the #503 set -- are never fast.
    for deploy in ("frontend/Dockerfile", "frontend/.dockerignore", "frontend/package.json",
                   "frontend/package-lock.json", "frontend/next.config.ts",
                   "frontend/railway.json", "frontend/.env.production"):
        check(f"deploy-shaped file is FULL: {deploy}", classify([deploy])["scope"], "full")
    check("a nested .env is FULL even inside src/",
          classify(["frontend/src/.env.local"])["scope"], "full")
    check("a NESTED package.json is still frontend (only the top-level one ships)",
          classify(["frontend/src/fixtures/package.json"])["scope"], "frontend")

    # DOCS SHAPE -- anything under docs/, markdown outside the code trees.
    check("root README is docs (no backend test reads it)", classify(["README.md"])["scope"], "docs")
    check("markdown under docs/ is docs", classify(["docs/product/x.md"])["scope"], "docs")
    check("a non-markdown file under docs/ is docs",
          classify(["docs/harness/wires.yml"])["scope"], "docs")
    check("markdown at the top of frontend/ is docs (cannot affect the build)",
          classify(["frontend/CLAUDE.md"])["scope"], "docs")

    # MARKDOWN THAT IS CODE -- recipes are served to agents; skills, agents and
    # workflow templates are instructions. Never the docs lane.
    for code_md in ("backend/src/recipes/rules.md", "backend/src/recipes/apply.md",
                    "backend/CLAUDE.md", ".claude/skills/hard-rules/SKILL.md",
                    ".claude/agents/reviewer-bugs.md", ".github/pull_request_template.md"):
        check(f"markdown in a code tree is FULL: {code_md}", classify([code_md])["scope"], "full")
    check("markdown under frontend/src/ takes the frontend lane, not docs",
          classify(["frontend/src/content/help.md"])["scope"], "frontend")
    check("40 docs + 1 recipe is FULL",
          classify([*many_docs, "backend/src/recipes/daily.md"])["scope"], "full")

    # FILES A BACKEND TEST READS -- full, so offline-suite runs on them.
    for read in sorted(_READ_BY_BACKEND_TESTS):
        check(f"read by a backend test is FULL: {read}", classify([read])["scope"], "full")
    check("a neighbour of a test-read file is still frontend",
          classify(["frontend/src/lib/other.json"])["scope"], "frontend")
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    missing = sorted(f for f in _READ_BY_BACKEND_TESTS if not (repo_root / f).exists())
    check("every test-read path still exists (a stale entry goes red here)", missing, [])

    # UNKNOWN IS NOT SAFE.
    check("an unlisted root file is FULL", classify(["foo.txt"])["scope"], "full")
    check("a brand-new top-level dir is FULL", classify(["newthing/x.ts"])["scope"], "full")
    check("an empty changeset is FULL", classify([])["scope"], "full")
    check("a path with .. is FULL", classify(["docs/../backend/x.py"])["scope"], "full")
    check("an absolute path is FULL", classify(["/etc/passwd"])["scope"], "full")

    # THE VERDICT NAMES ITS EVIDENCE.
    v = classify(["docs/a.md", "backend/src/x.py"])
    check("the verdict names the deciding file", "backend/src/x.py" in " ".join(v["why"]), True)

    _drill_tested(check)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    if passed != len(cases):
        print("DRILL FAILED -- the scope classifier no longer behaves as documented.")
    return 0 if passed == len(cases) else 1


def _drill_tested(check) -> None:
    """`tested` is refused on every road but the proven one. Fakes stand in for
    git and the API; the POSITIVE control comes first, because a prover that
    refuses everything passes every refusal below and saves nothing."""
    import copy
    import time

    repo, wf = "o/r", "ci-offline.yml"
    sha, head, tree = "a" * 40, "b" * 40, "c" * 40
    now = time.time()
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 30 * 3600))
    required = ["offline-suite", "frontend-e2e", "Analyze (python)"]

    def world() -> dict:
        ok_run = {"id": 7, "status": "completed", "conclusion": "success", "head_sha": head,
                  "created_at": fresh, "updated_at": fresh}
        push_run = {"id": 9, "status": "completed", "conclusion": "success", "head_sha": sha,
                    "created_at": fresh, "updated_at": fresh}
        return {
            f"repos/{repo}/commits/{sha}/pulls": [
                {"number": 42, "merged_at": fresh, "merge_commit_sha": sha,
                 "base": {"ref": "main"}, "head": {"sha": head}}],
            f"repos/{repo}/git/commits/{head}": {"tree": {"sha": tree}},
            f"repos/{repo}/rules/branches/main": [
                {"type": "deletion"},
                {"type": "required_status_checks", "parameters": {"required_status_checks":
                    [{"context": c} for c in required]}}],
            f"repos/{repo}/commits/{head}/check-runs?per_page=100&page=1": {"check_runs": [
                {"id": i + 1, "name": c, "status": "completed", "conclusion": "success"}
                for i, c in enumerate(required)]},
            f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={head}&event=pull_request&per_page=30":
                {"workflow_runs": [ok_run]},
            f"repos/{repo}/actions/runs/7/artifacts?name={tested_marker(tree)}":
                {"artifacts": [{"name": tested_marker(tree), "expired": False}]},
            f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={sha}&event=push&per_page=30":
                {"workflow_runs": [push_run]},
            f"repos/{repo}/actions/runs/9/artifacts?name={tested_marker(tree)}":
                {"artifacts": [{"name": tested_marker(tree), "expired": False}]},
        }

    def run(w: dict, *, event="push", ref=MAIN_REF, git_tree=tree, git_fails=False) -> dict:
        def git(_args):
            if git_fails:
                raise ProofError("git said no")
            return git_tree + "\n"

        def gh(path):
            if path not in w:
                raise ProofError(f"404 {path}")
            return copy.deepcopy(w[path])
        return prove_tested(event=event, sha=sha, ref=ref, repo=repo, workflow=wf,
                            git=git, gh=gh, now=now)

    # POSITIVE CONTROLS.
    v = run(world())
    check("tested: same tree + all required green + full-scope run -> TESTED", v["scope"], TESTED)
    check("tested: the summary line names the matched PR head and run",
          all(x in decision_line(v) for x in ("PR #42", head[:12], "run 7")), True)
    check("tested: the 06:00 schedule on a sha a fresh full push run passed -> TESTED",
          run(world(), event="schedule")["scope"], TESTED)

    # TREE.
    check("tested refused: PR head tree differs from main's tree",
          run(world(), git_tree="d" * 40)["scope"], "full")
    check("...and the refusal says the tree differs",
          "tree differs" in run(world(), git_tree="d" * 40)["why"][0], True)
    w = world()
    w[f"repos/{repo}/actions/runs/7/artifacts?name={tested_marker(tree)}"] = {"artifacts": []}
    check("tested refused: the PR run tested a DIFFERENT merge tree (no marker for this one)",
          run(w)["scope"], "full")

    # REQUIRED CHECKS.
    for label, mutate in (
        ("one required check FAILED", lambda r: r[0].update(conclusion="failure")),
        ("one required check still RUNNING", lambda r: r[0].update(status="in_progress",
                                                                   conclusion=None)),
        ("one required check SKIPPED", lambda r: r[0].update(conclusion="skipped")),
        ("one required check MISSING", lambda r: r.pop(0)),
    ):
        w = world()
        mutate(w[f"repos/{repo}/commits/{head}/check-runs?per_page=100&page=1"]["check_runs"])
        check(f"tested refused: {label}", run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/commits/{head}/check-runs?per_page=100&page=1"]["check_runs"].append(
        {"id": 99, "name": required[0], "status": "completed", "conclusion": "failure"})
    check("tested refused: the NEWEST run of a required check is red (older green ignored)",
          run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/rules/branches/main"] = [{"type": "deletion"}]
    check("tested refused: the branch rules name no required checks", run(w)["scope"], "full")

    # A REQUIRED CONTEXT THAT IS A COMMIT STATUS, NOT A CHECK RUN (`merge-cage`).
    def with_status(state: str | None, *, pages: int = 0) -> dict:
        w = world()
        w[f"repos/{repo}/rules/branches/main"][1]["parameters"]["required_status_checks"].append(
            {"context": "merge-cage"})
        filler = [{"context": f"other-{i}", "state": "success"} for i in range(100)]
        for page in range(1, pages + 1):
            w[f"repos/{repo}/commits/{head}/status?per_page=100&page={page}"] = {"statuses": filler}
        mine = [] if state is None else [{"context": "merge-cage", "state": state}]
        w[f"repos/{repo}/commits/{head}/status?per_page=100&page={pages + 1}"] = {"statuses": mine}
        return w
    v = run(with_status("success"))
    check("tested: a required context present only as a SUCCESS status -> TESTED",
          v["scope"], TESTED)
    for state in ("pending", "failure", "error"):
        check(f"tested refused: the required status is {state}",
              run(with_status(state))["scope"], "full")
    v = run(with_status(None))
    check("tested refused: a required context that is neither a check run nor a status",
          v["scope"], "full")
    check("...and the refusal names it missing", "merge-cage=missing" in v["why"][0], True)
    check("tested: the success status found on page 2 of the statuses -> TESTED",
          run(with_status("success", pages=1))["scope"], TESTED)
    check("tested refused: more than 500 statuses (a partial list is never read)",
          run(with_status("success", pages=5))["scope"], "full")

    # THE PR.
    w = world()
    w[f"repos/{repo}/commits/{sha}/pulls"] = []
    check("tested refused: no PR (a direct push to main)", run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/commits/{sha}/pulls"][0]["merge_commit_sha"] = "e" * 40
    check("tested refused: the PR's merge commit is not this sha", run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/commits/{sha}/pulls"][0]["merged_at"] = None
    check("tested refused: the PR is not merged", run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/commits/{sha}/pulls"].append(
        copy.deepcopy(w[f"repos/{repo}/commits/{sha}/pulls"][0]))
    check("tested refused: two PRs claim this merge commit", run(w)["scope"], "full")

    # THE EVIDENCE RUN.
    w = world()
    w[f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={head}&event=pull_request&per_page=30"][
        "workflow_runs"][0]["conclusion"] = "failure"
    check("tested refused: this workflow's run on the PR head FAILED", run(w)["scope"], "full")
    w = world()
    w[f"repos/{repo}/actions/runs/7/artifacts?name={tested_marker(tree)}"]["artifacts"][0][
        "expired"] = True
    check("tested refused: the marker artifact has expired", run(w)["scope"], "full")

    # EVENTS, REFS, ERRORS.
    check("tested refused: workflow_dispatch is always full",
          run(world(), event="workflow_dispatch")["scope"], "full")
    check("tested refused: a pull_request event never proves tested",
          run(world(), event="pull_request")["scope"], "full")
    check("tested refused: a push to a ref that is not main",
          run(world(), ref="refs/heads/feature")["scope"], "full")
    w = world()
    del w[f"repos/{repo}/rules/branches/main"]
    check("tested refused: an API error is full, never a pass", run(w)["scope"], "full")
    check("tested refused: a git error is full", run(world(), git_fails=True)["scope"], "full")

    # THE SCHEDULE.
    w = world()
    w[f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={sha}&event=push&per_page=30"][
        "workflow_runs"] = []
    check("schedule stays full: no push run on this sha (main moved since)",
          run(w, event="schedule")["scope"], "full")
    w = world()
    w[f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={sha}&event=push&per_page=30"][
        "workflow_runs"][0]["updated_at"] = stale
    check(f"schedule stays full: the push run is older than {SCHEDULE_MAX_AGE_H}h",
          run(w, event="schedule")["scope"], "full")
    w = world()
    w[f"repos/{repo}/actions/runs/9/artifacts?name={tested_marker(tree)}"] = {"artifacts": []}
    check("schedule stays full: the push run was itself `tested` (no full-scope marker)",
          run(w, event="schedule")["scope"], "full")
    w = world()
    w[f"repos/{repo}/actions/workflows/{wf}/runs?head_sha={sha}&event=push&per_page=30"][
        "workflow_runs"][0]["conclusion"] = "failure"
    check("schedule stays full: the push run on this sha failed",
          run(w, event="schedule")["scope"], "full")

    # WIRING: every workflow that can answer `tested` uploads the marker, and
    # only from a `full` decision -- or the proof above can never be met (or,
    # worse, could be met by a run that skipped its heavy steps).
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
    for name in ("ci.yml", "ci-offline.yml"):
        text = (root / name).read_text(encoding="utf-8") if (root / name).exists() else ""
        check(f"wiring: {name} asks for `tested` with --workflow {name}",
              f"--workflow {name}" in text, True)
        check(f"wiring: {name} uploads {TESTED_ARTIFACT_PREFIX}<tree> only when scope is full",
              TESTED_ARTIFACT_PREFIX in text
              and "if: steps.decide.outputs.scope == 'full'" in text, True)
        # A negated condition (`scope != 'docs'`) quietly admits `tested` and
        # every later word: the heavy step would run on a proven tree. Steps
        # name the scopes they run on.
        check(f"wiring: {name} has no `scope != ...` step condition",
              "outputs.scope != " in text, False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Decide which CI steps a diff needs.")
    ap.add_argument("files", nargs="*", help="changed paths (alternative to --base/--head)")
    ap.add_argument("--event", default="pull_request",
                    help="GitHub event name; anything but pull_request is full")
    ap.add_argument("--base", help="base revision (with --head: run git diff)")
    ap.add_argument("--head", default="HEAD", help="head revision for --base")
    ap.add_argument("--print-scope", action="store_true",
                    help="stdout carries only the scope word (JSON verdict goes to stderr)")
    ap.add_argument("--summary", action="store_true",
                    help="append a markdown verdict to $GITHUB_STEP_SUMMARY")
    ap.add_argument("--drill", action="store_true", help="break the classifier on purpose")
    # `tested` lookups (push / schedule only). Without --workflow nothing is
    # looked up and a non-PR event stays `full`, exactly as before.
    ap.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""),
                    help="the commit this run checked out")
    ap.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""), help="e.g. refs/heads/main")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name")
    ap.add_argument("--workflow", default="",
                    help="this workflow's file name (e.g. ci-offline.yml): enables `tested`")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    if args.event in ("push", "schedule") and args.workflow:
        import time
        verdict = prove_tested(event=args.event, sha=args.sha, ref=args.ref, repo=args.repo,
                               workflow=args.workflow, git=_git, gh=_gh, now=time.time())
        if verdict["scope"] != TESTED and "could not prove" in verdict["why"][0]:
            print(f"::warning::ci_scope: {verdict['why'][0]}", file=sys.stderr)
        _emit(verdict, args.print_scope, args.summary)
        return 0

    if args.event != "pull_request":
        _emit({"scope": "full",
               "why": [f"event `{args.event}` is not a pull request -- main is production, "
                       "so a push, a schedule or a dispatch runs the full gate unless it "
                       "can PROVE `tested`"],
               "by_kind": {}, "matched": {"sha": args.sha}}, args.print_scope, args.summary)
        return 0

    files: list[str] | None
    if args.base:
        files = changed_files(args.base, args.head)
        if files is None:
            # FAIL SAFE, LOUDLY. The safe direction is more checks, not fewer --
            # but a warning in the log so a permanently broken diff does not hide
            # the fast lane forever.
            print("::warning::ci_scope: git diff failed -- falling back to the full gate",
                  file=sys.stderr)
            _emit({"scope": "full", "why": ["git diff failed -- refusing to guess"],
                   "by_kind": {}}, args.print_scope, args.summary)
            return 0
    else:
        files = list(args.files)

    _emit(classify(files), args.print_scope, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
