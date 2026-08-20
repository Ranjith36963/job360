#!/usr/bin/env python3
"""The merge cage — decides whether a PR may reach production without the owner.

WHAT MAKES THIS DIFFERENT FROM A NORMAL MERGE GATE
--------------------------------------------------
`main` auto-deploys. There is no staging. So "the agent merged it" means "the agent
shipped it to real users". The owner asked for exactly one thing to stay his:

    "The only thing I want to wait for me is the decisions: product decisions,
     users, any kind of decisions, infrastructure decisions."

This file is that sentence turned into something a machine can enforce.

WHY "CI IS GREEN + A REVIEW" IS NOT ENOUGH
-------------------------------------------
Three measured reasons, all from this repo:

1. GREEN IS WEAKER THAN IT LOOKS. Of the guards CI runs, most have never been
   watched failing (see scripts/drill_registry.py). Some of "green" means
   "nothing that could fire, fired." Ten guards have shipped here unable to fire.
   Measured again on 2026-08-16: PR #327's `CodeQL` check run said
   "1 new alert including 1 medium severity security vulnerability" and concluded
   SUCCESS. The instrument saw it, printed it, and went green.

2. THE REVIEW TICK IS NOT THE REVIEW. CodeRabbit runs with
   `fail_commit_status: false`, so its status check can only ever say pass. Gate
   on the COMMENTS, never the tick.

3. NO CHECK IN THIS REPO MEASURES GETTING WORSE. Lint and types are pass/fail
   against rules that already exist. Nothing notices quality sliding — which is
   precisely what unattended merging erodes. So the cage carries RATCHETS:
   numbers that may hold or improve, never regress.

THE FOUR CAGES, AND WHY EACH IS NEEDED
---------------------------------------
    SIZE     how much change at once            (blast radius by volume)
    PATH     what a change is allowed to touch  (blast radius by subject)
    PROOF    the checks really ran and passed   (is green real?)
    REVIEW   no unresolved review thread        (is the comment answered?)
    RATCHET  the numbers did not get worse      (no slow decay)

All must pass. Any doubt REFUSES — the default is to ask the owner, never to
ship. A cage that guesses in the permissive direction is not a cage.

THE ONE STRUCTURAL RULE THIS FILE NOW OBEYS
--------------------------------------------
A VERDICT LINE MAY ONLY BE PRINTED BY THE EVIDENCE THAT PRODUCED IT.

Each cage returns a `Verdict` carrying its own `claim` — the sentence the owner
is allowed to read if, and only if, that cage actually PASSED. `plain_english`
assembles the message from the claims of cages that passed and cannot invent one.
This is not tidiness. The old ALLOW message said "no quality number went
backwards" while the ratchet cage had compared nothing, because no caller ever
passed `--baseline`. That sentence is now impossible to write: a cage that
returned early reports NOT CHECKED, and NOT CHECKED refuses.

Every dead end this cage hit is recorded in scripts/cage_blockers.py, and
`--drill` fails if any of those rules has no drill case. Read that file first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `import cage_blockers` works

# Windows consoles default to cp1252. The reasons below are the thing a human
# reads to make a merge decision, so they must survive the pipe they are read
# through. (`--drill` output was arriving as mojibake on the owner's own box.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = os.environ.get("GH_REPO", "Ranjith36963/job360")

# ─────────────────────────────────────────────────────────────────────────────
# EXIT CODES — a typed channel, never overloaded.
#
# Crash and refuse both exited 1, which made auto-merge.yml's `*) merge_cage
# crashed` arm unreachable: a Python traceback always exits 1, so the crash
# detector could not fire for any crash that actually existed. A crash-detector
# that cannot fire is the eleventh dead guard, shipped inside the PR written to
# end that class. And argparse's own exit 2 collided with "could not reach
# Slack", so a bad flag aborted the whole sweep while blaming Slack.
# ─────────────────────────────────────────────────────────────────────────────

EXIT_ALLOW = 0
EXIT_REFUSE = 1  # a VERDICT, not an error
EXIT_CANNOT_TELL_OWNER = 2
EXIT_CAGE_BROKE = 3  # refused, because the cage itself failed
EXIT_USAGE = 4


class CageBroke(Exception):
    """The cage cannot judge anything — bad ground, self-contradictory rules.

    Distinct from a refusal: a refusal is about the PR, this is about the cage.
    """


# ─────────────────────────────────────────────────────────────────────────────
# GROUND — assert where we are standing before measuring anything.
#
# Run from a directory that was not the repo, the ratchets failed OPEN: mypy
# read 0 (the best possible value) because its command ended `else 0`, and the
# drill ratchet degraded to its own error sentinel 999, which no real value can
# exceed. Judging a PR from that cwd produced zero ratchet complaints. The cage
# could not tell it was measuring an empty tree.
# ─────────────────────────────────────────────────────────────────────────────


def repo_root() -> Path:
    """The repo this file belongs to. Raises CageBroke if that is not knowable."""
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception as exc:  # git missing, sandbox, anything
        raise CageBroke(f"cannot run git to find the repo root ({exc}) — refusing to judge") from exc
    if out.returncode != 0:
        raise CageBroke(
            "I am not inside a git repository, so I cannot tell which tree I would be "
            f"measuring. FIX: run me from a checkout of {REPO}."
        )
    root = Path(out.stdout.strip()).resolve()
    expected = Path(__file__).resolve().parent.parent
    if root != expected:
        raise CageBroke(
            f"I am not standing in the repo I judge: git says the root is `{root}` but "
            f"this file lives under `{expected}`. Every ratchet below reads repo-relative "
            f"paths, so measuring from here would silently measure the wrong tree. "
            f"FIX: run scripts/merge_cage.py from its own checkout."
        )
    return root


# ─────────────────────────────────────────────────────────────────────────────
# PATTERNS — real glob semantics, because the old ones only looked like them.
#
# `pat.replace("*", "**")` and `p.replace("**", "*")` were no-ops: fnmatch has no
# glob-star and `*` already crosses `/`. The code read like glob-star support and
# was not, so the next person to write `frontend/**/x` would have got a meaning
# they did not ask for. Now:
#
#     *            any run of characters, stopping at `/`
#     **           any run of characters, crossing `/`
#     **/          zero or more whole directories, INCLUDING none — so a
#                  `**/`-prefixed pattern matches at the root as well as at depth
#
# `*` no longer crossing `/` would have LOOSENED every deny, so every recursive
# deny was rewritten to `**` in the same commit. The drill checks nested paths
# under each one.
#
# ── THE BASENAME FALLBACK IS GONE (2026-08-20) ───────────────────────────────
# There used to be a fifth rule: "no `/` at all -> match the BASENAME at any
# depth". It was written so `*.md` would keep working, and it is why
# `docs/product/pillars/README.md` classified as lane `harness`, auto_merge TRUE
# — `README.md` is listed in the fast lane for the ROOT readme, and the fallback
# quietly extended it to every README in the tree.
#
# Note what the old comment two lanes up blamed: "`*` crosses `/`". It does not.
# `*` compiles to `[^/]*` and stops dead at a slash (see below). The wrong
# mechanism was written down, so the drill was written to catch the wrong thing
# and passed. Both are corrected here.
#
# WHY DELETING IT IS SAFE, AND WHY IT COULD NOT BE DONE ALONE. `**/` already
# compiles to `(?:[^/]+/)*` — zero or more whole directories, and *zero* means it
# still matches at the root. So every pattern that genuinely wanted depth says so
# now: `**/*.env*`, `**/package.json`, `**/CLAUDE.md`. The fallback expressed
# nothing that `**/` cannot.
#
# The DIRECTIONS ARE NOT SYMMETRIC, which is the whole reason this is one commit:
#   on ALLOW, an over-wide pattern is fail-OPEN  — it auto-merges something.
#   on DENY,  an over-wide pattern is fail-CLOSED — it merely asks for a human.
# So deleting the fallback TIGHTENS allow (good, immediately) and LOOSENS every
# deny (bad, immediately). Every slash-free DENY entry was rewritten with `**/`
# in this same commit, and the drill pins a NESTED witness for each one. This is
# the same hazard, and the same remedy, as the `*`->`**` migration recorded above.
#
# The new failure direction is the safe one: a bare name now matches the root
# only, so a pattern someone forgets to prefix under-matches, the file matches no
# lane, and it escalates to `product_owner`. Forgetting costs one refusal.
# ─────────────────────────────────────────────────────────────────────────────


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path: str, pattern: str) -> bool:
    """Does this repo-relative path match this pattern?

    One matcher, one answer. `scripts/lane.py` imports this rather than writing a
    second one, so a change here changes both classifiers at once.

    A pattern means exactly what it says: it is anchored at the repo root and the
    only way to reach depth is to ask for it with `**/` or `**`. There is no
    implicit basename rule — see the note above for why it was removed and what
    had to move in the same commit.
    """
    return bool(_glob_to_re(pattern).match(path))


# ─────────────────────────────────────────────────────────────────────────────
# CAGE: PATHS
#
# DENY always beats ALLOW. If a file matches both, the owner decides.
#
# The deny list is not "dangerous code". It is the owner's own sentence:
# product decisions, user-facing decisions, infrastructure decisions. Plus two
# things a machine must never do unsupervised — delete, and edit its own guards.
#
# Every entry carries a FIX clause, because a refusal a human cannot act on is
# how a gate gets switched off at 7am. Most of these say plainly that nothing an
# agent can do will help, which is the honest answer and a useful one.
# ─────────────────────────────────────────────────────────────────────────────

_OWNER_MERGES = "FIX: nothing an agent can do — this is the owner's call, so he merges it by hand."

DENY: list[tuple[str, str]] = [
    # ── product: anything that changes what a user is shown or how it ranks ──
    ("backend/src/services/skill_matcher.py",
     f"changes how jobs are scored — a product decision. {_OWNER_MERGES}"),
    ("backend/src/services/deduplicator.py",
     f"changes which jobs are shown at all. {_OWNER_MERGES}"),
    ("backend/src/services/scoring/**",
     f"changes how jobs are scored — a product decision. {_OWNER_MERGES}"),
    ("backend/src/services/uk_gate.py",
     f"decides which jobs enter the catalogue. {_OWNER_MERGES}"),
    ("backend/src/services/visa_signal.py",
     f"changes what the visa spotlight shows. {_OWNER_MERGES}"),
    ("backend/src/services/profile/**",
     f"changes what is extracted from a user's CV. {_OWNER_MERGES}"),
    ("backend/src/models.py",
     f"normalized_key lives here; wrong dedup = duplicate or lost rows. {_OWNER_MERGES}"),
    # ── users: identity, sessions, anything that can lock someone out ────────
    ("backend/src/api/routes/auth.py", f"authentication — a user decision. {_OWNER_MERGES}"),
    ("backend/src/services/auth/**", f"authentication — a user decision. {_OWNER_MERGES}"),
    ("backend/src/api/routes/account*.py",
     f"account management — a user decision. {_OWNER_MERGES}"),
    ("backend/src/services/notifications/**",
     f"sends real messages to real people. {_OWNER_MERGES}"),
    # A per-user API route is denied on its PATH because a path cage cannot tell a
    # logging fix from a deleted `Depends(require_user)`. PR #315 was +45 lines in
    # exactly this file and would have merged on its filename alone; rules #12/#25
    # exist because a review found three real IDORs in these routes.
    ("backend/src/api/routes/profile.py",
     "a per-user API route — rules #12/#25. A filename cannot tell a logging fix "
     f"from a removed `Depends(require_user)`. {_OWNER_MERGES}"),
    # ── infrastructure ───────────────────────────────────────────────────────
    ("backend/migrations/**", f"schema change — irreversible against live data. {_OWNER_MERGES}"),
    ("**/*docker-compose*", f"infrastructure decision. {_OWNER_MERGES}"),
    ("**/*Dockerfile*", f"infrastructure decision. {_OWNER_MERGES}"),
    ("**/railway.json", f"infrastructure decision. {_OWNER_MERGES}"),
    ("**/*.env*", f"secrets and configuration. {_OWNER_MERGES}"),
    ("backend/src/core/settings.py",
     f"flags here change production behaviour globally. {_OWNER_MERGES}"),
    # ── dependency manifests ─────────────────────────────────────────────────
    # THE CAGE MAY NEVER BE MORE PERMISSIVE THAN A GATE ALREADY ON MAIN. These
    # were on the ALLOW list, and the cage has no semver awareness, so it would
    # have waved through PR #291 (motion 12.42.2 -> 13.0.0) which
    # dependabot-auto.yml already routes to a human. Manifests also carry
    # postinstall scripts: allowing one is allowing arbitrary code on the build host.
    ("**/package.json", "a dependency change — `.github/workflows/dependabot-auto.yml` already "
                     "owns this decision and sends MAJOR bumps to a human; a manifest also "
                     "runs postinstall scripts on the build host. "
                     "FIX: let dependabot-auto decide it, or the owner merges it by hand."),
    ("**/package-lock.json", "a dependency change — see package.json. "
                          "FIX: let dependabot-auto decide it, or the owner merges it by hand."),
    ("**/pyproject.toml", "a dependency change — see package.json. "
                       "FIX: let dependabot-auto decide it, or the owner merges it by hand."),
    ("**/requirements*.txt", "a dependency change — see package.json. "
                          "FIX: let dependabot-auto decide it, or the owner merges it by hand."),
    # ── documents that ARE decisions ─────────────────────────────────────────
    # `docs/**` and `*.md` are allowed, which meant the canonical text of the
    # owner's product rules could ship unsupervised while CLAUDE.md, which merely
    # points AT it, was denied. If a denied file delegates its authority to
    # another file, that file inherits the denial.
    ("docs/product/product_design_rules.md",
     f"the canonical text of owner rules #29/#30/#31 — changing it changes the "
     f"product. {_OWNER_MERGES}"),
    ("docs/product/plans/batch-2-decisions.md",
     f"a record of irreversible choices. {_OWNER_MERGES}"),
    # ── the harness must not quietly edit its own cage ───────────────────────
    (".github/**", "part of the harness that judges this very PR — an agent editing its "
                   f"own guards is how a cage is escaped. {_OWNER_MERGES}"),
    ("scripts/**", "part of the harness that judges this very PR — an agent editing its "
                   f"own guards is how a cage is escaped. {_OWNER_MERGES}"),
    (".claude/**", "the instructions agents read — editing them unsupervised is circular. "
                   + _OWNER_MERGES),
    ("**/CLAUDE.md", "the rules agents read; changing them unsupervised is circular. "
                  + _OWNER_MERGES),
]

ALLOW: list[str] = [
    "backend/tests/**",
    # Frontend tests are allowed by what they ARE, not by where someone hoped they
    # would live: 24 of this repo's 43 frontend unit tests sit beside the code they
    # test, so the old `__tests__/` pattern refused 55% of them as unknown paths.
    "frontend/src/**/__tests__/**",
    "frontend/src/**/*.test.ts",
    "frontend/src/**/*.test.tsx",
    "frontend/src/**/*.spec.ts",
    "frontend/src/**/*.spec.tsx",
    "frontend/tests/**",
    "docs/**",
    "*.md",
    "backend/src/api/routes/client_log.py",  # deliberately public: no user scoping
]

# ─────────────────────────────────────────────────────────────────────────────
# CAGE: RATCHETS
#
# Each is a number that may hold or improve and must never regress. This is the
# only cage that can see SLOW DECAY, which is what unattended merging actually
# costs you — no single PR looks bad, and six months later the numbers are gone.
#
# `direction` is what a HEALTHY change does: "down" for debt, "up" for coverage.
#
# `scope` is new and load-bearing:
#   "tree" — the number is a property of the checked-out tree, so comparing it
#            only means something when this process is standing on the PR's own
#            merge commit. The cage now CHECKS that rather than assuming it.
#
# Two rules learned the hard way and enforced here:
#   * NO SENTINELS. A ratchet either prints a number it measured or exits
#     non-zero. `else 0` made a missing file read as perfect health; `else 999`
#     made a deleted guard un-regressable forever.
#   * NEVER RE-IMPLEMENT THE CONSUMER'S PARSER. Each command shells out to the
#     script CI already runs. The old inline mypy counter counted comment lines
#     and reported 4 against a true 0.
# ─────────────────────────────────────────────────────────────────────────────

RATCHETS: list[dict] = [
    {
        "name": "mypy errors",
        "cmd": [sys.executable, "backend/scripts/mypy_ratchet.py", "--count"],
        # The file AND the flag. `--count` was added by this very branch; on
        # `main` the script exists and answers `usage: mypy_ratchet.py [-h]
        # [--update]`. Probing only the file read that as a BROKEN instrument
        # and refused the PR, when the truth is a MISSING one.
        "needs": {"file": "backend/scripts/mypy_ratchet.py", "supports": "--count"},
        "direction": "down",
        "scope": "tree",
        "why": "type errors were driven 803 -> 0; nothing may add them back",
    },
    {
        "name": "guards never watched failing",
        "cmd": [sys.executable, "scripts/drill_registry.py", "--count-owed"],
        "needs": {"file": "scripts/drill_registry.py", "supports": "--count-owed"},
        "direction": "down",
        "scope": "tree",
        "why": "guards that have never been watched failing are debt; it may only shrink",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# WHY A RATCHET NEEDS THREE ANSWERS, NOT TWO
#
# `measure_ratchets` used to answer "a number, or None". None meant BOTH "the
# command exists and blew up" and "the command is not in this tree at all", and
# the second one is not a fault — it is a fact about history. Measured
# 2026-08-17: `git ls-tree origin/main scripts/drill_registry.py` is EMPTY. The
# `guards never watched failing` ratchet does not exist on main. So every
# main-based PR produced `could not be measured` on both sides of the
# comparison, and an unmeasurable ratchet refuses. That is the same defect shape
# as the missing --baseline: a refusal about the WIRING wearing the clothes of a
# refusal about the PR.
#
# So three statuses, and the asymmetry is where the safety lives:
#
#   ok       a number was measured.
#   absent   that tree's instrument cannot answer this question at all — the
#            script is not there, or it is there and does not carry the flag.
#            Not a fault. Found the hard way on round 2 of the live run:
#            `backend/scripts/mypy_ratchet.py` IS on main, but `--count` was
#            added by this branch, so main answers `usage: mypy_ratchet.py [-h]
#            [--update]`. A file-existence probe read that as a BROKEN
#            instrument and refused 16 of 16 merged PRs for it. The capability,
#            not the file, is what has to be probed.
#   error    the instrument claims to support this and failed anyway. Always a
#            refusal — this is the real-breakage case, and treating it as
#            `absent` would be the permissive guess.
#
# The capability probe is STATIC (does the file contain the flag?) and never
# executes anything to decide. Both ways of being wrong fail safe: a flag spelled
# differently reads as `absent` (and zero comparisons refuses), and a flag that
# appears only in a comment reads as present, runs, fails, and refuses.
#
# base=ok + head=absent REFUSES: that is a PR deleting a ratchet, the one
# direction that must never be waved through. base=absent + head=absent is NOT
# APPLICABLE and is NAMED in the verdict, never silently folded into a pass.
# ─────────────────────────────────────────────────────────────────────────────

R_OK, R_ABSENT, R_ERROR = "ok", "absent", "error"

# The "open security alerts" ratchet was REMOVED, not silently kept. Its command
# was `gh api repos/<repo>/code-scanning/alerts?state=open` with no `ref`, which
# answers for the DEFAULT BRANCH: the same number before and after any PR, so
# `was` and `now` were equal by construction and it could never fire. Its job is
# now done PR-scoped and for real in check_proof, by reading the `CodeQL` check
# run's own title — measured 2026-08-16: PR #258 "1 new alert including 1 high
# severity security vulnerability", and PR #327 said the same at MEDIUM and still
# concluded SUCCESS.
NEW_ALERT_RE = re.compile(r"(\d+)\s+new\s+alert", re.I)

MAX_CHANGED_FILES = 40
MAX_CHANGED_LINES = 1200
PER_PAGE = 100

REQUIRED_CHECKS = ["Backend", "Frontend", "Chain wires", "CodeQL"]

# Checks that STRUCTURALLY CANNOT EXIST on a PR whose base is not `main`.
# `.github/workflows/codeql.yml` declares `pull_request: branches: ["main"]`, so a
# stacked PR (base = another feature branch) never gets a `CodeQL` check run at
# all. Requiring it by name there is an unclearable block — the PR can never
# satisfy it, no matter what the author does.
#
# The fix is NOT to quietly drop it and judge the thinner set as if it were the
# full one. That would hand out a one-branch bypass of the only check in this
# repo that reads NEW security alerts from a PR's own diff: base your work off a
# feature branch and the security question stops being asked, silently. So a
# non-main base is REFUSED, with the missing check named. Honest and unbypassable.
MAIN_ONLY_CHECKS = {"CodeQL"}
MAIN_BRANCH = "main"


# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Verdict:
    """One cage's answer, and the ONLY thing allowed to speak for it.

    `claim` is the sentence the owner may read — printable if and only if
    `status == "pass"`. This is what makes it structurally impossible to tell him
    a check passed when it never ran.
    """

    name: str
    status: str  # "pass" | "fail" | "not_checked"
    reasons: list[str] = field(default_factory=list)
    claim: str = ""

    @property
    def blocks(self) -> bool:
        # NOT CHECKED blocks. An unmeasured cage is not a passing one — that is
        # exactly the hole the RATCHET cage sat in for its whole life.
        return self.status != "pass"


def gh(args: list[str]) -> str:
    """Run gh and return stdout. Raises on failure — a failed probe must never
    read as a permissive answer, which is how a check becomes decoration."""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def page_was_full(n_items: int, per_page: int, what: str) -> list[str]:
    """A full page is not a complete answer.

    Latent today (MAX_CHANGED_FILES is 40) but the shape is the dangerous one:
    raise that cap above 100 and the 101st file — auth.py, say — becomes
    invisible to the path cage while the cage prints a confident verdict about
    the 100 it happened to see.
    """
    if n_items >= per_page:
        return [f"the {what} listing came back at the page limit ({n_items}), so I may not "
                f"have seen all of it, and I will not judge a change I cannot see whole. "
                f"FIX: teach merge_cage.py to paginate, or split the PR."]
    return []


# ─────────────────────────────────────────────────────────────────────────────


def check_lists() -> list[str]:
    """The rules must not contradict themselves.

    Two of the twelve ALLOW entries were unreachable for the cage's whole life —
    shadowed by a DENY glob, and DENY wins. 17% of the allow list read as
    permission and was not, and the drill was fully green with them dead. Dead
    configuration is a hard error, not a silent no-op.

    Only LITERAL allow entries are checked: `*.md` overlapping the `CLAUDE.md`
    deny is the documented precedence rule working as designed.
    """
    bad: list[str] = []
    for a in ALLOW:
        if any(ch in a for ch in "*?"):
            continue
        for pat, _why in DENY:
            if path_matches(a, pat):
                bad.append(f"ALLOW `{a}` is fully shadowed by DENY `{pat}`, so it can never "
                           f"take effect. It reads as a permission and is not.")
    return bad


def check_paths(files: list[str]) -> Verdict:
    """Every changed file must be explicitly allowed, and none denied."""
    reasons: list[str] = []
    for f in files:
        hit = next(((pat, why) for pat, why in DENY if path_matches(f, pat)), None)
        if hit:
            reasons.append(f"`{f}` — {hit[1]}")
            continue
        if not any(path_matches(f, p) for p in ALLOW):
            # Silence is not consent. An unrecognised path is a path nobody has
            # thought about, and the cage refuses what it does not know — but it
            # now says what would end the refusal.
            reasons.append(
                f"`{f}` — no rule covers this path, so nobody has decided it is safe to ship "
                f"unattended. FIX: either take this file out of the PR, or the owner adds a "
                f"matching pattern to ALLOW/DENY in scripts/merge_cage.py (that addition is "
                f"itself his decision, not an agent's)."
            )
    return Verdict(
        "PATH", "fail" if reasons else "pass", reasons,
        claim="only paths the owner has already decided are safe",
    )


def check_size(n_files: int, n_lines: int, deletions: list[str]) -> Verdict:
    reasons: list[str] = []
    if deletions:
        reasons.append(
            f"deletes {len(deletions)} file(s) ({', '.join(deletions[:3])}) — a machine must "
            f"not delete unsupervised. {_OWNER_MERGES}")
    if n_files > MAX_CHANGED_FILES:
        reasons.append(
            f"{n_files} files changed (cap {MAX_CHANGED_FILES}) — a change this wide is a "
            f"judgement call by size alone. FIX: split it into PRs under the cap, or the "
            f"owner merges it by hand.")
    if n_lines > MAX_CHANGED_LINES:
        reasons.append(
            f"{n_lines} lines changed (cap {MAX_CHANGED_LINES}) — same reason. FIX: split it "
            f"into PRs under the cap, or the owner merges it by hand.")
    return Verdict("SIZE", "fail" if reasons else "pass", reasons,
                   claim=f"small enough to reason about ({n_files} files, {n_lines} lines)")


def judge_check_runs(runs: list[dict], total_count: int, base_ref: str = MAIN_BRANCH) -> list[str]:
    """Pure half of the PROOF cage, so it can be drilled with real shapes.

    `base_ref` is load-bearing, not decoration. Measured 2026-08-17: PRs #343,
    #344, #345 and #346 all have base `feat/every-guard-declares-its-drill`, and
    all four are MISSING `Analyze (python)` and `Analyze (javascript-typescript)`
    on their head SHAs — codeql.yml only fires for main-targeted PRs. The cage
    judged them against a list containing a check that could not exist, i.e. it
    refused them for a reason no author could ever clear, while ci.yml and
    security.yml really did run. Naming the reason is the whole fix.
    """
    reasons: list[str] = []
    cannot = ", ".join(sorted(MAIN_ONLY_CHECKS))
    if not base_ref:
        # Caught by this file's own drill on the first run: the guard was
        # `if base_ref and base_ref != MAIN_BRANCH`, so an unreadable base
        # short-circuited into the branch that judges against the FULL check
        # list — the permissive guess, in the one place a permissive guess
        # silently restores a security check that cannot be there.
        reasons.append(
            f"I could not read this PR's base branch, so I cannot tell whether it targets "
            f"`{MAIN_BRANCH}` — and `{cannot}` only runs for main-targeted PRs. An unknown "
            f"base is not `{MAIN_BRANCH}`; guessing the permissive way is how a check comes "
            f"back to life on paper. "
            f"FIX: check `gh pr view <PR> --json baseRefName` returns something, then re-judge.")
    elif base_ref != MAIN_BRANCH:
        reasons.append(
            f"this PR's base is `{base_ref}`, not `{MAIN_BRANCH}`. `{cannot}` only runs for "
            f"main-targeted PRs (.github/workflows/codeql.yml declares "
            f"`pull_request: branches: [\"main\"]`), so on this base it cannot exist — and I "
            f"will not call a thinner check set a pass, because that would make \"branch off "
            f"a feature branch\" a way to stop the security question being asked. "
            f"FIX: retarget this PR at `{MAIN_BRANCH}`, or the owner merges the stack by hand.")
    if not runs:
        # `return reasons + [...]`, never `return [...]`: an early return that drops
        # a reason already found is how a cage forgets what it knew. The base-ref
        # finding above must survive to the owner.
        return reasons + [
            "NO CHECKS RAN AT ALL. An empty check list is not a pass — it is the "
            "signature of a PR opened with GITHUB_TOKEN, which GitHub refuses to start "
            "workflows for. Nothing about this change has been verified. "
            "FIX: push an empty commit from a human account, or re-run the workflows, "
            "then re-judge."]
    reasons += page_was_full(len(runs), PER_PAGE, "check-run")
    if total_count and total_count > len(runs):
        reasons.append(f"GitHub reports {total_count} check runs and I received {len(runs)} — "
                       f"I am missing some and will not judge a partial list. "
                       f"FIX: teach merge_cage.py to paginate.")

    names = [r.get("name", "") for r in runs]
    for req in REQUIRED_CHECKS:
        # On a non-main base the reason above already says why this one is absent.
        # Saying it twice, once in a form the author cannot act on, is noise.
        if base_ref != MAIN_BRANCH and req in MAIN_ONLY_CHECKS:
            continue
        if not any(req.lower() in n.lower() for n in names):
            reasons.append(
                f"required check `{req}` did not run — it cannot pass by being absent. This is "
                f"almost always a stale base: the PR was branched before that check existed. "
                f"FIX: `gh pr update-branch <PR>` (or rebase onto main), wait for CI, re-judge.")

    for r in runs:
        name = r.get("name")
        concl = r.get("conclusion")
        if r.get("status") != "completed":
            reasons.append(f"`{name}` has not finished ({r.get('status')}). "
                           f"FIX: wait for it, then re-judge.")
        elif concl not in ("success", "skipped", "neutral"):
            url = r.get("html_url") or ""
            reasons.append(f"`{name}` -> {concl}. FIX: make it green — {url}".rstrip(" -"))

        # A check that saw the problem, printed it, and went green anyway. On PR
        # #327 `CodeQL` concluded SUCCESS with the title "1 new alert including 1
        # medium severity security vulnerability", and that alert is still open
        # today. Reading the conclusion alone would miss it, so read the title.
        title = (r.get("output") or {}).get("title") or ""
        m = NEW_ALERT_RE.search(title)
        if m and m.group(1) != "0":
            reasons.append(
                f"`{name}` reports {m.group(1)} NEW security alert(s) in this PR's own changes "
                f"(\"{title}\") — note its conclusion can still be `{concl}`, which is how "
                f"nine alerts reached production here. "
                f"FIX: fix the alert, or dismiss it with a reason in the Security tab, "
                f"then re-judge.")
    return reasons


def check_proof(pr: int, base_ref: str = MAIN_BRANCH) -> Verdict:
    """Did the checks really run, and really pass?

    The dangerous answer here is not 'red'. It is 'nothing ran' — a PR opened by a
    workflow using GITHUB_TOKEN gets NO check runs at all, and a naive
    'any failures?' test reads that empty list as success.
    """
    sha = gh(["api", f"repos/{REPO}/pulls/{pr}", "-q", ".head.sha"])
    data = json.loads(gh(["api", f"repos/{REPO}/commits/{sha}/check-runs?per_page={PER_PAGE}"]))
    runs = data.get("check_runs", [])
    reasons = judge_check_runs(runs, int(data.get("total_count") or 0), base_ref)
    return Verdict("PROOF", "fail" if reasons else "pass", reasons,
                   claim="every required check really ran and really passed")


_THREADS_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100){
        pageInfo{hasNextPage}
        nodes{
          isResolved isOutdated path line
          comments(first:1){nodes{author{login}}}
        }
      }
    }
  }
}
"""


def judge_threads(nodes: list[dict], has_next: bool) -> list[str]:
    """Pure half of the REVIEW cage.

    This used to read REST `pulls/{pr}/comments`, an endpoint with NO resolution
    field, while printing "N unresolved review comment(s)". It was wrong in both
    directions — PR #336 showed 4 when GraphQL says all four threads are resolved,
    PR #258 showed 4 when GraphQL shows 5 — and because resolving in the UI does
    not delete the REST comment, the block was UNCLEARABLE. A check whose wording
    claims a state must read an API that can express that state.

    `isOutdated` is deliberately NOT excluded. Outdated only means the code moved
    under the comment, which is the normal state of an unfixed finding after any
    later commit — excluding it would hand out a one-line bypass: push a
    whitespace commit and every open finding clears itself.
    """
    reasons: list[str] = []
    if has_next:
        reasons.append("this PR has more than 100 review threads and I only read the first "
                       "page. FIX: teach merge_cage.py to paginate, or split the PR.")
    open_threads = [n for n in nodes if not n.get("isResolved")]
    if open_threads:
        def who(n: dict) -> str:
            cs = ((n.get("comments") or {}).get("nodes") or [{}])
            return ((cs[0].get("author") or {}).get("login")) or "?"
        sample = "; ".join(f"{n.get('path')}:{n.get('line')} ({who(n)})" for n in open_threads[:4])
        more = f" (+{len(open_threads) - 4} more)" if len(open_threads) > 4 else ""
        reasons.append(
            f"{len(open_threads)} unresolved review thread(s): {sample}{more}. "
            f"FIX: act on each one, then click Resolve conversation on the PR — resolving is "
            f"what clears this, so it is a block you can actually finish.")
    return reasons


def check_review(pr: int) -> Verdict:
    """Unresolved review THREADS, never the status tick.

    CodeRabbit runs with `fail_commit_status: false`, so its tick is structurally
    incapable of saying no.
    """
    owner, _, name = REPO.partition("/")
    raw = gh(["api", "graphql", "-f", f"query={_THREADS_QUERY}",
              "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={pr}"])
    threads = (json.loads(raw)["data"]["repository"]["pullRequest"]["reviewThreads"])
    reasons = judge_threads(threads.get("nodes") or [],
                            bool((threads.get("pageInfo") or {}).get("hasNextPage")))
    return Verdict("REVIEW", "fail" if reasons else "pass", reasons,
                   claim="no reviewer is still waiting on an answer")


def is_worse(direction: str, was: int, now: int) -> bool:
    """The whole ratchet decision, in one place.

    Extracted deliberately: the drill below tests THIS function. A drill that
    re-implements the comparison it is checking proves only that the drill agrees
    with itself — which is how a self-test greps for a string and stays green
    after the code it guards is deleted.
    """
    return now > was if direction == "down" else now < was


def ground_problem(expected_sha: str, actual_sha: str) -> str | None:
    """Is a tree-scoped comparison meaningful from where we stand?

    auto-merge.yml deliberately checks out `main` so a PR cannot widen the rules
    that judge it. That is right — and it means a ratchet measured HERE is main's
    number, not the PR's. Comparing main-to-main would print "no number regressed"
    having compared a value to itself, which is exactly the class of lie this file
    exists to stop. So say so instead of pretending.
    """
    if not expected_sha or not actual_sha:
        return ("I cannot tell which commit I am standing on, so I cannot say whether these "
                "numbers belong to this PR. FIX: run the ratchets from a checkout of "
                "`refs/pull/<PR>/merge`.")
    if expected_sha != actual_sha:
        return (f"these numbers were measured on `{actual_sha[:8]}`, not on this PR's merge "
                f"commit `{expected_sha[:8]}` — comparing them would answer a question about "
                f"a different tree. FIX: measure from a checkout of `refs/pull/<PR>/merge`, or "
                f"drop --baseline and accept that the ratchet cage did not run.")
    return None


def measured_tree() -> Path:
    """The tree whose NUMBERS are read. Distinct from ROOT, which is where the
    RULES come from — see `--tree` in main()."""
    return MEASURE_TREE or ROOT


def head_sha(tree: Path | None = None) -> str:
    t = tree or measured_tree()
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30, cwd=str(t))
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def capability_gap(tree: Path, needs: object) -> str:
    """Why this tree's instrument cannot answer, or "" if it can.

    STATIC ON PURPOSE. Asking the tree's script "do you support --count?" by
    running it means reading an argparse usage message and guessing, and a guess
    in this position decides whether a PR is refused. Reading the file is
    deterministic and both errors fail safe.
    """
    if not needs:
        return ""
    if isinstance(needs, str):
        needs = {"file": needs}
    if not isinstance(needs, dict):
        return f"the ratchet's `needs` is malformed ({needs!r})"
    rel = str(needs.get("file") or "")
    target = tree / rel
    if not target.exists():
        return f"{rel} is not in this tree"
    flag = needs.get("supports")
    if flag:
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"{rel} could not be read ({exc})"
        if str(flag) not in text:
            return f"{rel} in this tree has no `{flag}` — it cannot answer this question"
    return ""


def measure_ratchets(tree: Path | None = None) -> dict[str, dict]:
    """Current value of every ratchet, as {name: {status, value, detail}}.

    Three answers, never two — see the comment above `R_OK`. `absent` is a fact
    about the tree's history; `error` is a fault and always refuses.
    """
    t = tree or measured_tree()
    vals: dict[str, dict] = {}
    for r in RATCHETS:
        gap = capability_gap(t, r.get("needs"))
        if gap:
            vals[r["name"]] = {"status": R_ABSENT, "value": None, "detail": gap}
            continue
        try:
            out = subprocess.run(r["cmd"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=300, cwd=str(t))
            if out.returncode != 0:
                vals[r["name"]] = {"status": R_ERROR, "value": None,
                                   "detail": (out.stderr or out.stdout).strip()[-200:]}
                continue
            vals[r["name"]] = {"status": R_OK,
                               "value": int(out.stdout.strip().splitlines()[-1]), "detail": ""}
        except Exception as exc:
            vals[r["name"]] = {"status": R_ERROR, "value": None,
                               "detail": f"{type(exc).__name__}: {exc}"}
    return vals


def as_reading(raw: object) -> dict:
    """Normalise one baseline entry into {status, value}.

    Accepts the flat legacy form (`5`, `null`) as well as the current object
    form, because a baseline is passed in as text on a command line and a
    half-upgraded caller must not be read as something it did not say. A flat
    `null` is `error`, NOT `absent`: the old format could not express "the
    script was not there", so assuming the harmless meaning would be exactly
    the permissive guess this file exists to refuse.
    """
    if isinstance(raw, dict):
        st = raw.get("status")
        if st in (R_OK, R_ABSENT, R_ERROR):
            return {"status": st, "value": raw.get("value")}
        return {"status": R_ERROR, "value": None}
    if isinstance(raw, bool):  # bool is an int in Python; a bool is not a reading
        return {"status": R_ERROR, "value": None}
    if isinstance(raw, int):
        return {"status": R_OK, "value": raw}
    return {"status": R_ERROR, "value": None}


def check_ratchets(base_values: dict | None, expected_sha: str = "",
                   tree: Path | None = None) -> Verdict:
    """Numbers may hold or improve. Never regress."""
    reasons: list[str] = []
    notes: list[str] = []
    now_values = measure_ratchets(tree)
    for r in RATCHETS:
        now = now_values.get(r["name"], {"status": R_ERROR, "detail": "not measured at all"})
        if now["status"] == R_ERROR:
            reasons.append(
                f"ratchet `{r['name']}` could not be measured ({now.get('detail') or 'no detail'})"
                f" — an unmeasurable ratchet is not a passing one. FIX: run "
                f"`{' '.join(str(c) for c in r['cmd'][1:])}` from the repo root and make it "
                f"print a number.")
    if reasons:
        return Verdict("RATCHET", "fail", reasons, claim="no quality number went backwards")

    if base_values is None:
        # NOT CHECKED, and NOT CHECKED refuses. This is the hole the cage sat in
        # for its whole life: nothing passes --baseline, so every ratchet hit an
        # `if base_values is None: continue` and the ALLOW message still told the
        # owner "no quality number went backwards".
        return Verdict(
            "RATCHET", "not_checked",
            ["no baseline was given, so no number was compared to anything — I will not call "
             "that a pass. FIX: measure the base with `python scripts/merge_cage.py --measure` "
             "on a checkout of the PR's BASE, then judge from a checkout of "
             "`refs/pull/<PR>/merge` passing `--baseline '<that JSON>'`."],
            claim="no quality number went backwards")

    if any(r.get("scope") == "tree" for r in RATCHETS):
        problem = ground_problem(expected_sha, head_sha(tree))
        if problem:
            return Verdict("RATCHET", "fail", [f"ratchet comparison refused: {problem}"],
                           claim="no quality number went backwards")

    compared = 0
    for r in RATCHETS:
        was = as_reading(base_values.get(r["name"]))
        now = now_values[r["name"]]

        if was["status"] == R_ERROR:
            reasons.append(
                f"ratchet `{r['name']}` has no readable value in the baseline, so there is "
                f"nothing to compare — and I will not call an uncompared number a pass. "
                f"FIX: regenerate the baseline with `python scripts/merge_cage.py --measure` "
                f"on a checkout of the PR's BASE.")
            continue

        # THE DANGEROUS DIRECTION, AND THE ONLY ONE THAT IS A REFUSAL HERE: the
        # base could measure this number and the PR's tree cannot. That is a PR
        # deleting a ratchet, which is how a ratchet stops ratcheting forever.
        if was["status"] == R_OK and now["status"] == R_ABSENT:
            reasons.append(
                f"ratchet `{r['name']}` was measurable on the base ({was['value']}) and is NOT "
                f"measurable in this PR's tree — {now.get('detail')}. A PR that removes a "
                f"ratchet removes every future comparison, so this is a refusal, not a note. "
                f"FIX: put `{r.get('needs')}` back, or the owner merges it by hand.")
            continue

        if was["status"] == R_ABSENT and now["status"] == R_ABSENT:
            # NOT APPLICABLE. Neither tree has the ratchet, so nothing regressed
            # and nothing was checked. Named out loud, never folded into a pass.
            notes.append(f"`{r['name']}` does not exist in this lineage ({now.get('detail')}), "
                         f"so it was NOT compared")
            continue

        if was["status"] == R_ABSENT and now["status"] == R_OK:
            notes.append(f"`{r['name']}` is new in this PR ({now['value']}) — there is no "
                         f"earlier value to compare it to")
            continue

        assert was["value"] is not None and now["value"] is not None
        compared += 1
        if is_worse(r["direction"], was["value"], now["value"]):
            reasons.append(f"ratchet `{r['name']}` got worse: {was['value']} -> {now['value']} "
                           f"({r['why']}). FIX: bring it back to {was['value']} or better "
                           f"in this PR.")

    # ZERO COMPARISONS IS NOT A PASS. Without this the n/a arm above becomes a
    # way for the whole cage to evaporate: a lineage with no ratchet in it would
    # print "no quality number went backwards" having compared nothing, which is
    # the precise sentence this file was rewritten to make impossible.
    if not reasons and compared == 0:
        reasons.append(
            "not one ratchet could be compared against the base"
            + (" — " + "; ".join(notes) if notes else "")
            + ". A cage that measured nothing did not pass; it did not run. "
              "FIX: land at least one ratchet on the base branch, or the owner merges by hand.")

    # THE CLAIM MUST NAME WHAT IT ACTUALLY COVERED. "no quality number went
    # backwards" over zero compared numbers is the same sentence that was
    # printed for the cage's whole life while nothing had been compared at all.
    claim = f"{compared} quality number(s) compared against the base, none went backwards"
    if notes:
        claim += " (" + "; ".join(notes) + ")"
    return Verdict("RATCHET", "fail" if reasons else "pass", reasons, claim=claim)


# ─────────────────────────────────────────────────────────────────────────────


def decide(pr: int, base_values: dict[str, int] | None = None) -> tuple[bool, list[Verdict], dict]:
    """ALLOW only if every cage PASSES. Any error, any doubt, REFUSES.

    Every `except` here is `except Exception`, not a hand-picked tuple. The old
    tuple (RuntimeError, JSONDecodeError, KeyError) missed FileNotFoundError (gh
    not on PATH) and subprocess.TimeoutExpired (the GitHub API does hang), both of
    which `gh()` raises — so two ordinary field conditions produced a traceback
    instead of a verdict. BaseException is deliberately NOT used: it would swallow
    Ctrl-C and argparse's SystemExit.
    """
    verdicts: list[Verdict] = []
    meta: dict = {}
    try:
        files_raw = gh(["api", f"repos/{REPO}/pulls/{pr}/files?per_page={PER_PAGE}"])
        files_json = json.loads(files_raw)
        files = [f["filename"] for f in files_json]
        deletions = [f["filename"] for f in files_json if f.get("status") == "removed"]
        changed_lines = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files_json)
        pr_json = json.loads(gh(["api", f"repos/{REPO}/pulls/{pr}"]))
        # A MISSING base ref is not `main`. Defaulting an unknown base to the one
        # value that unlocks the full check list would be the permissive guess,
        # and the permissive guess is what this file exists to refuse. An empty
        # string is not `main`, so judge_check_runs says so and the cage refuses.
        base_ref = ((pr_json.get("base") or {}).get("ref")) or ""
        meta = {"files": len(files), "lines": changed_lines,
                "title": pr_json.get("title") or f"PR #{pr}",
                "base": base_ref,
                "merge_sha": pr_json.get("merge_commit_sha") or ""}
    except Exception as exc:
        # A cage that cannot see the change must never approve it.
        return False, [Verdict("READ", "fail", [
            f"could not read PR #{pr} ({type(exc).__name__}: {exc}) — refusing on principle. "
            f"FIX: check `gh auth status` and that the PR exists, then re-judge."])], meta

    truncation = page_was_full(len(files), PER_PAGE, "changed-files")
    if truncation:
        verdicts.append(Verdict("READ", "fail", truncation))

    verdicts.append(check_size(len(files), changed_lines, deletions))
    verdicts.append(check_paths(files))

    for name, fn in (("PROOF", lambda p: check_proof(p, base_ref)), ("REVIEW", check_review)):
        try:
            verdicts.append(fn(pr))
        except Exception as exc:
            verdicts.append(Verdict(name, "fail", [
                f"the {name} cage could not run ({type(exc).__name__}: {exc}) — a cage that "
                f"could not look is not a cage that approved. FIX: re-run once the GitHub API "
                f"is reachable."]))

    try:
        verdicts.append(check_ratchets(base_values, meta.get("merge_sha", ""), measured_tree()))
    except Exception as exc:
        verdicts.append(Verdict("RATCHET", "fail", [
            f"the RATCHET cage could not run ({type(exc).__name__}: {exc}) — refusing. "
            f"FIX: run `python scripts/merge_cage.py --measure` and fix whatever it reports."]))

    return (not any(v.blocks for v in verdicts)), verdicts, meta


def blocks_of(verdicts: list[Verdict]) -> list[str]:
    """Every reason, plus an explicit line for any cage that did not run.

    A cage with status `not_checked` and no reasons would otherwise block
    silently — refusing without saying why is its own kind of dead guard.
    """
    out: list[str] = []
    for v in verdicts:
        if v.status == "pass":
            continue
        out += v.reasons or [f"the {v.name} cage did not run, and NOT CHECKED is not a pass."]
    return out


# ─────────────────────────────────────────────────────────────────────────────


def slack(channel: str, text: str) -> bool:
    """Post to Slack. Returns False loudly rather than pretending it worked —
    an alert path that exits 0 when it did nothing kept this harness mute for
    weeks."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK: no SLACK_BOT_TOKEN in the environment — nothing was sent.", file=sys.stderr)
        return False
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "text": text}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode())
    except Exception as exc:
        print(f"SLACK: post failed ({type(exc).__name__}: {exc}).", file=sys.stderr)
        return False
    if not body.get("ok"):
        print(f"SLACK: refused -> {body.get('error')}", file=sys.stderr)
        return False
    return True


def plain_english(pr: int, meta: dict, allowed: bool, verdicts: list[Verdict]) -> str:
    """The message the owner actually reads.

    THE ALLOW SENTENCE IS BUILT FROM THE CAGES THAT PASSED, and from nothing else.
    It used to be a hardcoded string listing four checks while one of them had
    never run. A claim now cannot exist without the evidence that produced it.
    """
    title = meta.get("title", f"PR #{pr}")
    plain = title.split(": ", 1)[1] if ": " in title else title
    link = f"https://github.com/{REPO}/pull/{pr}"
    size = f"{meta.get('files', '?')} files, {meta.get('lines', '?')} lines"

    if allowed:
        claims = [v.claim for v in verdicts if v.status == "pass" and v.claim]
        checked = "; ".join(claims) if claims else "no cage reported anything"
        return (f":shipit: *Merged to production — PR #{pr}*\n"
                f"*What it does:* {plain}\n"
                f"*Size:* {size}\n"
                f"*What was actually checked:* {checked}.\n"
                f"{link}")
    blocks = blocks_of(verdicts)
    reasons = "\n".join(f"  • {b}" for b in blocks[:6])
    more = f"\n  _(+{len(blocks) - 6} more)_" if len(blocks) > 6 else ""
    return (f":raising_hand: *Needs your call — PR #{pr}*\n"
            f"*What it does:* {plain}\n"
            f"*Size:* {size}\n"
            f"*Why I did not merge it:*\n{reasons}{more}\n"
            f"{link}")


# ─────────────────────────────────────────────────────────────────────────────
# THE ADVISOR. The cage's most valuable output is not the verdict — it is the
# sentence naming WHICH of the seven files in front of the owner is the one he
# should actually look at. Measured: median PR here is open 12.9 minutes, and in
# 25 of the last 25 merges the final check completed BEFORE `mergedAt`. He is not
# the bottleneck and the merge button is not the leak, so automating the click
# buys ~13 minutes of wall clock and zero attention. Naming the risky file buys
# attention, on 100% of PRs instead of the 3% a merge lane could ever cover.
#
# ADVICE NEVER MERGES. There is no flag on this file that merges any more — the
# old `--merge` was removed, and `--merge` now exits with a usage error, drilled
# below. That is a capability deletion, not a default change: a default can be
# flipped by a repo variable nobody reviews.
# ─────────────────────────────────────────────────────────────────────────────

MARKER = "<!-- merge-cage-advice -->"
LABEL_OWNER = "owner-decision"
LABEL_SAFE = "agent-safe"


def advice_label(allowed: bool) -> str:
    return LABEL_SAFE if allowed else LABEL_OWNER


def advice_markdown(pr: int, meta: dict, allowed: bool, verdicts: list[Verdict]) -> str:
    """The PR comment. Same evidence rule as `plain_english`: a claim may only be
    printed by the cage that produced it, so a cage that did not run cannot
    appear as reassurance."""
    size = f"{meta.get('files', '?')} files, {meta.get('lines', '?')} lines"
    base = meta.get("base") or "?"
    head = [MARKER, "### Merge cage — advisory only", "",
            f"**Size:** {size} · **Base:** `{base}`", ""]

    if allowed:
        claims = [v.claim for v in verdicts if v.status == "pass" and v.claim]
        head += [
            f"**`{LABEL_SAFE}`** — nothing in this PR is a decision the cage was told to "
            "reserve for you.", "",
            "What was actually checked: " + ("; ".join(claims) if claims
                                             else "_no cage reported anything_") + ".", "",
            "This is advice. It merges nothing, and it never will — no flag on "
            "`scripts/merge_cage.py` can merge.",
        ]
    else:
        blocks = blocks_of(verdicts)
        head += [f"**`{LABEL_OWNER}`** — {len(blocks)} thing(s) here are yours to decide.", ""]
        head += [f"- {b}" for b in blocks[:8]]
        if len(blocks) > 8:
            head += ["", f"_(+{len(blocks) - 8} more)_"]
        skipped = [v.name for v in verdicts if v.status == "not_checked"]
        if skipped:
            head += ["", f"Cages that did **not** run: {', '.join(skipped)}. "
                         f"Not checked is not passed."]
        head += ["", "This is advice, not a block. Merge it yourself whenever you like — "
                     "the list above is only saying which part is the decision."]
    return "\n".join(head)


# ─────────────────────────────────────────────────────────────────────────────
# The drill. This file decides what reaches real users, so it is the last place
# a silent failure is acceptable. Each break below is a way a permissive bug
# could let something through.
#
# TWO THINGS THE DRILL NOW DOES THAT IT DID NOT:
#   * COVERAGE. It lists every function on the decision path and FAILS if any is
#     undrilled. "7/7 passed" used to mean "the path list works" and was read as
#     "the cage works" — six of those seven cases called check_paths.
#   * THE BLOCKER LOG. Every rule in scripts/cage_blockers.py names the drill case
#     that goes red if its fix is removed. A rule whose drill is missing or
#     renamed fails here, so a lesson cannot quietly stop being enforced.
# ─────────────────────────────────────────────────────────────────────────────

# Every function `decide()` calls, by name. Adding a call here without a drill
# case that touches it turns the drill red.
DECISION_PATH = [
    "check_size", "check_paths", "judge_check_runs", "check_proof", "judge_threads",
    "check_review", "measure_ratchets", "check_ratchets", "is_worse", "ground_problem",
    "as_reading", "measured_tree", "capability_gap",
    "page_was_full", "path_matches", "check_lists", "blocks_of", "plain_english", "decide",
    "slack",  # the announce path: a verdict nobody hears is a verdict that did not happen
    # The advisory path is now the cage's ONLY output that reaches a human on
    # every PR, so it is on the decision path even though `decide` does not call
    # it. An unread verdict is a verdict that did not happen.
    "advice_markdown", "advice_label",
]


def self_drill() -> int:  # noqa: C901 - a drill is a list, not a branch tree
    from cage_blockers import BLOCKERS, undrilled

    print("DRILL - breaking the merge cage on purpose. It must REFUSE and say why.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []
    touched: set[str] = set()

    def red(name: str, verdict_or_reasons: Verdict | list[str], needle: str, fns: list[str]) -> None:
        reasons = (verdict_or_reasons.reasons if isinstance(verdict_or_reasons, Verdict)
                   else verdict_or_reasons)
        hit = next((r for r in reasons if needle.lower() in r.lower()), "")
        results.append((name, bool(hit), hit))
        touched.update(fns)

    def ok(name: str, passed: bool, detail: str, fns: list[str]) -> None:
        # `detail` describes the FAILURE, so it is only shown when there is one.
        # (Printing it under a PASS line made every green case read as a refusal.)
        results.append((name, passed, "" if passed else detail))
        touched.update(fns)

    P = ["check_paths", "path_matches"]

    # ── CAGE 1: PATHS ────────────────────────────────────────────────────────
    red("scoring change is refused",
        check_paths(["backend/src/services/skill_matcher.py"]), "product decision", P)
    red("auth change is refused",
        check_paths(["backend/src/api/routes/auth.py"]), "user decision", P)
    red("infra change is refused",
        check_paths(["docker-compose.prod.yml"]), "infrastructure", P)
    red("the agent editing its own guards is refused",
        check_paths([".github/workflows/ci.yml"]), "cage is escaped", P)
    red("an unrecognised path is refused",
        check_paths(["backend/src/some_new_module.py"]), "nobody has decided", P)

    # B16 — a per-user API route may not merge on the strength of its filename.
    red("a per-user API route is refused on its path",
        check_paths(["backend/src/api/routes/profile.py"]), "#12/#25", P)
    # B11 — the cage may never be more permissive than a gate already on main.
    red("a dependency manifest is refused and points at dependabot-auto",
        check_paths(["frontend/package.json"]), "dependabot-auto", P)
    # B12 — a document that IS a decision inherits the denial it delegates from.
    red("a document that is itself a product decision is refused",
        check_paths(["docs/product/product_design_rules.md"]), "product", P)
    # B13 — `**` really crosses directories now, and `*` really stops at one.
    #        If the rewrite from `x/*` to `x/**` had been botched, this goes red.
    #        Each nested path must be refused BY ITS DENY REASON. Counting
    #        refusals is not enough: a narrowed deny lets the file fall through to
    #        "no rule covers this path", which is still a refusal and still the
    #        wrong answer — the same file, refused for a reason that no longer
    #        knows what it is protecting. (Measured: a mutation narrowing
    #        `scripts/**` back to `scripts/*` left a count-based drill green.)
    nested = [("scripts/deep/nested/tool.py", "cage is escaped"),
              ("backend/src/services/profile/deep/inner.py", "extracted from a user's CV"),
              (".github/workflows/sub/x.yml", "cage is escaped"),
              ("backend/migrations/versions/0001_x.py", "schema change"),
              ("backend/src/services/auth/session/store.py", "authentication")]
    missed = [f for f, needle in nested
              if not any(needle in r for r in check_paths([f]).reasons)]
    ok("a nested file under a recursive deny is still refused, and for the right reason",
       not missed, f"fell through to a generic refusal: {missed}", P)
    # B13b — the four properties the pattern language now rests on. The last
    #        clause of this case used to assert the OPPOSITE of the fourth:
    #        `path_matches("x/y/README.md", "*.md")` was True, because any
    #        slash-free pattern was re-matched against the basename at any depth.
    #        That is what put `docs/product/pillars/README.md` in the fast lane
    #        with auto_merge TRUE. The fallback is deleted; a bare name is root-only.
    #
    #        The third and fourth clauses together are what made deleting it safe:
    #        `**/` means "zero or more whole directories", and ZERO is why a
    #        `**/`-prefixed pattern still catches the root file. Without that,
    #        rewriting every slash-free DENY to `**/` would have quietly stopped
    #        denying the root copies while appearing to fix the depth ones.
    ok("glob semantics are real (`*` stops at `/`, `**` does not, bare = root only)",
       path_matches("a/b.py", "a/*")
       and not path_matches("a/b/c.py", "a/*")
       and path_matches("a/b/c.py", "a/**")
       and not path_matches("x/y/README.md", "*.md")      # fallback is GONE
       and path_matches("x/y/README.md", "**/*.md")       # depth, asked for
       and path_matches("README.md", "**/*.md"),          # ...and still the root
       "the matcher does not mean what it looks like", ["path_matches"])

    # 7. NEGATIVE CONTROL. The safe changes must pass cleanly, or the cage is
    #    just a wall. B10: a co-located frontend unit test is the safest change
    #    that exists here, and 24 of them were refused as unknown paths.
    safe = ["docs/README.md", "backend/tests/test_x.py",
            "frontend/src/components/profile/PreferencesForm.test.tsx",
            "frontend/src/lib/__tests__/api.test.ts", "frontend/src/middleware.test.ts"]
    v_safe = check_paths(safe)
    ok("NEGATIVE CONTROL (a co-located frontend unit test passes the path cage)",
       v_safe.status == "pass", f"the cage refused a change it should allow: {v_safe.reasons}", P)

    # ── CAGE: SIZE ───────────────────────────────────────────────────────────
    red("deleting a file is refused",
        check_size(1, 10, ["backend/src/x.py"]), "must not delete", ["check_size"])
    red("a change too wide to reason about is refused",
        check_size(999, 10, []), "judgement call by size", ["check_size"])

    # ── CAGE: PROOF ──────────────────────────────────────────────────────────
    red("an empty check list is refused, not read as 'no failures'",
        judge_check_runs([], 0), "NO CHECKS RAN", ["judge_check_runs"])
    done = [{"name": n, "status": "completed", "conclusion": "success", "output": {}}
            for n in REQUIRED_CHECKS]
    red("a required check that is absent is refused",
        judge_check_runs(done[1:], len(done) - 1), "cannot pass by being absent",
        ["judge_check_runs"])
    red("a failing check is refused",
        judge_check_runs(done[:-1] + [{"name": "CodeQL", "status": "completed",
                                       "conclusion": "failure", "output": {}}], len(done)),
        "-> failure", ["judge_check_runs"])
    red("a check still running is refused",
        judge_check_runs(done[:-1] + [{"name": "CodeQL", "status": "in_progress",
                                       "conclusion": None, "output": {}}], len(done)),
        "has not finished", ["judge_check_runs"])
    # The measured live hole: green conclusion, new alert in the title.
    red("a GREEN check reporting a NEW security alert is still refused",
        judge_check_runs(done[:-1] + [{"name": "CodeQL", "status": "completed",
                                       "conclusion": "success",
                                       "output": {"title": "1 new alert including 1 medium "
                                                           "severity security vulnerability"}}],
                         len(done)),
        "NEW security alert", ["judge_check_runs"])
    ok("NEGATIVE CONTROL (a clean, complete check list passes)",
       not judge_check_runs(done, len(done)), "the cage refused a green PR",
       ["judge_check_runs"])
    # B14 — a full page is not a complete answer.
    red("a full page of results refuses instead of judging a partial list",
        page_was_full(PER_PAGE, PER_PAGE, "changed-files"), "page limit", ["page_was_full"])
    ok("NEGATIVE CONTROL (a short page is not treated as truncated)",
       not page_was_full(3, PER_PAGE, "changed-files"), "a short page was called truncated",
       ["page_was_full"])

    # ── CAGE: REVIEW ─────────────────────────────────────────────────────────
    # B07 — the exact shapes measured on PR #336 (all resolved) and #258 (open).
    thread_open = {"isResolved": False, "isOutdated": False, "path": "a.py", "line": 1,
                   "comments": {"nodes": [{"author": {"login": "coderabbitai"}}]}}
    thread_done = {"isResolved": True, "isOutdated": True, "path": "b.py", "line": 2,
                   "comments": {"nodes": [{"author": {"login": "coderabbitai"}}]}}
    red("an unresolved review thread is refused",
        judge_threads([thread_open, thread_done], False), "unresolved review thread",
        ["judge_threads"])
    ok("a resolved review thread does not block; an unresolved one does",
       not judge_threads([thread_done, thread_done], False)
       and bool(judge_threads([thread_open], False)),
       "resolved threads still block — the owner cannot clear this", ["judge_threads"])
    # An outdated-but-unresolved thread must STILL block: otherwise a whitespace
    # commit clears every open finding.
    ok("an OUTDATED unresolved thread still blocks (no whitespace-commit bypass)",
       bool(judge_threads([{**thread_open, "isOutdated": True}], False)),
       "outdated was treated as resolved — that is a one-commit bypass", ["judge_threads"])
    red("more review threads than I can see is refused",
        judge_threads([], True), "more than 100 review threads", ["judge_threads"])

    # ── CAGE: RATCHET ────────────────────────────────────────────────────────
    cases = [("down", 0, 5, True), ("down", 5, 0, False), ("down", 3, 3, False),
             ("up", 10, 3, True), ("up", 3, 10, False)]
    bad = [c for c in cases if is_worse(c[0], c[1], c[2]) != c[3]]
    ok("a quality number going backwards is refused (and improving is not)",
       not bad, f"wrong verdict for {bad}" if bad else "", ["is_worse"])

    # B04 — an un-baselined ratchet is NOT CHECKED, NOT CHECKED refuses, and the
    #       allow text may not claim it. This is the exact hole the cage sat in.
    v_nb = check_ratchets(None)
    claimed = plain_english(1, {"title": "x: y"}, True, [v_nb])
    ok("an un-baselined ratchet is NOT CHECKED and refuses, and no allow text claims it",
       v_nb.status == "not_checked" and v_nb.blocks
       and "no quality number went backwards" not in claimed
       and bool(blocks_of([v_nb])),
       f"status={v_nb.status} blocks={v_nb.blocks} text={claimed[:120]}",
       ["check_ratchets", "measure_ratchets", "plain_english", "blocks_of"])

    # B05 — the cage's number must equal the number its consumer reports.
    consumer = subprocess.run([sys.executable, "backend/scripts/mypy_ratchet.py", "--count"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(ROOT), timeout=120)
    cage_reading = measure_ratchets().get("mypy errors") or {}
    cage_val = cage_reading.get("value")
    same = consumer.returncode == 0 and cage_reading.get("status") == R_OK \
        and int(consumer.stdout.strip().splitlines()[-1]) == cage_val
    ok("the cage's mypy number equals the number its consumer reports",
       same, f"cage={cage_val} consumer={consumer.stdout.strip()!r} rc={consumer.returncode}",
       ["measure_ratchets"])

    # B06 — a ratchet that cannot measure REFUSES; it never reads as 0 or 999.
    saved = list(RATCHETS)
    try:
        RATCHETS[:] = [{"name": "drill", "cmd": [sys.executable, "-c", "raise SystemExit(2)"],
                        "direction": "down", "scope": "tree", "why": "drill"}]
        red("an unmeasurable ratchet refuses instead of reading as perfect",
            check_ratchets({"drill": 0}), "could not be measured",
            ["check_ratchets", "measure_ratchets"])
        RATCHETS[:] = [{"name": "drill", "cmd": [sys.executable, "-c", "print(7)"],
                        "direction": "down", "scope": "tree", "why": "drill"}]
        red("a ratchet that got worse is refused with both numbers named",
            check_ratchets({"drill": 3}, expected_sha=head_sha()), "3 -> 7",
            ["check_ratchets"])
        v_ok = check_ratchets({"drill": 9}, expected_sha=head_sha())
        ok("NEGATIVE CONTROL (an improving ratchet passes)", v_ok.status == "pass",
           f"improving ratchet was refused: {v_ok.reasons}", ["check_ratchets"])

        # B21 — THE THREE ANSWERS. `absent` and `error` were one value (None) and
        # the difference decides whether a main-based PR can ever be judged:
        # `scripts/drill_registry.py` is NOT on origin/main (measured 2026-08-17,
        # `git ls-tree origin/main` is empty for it), so that ratchet is absent on
        # BOTH sides of every main-based PR. Folding absent into error refused
        # 100% of them for a fact about history, not about the PR.
        RATCHETS[:] = [
            {"name": "present", "cmd": [sys.executable, "-c", "print(1)"],
             "direction": "down", "scope": "tree", "why": "drill"},
            {"name": "gone", "cmd": [sys.executable, "-c", "print(1)"],
             "needs": "no/such/file/anywhere.py",
             "direction": "down", "scope": "tree", "why": "drill"},
        ]
        vals = measure_ratchets()
        ok("a ratchet whose own script is missing reads ABSENT, not ERROR and not a number",
           vals["gone"]["status"] == R_ABSENT and vals["present"]["status"] == R_OK
           and vals["gone"]["value"] is None,
           f"got {vals}", ["measure_ratchets", "capability_gap"])

        # B24 — the live one. The FILE was there and the FLAG was not, so a
        # file-existence probe called a missing instrument a broken one and
        # refused 16 of 16 merged PRs with `usage: mypy_ratchet.py [-h]
        # [--update]` as the explanation.
        ok("a script that exists but does not carry the flag reads ABSENT, not ERROR",
           capability_gap(ROOT, {"file": "backend/scripts/mypy_ratchet.py",
                                 "supports": "--no-such-flag-anywhere"}).endswith(
               "it cannot answer this question")
           and capability_gap(ROOT, {"file": "backend/scripts/mypy_ratchet.py",
                                     "supports": "--count"}) == ""
           and capability_gap(ROOT, {"file": "nope/nope.py"}) != "",
           "the capability probe cannot tell a missing flag from a working one",
           ["capability_gap"])

        # base absent + head absent -> NOT APPLICABLE, named, does not block.
        v_na = check_ratchets({"present": 1, "gone": {"status": R_ABSENT, "value": None}},
                              expected_sha=head_sha())
        ok("a ratchet absent from BOTH base and PR is named as not-compared, and does not block",
           v_na.status == "pass" and "does not exist in this lineage" in v_na.claim
           and "1 quality number(s) compared" in v_na.claim,
           f"status={v_na.status} claim={v_na.claim!r} reasons={v_na.reasons}",
           ["check_ratchets", "as_reading"])

        # THE DANGEROUS DIRECTION: base could measure it, the PR cannot.
        red("a PR that makes a measurable ratchet unmeasurable is refused",
            check_ratchets({"present": 1, "gone": 4}, expected_sha=head_sha()),
            "removes a ratchet removes every future comparison", ["check_ratchets"])

        # ZERO COMPARISONS IS NOT A PASS — otherwise the n/a arm above is a way
        # for the whole RATCHET cage to evaporate on a tree with no ratchets.
        RATCHETS[:] = [{"name": "gone", "cmd": [sys.executable, "-c", "print(1)"],
                        "needs": "no/such/file/anywhere.py",
                        "direction": "down", "scope": "tree", "why": "drill"}]
        red("a PR where NOTHING could be compared is refused, not passed",
            check_ratchets({"gone": {"status": R_ABSENT, "value": None}},
                           expected_sha=head_sha()),
            "not one ratchet could be compared", ["check_ratchets"])

        # A flat `null` in a baseline is an ERROR, never `absent`. The old format
        # could not say "the script was not there", so reading the harmless
        # meaning into it would be the permissive guess.
        RATCHETS[:] = [{"name": "drill", "cmd": [sys.executable, "-c", "print(1)"],
                        "direction": "down", "scope": "tree", "why": "drill"}]
        red("a baseline entry of `null` refuses instead of being read as 'not applicable'",
            check_ratchets({"drill": None}, expected_sha=head_sha()),
            "no readable value in the baseline", ["check_ratchets", "as_reading"])
        ok("as_reading never turns a non-number into a number",
           as_reading(3)["status"] == R_OK and as_reading(None)["status"] == R_ERROR
           and as_reading(True)["status"] == R_ERROR and as_reading("0")["status"] == R_ERROR
           and as_reading({"status": R_ABSENT, "value": None})["status"] == R_ABSENT
           and as_reading({"status": "nonsense"})["status"] == R_ERROR,
           "a baseline value was coerced into something it did not say", ["as_reading"])
    finally:
        RATCHETS[:] = saved

    # B06 — measuring the wrong tree is refused, not quietly compared.
    ok("the cage refuses when it is not standing in the repo it judges",
       bool(ground_problem("a" * 40, "b" * 40)) and bool(ground_problem("", "b" * 40))
       and ground_problem("a" * 40, "a" * 40) is None,
       "a comparison against the wrong tree was allowed to stand", ["ground_problem"])

    # ── THE WHOLE CAGE, END TO END, WITH A FAKE GITHUB ────────────────────────
    # check_proof and check_review were never drilled — the two cages that talk to
    # the network, and the two that carried real bugs. Drilled here by feeding
    # them real response SHAPES through the real functions, not by asserting on
    # their internals.
    saved_gh = globals()["gh"]
    try:
        def fake_gh(a: list[str]) -> str:
            joined = " ".join(a)
            if "graphql" in joined:
                return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
                    "pageInfo": {"hasNextPage": False}, "nodes": [thread_done]}}}}})
            if a[-1] == ".head.sha":
                return "d" * 40
            if "check-runs" in joined:
                return json.dumps({"total_count": len(done), "check_runs": done})
            raise RuntimeError(f"the drill did not expect: {joined}")
        globals()["gh"] = fake_gh
        v_proof = check_proof(1)
        ok("check_proof passes a clean, complete check list end to end",
           v_proof.status == "pass", f"{v_proof.status}: {v_proof.reasons}", ["check_proof"])
        v_rev = check_review(1)
        ok("check_review passes when every thread is resolved end to end",
           v_rev.status == "pass", f"{v_rev.status}: {v_rev.reasons}", ["check_review"])
    finally:
        globals()["gh"] = saved_gh

    # slack() must fail LOUDLY, never quietly. An alert path that exits 0 having
    # sent nothing kept this harness mute for weeks.
    saved_token = os.environ.pop("SLACK_BOT_TOKEN", None)
    try:
        ok("slack with no token returns False instead of pretending it spoke",
           slack("x", "y") is False, "slack() claimed success with no token", ["slack"])
    finally:
        if saved_token is not None:
            os.environ["SLACK_BOT_TOKEN"] = saved_token

    # ── SELF-CONSISTENCY ─────────────────────────────────────────────────────
    # B09 — dead configuration is a hard error.
    live = check_lists()
    probe = _shadow_probe()
    ok("an allow entry shadowed by a deny pattern stops the cage dead",
       not live and bool(probe),
       f"the live lists already contradict themselves: {live}" if live
       else "check_lists cannot see a shadowed allow entry",
       ["check_lists"])

    # B03 — the exit codes must be four different numbers, or the caller's crash
    #       arm is unreachable dead code, which is how this repo got here.
    codes = [EXIT_ALLOW, EXIT_REFUSE, EXIT_CANNOT_TELL_OWNER, EXIT_CAGE_BROKE, EXIT_USAGE]
    ok("crash, refuse, slack-failure and usage have four different exit codes",
       len(set(codes)) == len(codes), f"collision in {codes}", [])

    # B22 — --tree separates the tree MEASURED from the tree the RULES come from.
    #       Without it the only way to measure a PR's own numbers was to run the
    #       PR's own copy of this file, i.e. to let the thing being judged supply
    #       the judge. Both halves are drilled: a junk --tree must BREAK the cage
    #       (exit 3, never a quiet fallback to "here"), and with no --tree the
    #       measured tree must still be exactly ROOT.
    def run_main(argv: list[str]) -> int:
        """main() end to end, including the argparse exits it deliberately raises."""
        try:
            return main(argv)
        except SystemExit as e:
            return int(e.code or 0)

    rc_tree = run_main(["1", "--tree", str(ROOT / "no-such-directory-anywhere")])
    ok("a --tree that is not a directory is a usage error, not a silent fallback to here",
       rc_tree == EXIT_USAGE, f"exit {rc_tree} (expected {EXIT_USAGE})", ["measured_tree"])
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        rc_nogit = run_main(["1", "--tree", _td])
        ok("a --tree that is not a git checkout breaks the cage instead of being measured",
           rc_nogit == EXIT_CAGE_BROKE, f"exit {rc_nogit} (expected {EXIT_CAGE_BROKE})",
           ["measured_tree"])
    globals()["MEASURE_TREE"] = None
    ok("with no --tree, the measured tree is exactly the tree the rules came from",
       measured_tree() == ROOT, f"{measured_tree()} != {ROOT}", ["measured_tree"])

    # B25 — argparse abbreviates unique prefixes by default. `--dri` silently
    #       meaning `--drill` is a cage whose MODE can be changed by a typo, and
    #       cage_replay.py's own drill caught the live version of this
    #       (`--merge` was accepted as `--merged`).
    rc_abbrev = run_main(["--dril"])
    ok("an abbreviated flag is rejected, not silently expanded into a different mode",
       rc_abbrev == EXIT_USAGE, f"exit {rc_abbrev} (expected {EXIT_USAGE}) — `--dril` was "
                                f"accepted as `--drill`", [])

    # B23 — --replay used to print an agreement rate that was 0/N by
    #       construction (no baseline -> not_checked -> blocks). A convincing
    #       wrong number is worse than no number, so it must not answer at all.
    rc_replay = run_main(["--replay", "5"])
    ok("--replay refuses to answer instead of printing an agreement rate it cannot compute",
       rc_replay == EXIT_USAGE, f"exit {rc_replay} (expected {EXIT_USAGE})", [])

    # B01/B02 — garbage on any input surface must produce a REFUSAL, not a stack
    #           trace. Fed end to end through main(), not asserted on internals.
    #           EXIT_REFUSE specifically, not merely "not a traceback": a known
    #           bad input deserves a clean verdict naming the flag, and falling
    #           through to the catch-all (exit 3, "the cage broke") would mean the
    #           parse had drifted back outside its guard.
    rc_bad_baseline = main(["1", "--baseline", "not-json"])
    ok("a malformed --baseline refuses cleanly, naming the flag, instead of crashing",
       rc_bad_baseline == EXIT_REFUSE,
       f"exit {rc_bad_baseline} (expected {EXIT_REFUSE}; {EXIT_CAGE_BROKE} means it escaped "
       f"to the catch-all instead of being handled where it is parsed)", ["decide"])

    # The OUTERMOST net, drilled by forcing something no handler expects all the
    # way through main(). Found by mutation testing: removing the catch-all left
    # every other crash case green, because they are all caught one layer lower.
    # An untested last line of defence is not a last line of defence.
    saved_decide = globals()["decide"]
    try:
        def exploding_decide(*_a: object, **_k: object) -> tuple[bool, list[Verdict], dict]:
            raise MemoryError("something no handler anticipated")
        globals()["decide"] = exploding_decide
        rc_boom = main(["1"])
        ok("an exception nobody anticipated becomes a REFUSE, not a traceback",
           rc_boom == EXIT_CAGE_BROKE,
           f"exit {rc_boom} (expected {EXIT_CAGE_BROKE}) — the catch-all around main() is gone",
           ["decide"])
    finally:
        globals()["decide"] = saved_decide

    saved_gh = globals()["gh"]
    try:
        def dead_gh(_a: list[str]) -> str:
            raise FileNotFoundError(2, "No such file or directory", "gh")
        globals()["gh"] = dead_gh
        allowed_x, v_x, _ = decide(1)
        ok("gh vanishing mid-judgement refuses instead of crashing",
           not allowed_x and any("FileNotFoundError" in r for r in blocks_of(v_x)),
           f"allowed={allowed_x} reasons={blocks_of(v_x)[:1]}", ["decide", "blocks_of"])

        def slow_gh(_a: list[str]) -> str:
            raise subprocess.TimeoutExpired("gh", 120)
        globals()["gh"] = slow_gh
        allowed_t, v_t, _ = decide(1)
        ok("gh timing out refuses instead of crashing",
           not allowed_t and any("TimeoutExpired" in r for r in blocks_of(v_t)),
           f"allowed={allowed_t} reasons={blocks_of(v_t)[:1]}", ["decide"])
    finally:
        globals()["gh"] = saved_gh

    # B08 — every refusal must say what would make it mergeable.
    sample_reasons: list[str] = []
    for v in (check_paths(["backend/src/services/uk_gate.py", "backend/src/zzz.py",
                           "frontend/package.json"]),
              check_size(999, 99999, ["a.py"]),
              check_ratchets(None)):
        sample_reasons += v.reasons
    sample_reasons += judge_check_runs([], 0)
    sample_reasons += judge_check_runs(done[1:], len(done) - 1)
    sample_reasons += judge_threads([thread_open], True)
    vague = [r for r in sample_reasons if "FIX:" not in r]
    ok("every refusal reason says what would make it mergeable",
       not vague, f"{len(vague)} reason(s) with no FIX clause, e.g. {vague[:1]}",
       ["check_paths", "check_size", "judge_check_runs", "judge_threads", "check_ratchets"])

    # ── THE CAGE MUST BE ABLE TO SAY YES ─────────────────────────────────────
    # B18. Forty-four drills proved this cage can say NO. Not one proved it can
    # say YES, which is exactly why nobody noticed that ALLOW was unreachable in
    # production for the cage's whole life: auto-merge.yml:114 passed no
    # `--baseline`, `check_ratchets(None)` returns `not_checked`, and
    # `not_checked` blocks. Every PR was refused for a reason about the WIRING.
    # A gate that cannot say yes is the same defect class as a guard that cannot
    # say no, and it is worse in one way: it gets switched off, and then it
    # protects nothing.
    saved_gh = globals()["gh"]
    saved_r = list(RATCHETS)
    try:
        sha_now = head_sha()

        def perfect_gh(a: list[str]) -> str:
            j = " ".join(a)
            if "graphql" in j:
                return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
                    "pageInfo": {"hasNextPage": False}, "nodes": [thread_done]}}}}})
            if a[-1] == ".head.sha":
                return "d" * 40
            if "check-runs" in j:
                return json.dumps({"total_count": len(done), "check_runs": done})
            if "/files" in j:
                return json.dumps([{"filename": "docs/README.md", "status": "modified",
                                    "additions": 3, "deletions": 1}])
            if j.endswith("pulls/1"):
                return json.dumps({"title": "docs: tidy the readme",
                                   "merge_commit_sha": sha_now, "base": {"ref": "main"}})
            raise RuntimeError(f"the drill did not expect: {j}")

        globals()["gh"] = perfect_gh
        RATCHETS[:] = [{"name": "drill", "cmd": [sys.executable, "-c", "print(3)"],
                        "direction": "down", "scope": "tree", "why": "drill"}]
        allowed_y, v_y, meta_y = decide(1, {"drill": 3})
        ok("ALLOW IS REACHABLE (a perfect docs PR with a baseline is allowed end to end)",
           allowed_y, f"the cage refused a PR with nothing wrong with it: {blocks_of(v_y)}",
           ["decide", "check_proof", "check_review", "check_ratchets"])

        md_yes = advice_markdown(1, meta_y, allowed_y, v_y)
        ok("the advice for an allowed PR is labelled agent-safe and merges nothing",
           LABEL_SAFE in md_yes and MARKER in md_yes and "no cage reported anything" not in md_yes,
           f"advice text was wrong: {md_yes[:200]}", ["advice_markdown", "advice_label"])

        # The SAME PR, one field changed: base is a feature branch. codeql.yml
        # only fires for main-targeted PRs, so `CodeQL` cannot exist here.
        # Measured on PRs #343/#344/#345/#346, all four missing both Analyze jobs.
        def stacked_gh(a: list[str]) -> str:
            if " ".join(a).endswith("pulls/1"):
                return json.dumps({"title": "docs: tidy", "merge_commit_sha": sha_now,
                                   "base": {"ref": "feat/every-guard-declares-its-drill"}})
            return perfect_gh(a)

        globals()["gh"] = stacked_gh
        allowed_s, v_s, _ = decide(1, {"drill": 3})
        ok("a PR based on a feature branch is refused, naming the check that cannot fire",
           not allowed_s and any("cannot exist" in r and "CodeQL" in r for r in blocks_of(v_s)),
           f"allowed={allowed_s} reasons={blocks_of(v_s)[:2]}",
           ["decide", "judge_check_runs", "check_proof"])
    finally:
        globals()["gh"] = saved_gh
        RATCHETS[:] = saved_r

    # B19 — a missing base ref must not be guessed as `main`. The permissive
    # guess is the one that unlocks the full check list, and guessing in the
    # permissive direction is the whole thing this file refuses to do.
    red("a PR whose base ref could not be read is refused, not assumed to be main",
        judge_check_runs(done, len(done), ""), "not `main`", ["judge_check_runs"])
    ok("NEGATIVE CONTROL (a main-based PR with a full check list still passes)",
       not judge_check_runs(done, len(done), "main"),
       "base-awareness broke the ordinary main-targeted case", ["judge_check_runs"])

    # B17 — the merge capability is GONE, not defaulted off. A default can be
    # flipped by a repo variable nobody reviews; a deleted flag cannot. Asserted
    # end to end through main(), not by grepping for the string — a self-test
    # that greps stayed fully green here after the only authorisation call was
    # deleted.
    # argparse signals a usage error by RAISING (`_Parser.error` -> SystemExit),
    # so this must catch it. Asserting on a return value alone would have made
    # this case blow the drill up rather than report — a self-test that crashes
    # is not a self-test that passed.
    try:
        rc_merge = main(["1", "--merge"])
    except SystemExit as exc:
        rc_merge = int(exc.code or 0)
    ok("`--merge` no longer exists: the cage cannot merge anything at all",
       rc_merge == EXIT_USAGE,
       f"exit {rc_merge} (expected {EXIT_USAGE}) — merge_cage still accepts a merge flag",
       ["decide"])

    # ── COVERAGE + THE BLOCKER LOG ───────────────────────────────────────────
    missing = [f for f in DECISION_PATH if f not in touched]
    ok("COVERAGE (every function on the decision path is drilled)",
       not missing, f"undrilled: {', '.join(missing)}", [])

    names = {n for n, _, _ in results}
    orphan = [b.id for b in BLOCKERS if b.drill and b.drill not in names]
    no_drill = [b.id for b in undrilled()]
    ok("THE BLOCKER LOG (every recorded dead end names a drill that exists)",
       not orphan and not no_drill,
       f"undrilled blockers: {no_drill}; blockers naming a drill that does not exist: {orphan}",
       [])

    print()
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if passed and detail:
            print(f"         REFUSED -> {detail[:140]}")
        elif not passed and detail:
            print(f"         {detail[:220]}")

    n = sum(1 for _, p, _ in results if p)
    print()
    print(f"COVERED {len(DECISION_PATH) - len(missing)}/{len(DECISION_PATH)} decision-path "
          f"functions; {len(BLOCKERS)} blockers on record, all drilled."
          if not missing and not orphan and not no_drill else
          f"COVERAGE INCOMPLETE — undrilled: {missing or '-'}; orphan blockers: {orphan or '-'}")
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The cage did not refuse something it claims to refuse. Do not trust it.")
        return 1
    print("Every unsafe change was refused and named; the safe change was allowed.")
    return 0


def _shadow_probe() -> list[str]:
    """Inject a deliberately shadowed ALLOW entry and see whether check_lists sees it."""
    saved = list(ALLOW)
    try:
        ALLOW.append("backend/src/services/profile/storage.py")  # the real historic pair
        return check_lists()
    finally:
        ALLOW[:] = saved


# ─────────────────────────────────────────────────────────────────────────────


def replay(limit: int) -> int:
    """DELETED AS AN ANSWER, KEPT AS A SIGNPOST.

    This function used to loop `decide(pr)` with NO baseline. `check_ratchets(None)`
    returns `not_checked` and `not_checked` blocks, so it returned 0/N by
    construction — for every N, on every repo, forever. It could not have produced
    the "13 of 245 (5.3%)" figure that has been quoted from it, and any decision
    resting on that number is resting on nothing.

    A real replay needs a checkout per PR (the base, to measure the baseline, and
    `refs/pull/<N>/merge`, to measure the PR's own tree). That is worktree
    management, so it lives in a runner: scripts/cage_replay.py. Leaving a
    convincing-looking wrapper here would just be a second place for the same lie
    to come from.
    """
    print("merge_cage --replay no longer answers, because the answer it used to give was "
          "0/N by construction.", file=sys.stderr)
    print(f"  It called decide(pr) with no --baseline, so RATCHET returned `not_checked` and "
          f"`not_checked` blocks. Every PR refused, whatever the PR was.\n"
          f"  FIX: `python scripts/cage_replay.py --merged {limit}` — it does the two "
          f"checkouts per PR that a tree-scoped ratchet actually requires.", file=sys.stderr)
    return EXIT_USAGE


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which the caller reads as 'could not
    reach Slack — stop the sweep'. A bad flag must not be reported as an outage."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"merge_cage: usage error — {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev=False. argparse abbreviates unique prefixes by default, so a
    # flag that does not exist can still be ACCEPTED as a shortening of one that
    # does — cage_replay.py's own drill caught exactly that (`--merge` silently
    # became `--merged`). On a repo where merging is deploying, a flag that means
    # something other than what it says is a trapdoor, not a convenience.
    ap = _Parser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("pr", nargs="?", type=int, help="PR number to judge")
    ap.add_argument("--drill", action="store_true", help="break the cage on purpose")
    ap.add_argument("--slack", action="store_true", help="announce the verdict in Slack")
    ap.add_argument("--advise", metavar="FILE",
                    help="write a markdown verdict for a PR comment; NEVER merges")
    ap.add_argument("--verdict-json", metavar="FILE",
                    help="write the per-cage verdicts as JSON. Added because the only way to "
                         "ask 'which cage refused, and would it still refuse tomorrow?' was to "
                         "grep the English reasons — and a reader that greps prose is the same "
                         "instrument-shaped mistake as a self-test that greps its own source.")
    ap.add_argument("--baseline", help="JSON of ratchet values measured on the PR's base")
    ap.add_argument("--measure", action="store_true", help="print current ratchet values as JSON")
    ap.add_argument("--tree", metavar="DIR",
                    help="the checkout whose NUMBERS are measured (default: this file's own). "
                         "The RULES always come from this file's checkout — that separation is "
                         "the point: a PR must not be judged by its own copy of the cage.")
    ap.add_argument("--blockers", action="store_true", help="print the blocker log and exit")
    ap.add_argument("--replay", type=int, metavar="N",
                    help="judge the last N merged PRs and print the agreement rate")
    args = ap.parse_args(argv)

    # EVERYTHING below runs inside one guard. A cage may fail; it may never fail
    # ambiguously. Any escaping exception becomes a REFUSAL that names itself and
    # exits with its own code, so the caller's crash arm is reachable by a real
    # input instead of being dead code.
    try:
        if args.blockers:
            from cage_blockers import render, undrilled
            print(render())
            return EXIT_REFUSE if undrilled() else EXIT_ALLOW

        # Ground first. Every ratchet below reads repo-relative paths, so judging
        # from the wrong tree measures the wrong thing and says nothing about it.
        if _GROUND_ERROR:
            raise CageBroke(_GROUND_ERROR)

        # Self-consistency before anything else: rules that contradict each other
        # cannot judge, and a rule that can never fire reads as a permission.
        contradictions = check_lists()
        if contradictions:
            raise CageBroke("the cage's own rules contradict each other, so it cannot judge:\n  "
                            + "\n  ".join(contradictions))

        # THE TREE UNDER MEASUREMENT. Set before anything reads a number.
        global MEASURE_TREE
        if args.tree:
            t = Path(args.tree).resolve()
            if not t.is_dir():
                ap.error(f"--tree {args.tree} is not a directory")
            probe = subprocess.run(["git", "-C", str(t), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=30)
            if probe.returncode != 0:
                # A tree whose HEAD cannot be read cannot be ground-checked, and a
                # ratchet compared without a ground check is a number about an
                # unknown tree. Refuse where it is parsed, not six frames later.
                raise CageBroke(
                    f"--tree {t} is not a git checkout I can read a commit from "
                    f"({probe.stderr.strip()[:120]}), so I could not prove which tree the "
                    f"numbers belong to. FIX: point --tree at a `git worktree add` of "
                    f"`refs/pull/<N>/merge`.")
            MEASURE_TREE = t

        if args.drill:
            return self_drill()
        if args.measure:
            print(json.dumps(measure_ratchets()))
            return EXIT_ALLOW
        if args.replay:
            return replay(args.replay)
        if args.pr is None:
            ap.error("a PR number is required unless --drill, --measure, --replay or --blockers")

        try:
            base = json.loads(args.baseline) if args.baseline else None
            if base is not None and not isinstance(base, dict):
                raise ValueError("expected a JSON object of {ratchet name: number}")
        except Exception as exc:
            # Not a crash and not a silent None: a baseline I cannot read is a
            # ratchet I cannot compare, which is a refusal.
            print(f"merge_cage: PR #{args.pr}")
            print("VERDICT: ASK THE OWNER — 1 reason(s)")
            print(f"  * --baseline is not readable JSON ({exc}) — I will not judge with a "
                  f"baseline I cannot parse. FIX: pass the exact output of "
                  f"`python scripts/merge_cage.py --measure`.")
            return EXIT_REFUSE

        allowed, verdicts, meta = decide(args.pr, base)
        blocks = blocks_of(verdicts)

        print(f"merge_cage: PR #{args.pr} — {meta.get('files', '?')} files, "
              f"{meta.get('lines', '?')} lines")
        if allowed:
            claims = [v.claim for v in verdicts if v.status == "pass" and v.claim]
            print("VERDICT: ALLOW — " + "; ".join(claims) + ".")
        else:
            print(f"VERDICT: ASK THE OWNER — {len(blocks)} reason(s)")
            for b in blocks:
                print(f"  * {b}")
            skipped = [v.name for v in verdicts if v.status == "not_checked"]
            if skipped:
                print(f"  (cages that did NOT run: {', '.join(skipped)} — "
                      f"not checked is not passed)")

        if args.slack:
            chan = "ready-to-merge" if allowed else "needs-your-decision"
            if not slack(chan, plain_english(args.pr, meta, allowed, verdicts)):
                # Announcing is part of the job. A merge nobody was told about is
                # indistinguishable from a merge that never happened.
                print("REFUSING TO PROCEED: the owner could not be told.", file=sys.stderr)
                return EXIT_CANNOT_TELL_OWNER

        if args.advise:
            Path(args.advise).write_text(advice_markdown(args.pr, meta, allowed, verdicts),
                                         encoding="utf-8")
            print(f"advice written to {args.advise} (label: {advice_label(allowed)})")

        if args.verdict_json:
            Path(args.verdict_json).write_text(json.dumps({
                "pr": args.pr, "allowed": allowed, "meta": meta,
                "cages": [{"name": v.name, "status": v.status, "reasons": v.reasons,
                           # The claim is emitted ONLY for a cage that passed —
                           # the same rule the printed output obeys. A machine
                           # reader must not be able to see a sentence a human
                           # reader is forbidden.
                           "claim": v.claim if v.status == "pass" else ""}
                          for v in verdicts],
            }, indent=1), encoding="utf-8")

        return EXIT_ALLOW if allowed else EXIT_REFUSE

    except SystemExit:
        raise
    except CageBroke as exc:
        print(f"VERDICT: REFUSE — THE CAGE ITSELF BROKE.\n  * {exc}", file=sys.stderr)
        return EXIT_CAGE_BROKE
    except Exception as exc:
        import traceback
        print(f"VERDICT: REFUSE — THE CAGE ITSELF BROKE.\n"
              f"  * unexpected {type(exc).__name__}: {exc}\n"
              f"  * FIX: this is a bug in scripts/merge_cage.py. Nothing was merged, which is "
              f"the safe direction. Add the input that caused it to scripts/cage_blockers.py.",
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_CAGE_BROKE


# Resolved once, at import, so every ratchet and every git call below knows which
# tree it is standing in. The failure is REMEMBERED, not swallowed: main() raises
# it before judging anything. A ground error that only logged would be the same
# shape as the ratchet that read a missing file as perfect health.
try:
    ROOT = repo_root()
    _GROUND_ERROR: str | None = None
except CageBroke as _exc:
    ROOT = Path(__file__).resolve().parent.parent
    _GROUND_ERROR = str(_exc)

# The tree whose NUMBERS are read, when it is not ROOT. `None` until --tree says
# otherwise, so the default behaviour is byte-for-byte what it was.
MEASURE_TREE: Path | None = None

if __name__ == "__main__":
    raise SystemExit(main())
