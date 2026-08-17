#!/usr/bin/env python3
"""Would `main-production-gate` actually stop a merge — or just wedge the repo?

WHAT THIS IS FOR
----------------
Repository ruleset 20793798 names ELEVEN required status checks and sits at
`enforcement: "disabled"`. `main` has no branch protection either (measured:
`gh api repos/Ranjith36963/job360/branches/main/protection` -> 404). So nothing
requires CI green to merge today. The ruleset is a request, not a guarantee.

The obvious fix — flip it to `active` — carries a failure worse than the current
state, and it is the same shape as the ten guards that shipped here unable to fire:

    a required status check is matched by CONTEXT NAME.

Name something no job reports and that check can never be satisfied. GitHub does
not error, warn or time out; the PR reads "Expected — waiting for status to be
reported" forever. One admin, an auto-deploying default branch: that is a
self-inflicted outage.

So this script does NOT check "is enforcement on". That is one boolean and it
passes forever once flipped — a guard reporting success about a different question.
It checks the load-bearing one:

    would every required context actually BE REPORTED, by the app the rule pins,
    on the commits this gate will judge?

THE HONEST COMPLICATION
-----------------------
A check can be legitimately absent from a commit, and confusing that with "the
context is dead" gets you wrong in both directions: permissive wedges the repo,
strict cries wolf until the alarm is ignored.

Measured here on 2026-08-17: 7 of the last 30 commits on `main` carry no CI at
all. Not a bug — only the TIP of a push gets a workflow run, so the commits
underneath it never do. No ruleset will ever judge those commits.

The population a required check actually acts on is PULL REQUEST HEAD COMMITS. So
presence is measured there and nowhere else, and the two verdicts are kept apart:

    never observed on ANY sampled head          -> DEAD_CONTEXT   (wedge)
    observed, but never from the pinned app     -> WRONG_APP      (wedge)
    observed on some heads, missing from others -> INTERMITTENT   (wedge; the
                                                    PRs it is missing from would
                                                    hang, so this is not a nit)
    absent from a non-PR main commit            -> SILENCE

WHY IT READS A RECORDED SNAPSHOT
--------------------------------
The offline `--check` reads a committed snapshot so the PR gate needs no network
and no token. A snapshot nobody refreshes is a guard certifying a world that has
moved, so the snapshot carries its own fetch time and goes RED when stale. `--live`
re-reads GitHub and rewrites it.

WHAT THIS SCRIPT WILL NOT DO
----------------------------
It never writes to GitHub. `--enable-plan` PRINTS the exact `gh api` call that
flips enforcement and the exact call that reverts it, and writes both request
bodies to disk. Enabling is the owner's action. The rollback matters more than the
enable, so the rollback body is written FIRST and from the live ruleset, which
makes it a true restore rather than a reconstruction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_SLUG = "Ranjith36963/job360"
RULESET_ID = 20793798
SNAPSHOT = ROOT / ".github" / "rulesets" / "main-gate-snapshot.json"

# How many merged-PR head commits form the presence population. Small enough to
# stay a cheap API read, large enough that one unusual PR cannot swing a verdict.
SAMPLE_PRS = 24
# The window that decides INTERMITTENT. A check added last week is present on
# every head SINCE it was added and absent from everything before — judging it
# over the whole history would permanently mislabel every new check as flaky.
RECENT_HEADS = 8
MAX_SNAPSHOT_AGE_DAYS = 14

WEDGE = "wedge"
WARN = "warn"
INFO = "info"


# ─────────────────────────────────────────────────────────────────────────────
# The world, as data. Everything below `analyze` is pure and offline, so the
# drill can break it on purpose without touching the network.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RequiredContext:
    context: str
    integration_id: int | None = None


@dataclass(frozen=True)
class CheckRun:
    name: str
    app_id: int | None
    conclusion: str | None


@dataclass(frozen=True)
class Observation:
    """One commit and the check-runs GitHub actually reported on it."""

    sha: str
    label: str
    is_pr_head: bool
    number: int | None
    author: str
    runs: tuple[CheckRun, ...]


@dataclass(frozen=True)
class World:
    fetched_at: dt.datetime
    pr_heads: tuple[Observation, ...] = ()
    main_commits: tuple[Observation, ...] = ()
    open_prs: tuple[Observation, ...] = ()


@dataclass(frozen=True)
class Ruleset:
    ruleset_id: int
    name: str
    enforcement: str
    required: tuple[RequiredContext, ...]
    bypass_actors: tuple = ()
    include_refs: tuple = ()


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    detail: str


# ─────────────────────────────────────────────────────────────────────────────
# THE ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────


def _allowed_apps(ruleset: Ruleset) -> set[int]:
    """The integration ids the ruleset pins. Empty means it pins none."""
    return {rc.integration_id for rc in ruleset.required if rc.integration_id is not None}


def _passing(conclusion: str | None) -> bool:
    # GitHub treats a skipped/neutral check as satisfying a required check. That
    # is why an always-skipped job must never be REQUIRED and never OFFERED as a
    # candidate: it would install a guard that cannot fail.
    return conclusion in {"success", "skipped", "neutral", None}


def analyze(
    ruleset: Ruleset,
    world: World,
    *,
    require_active: bool = False,
    max_snapshot_age_days: int = MAX_SNAPSHOT_AGE_DAYS,
    recent_heads: int = RECENT_HEADS,
) -> list[Finding]:
    """Every way this ruleset could wedge the repo, named. Pure; no network."""
    out: list[Finding] = []
    allowed = _allowed_apps(ruleset)

    # ── 0. is the evidence itself still current? ─────────────────────────────
    if world.fetched_at is not None:
        age = dt.datetime.now(dt.timezone.utc) - world.fetched_at
        if age > dt.timedelta(days=max_snapshot_age_days):
            out.append(Finding(
                WEDGE, "STALE_SNAPSHOT",
                f"the recorded evidence is {age.days} days old (limit "
                f"{max_snapshot_age_days}). Every verdict below describes a world that "
                f"has moved. Refresh with `python scripts/ruleset_gate.py --live`.",
            ))

    # ── 1. heads that carry no checks at all ─────────────────────────────────
    # Reported once per commit rather than once per missing context: eleven
    # INTERMITTENT lines about one commit that never ran anything hides the cause.
    def _has_any(obs: Observation) -> bool:
        return any(r.app_id in allowed or not allowed for r in obs.runs)

    usable_heads = []
    for obs in world.pr_heads:
        if _has_any(obs):
            usable_heads.append(obs)
        else:
            out.append(Finding(
                WEDGE, "MERGED_PR_NO_CHECKS",
                f"{obs.label} ({obs.sha[:8]}, by {obs.author}) merged into main with NO "
                f"check-runs from the pinned app. It could not have satisfied a single "
                f"required context — the gate would have blocked it forever.",
            ))

    for obs in world.open_prs:
        if not _has_any(obs):
            out.append(Finding(
                WEDGE, "OPEN_PR_NO_CHECKS",
                f"open {obs.label} (by {obs.author}) has NO check-runs at all, so it can "
                f"never satisfy any required context. GitHub does not start workflows for "
                f"commits pushed with the default GITHUB_TOKEN, which is how every PR the "
                f"harness loops open arrives. Under an active ruleset those PRs become "
                f"permanently unmergeable and the automation silently stops landing.",
            ))

    recent = usable_heads[:recent_heads]

    # ── 2. every required context, against what was actually reported ────────
    for rc in ruleset.required:
        by_name = [r for obs in world.pr_heads + world.main_commits for r in obs.runs if r.name == rc.context]
        if not by_name:
            out.append(Finding(
                WEDGE, "DEAD_CONTEXT",
                f"required context {rc.context!r} has never been reported on ANY sampled "
                f"commit. Nothing produces that name, so the check can never go green and "
                f"nothing could ever merge. This is the lockout.",
            ))
            continue

        right_app = [r for r in by_name if rc.integration_id is None or r.app_id == rc.integration_id]
        if not right_app:
            saw = sorted({str(r.app_id) for r in by_name})
            out.append(Finding(
                WEDGE, "WRONG_APP",
                f"required context {rc.context!r} IS reported, but only by app(s) "
                f"{', '.join(saw)} — the rule pins integration_id {rc.integration_id}. A "
                f"required check matches on app as well as name, so this waits for a report "
                f"the pinned app will never send.",
            ))
            continue

        if recent:
            missing = [o for o in recent
                       if not any(r.name == rc.context
                                  and (rc.integration_id is None or r.app_id == rc.integration_id)
                                  for r in o.runs)]
            if missing:
                out.append(Finding(
                    WEDGE, "INTERMITTENT",
                    f"required context {rc.context!r} reported on only "
                    f"{len(recent) - len(missing)}/{len(recent)} recent PR heads; absent from "
                    f"{', '.join(o.label for o in missing)}. A PR it is absent from waits "
                    f"forever. Give the producing job a presence contract (run it "
                    f"unconditionally, or emit a skipped placeholder) before requiring it.",
                ))

        dupes = [o for o in recent if sum(1 for r in o.runs if r.name == rc.context) > 1]
        if dupes:
            out.append(Finding(
                WARN, "AMBIGUOUS_CONTEXT",
                f"required context {rc.context!r} matches MORE THAN ONE check-run on "
                f"{dupes[0].label}. Which run satisfies the rule is not something this "
                f"script can determine, and it is not something to discover during an "
                f"outage. Rename the jobs so one context means one run.",
            ))

    # ── 3. the reverse question: what runs on everything and is NOT required ──
    required_names = {rc.context for rc in ruleset.required}
    if recent:
        seen: dict[str, list[CheckRun]] = {}
        for obs in recent:
            for r in obs.runs:
                if allowed and r.app_id not in allowed:
                    continue  # a third-party app cannot satisfy an Actions-pinned rule
                seen.setdefault(r.name, []).append(r)
        for name, runs in sorted(seen.items()):
            if name in required_names:
                continue
            if all(r.conclusion == "skipped" for r in runs):
                continue  # vacuous: a skipped check satisfies a required check
            if len(runs) == len(recent):
                out.append(Finding(
                    INFO, "UNREQUIRED_CANDIDATE",
                    f"{name!r} reported on all {len(recent)} recent PR heads and is NOT "
                    f"required. It runs on every PR already, so requiring it costs nothing "
                    f"and closes a hole.",
                ))
                continue

            # NEW is not FLAKY, and collapsing the two loses the check that
            # matters most. `Chain wires (harness)` landed 2026-08-16 and has
            # only had three PRs to report on; judged by "present on every head"
            # it would be silently dropped — and it is the one job proving the
            # guards can still fire. A check present on the most recent heads
            # CONTIGUOUSLY was added; one that flickers on and off was not.
            present = [any(r.name == name for r in o.runs) for o in recent]
            streak = 0
            for p in present:
                if not p:
                    break
                streak += 1
            if streak >= 2 and streak == sum(present):
                out.append(Finding(
                    INFO, "NEW_CHECK",
                    f"{name!r} is NEW: it has reported on the {streak} most recent PR heads "
                    f"and on none before them, so its {streak}/{len(recent)} rate is age, not "
                    f"flakiness. Requiring it is safe once it has run on a full window; "
                    f"requiring it today is also safe, because it runs unconditionally.",
                ))
                continue

            # Present, absent, present. Two causes produce this exact shape and
            # check-run data CANNOT tell them apart:
            #   * the job is genuinely flaky/conditional, or
            #   * the job is new and a PR branched BEFORE it merged from a stale
            #     base, so its head never carried the job at all.
            # Measured: `Chain wires (harness)` landed in PR #333 at 08:30:41Z and
            # PR #273 merged at 08:35:57Z off an older branch, producing exactly
            # this. The consequence is identical either way — require it today and
            # every un-rebased branch hangs — so this is REPORTED with the gap
            # named, and never turned into a recommendation.
            missing = [o.label for o, p in zip(recent, present) if not p]
            out.append(Finding(
                WARN, "UNREQUIRED_PARTIAL",
                f"{name!r} reported on {sum(present)}/{len(recent)} recent PR heads, absent "
                f"from {', '.join(missing)}, and NOT contiguous — so this is either a flaky "
                f"job or a new one that older branches never ran. This script cannot tell "
                f"those apart from check-run data. Do NOT require it until the gap is "
                f"explained: whichever cause it is, an un-rebased PR would hang forever.",
            ))

    # ── 4. enforcement, reported — never mistaken for the verdict ────────────
    if ruleset.enforcement != "active":
        out.append(Finding(
            WEDGE if require_active else INFO, "ENFORCEMENT_OFF",
            f"ruleset {ruleset.name!r} is at enforcement={ruleset.enforcement!r}. It names "
            f"{len(ruleset.required)} required checks and stops nothing. Note this is NOT "
            f"the same claim as 'main is unsafe once enabled': main auto-deploys on merge, "
            f"so an active ruleset stops broken code MERGING and does nothing about correct "
            f"code deployed at the wrong moment.",
        ))

    for actor in ruleset.bypass_actors or ():
        if isinstance(actor, dict) and actor.get("bypass_mode") == "always":
            out.append(Finding(
                INFO, "BYPASS_ACTOR",
                f"{actor.get('actor_type')} id={actor.get('actor_id')} bypasses this ruleset "
                f"always. This is the escape hatch that makes enabling recoverable — but "
                f"confirm the role NAME in the UI (Settings -> Rules), because this script "
                f"reads a numeric id and cannot resolve it to a role.",
            ))

    return out


# Findings about the live PR QUEUE rather than the ruleset's own configuration.
# A PR author cannot fix a bot PR sitting in the queue, so failing their build on
# it is how an alarm gets ignored and then switched off. They are still printed
# in full — only the exit code is scoped.
QUEUE_CODES = {"OPEN_PR_NO_CHECKS", "MERGED_PR_NO_CHECKS"}


def blocking(findings: list[Finding], scope: str = "all") -> list[Finding]:
    """The findings that should turn a build RED, for the given audience."""
    wedges = [f for f in findings if f.level == WEDGE and f.code != "ENFORCEMENT_OFF"]
    if scope == "ruleset":
        return [f for f in wedges if f.code not in QUEUE_CODES]
    return wedges


def safe_to_enable(findings: list[Finding]) -> bool:
    """True only when nothing here would wedge a merge.

    ENFORCEMENT_OFF is excluded on purpose: enforcement being off is the
    PRECONDITION for enabling, not an objection to it. Nothing else is excluded —
    this is the owner-facing verdict, so it uses the WIDEST scope.
    """
    return not blocking(findings, scope="all")


# ─────────────────────────────────────────────────────────────────────────────
# READING GITHUB. Read-only, always. Never a write.
# ─────────────────────────────────────────────────────────────────────────────


def _gh(path: str) -> object:
    proc = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        encoding="utf-8",   # `gh` prints em-dashes; text=True alone decodes with the
        errors="replace",   # Windows locale and raises UnicodeDecodeError. Measured.
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path} -> {proc.returncode}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def fetch_ruleset(repo: str = REPO_SLUG, ruleset_id: int = RULESET_ID) -> tuple[Ruleset, dict]:
    raw = _gh(f"repos/{repo}/rulesets/{ruleset_id}")
    required: list[RequiredContext] = []
    for rule in raw.get("rules", []):
        if rule.get("type") == "required_status_checks":
            for c in rule.get("parameters", {}).get("required_status_checks", []):
                required.append(RequiredContext(c["context"], c.get("integration_id")))
    cond = (raw.get("conditions") or {}).get("ref_name", {})
    return Ruleset(
        ruleset_id=raw["id"],
        name=raw["name"],
        enforcement=raw["enforcement"],
        required=tuple(required),
        bypass_actors=tuple(raw.get("bypass_actors") or ()),
        include_refs=tuple(cond.get("include") or ()),
    ), raw


def _runs_for(repo: str, sha: str) -> tuple[CheckRun, ...]:
    data = _gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    return tuple(
        CheckRun(cr["name"], (cr.get("app") or {}).get("id"), cr.get("conclusion"))
        for cr in data.get("check_runs", [])
    )


def newest_merged_first(prs: list[dict]) -> list[dict]:
    """Order merged PRs by WHEN THEY MERGED, newest first.

    The API's `sort=updated` is not this: a comment on an old PR bumps it to the
    front. Measured while building this — #273 landed between #337 and #333,
    which broke the contiguity test and reported the genuinely-new
    `Chain wires (harness)` as flaky. The window only means "newest" if it is
    ordered by the event the window is about.
    """
    return sorted(prs, key=lambda p: (p.get("merged_at") or ""), reverse=True)


def fetch_world(repo: str = REPO_SLUG, sample: int = SAMPLE_PRS) -> World:
    heads: list[Observation] = []
    prs = _gh(f"repos/{repo}/pulls?state=closed&base=main&per_page={sample}&sort=updated&direction=desc")
    for p in newest_merged_first([x for x in prs if x.get("merged_at")]):
        heads.append(Observation(
            sha=p["head"]["sha"], label=f"PR #{p['number']}", is_pr_head=True,
            number=p["number"], author=p["user"]["login"], runs=_runs_for(repo, p["head"]["sha"]),
        ))

    mains: list[Observation] = []
    for c in _gh(f"repos/{repo}/commits?sha=main&per_page=30"):
        mains.append(Observation(
            sha=c["sha"], label=f"main {c['sha'][:8]}", is_pr_head=False, number=None,
            author=(c.get("author") or {}).get("login") or "unknown",
            runs=_runs_for(repo, c["sha"]),
        ))

    opens: list[Observation] = []
    for p in _gh(f"repos/{repo}/pulls?state=open&base=main&per_page=50"):
        opens.append(Observation(
            sha=p["head"]["sha"], label=f"PR #{p['number']}", is_pr_head=True,
            number=p["number"], author=p["user"]["login"], runs=_runs_for(repo, p["head"]["sha"]),
        ))

    return World(dt.datetime.now(dt.timezone.utc), tuple(heads), tuple(mains), tuple(opens))


# ── snapshot round-trip ──────────────────────────────────────────────────────


def _obs_to_json(o: Observation) -> dict:
    return {"sha": o.sha, "label": o.label, "is_pr_head": o.is_pr_head, "number": o.number,
            "author": o.author,
            "runs": [{"name": r.name, "app_id": r.app_id, "conclusion": r.conclusion} for r in o.runs]}


def _obs_from_json(d: dict) -> Observation:
    return Observation(d["sha"], d["label"], d["is_pr_head"], d.get("number"), d.get("author", "?"),
                       tuple(CheckRun(r["name"], r["app_id"], r["conclusion"]) for r in d["runs"]))


def save_snapshot(ruleset_raw: dict, world: World, path: Path = SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_what": "Recorded evidence for scripts/ruleset_gate.py. Refresh with --live.",
        "fetched_at": world.fetched_at.isoformat(),
        "ruleset": ruleset_raw,
        "pr_heads": [_obs_to_json(o) for o in world.pr_heads],
        "main_commits": [_obs_to_json(o) for o in world.main_commits],
        "open_prs": [_obs_to_json(o) for o in world.open_prs],
    }, indent=1), encoding="utf-8")


def load_snapshot(path: Path = SNAPSHOT) -> tuple[Ruleset, World, dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    raw = d["ruleset"]
    required = [RequiredContext(c["context"], c.get("integration_id"))
                for rule in raw.get("rules", []) if rule.get("type") == "required_status_checks"
                for c in rule.get("parameters", {}).get("required_status_checks", [])]
    cond = (raw.get("conditions") or {}).get("ref_name", {})
    rs = Ruleset(raw["id"], raw["name"], raw["enforcement"], tuple(required),
                 tuple(raw.get("bypass_actors") or ()), tuple(cond.get("include") or ()))
    world = World(
        dt.datetime.fromisoformat(d["fetched_at"]),
        tuple(_obs_from_json(o) for o in d["pr_heads"]),
        tuple(_obs_from_json(o) for o in d["main_commits"]),
        tuple(_obs_from_json(o) for o in d["open_prs"]),
    )
    return rs, world, raw


# ─────────────────────────────────────────────────────────────────────────────
# THE ENABLE / ROLLBACK PLAN. Printed, never run.
# ─────────────────────────────────────────────────────────────────────────────


def _body(raw: dict, enforcement: str) -> dict:
    """A clean PUT body. The GET response carries `id`, `node_id`, `_links`,
    `created_at` and `source`, which are server-owned; echoing them back is at
    best ignored and at worst rejected. Only the five settable fields go out, and
    every one is copied from the LIVE ruleset — so the rollback body is a
    restore, not a reconstruction from memory."""
    return {
        "name": raw["name"],
        "target": raw.get("target", "branch"),
        "enforcement": enforcement,
        "bypass_actors": raw.get("bypass_actors", []),
        "conditions": raw.get("conditions", {}),
        "rules": raw.get("rules", []),
    }


def enable_plan(raw: dict, outdir: Path) -> str:
    outdir.mkdir(parents=True, exist_ok=True)
    now = raw["enforcement"]
    files = {}
    for mode in ("disabled", "evaluate", "active"):
        p = outdir / f"ruleset-{raw['id']}-{mode}.json"
        p.write_text(json.dumps(_body(raw, mode), indent=1), encoding="utf-8")
        files[mode] = p.relative_to(ROOT).as_posix()
    rollback = files[now] if now in files else files["disabled"]

    return f"""
THE ROLLBACK FIRST. Nothing below should be run until this one is understood.
It restores enforcement={now!r} — the state measured right now — from a body
copied out of the live ruleset:

    gh api --method PUT repos/{REPO_SLUG}/rulesets/{raw['id']} \\
      --input {rollback}

  Rollback takes effect immediately; a ruleset is not cached. And note the real
  safety net: rulesets have no "include administrators" switch, so an admin can
  always PUT this back. Being locked out is recoverable in one command — which is
  why the honest risk here is a WEDGED QUEUE, not a locked door.

STEP 1 — DRY RUN. `evaluate` records what WOULD have been blocked and blocks
nothing. Run it and leave it for a week:

    gh api --method PUT repos/{REPO_SLUG}/rulesets/{raw['id']} \\
      --input {files['evaluate']}

STEP 2 — READ WHAT IT WOULD HAVE BLOCKED. Empty output means it recorded nothing,
which is NOT the same as "it approved everything":

    gh api "repos/{REPO_SLUG}/rulesets/rule-suites?ref=refs/heads/main&per_page=50"

STEP 3 — ONLY IF STEP 2 IS CLEAN AND THIS SCRIPT SAYS SAFE:

    gh api --method PUT repos/{REPO_SLUG}/rulesets/{raw['id']} \\
      --input {files['active']}

STEP 4 — PROVE IT IS ON, rather than assuming the PUT did what you meant:

    gh api repos/{REPO_SLUG}/rulesets/{raw['id']} --jq .enforcement
"""


# ─────────────────────────────────────────────────────────────────────────────
# THE DRILL. Break it on purpose; demand red. Plus controls that must stay quiet.
# ─────────────────────────────────────────────────────────────────────────────


def _mk(names, *, app=15368, conclusion="success", label="PR #1", pr=True, author="human", number=1):
    return Observation(
        sha=f"{number:040d}", label=label, is_pr_head=pr, number=number, author=author,
        runs=tuple(CheckRun(n, app, conclusion) if isinstance(n, str) else CheckRun(*n) for n in names),
    )


def self_drill() -> int:
    print("DRILL - breaking the ruleset checker on purpose. It must go RED and name why.")
    print("=" * 74)
    G = ["Backend (Python 3.12)", "Frontend (Node 20)", "offline-suite"]
    app = 15368

    def rs(contexts, enforcement="disabled", pin=app):
        return Ruleset(1, "drill", enforcement,
                       tuple(RequiredContext(c, pin) for c in contexts),
                       ({"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"},),
                       ("~DEFAULT_BRANCH",))

    def world(heads, mains=(), opens=(), age_days=0):
        return World(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days),
                     tuple(heads), tuple(mains), tuple(opens))

    healthy = world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)])
    results: list[tuple[str, bool, str]] = []

    def expect(name, findings, code, level=WEDGE):
        hit = next((f for f in findings if f.code == code and f.level == level), None)
        results.append((name, hit is not None, hit.detail if hit else f"expected {code}/{level}, got "
                        + (", ".join(sorted({f.code for f in findings})) or "nothing")))

    def expect_quiet(name, findings):
        bad = [f for f in findings if f.level == WEDGE]
        results.append((name, not bad, "" if not bad else f"expected silence, got {bad[0].code}: {bad[0].detail[:90]}"))

    # 1. THE LOCKOUT. A required context nothing on earth reports.
    expect("required context that no job reports",
           analyze(rs(G + ["Analyze (python)"]), healthy), "DEAD_CONTEXT")

    # 2. Right name, WRONG APP — reported, but not by the pinned integration.
    codeql_world = world([_mk(G + [("CodeQL", 57789, "success")], number=i, label=f"PR #{i}")
                          for i in range(1, 9)])
    expect("context reported only by a different app than the rule pins",
           analyze(rs(G + ["CodeQL"]), codeql_world), "WRONG_APP")

    # 3. Present on most heads, missing from one -> that PR hangs forever.
    flaky = world([_mk(G, number=1, label="PR #1"), _mk(G[:-1], number=2, label="PR #2")]
                  + [_mk(G, number=i, label=f"PR #{i}") for i in range(3, 9)])
    expect("required context missing from ONE recent PR head", analyze(rs(G), flaky), "INTERMITTENT")

    # 4. NEGATIVE CONTROL for 3, and the whole reason this is not a wolf-crier:
    #    the SAME absence on a non-PR main commit must produce nothing. Measured,
    #    7 of 30 main commits look like this and no ruleset will ever judge them.
    expect_quiet("NEGATIVE CONTROL: same context absent from non-PR main commits",
                 analyze(rs(G), world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)],
                                      mains=[_mk([], pr=False, number=90), _mk([], pr=False, number=91)])))

    # 5. An open PR with no checks at all — the GITHUB_TOKEN trap, live today.
    expect("open PR with zero check-runs (bot-authored, no workflow ever starts)",
           analyze(rs(G), world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)],
                                opens=[_mk([], label="PR #342", author="github-actions[bot]", number=342)])),
           "OPEN_PR_NO_CHECKS")

    # 6. NEGATIVE CONTROL for 5: a merely RED PR is the gate WORKING, not a wedge.
    expect_quiet("NEGATIVE CONTROL: an open PR whose checks ran and FAILED",
                 analyze(rs(G), world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)],
                                      opens=[_mk(G, conclusion="failure", label="PR #268", number=268)])))

    # 7. Stale evidence must not certify a world that has moved.
    expect("evidence older than the freshness limit",
           analyze(rs(G), world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)], age_days=45)),
           "STALE_SNAPSHOT")

    # 8. NEGATIVE CONTROL: a fully healthy world must be silent.
    expect_quiet("NEGATIVE CONTROL: healthy ruleset over healthy observations", analyze(rs(G), healthy))

    # 9. NEGATIVE CONTROL: an unrelated ruleset field must not move the verdict.
    a = sorted(f.code for f in analyze(rs(G), healthy))
    b = sorted(f.code for f in analyze(replace(rs(G), name="renamed"), healthy))
    results.append(("NEGATIVE CONTROL: renaming the ruleset changes nothing", a == b,
                    "" if a == b else f"{a} != {b}"))

    # 10. The reverse question: a check that runs everywhere and is not required.
    chain = world([_mk(G + ["Chain wires (harness)"], number=i, label=f"PR #{i}") for i in range(1, 9)])
    expect("a check running on every PR head that is NOT required is named",
           analyze(rs(G), chain), "UNREQUIRED_CANDIDATE", INFO)

    # 11. NEGATIVE CONTROL: an always-skipped check must NOT be offered. Requiring
    #     one installs a guard that cannot fail — this repo's signature bug.
    skipped = world([_mk(G + [("auto-fix", app, "skipped")], number=i, label=f"PR #{i}") for i in range(1, 9)])
    offered = " ".join(f.detail for f in analyze(rs(G), skipped) if f.code == "UNREQUIRED_CANDIDATE")
    results.append(("NEGATIVE CONTROL: an always-skipped check is not offered as a candidate",
                    "auto-fix" not in offered, offered[:120]))

    # 12. A brand-new check must be named as NEW, not silently dropped for having
    #     a low presence rate. This is the case that nearly hid `Chain wires`.
    new = world([_mk(G + ["Chain wires (harness)"], number=i, label=f"PR #{i}") for i in (1, 2, 3)]
                + [_mk(G, number=i, label=f"PR #{i}") for i in range(4, 9)])
    expect("a check present only on the newest heads is reported as NEW, not dropped",
           analyze(rs(G), new), "NEW_CHECK", INFO)

    # 13. NEGATIVE CONTROL for 12: on/off/on is flaky, not new, and recommending
    #     it would be recommending a check that wedges the PRs it skips.
    flick = world([_mk(G + ["flaky"], number=1, label="PR #1"), _mk(G, number=2, label="PR #2"),
                   _mk(G + ["flaky"], number=3, label="PR #3")]
                  + [_mk(G, number=i, label=f"PR #{i}") for i in range(4, 9)])
    said = " ".join(f.detail for f in analyze(rs(G), flick) if f.code in {"NEW_CHECK", "UNREQUIRED_CANDIDATE"})
    results.append(("NEGATIVE CONTROL: a flickering check is not called new", "flaky" not in said, said[:120]))

    # 13b. ...but it must not be SWALLOWED either. A gap nobody is told about is
    #      how `Chain wires (harness)` nearly vanished from this report.
    expect("a present-absent-present check is still reported, with the gap named",
           analyze(rs(G), flick), "UNREQUIRED_PARTIAL", WARN)

    # 14. The PR-gate scope must mute the live queue WITHOUT becoming a way to
    #     pass. Muting the wrong thing here is how a gate quietly stops gating.
    q = analyze(rs(G), world([_mk(G, number=i, label=f"PR #{i}") for i in range(1, 9)],
                             opens=[_mk([], label="PR #342", author="github-actions[bot]", number=342)]))
    d = analyze(rs(G + ["nothing-emits-this"]), healthy)
    scoped_ok = not blocking(q, scope="ruleset") and blocking(q, scope="all")
    scoped_strict = bool(blocking(d, scope="ruleset"))
    results.append(("PR-gate scope mutes the live queue but still fails on a dead context",
                    scoped_ok and scoped_strict,
                    f"queue-muted={not blocking(q, 'ruleset')} dead-still-red={scoped_strict}"))

    # 15. safe_to_enable must not be a rubber stamp.
    ok_clean = safe_to_enable(analyze(rs(G), healthy))
    ok_dead = safe_to_enable(analyze(rs(G + ["nothing-emits-this"]), healthy))
    results.append(("safe_to_enable says NO on a dead context and YES on a clean world",
                    ok_clean and not ok_dead, f"clean={ok_clean} dead={ok_dead}"))

    print()
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if detail:
            print(f"         {'caught -> ' if ok else ''}{detail.splitlines()[0][:145]}")

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print("=" * 74)
    print(f"DRILL RESULT: {passed}/{len(results)} passed")
    if passed != len(results):
        print("The checker did NOT catch something it claims to catch. Do not trust its green.")
        return 1
    print("Every deliberate break was caught and named; every harmless world stayed quiet.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────


def _report(ruleset: Ruleset, world: World, findings: list[Finding], scope: str = "all") -> None:
    print(f"ruleset {ruleset.ruleset_id} {ruleset.name!r} - enforcement={ruleset.enforcement!r}, "
          f"{len(ruleset.required)} required contexts, refs {list(ruleset.include_refs)}")
    print(f"evidence: {len(world.pr_heads)} merged-PR heads, {len(world.main_commits)} main commits, "
          f"{len(world.open_prs)} open PRs, fetched {world.fetched_at:%Y-%m-%d %H:%M}Z")
    print()
    for level in (WEDGE, WARN, INFO):
        rows = [f for f in findings if f.level == level]
        if not rows:
            continue
        print(f"{level.upper()} ({len(rows)})")
        print("-" * 74)
        for f in rows:
            print(f"  [{f.code}] {f.detail}")
        print()
    ok = safe_to_enable(findings)
    print("=" * 74)
    if ok:
        print("SAFE TO ENABLE: every required context was reported by the pinned app on every")
        print("recent PR head. Still go through `evaluate` first - see --enable-plan.")
    else:
        print("NOT SAFE TO ENABLE: at least one required context could not go green. Flipping")
        print("enforcement now would leave PRs pending forever with no error message.")
    if scope == "ruleset":
        muted = [f for f in findings if f.level == WEDGE and f.code in QUEUE_CODES]
        print(f"(exit code scoped to `ruleset`: {len(muted)} live-queue wedge(s) printed above "
              f"but not failing THIS build, because no commit here can fix them.)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="analyse the recorded snapshot (offline)")
    ap.add_argument("--live", action="store_true", help="re-read GitHub, rewrite the snapshot, analyse")
    ap.add_argument("--drill", action="store_true", help="break this checker on purpose; it must go red")
    ap.add_argument("--enable-plan", action="store_true", help="print the exact enable AND rollback calls")
    ap.add_argument("--require-active", action="store_true", help="treat enforcement != active as a wedge")
    ap.add_argument("--max-age-days", type=int, default=MAX_SNAPSHOT_AGE_DAYS)
    ap.add_argument("--scope", choices=("ruleset", "all"), default="all",
                    help="which findings set the EXIT CODE. `ruleset` = only what a commit can "
                         "cause (dead/wrong-app/intermittent contexts, stale evidence); `all` "
                         "adds the live PR queue. Everything is printed either way.")
    args = ap.parse_args(argv)

    if args.drill:
        return self_drill()

    if args.live:
        ruleset, raw = fetch_ruleset()
        world = fetch_world()
        save_snapshot(raw, world)
        print(f"snapshot refreshed -> {SNAPSHOT.relative_to(ROOT).as_posix()}")
    else:
        if not SNAPSHOT.is_file():
            print(f"NO SNAPSHOT at {SNAPSHOT}. Run `--live` once to record one. Refusing to "
                  f"report on evidence that does not exist.")
            return 1
        ruleset, world, raw = load_snapshot()

    if args.enable_plan:
        print(enable_plan(raw, ROOT / ".github" / "rulesets"))
        print("NOT RUN. Enabling is the owner's action.")
        return 0

    findings = analyze(ruleset, world, require_active=args.require_active,
                       max_snapshot_age_days=args.max_age_days)
    _report(ruleset, world, findings, scope=args.scope)
    return 1 if blocking(findings, scope=args.scope) else 0


if __name__ == "__main__":
    raise SystemExit(main())
