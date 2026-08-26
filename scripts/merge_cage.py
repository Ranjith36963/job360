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

# ─────────────────────────────────────────────────────────────────────────────
# ONE LIST, TWO READERS  (2026-08-26)
#
# ALLOW and DENY used to be typed out HERE, beside `.github/merge-policy.yml`,
# which classifies the very same paths for `scripts/lane.py`. Two lists
# answering one question drift, and these had — far past drift, into flat
# contradiction. Measured on this tree the day before this change:
#
#   of the 38 paths the two FAST lanes declared auto-mergeable, SIX could
#   actually pass this cage.
#
#   `backend/src/**` and `frontend/src/**` — the entire product — read as
#   "no rule covers this path". `scripts/**` and `.claude/**` were declared
#   fast-lane by the policy and DENIED outright here. The cage won every time,
#   silently, and the policy read like a promise the machine could not keep.
#
# So the policy is now the only place a path is classified, and this file
# DERIVES its two lists from it:
#
#   lane with `auto_merge: true`   ->  ALLOW
#   lane with `auto_merge: false`  ->  DENY
#   no lane at all                 ->  refused, exactly as before
#
# What stays here is the SENTENCE each refusal prints. A reason is not a
# classification — it is the thing that makes a refusal actionable at 7am — and
# the policy is the owner's dial, not a home for twenty paragraphs. Any denied
# path without a sentence falls back to its lane's own description, so a new
# entry in the policy is never mute.
#
# `check_lists()` now fails the build on BOTH new failure modes: a reason that
# names a pattern the policy no longer has (dead configuration), and a fast-lane
# path that a deny pattern swallows whole (the contradiction described above).
# ─────────────────────────────────────────────────────────────────────────────

POLICY_PATH = Path(__file__).resolve().parent.parent / ".github" / "merge-policy.yml"

# pattern -> the sentence printed when it refuses. Keys MUST exist in the policy.
DENY_REASONS: dict[str, str] = {
    # ── product: anything that changes what a user is shown or how it ranks ──
    "backend/src/services/skill_matcher.py":
        f"changes how jobs are scored — a product decision. {_OWNER_MERGES}",
    "backend/src/services/deduplicator.py":
        f"changes which jobs are shown at all. {_OWNER_MERGES}",
    "backend/src/services/scoring/**":
        f"changes how jobs are scored — a product decision. {_OWNER_MERGES}",
    "backend/src/services/uk_gate.py":
        f"decides which jobs enter the catalogue. {_OWNER_MERGES}",
    "backend/src/services/visa_signal.py":
        f"changes what the visa spotlight shows. {_OWNER_MERGES}",
    "backend/src/services/profile/**":
        f"changes what is extracted from a user's CV. {_OWNER_MERGES}",
    "backend/src/models.py":
        f"normalized_key lives here; wrong dedup = duplicate or lost rows. {_OWNER_MERGES}",
    # ── users: identity, sessions, anything that can lock someone out ────────
    "backend/src/api/routes/auth.py": f"authentication — a user decision. {_OWNER_MERGES}",
    "backend/src/services/auth/**": f"authentication — a user decision. {_OWNER_MERGES}",
    "backend/src/api/routes/account*.py":
        f"account management — a user decision. {_OWNER_MERGES}",
    "backend/src/services/notifications/**":
        f"sends real messages to real people. {_OWNER_MERGES}",
    # A per-user API route is denied on its PATH because a path cage cannot tell a
    # logging fix from a deleted `Depends(require_user)`. PR #315 was +45 lines in
    # exactly this file and would have merged on its filename alone; rules #12/#25
    # exist because a review found three real IDORs in these routes.
    "backend/src/api/routes/profile.py":
        "a per-user API route — rules #12/#25. A filename cannot tell a logging fix "
        f"from a removed `Depends(require_user)`. {_OWNER_MERGES}",
    # ── infrastructure ───────────────────────────────────────────────────────
    "backend/migrations/**":
        f"schema change — irreversible against live data. {_OWNER_MERGES}",
    "**/*docker-compose*": f"infrastructure decision. {_OWNER_MERGES}",
    "**/*Dockerfile*": f"infrastructure decision. {_OWNER_MERGES}",
    "**/railway.json": f"infrastructure decision. {_OWNER_MERGES}",
    "**/*.env*": f"secrets and configuration. {_OWNER_MERGES}",
    "backend/src/core/settings.py":
        f"flags here change production behaviour globally. {_OWNER_MERGES}",
    # ── dependency manifests ─────────────────────────────────────────────────
    # THE CAGE MAY NEVER BE MORE PERMISSIVE THAN A GATE ALREADY ON MAIN. These
    # were on the ALLOW list, and the cage has no semver awareness, so it would
    # have waved through PR #291 (motion 12.42.2 -> 13.0.0) which
    # dependabot-auto.yml already routes to a human. Manifests also carry
    # postinstall scripts: allowing one is allowing arbitrary code on the build host.
    "**/package.json":
        "a dependency change — `.github/workflows/dependabot-auto.yml` already "
        "owns this decision and sends MAJOR bumps to a human; a manifest also "
        "runs postinstall scripts on the build host. "
        "FIX: let dependabot-auto decide it, or the owner merges it by hand.",
    "**/package-lock.json": "a dependency change — see package.json. "
        "FIX: let dependabot-auto decide it, or the owner merges it by hand.",
    "**/pyproject.toml": "a dependency change — see package.json. "
        "FIX: let dependabot-auto decide it, or the owner merges it by hand.",
    "**/requirements*.txt": "a dependency change — see package.json. "
        "FIX: let dependabot-auto decide it, or the owner merges it by hand.",
    # ── documents that ARE decisions ─────────────────────────────────────────
    # The rest of `docs/product/` moved to the fast lane on 2026-08-26 (owner's
    # call: a doc is prose). These two did not, because they are not prose: if a
    # denied file delegates its authority to another file, that file inherits
    # the denial.
    "docs/product/product_design_rules.md":
        "the canonical text of owner rules #29/#30/#31 — changing it changes the "
        f"product. {_OWNER_MERGES}",
    "docs/product/plans/batch-2-decisions.md":
        f"a record of irreversible choices. {_OWNER_MERGES}",
    # ── the harness must not quietly edit its own cage ───────────────────────
    ".github/**": "part of the harness that judges this very PR — an agent editing its "
                  f"own guards is how a cage is escaped. {_OWNER_MERGES}",
    "scripts/**": "part of the harness that judges this very PR — an agent editing its "
                  f"own guards is how a cage is escaped. {_OWNER_MERGES}",
    ".claude/**": "the instructions agents read — editing them unsupervised is circular. "
                  + _OWNER_MERGES,
    "**/CLAUDE.md": "the rules agents read; changing them unsupervised is circular. "
                    + _OWNER_MERGES,
}


def _load_lists() -> tuple[list[str], list[tuple[str, str]]]:
    """Read the lane map and split it into (allow, deny).

    Raises rather than defaulting. A missing, empty or malformed policy must
    never read as "no restrictions" — that is the `else: allow` bug wearing a
    different hat, and it is the one this whole file exists to prevent.

    This runs at IMPORT, before argparse, so a broken policy has to fail as a
    sentence rather than a traceback: `scripts/lane.py` imports this module, and
    a stack trace at import is the kind of failure people work around.
    """
    import yaml

    if not POLICY_PATH.exists():
        raise SystemExit(f"merge_cage: policy file not found: {POLICY_PATH}")
    try:
        loaded = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed YAML, bad encoding, anything
        raise SystemExit(
            f"merge_cage: {POLICY_PATH.name} could not be parsed, so no path is classified "
            f"and nothing may merge unattended: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise SystemExit(
            f"merge_cage: {POLICY_PATH.name} is not a mapping at its root "
            f"({type(loaded).__name__}), so no lane can be read and nothing may merge "
            f"unattended."
        )
    lanes = loaded.get("lanes") or {}
    if not isinstance(lanes, dict):
        raise SystemExit(
            f"merge_cage: `lanes:` in {POLICY_PATH.name} is a {type(lanes).__name__}, not a "
            f"mapping of lane name to lane."
        )
    if not lanes:
        raise SystemExit(f"merge_cage: {POLICY_PATH} describes no lanes")

    allow: list[str] = []
    deny: list[tuple[str, str]] = []
    for name, lane in lanes.items():
        # SHAPE FIRST, ALWAYS. A `null` lane makes `.get` raise AttributeError
        # at import — a traceback, from inside a module `lane.py` imports, which
        # is precisely the failure the SystemExit above exists to avoid. Worse,
        # a `paths:` written as a bare string would `extend` CHARACTER BY
        # CHARACTER: `"backend/src/**"` becomes 14 one-letter patterns, the real
        # pattern silently disappears, and on the ALLOW side that is fail-OPEN.
        if not isinstance(lane, dict):
            raise SystemExit(
                f"merge_cage: lane `{name}` in {POLICY_PATH.name} is not a mapping "
                f"({type(lane).__name__}), so its paths cannot be read and nothing may "
                f"merge unattended."
            )
        paths = lane.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise SystemExit(
                f"merge_cage: `paths:` for lane `{name}` in {POLICY_PATH.name} must be a list "
                f"of strings (got {type(paths).__name__}). A bare string would be read one "
                f"character at a time."
            )
        # `auto_merge` DECIDES THE LANE'S DIRECTION, so it may not be merely
        # truthy. YAML quotes are easy to add by accident and `"false"` is a
        # non-empty string: read loosely, an OWNER lane would hand every one of
        # its paths to ALLOW. That is fail-OPEN on the one field that separates
        # "a machine may decide this" from "only Ranjith may".
        auto = lane.get("auto_merge")
        if not isinstance(auto, bool):
            raise SystemExit(
                f"merge_cage: `auto_merge` for lane `{name}` in {POLICY_PATH.name} is "
                f"{auto!r} ({type(auto).__name__}), not a bool. Write `true` or `false` "
                f"unquoted — a quoted \"false\" is a non-empty string and would read as YES."
            )
        if auto:
            allow.extend(paths)
            continue
        fallback = (
            f"{(lane.get('description') or name).rstrip('.')} "
            f"(`{name}` lane in {POLICY_PATH.name}). {_OWNER_MERGES}"
        )
        deny.extend((p, DENY_REASONS.get(p, fallback)) for p in paths)
    return allow, deny


ALLOW, DENY = _load_lists()

# THE HAND-TYPED `ALLOW` LIST USED TO SIT HERE. It is gone, not moved: every
# entry it carried is now reachable through the policy's own fast lanes, and
# keeping a second copy is the bug this change exists to end.
#
# Two lessons from it are worth keeping, because they are about `path_matches`
# and they outlive the list:
#
#   * NEVER ALLOW BY FILE TYPE. `*.md` was once on this list. A slash-free
#     pattern matched a BASENAME at every depth, so it reached markdown anywhere
#     in the repo — including 43 of the 45 documents that a 109-file move had
#     just been performed to protect. Lanes are named by DIRECTORY for this
#     reason. (The basename rule itself was removed later; the habit it taught
#     is the thing to avoid.)
#
#   * ALLOW BY WHAT A FILE IS, NOT WHERE SOMEONE HOPED IT WOULD LIVE. The old
#     `frontend/src/**/__tests__/**` refused 55% of this repo's frontend unit
#     tests, which sit beside the code they test. `frontend/src/**` — one
#     policy line — covers them without anyone having to guess a convention.

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

# EXACT check-run names, matched by EQUALITY. These were substrings, and a
# substring cuts both ways -- `Frontend` is inside `frontend-e2e`, so:
#   * a SKIPPED optional `frontend-e2e` was refused as a skipped REQUIRED check;
#   * a SUCCESSFUL optional `frontend-e2e` satisfied the presence loop below,
#     so an ABSENT required `Frontend (Node 20)` would have looked present.
# The second is the dangerous direction and it survived this morning's fix,
# which only addressed the first. (CodeRabbit, PR #357.)
#
# Exact names mean a renamed job reads as ABSENT -- which the presence loop
# already refuses, loudly, with the real fix. That is the safe way to be wrong.
REQUIRED_CHECKS = [
    "Backend (Python 3.12)",
    "Frontend (Node 20)",
    "Chain wires (harness)",
    "CodeQL",
]

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


def _probe_for(pattern: str) -> str:
    """A concrete path that the given policy pattern would match.

    Only good enough to ask "is this whole lane entry reachable?" — it turns
    `backend/src/**` into `backend/src/probe`, not into anything real.
    """
    return (
        pattern.replace("**/", "probe/")
        .replace("/**", "/probe")
        .replace("**", "probe")
        .replace("*", "probe")
        .replace("?", "p")
    )


def check_lists() -> list[str]:
    """The rules must not contradict themselves.

    Three failure modes, all of them things this repo has actually shipped:

    1. AN ALLOW ENTRY SHADOWED WHOLE BY A DENY. Two of the twelve original ALLOW
       entries were unreachable for the cage's entire life, and the drill was
       fully green with them dead. Dead configuration is a hard error here.

    2. A FAST LANE THE CAGE SWALLOWS. Since 2026-08-26 both lists come from
       `.github/merge-policy.yml`, so an entry declared `auto_merge: true` whose
       own probe is DENIED means the policy is promising something the cage will
       always refuse. Measured the day before that change: 32 of the 38 declared
       fast-lane paths were in exactly this state — `scripts/**` and
       `.claude/**` denied outright, `backend/src/**` and `frontend/src/**`
       unknown. A specific deny under a broad allow (`backend/src/models.py`
       inside `backend/src/**`) is the intended precedence and does NOT fire
       this: the probe for the broad entry is not the specific file.

    3. A REASON FOR A PATH THE POLICY NO LONGER HAS. `DENY_REASONS` annotates
       patterns; a key that matches nothing is a sentence nobody will ever read,
       and — worse — it looks like a rule while being none.
    """
    bad: list[str] = []

    for a in ALLOW:
        if any(ch in a for ch in "*?"):
            continue
        for pat, _why in DENY:
            if path_matches(a, pat):
                bad.append(f"ALLOW `{a}` is fully shadowed by DENY `{pat}`, so it can never "
                           f"take effect. It reads as a permission and is not.")

    for a in ALLOW:
        probe = _probe_for(a)
        hit = next((pat for pat, _ in DENY if path_matches(probe, pat)), None)
        if hit:
            bad.append(
                f"the policy declares `{a}` auto-mergeable, but DENY `{hit}` swallows the "
                f"whole entry (probe `{probe}`), so nothing under it can ever merge "
                f"unattended. One of the two is wrong — decide which in "
                f"{POLICY_PATH.name}."
            )

    deny_patterns = {pat for pat, _ in DENY}
    for key in DENY_REASONS:
        if key not in deny_patterns:
            bad.append(
                f"DENY_REASONS carries `{key}`, which no lane in {POLICY_PATH.name} lists any "
                f"more. It reads as a rule and is only a sentence. Remove it, or put the path "
                f"back in an `auto_merge: false` lane."
            )

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
                f"unattended. FIX: either take this file out of the PR, or the owner adds it "
                f"to a lane in {POLICY_PATH.name} (that addition is itself his decision, not "
                f"an agent's). Editing scripts/merge_cage.py cannot classify a path any more "
                f"— since 2026-08-26 it derives both lists from that file."
            )
    return Verdict(
        "PATH", "fail" if reasons else "pass", reasons,
        claim="only paths the owner has already decided are safe",
    )


# ── WHAT THE SIZE CAP IS ACTUALLY MEASURING ─────────────────────────────────
# The cap is a REVIEWABILITY budget: "past this, no reviewer human or machine is
# actually reading it, they are skimming." So the number it counts must be the
# number a reviewer has to READ.
#
# Recorded fixtures are not that. PR #346 is 3,482 lines, of which 2,120 are one
# captured GitHub API response -- `scripts/fixtures/review_threads/recorded.json`.
# Nobody reads a recorded payload line by line; they read the 1,362 lines of code
# that consume it. Counting the payload against a reading budget measures bytes
# and calls them review surface. #345 was the same shape: 8,106 of its 9,757 lines
# were one generated ruleset snapshot.
#
# THE OBVIOUS WAY TO GET THIS WRONG is to write "fixtures don't count" and hand
# out a bypass: drop a 900-line module in `fixtures/` and the cap never sees it.
# So the rule is deliberately narrow on BOTH axes and it is never silent:
#
#   * PATH  — the file must live in a directory literally named `fixtures`.
#   * TYPE  — and carry a recorded-data extension. Source extensions are NEVER
#             exempt, wherever they sit. A `.py` under `fixtures/` is code that
#             happens to be filed oddly, and it counts in full.
#   * VOICE — the discount is reported in the verdict, every time. An exemption
#             nobody can see is indistinguishable from a hole.
_FIXTURE_DIR = "/fixtures/"
_RECORDED_EXT = (".json", ".txt", ".csv", ".xml", ".har", ".snap")


def review_surface(files_json: list[dict]) -> tuple[int, int]:
    """Split changed lines into (what a reviewer reads, what is recorded data).

    Pure, so the drill tests THIS and not a re-implementation of it.
    """
    read = exempt = 0
    for f in files_json:
        n = int(f.get("additions", 0)) + int(f.get("deletions", 0))
        name = str(f.get("filename", ""))
        recorded = (_FIXTURE_DIR in f"/{name}") and name.lower().endswith(_RECORDED_EXT)
        if recorded:
            exempt += n
        else:
            read += n
    return read, exempt


def check_size(n_files: int, n_lines: int, deletions: list[str],
               exempt_lines: int = 0) -> Verdict:
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
                   claim=(f"small enough to reason about ({n_files} files, "
                          f"{n_lines} lines to read"
                          + (f" + {exempt_lines} recorded-fixture lines not counted"
                             if exempt_lines else "") + ")"))


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
        if not any(req == n for n in names):
            reasons.append(
                f"required check `{req}` did not run — it cannot pass by being absent. This is "
                f"almost always a stale base: the PR was branched before that check existed. "
                f"FIX: `gh pr update-branch <PR>` (or rebase onto main), wait for CI, re-judge.")

    for r in runs:
        name = r.get("name")
        concl = r.get("conclusion")
        # SKIPPED IS NOT PENDING, BUT FOR A REQUIRED CHECK IT IS NOT A PASS EITHER.
        # Two true things pulling opposite ways, and this line honoured only one:
        #   * counting only `success` as green reported 3-6 "pending" checks on
        #     every PR; all were SKIPPED by a path filter or an `if:` guard, and
        #     GitHub itself said mergeStateStatus CLEAN. A cage that waits on
        #     those waits forever and looks exactly like a broken merge queue.
        #   * a check in REQUIRED_CHECKS that skipped proved NOTHING. The loop
        #     above already refuses a required check that is ABSENT, on the
        #     grounds that it "cannot pass by being absent" -- a required check
        #     that ran and skipped is that same claim wearing a tick.
        # So the rule is SCOPED, not global: optional checks may skip, required
        # checks must actually succeed.
        # (CodeRabbit on PR #336; ported here because this lineage did not have
        # it and #336's copy of merge_cage.py is being dropped rather than merged.)
        required = (name or "") in REQUIRED_CHECKS

        # NEVER ANSWERED is a different fact from FAILED, and it is checked FIRST
        # because it is the more specific one -- and because the required/optional
        # split below would otherwise swallow it. (`Frontend` substring-matches
        # `frontend-e2e`, so a cancelled e2e job was being told "make it green".)
        #
        # `skipped` and `neutral` are deliberately NOT in this set: they have their
        # own, different, correct handling below -- skipped is a legitimate pass
        # for an optional check and a refusal for a required one.
        no_verdict = concl in ("cancelled", "timed_out", "stale")

        if r.get("status") != "completed":
            reasons.append(f"`{name}` has not finished ({r.get('status')}). "
                           f"FIX: wait for it, then re-judge.")
        elif no_verdict:
            url = r.get("html_url") or ""
            reasons.append(
                f"`{name}` -> {concl}: it never produced a verdict, so this is not a failing "
                f"test -- it is a MISSING ANSWER, and it still blocks. The usual cause here is "
                f"a job hitting its `timeout-minutes` or being superseded by a newer push. "
                f"FIX: re-run it (`gh run rerun <run-id> --failed`) and re-judge. If it keeps "
                f"not finishing, the job is too slow or is hanging, and THAT is the bug -- do "
                f"not raise the timeout to hide it — {url}".rstrip(" -"))
        elif required and concl != "success":
            url = r.get("html_url") or ""
            reasons.append(
                f"required check `{name}` -> {concl}, and only `success` counts for a required "
                f"check -- `{concl}` means it did not prove it ran and passed. FIX: make it "
                f"actually run and go green, or take it out of REQUIRED_CHECKS "
                f"(that removal is the owner's decision, not an agent's) -- {url}".rstrip(" -"))
        elif concl not in ("success", "skipped", "neutral"):
            url = r.get("html_url") or ""
            # See the `no_verdict` note above: everything reaching this line DID
            # produce a verdict, and that verdict is "failed". "Make it green" is
            # the right instruction here and the wrong one there.
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


# ─────────────────────────────────────────────────────────────────────────────
# CAGE: TAGS — the lane's `requires:` list, proved against real check runs.
#
# WHY THIS EXISTS AS A SEPARATE CAGE FROM PROOF.
# PROOF asks "did the REQUIRED_CHECKS run and pass?" — a fixed list, the same
# for every PR. The policy asks a different question: the PRODUCT lane requires
# `verify`, and the HARNESS lane does not. `verify-live.yml` is NOT in the
# repo's ruleset (measured 2026-08-24: the ruleset names 11 contexts and none of
# them is `verify`), so GitHub will happily report a product PR as CLEAN with
# the app never once started. PROOF would agree with GitHub. This cage is the
# only thing standing between "green" and "watched alive".
#
# THE SHAPE OF THE MISTAKE THIS AVOIDS: reading `requires:` and then trusting a
# tag because the policy names it. A tag is a CLAIM; a check run is EVIDENCE.
# Every tag here either maps to check runs that must really have succeeded, or
# is proved by another cage in this same file. A tag that maps to neither is a
# hard refusal — an unrecognised requirement is not a satisfied one.
# ─────────────────────────────────────────────────────────────────────────────

TAG_CHECKS: dict[str, tuple[str, ...]] = {
    "ci": ("Backend (Python 3.12)", "Frontend (Node 20)", "offline-suite", "frontend-e2e"),
    "security": ("gitleaks (secret scan)", "bandit (python static analysis)",
                 "pip-audit (backend deps)", "npm audit (frontend deps)", "CodeQL"),
    "verify": ("verify / backend", "verify / frontend"),
    "drill": ("Chain wires (harness)",),
}

# Tags this file proves with its own cages rather than with a check run. Named
# explicitly so an unknown tag cannot fall through to "nothing to check".
TAGS_PROVED_BY_CAGE = {"review": "REVIEW", "ratchets": "RATCHET"}


def judge_tags(requires: list[str], runs: list[dict], base_ref: str = MAIN_BRANCH) -> list[str]:
    """Pure half of the TAGS cage, so a drill can feed it real shapes.

    SKIPPED is not a pass here, for the same reason it is not a pass for a
    required check: a job that skipped proved nothing, and the whole point of a
    lane tag is that the evidence really exists.
    """
    reasons: list[str] = []
    if not requires:
        # A lane with an empty `requires` would auto-merge on nothing at all.
        # lane.py's own drill already refuses that shape; this is the second
        # door, because the two files can drift.
        return ["this lane requires NO tags at all, so merging it would be merging on "
                "nothing. FIX: give the lane a `requires:` list in "
                ".github/merge-policy.yml (that is the owner's decision)."]
    by_name = {r.get("name", ""): r for r in runs}
    for tag in requires:
        if tag in TAGS_PROVED_BY_CAGE:
            continue  # the named cage's own verdict carries it
        wanted = TAG_CHECKS.get(tag)
        if wanted is None:
            reasons.append(
                f"the lane requires the tag `{tag}` and I do not know what evidence proves it, "
                f"so I cannot say it is satisfied — and an unrecognised requirement is never a "
                f"met one. FIX: add `{tag}` to TAG_CHECKS in scripts/merge_cage.py (naming the "
                f"check runs that prove it) or to TAGS_PROVED_BY_CAGE.")
            continue
        for name in wanted:
            # A check that only runs for main-targeted PRs cannot be demanded on
            # another base; judge_check_runs already says so in its own words.
            if base_ref != MAIN_BRANCH and name in MAIN_ONLY_CHECKS:
                continue
            run = by_name.get(name)
            if run is None:
                reasons.append(
                    f"tag `{tag}` needs `{name}` and that check never ran on this PR. A tag "
                    f"cannot be earned by absence. FIX: rebase onto main so the workflow "
                    f"exists on this branch, wait for it, then re-judge.")
            elif run.get("status") != "completed":
                reasons.append(f"tag `{tag}` needs `{name}` and it has not finished "
                               f"({run.get('status')}). FIX: wait for it, then re-judge.")
            elif run.get("conclusion") != "success":
                reasons.append(
                    f"tag `{tag}` needs `{name}` and it concluded `{run.get('conclusion')}`. "
                    f"Only `success` earns a tag — `skipped` and `neutral` prove nothing, which "
                    f"is exactly what a lane tag is supposed to prove. FIX: make it run and go "
                    f"green.")
    return reasons


def check_tags(pr: int, lane: dict, base_ref: str = MAIN_BRANCH) -> Verdict:
    """Prove every tag the PR's own lane demands."""
    sha = gh(["api", f"repos/{REPO}/pulls/{pr}", "-q", ".head.sha"])
    data = json.loads(gh(["api", f"repos/{REPO}/commits/{sha}/check-runs?per_page={PER_PAGE}"]))
    runs = data.get("check_runs", [])
    requires = list(lane.get("requires") or [])
    reasons = judge_tags(requires, runs, base_ref)
    return Verdict("TAGS", "fail" if reasons else "pass", reasons,
                   claim=f"every tag the `{lane.get('lane', '?')}` lane demands "
                         f"({', '.join(requires) or 'none'}) really ran and really passed")


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


def decide(pr: int, base_values: dict[str, int] | None = None,
           lane: dict | None = None) -> tuple[bool, list[Verdict], dict]:
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
        # The cap counts what a reviewer READS -- recorded fixtures are reported
        # separately rather than folded in. See review_surface().
        changed_lines, exempt_lines = review_surface(files_json)
        pr_json = json.loads(gh(["api", f"repos/{REPO}/pulls/{pr}"]))
        # A MISSING base ref is not `main`. Defaulting an unknown base to the one
        # value that unlocks the full check list would be the permissive guess,
        # and the permissive guess is what this file exists to refuse. An empty
        # string is not `main`, so judge_check_runs says so and the cage refuses.
        base_ref = ((pr_json.get("base") or {}).get("ref")) or ""
        meta = {"files": len(files), "lines": changed_lines,
                "fixture_lines": exempt_lines,
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

    verdicts.append(check_size(len(files), changed_lines, deletions, exempt_lines))
    verdicts.append(check_paths(files))

    for name, fn in (("PROOF", lambda p: check_proof(p, base_ref)), ("REVIEW", check_review)):
        try:
            verdicts.append(fn(pr))
        except Exception as exc:
            verdicts.append(Verdict(name, "fail", [
                f"the {name} cage could not run ({type(exc).__name__}: {exc}) — a cage that "
                f"could not look is not a cage that approved. FIX: re-run once the GitHub API "
                f"is reachable."]))

    # THE LANE ITSELF IS A CAGE. Expressed as a verdict rather than a special
    # exit code on purpose: every downstream reader — the printed reasons, the
    # Slack line, --advise, --verdict-json, the exit code — already knows how to
    # carry a Verdict, and a second channel is a second thing to keep honest.
    if lane is not None and not lane.get("auto_merge"):
        why = [w for w in (lane.get("why") or []) if w][:3]
        detail = " ".join(why) if why else "no reason was recorded"
        verdicts.append(Verdict("LANE", "fail", [
            f"this PR is in the `{lane.get('lane', '?')}` lane, and that lane is "
            f"`auto_merge: false` in .github/merge-policy.yml — a machine may not decide it. "
            f"{detail} "
            f"FIX: nothing an agent can do. Read the file named above and merge it yourself "
            f"if you agree."]))

    # THE LANE'S OWN REQUIREMENTS. Only asked when a lane verdict was supplied —
    # `--advise` judges without one and must keep behaving exactly as it did.
    if lane is not None:
        try:
            verdicts.append(check_tags(pr, lane, base_ref))
        except Exception as exc:
            verdicts.append(Verdict("TAGS", "fail", [
                f"the TAGS cage could not run ({type(exc).__name__}: {exc}) — a cage that "
                f"could not look is not a cage that approved. FIX: re-run once the GitHub "
                f"API is reachable."]))

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


def plain_english(pr: int, meta: dict, allowed: bool, verdicts: list[Verdict],
                  merged: bool = False) -> str:
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
        # THE ANNOUNCEMENT MAY NOT OUTRUN THE ACT.
        # `merged` is True only when `request_auto_merge` returned 0 — never
        # from the verdict, never from the presence of a flag. The old lineage
        # printed "Merged to production" for every allowed PR on a branch with
        # no merge capability at all, so that sentence was false 100% of the
        # time; CodeRabbit raised the dry-run half of the same defect on PR
        # #336. The cure is that the sentence is chosen by WHAT HAPPENED.
        #
        # And it still does not say "merged": queued is not landed. GitHub holds
        # the PR until `main-production-gate` is satisfied, and it can sit there,
        # or be dropped if a check goes red. Saying "merged" here would be the
        # same defect one step further down the wire.
        headline = (f":inbox_tray: *Queued for merge — PR #{pr}* "
                    f"(GitHub will land it when the ruleset is satisfied)" if merged
                    else f":white_check_mark: *Approved — PR #{pr}* (nothing has been merged)")
        return (f"{headline}\n"
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
    # The lane's own `requires:` list. On the decision path because the PRODUCT
    # lane's `verify` tag is the ONLY thing that makes a product change be
    # watched alive before it is trusted, and `verify` is not in the repo's
    # ruleset — so nothing else in GitHub or in this file would notice its
    # absence.
    "judge_tags", "check_tags",
    # THE ARM. On the decision path because `--auto` is the whole reason the arm
    # is allowed to exist — see the drill case that captures its real argv.
    "request_auto_merge",
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
    # The witness moved on 2026-08-26 and the invariant did not. It used to be
    # `backend/src/some_new_module.py`, which was only "unrecognised" because
    # the cage's hand-typed ALLOW list had never heard of `backend/src/**` —
    # while the policy called that whole tree the fast lane. Now that both read
    # one list, a genuinely unclassified path is one in no lane at all.
    red("an unrecognised path is refused",
        check_paths(["some_new_top_level/thing.py"]), "nobody has decided", P)

    # B16 — a per-user API route may not merge on the strength of its filename.
    red("a per-user API route is refused on its path",
        check_paths(["backend/src/api/routes/profile.py"]), "#12/#25", P)
    # B11 — the cage may never be more permissive than a gate already on main.
    red("a dependency manifest is refused and points at dependabot-auto",
        check_paths(["frontend/package.json"]), "dependabot-auto", P)
    # B12 — a document that IS a decision inherits the denial it delegates from.
    red("a document that is itself a product decision is refused",
        check_paths(["docs/product/product_design_rules.md"]), "product", P)

    # B12b — THE TWO DOCUMENTS THAT ARE DECISIONS SURVIVE THEIR OWN DIRECTORY.
    #
    #        This case used to assert that NOTHING under `docs/product/` could be
    #        machine-merged. The owner reversed that on 2026-08-26: prose is
    #        prose, and `verify` — fifteen minutes of watching production —
    #        cannot say anything true about a markdown file.
    #
    #        The half that was always the real invariant is kept, and it is now
    #        HARDER to satisfy than before, not easier. Previously these two
    #        files sat inside a directory that was denied wholesale, so their own
    #        DENY lines were belt-and-braces. Now the directory around them is
    #        the FAST lane and those two lines are the only thing standing up. A
    #        specific deny beating a broad allow is the precedence rule; this is
    #        the case that proves it still holds.
    escaped = [f for f in ("docs/product/product_design_rules.md",
                           "docs/product/plans/batch-2-decisions.md")
               if check_paths([f]).status == "pass"]
    ok("a document that IS a decision is refused inside a fast-lane directory",
       not escaped, f"a decision document became machine-mergeable: {escaped}", P)

    # B12b-ii — and the reversal really happened: ordinary product prose merges.
    #           A cage that refuses everything is an off switch, which is the
    #           failure this file was rewritten to end. Deleting the two lines
    #           above would leave B12b green and this red only if it is asserted
    #           separately, so it is.
    stuck = [f for f in ("docs/product/PRD.md",
                         "docs/product/pillars/README.md",
                         "docs/product/research/anything.md")
             if check_paths([f]).status != "pass"]
    ok("ordinary product prose takes the fast lane (owner's call, 2026-08-26)",
       not stuck, f"product prose is still refused: {stuck}", P)

    # B12c — and the refusal must not be bought by refusing ALL documents. The
    #        harness lane is Ranjith's own record; breaking it cannot reach a
    #        user, so it stays fast. A cage that refuses everything is an off
    #        switch, which is the failure this whole file was rewritten to end.
    doc_safe = ["docs/harness/IMPLEMENTATION_LOG.md", "docs/harness/reviews/x.md",
                "docs/_archive/CurrentStatus.md", "docs/archive/README.md",
                "docs/README.md"]
    v_docs = check_paths(doc_safe)
    ok("NEGATIVE CONTROL (harness + archived documents still pass the path cage)",
       v_docs.status == "pass", f"the cage refused a harness doc: {v_docs.reasons}", P)

    # B12d — THERE IS NO BASENAME RULE. A slash-free pattern once matched at
    #        every depth, so `README.md` on the allow list reached a README
    #        anywhere in the repo. The witness had to move on 2026-08-26 —
    #        `docs/product/pillars/README.md` is now legitimately allowed by its
    #        DIRECTORY — but the property is unchanged and still worth pinning:
    #        an allow entry must never leak to a directory nobody classified.
    ok("a root file is allowed by its path, and its basename does not leak downward",
       check_paths(["README.md"]).status == "pass"
       and check_paths(["unlisted_dir/README.md"]).status == "fail",
       "a bare basename rule crept back into path_matches", P)

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
        # `done` is the REQUIRED_CHECKS list, which is not the same set as the
        # tag checks — so a harness lane whose `ci` tag names jobs this fake
        # GitHub never reports must REFUSE. That is the honest end-to-end
        # answer: the wrapper really read the API and really judged what it got.
        v_tags = check_tags(1, {"lane": "harness", "requires": ["ci", "review", "drill"]})
        ok("check_tags reads the real API and refuses a tag whose checks are absent",
           v_tags.status == "fail" and any("never ran" in r for r in v_tags.reasons),
           f"{v_tags.status}: {v_tags.reasons}", ["check_tags"])
        v_tags_ok = check_tags(1, {"lane": "harness", "requires": ["review"]})
        ok("...and passes end to end for a lane whose tags another cage carries",
           v_tags_ok.status == "pass", f"{v_tags_ok.status}: {v_tags_ok.reasons}",
           ["check_tags"])
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

    # B09b — A FAST LANE THE CAGE SWALLOWS WHOLE. This is the state the repo was
    #        actually in: 32 of 38 declared fast-lane paths unreachable, and the
    #        drill fully green the entire time, because nothing asked. A guard
    #        with no witness is a guard nobody has watched fail — the same law
    #        `drill_registry.py` applies to every other guard here.
    ok("a fast lane a deny pattern swallows whole stops the cage dead",
       bool(_swallowed_lane_probe()),
       "check_lists cannot see a fast-lane path that is denied outright — the exact "
       "contradiction this change was written to end could come straight back",
       ["check_lists"])

    # B09c — ...and a reason for a path the policy no longer classifies. Deleting
    #        the third loop must not leave the drill green: a stale DENY_REASONS
    #        key reads like a rule and is only a sentence.
    ok("a DENY_REASONS key the policy dropped stops the cage dead",
       bool(_stale_reason_probe()),
       "check_lists cannot see a reason whose pattern no lane lists any more",
       ["check_lists"])

    # B09d — A MALFORMED POLICY MUST REFUSE AS A SENTENCE, AND `auto_merge` MUST
    #        BE A BOOL. The last one is the only shape whose failure is
    #        fail-OPEN: `auto_merge: "false"` is a non-empty string, so a loose
    #        read hands every path in an OWNER lane to ALLOW. One stray pair of
    #        YAML quotes would have made the whole owner lane machine-mergeable.
    survived = _bad_policy_probe()
    ok("a malformed policy refuses as a sentence, and a quoted auto_merge cannot open a lane",
       not survived,
       f"these broken policies were accepted instead of refused: {survived}",
       ["_load_lists"])

    # B03 — the exit codes must be four different numbers, or the caller's crash
    #       arm is unreachable dead code, which is how this repo got here.
    codes = [EXIT_ALLOW, EXIT_REFUSE, EXIT_CANNOT_TELL_OWNER, EXIT_CAGE_BROKE, EXIT_USAGE]
    #       THE CONSTANT CHECK ALONE CANNOT GO RED FOR THE DEFECT B03 RECORDS.
    #       `codes` is built from the constants, so it only fails if someone edits
    #       the constant block -- while the real regression is behavioural: a
    #       crash exiting 1, or argparse exiting 2, making a caller's arm
    #       unreachable. Those leave this green. Kept as a cheap invariant; the
    #       behaviour is now driven end to end below.
    #       (CodeRabbit on PR #336, ported.)
    ok("crash, refuse, slack-failure and usage have four different exit codes",
       len(set(codes)) == len(codes), f"collision in {codes}", [])

    # B03b — a bad flag must produce EXIT_USAGE, so any caller switching on that
    #        code has a reachable arm. argparse RAISES SystemExit rather than
    #        returning, so the code must be caught, not read from a return value.
    #        Writing this on #336 proved the point: the first version called
    #        main() bare and killed the whole drill run, and a self-test that
    #        aborts the suite it belongs to reports nothing at all.
    def _exit_code_of(argv: list[str]) -> int:
        try:
            return int(main(argv) or 0)
        except SystemExit as exc:
            return int(exc.code or 0)

    rc_usage = _exit_code_of(["--no-such-flag"])
    ok("a bad flag exits EXIT_USAGE, so a caller's usage arm is reachable",
       rc_usage == EXIT_USAGE,
       f"exit {rc_usage} (expected {EXIT_USAGE}) -- the usage arm is dead code",
       ["main"])

    # B18 — A REQUIRED CHECK THAT SKIPPED IS NOT A PASS, AND AN OPTIONAL ONE IS.
    #       Both halves, because either alone is a bug already shipped here.
    _req = REQUIRED_CHECKS[0]
    ok("a REQUIRED check that skipped is refused, not counted as a pass",
       any("only `success` counts" in r for r in judge_check_runs(
           [{"name": _req, "status": "completed", "conclusion": "skipped"}], 1)),
       "a required check proved nothing and still passed the cage",
       ["judge_check_runs"])
    _opt = [{"name": n, "status": "completed", "conclusion": "success"}
            for n in REQUIRED_CHECKS]
    _opt.append({"name": "Doc clutter (CURATE gear)", "status": "completed",
                 "conclusion": "skipped"})
    ok("an OPTIONAL check that skipped is still green (a path filter is not a failure)",
       not judge_check_runs(_opt, len(_opt)),
       f"a correctly-skipped optional check blocked the PR: {judge_check_runs(_opt, len(_opt))}",
       ["judge_check_runs"])

    # B20 — "NEVER ANSWERED" IS NOT "FAILED", AND THE FIX TEXT MUST SAY SO.
    #       Found live 2026-08-21: four PRs (#349 #350 #351 #353) had been red for
    #       two days on `frontend-e2e`. Nothing was wrong with any of them -- the
    #       job takes about 2 minutes (1m43s-2m01s across the last six green runs)
    #       and had run 20m18s into a `timeout-minutes: 20`, which GitHub records
    #       as `cancelled`. The cage said "FIX: make it green" about code that was
    #       already green, so nobody re-ran it and the red became permanent.
    #
    #       All four cases are drilled, because the earlier version of this fix
    #       was swallowed by the required/optional split -- `Frontend`
    #       substring-matches `frontend-e2e`, so the required arm answered first
    #       and the new message never appeared. It passed review by eye and did
    #       nothing.
    def _runs_with(name: str, concl: str) -> list[dict]:
        rs = [{"name": n, "status": "completed", "conclusion": "success"}
              for n in REQUIRED_CHECKS]
        rs.append({"name": name, "status": "completed", "conclusion": concl})
        return rs

    def _said(name: str, concl: str) -> str:
        return " ".join(r for r in judge_check_runs(_runs_with(name, concl), 5) if name in r)

    ok("a REQUIRED check that was cancelled is told to be RE-RUN, not fixed",
       "MISSING ANSWER" in _said("Frontend", "cancelled"),
       "the cage tells the owner to fix code that never failed", ["judge_check_runs"])
    ok("...and it still BLOCKS -- a missing answer is never consent",
       bool(_said("Frontend", "cancelled")),
       "a cancelled check stopped blocking; that is a bypass, not a diagnosis",
       ["judge_check_runs"])
    ok("an OPTIONAL check that was cancelled gets the same re-run advice",
       "MISSING ANSWER" in _said("Doc clutter (CURATE gear)", "cancelled"),
       "only required checks got the honest message", ["judge_check_runs"])
    ok("a check that really FAILED is still told to go green",
       "make it green" in _said("Doc clutter (CURATE gear)", "failure"),
       "a real failure was mislabelled as a missing answer -- that direction hides bugs",
       ["judge_check_runs"])

    # B22 — THE SIZE CAP COUNTS REVIEW SURFACE, AND THE EXEMPTION IS NOT A DOOR.
    #       The discount exists because a 2,120-line recorded API payload is not
    #       something anyone reads. The danger is obvious -- "fixtures do not
    #       count" is one careless step from "put your module in fixtures/ and
    #       skip the cap" -- so the bypass is drilled explicitly, not assumed shut.
    _fx = [{"filename": "scripts/fixtures/review_threads/recorded.json",
            "additions": 2120, "deletions": 0},
           {"filename": "scripts/review_debt.py", "additions": 883, "deletions": 0}]
    _read, _ex = review_surface(_fx)
    ok("a recorded fixture is not counted as review surface",
       (_read, _ex) == (883, 2120), f"got read={_read} exempt={_ex}", ["review_surface"])

    _bypass = [{"filename": "scripts/fixtures/sneaky.py", "additions": 900, "deletions": 0}]
    ok("SOURCE under fixtures/ is NOT exempt -- the obvious bypass stays shut",
       review_surface(_bypass) == (900, 0),
       "a .py under fixtures/ escaped the cap", ["review_surface"])

    _elsewhere = [{"filename": "backend/data/big.json", "additions": 900, "deletions": 0}]
    ok("recorded data OUTSIDE a fixtures/ directory is still counted",
       review_surface(_elsewhere) == (900, 0),
       "the extension alone was enough to exempt a file", ["review_surface"])

    _v_ex = check_size(3, 900, [], exempt_lines=2120)
    ok("the discount is SAID OUT LOUD, never silent",
       "recorded-fixture lines not counted" in (_v_ex.claim or ""),
       "an exemption nobody can see is indistinguishable from a hole", ["check_size"])

    # B21 — A REQUIRED CHECK IS MATCHED BY EXACT NAME, AND THE DANGEROUS
    #       DIRECTION IS DRILLED. These were substrings; `Frontend` is inside
    #       `frontend-e2e`, so a SUCCESSFUL optional run satisfied the
    #       presence loop and an ABSENT required check looked present. This
    #       morning's fix only addressed the noisy direction (a skipped optional
    #       reading as a skipped required) and left the silent one open.
    def _rs(pairs):
        return [{"name": n, "status": "completed", "conclusion": c} for n, c in pairs]

    _all_req = [(n, "success") for n in REQUIRED_CHECKS]
    _no_fe = [p for p in _all_req if p[0] != "Frontend (Node 20)"]
    _no_fe = _no_fe + [("frontend-e2e", "success")]
    ok("a green OPTIONAL check cannot stand in for an ABSENT required one",
       bool(judge_check_runs(_rs(_no_fe), len(_no_fe))),
       "an absent required check was satisfied by a similarly-named optional one",
       ["judge_check_runs"])
    _opt_skip = _all_req + [("frontend-e2e", "skipped")]
    ok("...and a skipped OPTIONAL check with a similar name still does not block",
       not judge_check_runs(_rs(_opt_skip), len(_opt_skip)),
       "an optional skip was mistaken for a required one", ["judge_check_runs"])

    # ── THE TAGS CAGE ────────────────────────────────────────────────────────
    # The PRODUCT lane requires `verify`, and `verify-live.yml` is NOT one of
    # the 11 contexts in this repo's ruleset (measured 2026-08-24). So GitHub
    # will report a product PR as CLEAN with the app never once started, and
    # PROOF — which only knows REQUIRED_CHECKS — would agree with GitHub. Every
    # case below is that hole, from a different angle.
    T = ["judge_tags"]
    _prod_req = ["ci", "review", "security", "verify", "ratchets"]
    _full = _rs([(n, "success") for n in
                 (*TAG_CHECKS["ci"], *TAG_CHECKS["security"], *TAG_CHECKS["verify"])])
    ok("a product PR with every tag's checks green passes the TAGS cage",
       not judge_tags(_prod_req, _full),
       "the tags cage refused a PR whose every required check really passed", T)

    _no_verify = [r for r in _full if not r["name"].startswith("verify")]
    ok("THE HOLE: a product PR that never ran `verify` is refused",
       any("verify" in r for r in judge_tags(_prod_req, _no_verify)),
       "a product change merged without the app ever being started — the one "
       "thing the product lane's speed is borrowed against", T)

    _verify_skipped = [r for r in _full if not r["name"].startswith("verify")] + \
        _rs([(n, "skipped") for n in TAG_CHECKS["verify"]])
    ok("...and a `verify` that SKIPPED does not earn the tag either",
       bool(judge_tags(_prod_req, _verify_skipped)),
       "a skipped job was allowed to prove something — a skip proves nothing, "
       "which is the entire point of a tag", T)

    _verify_pending = [r for r in _full if not r["name"].startswith("verify")] + \
        [{"name": TAG_CHECKS["verify"][0], "status": "in_progress", "conclusion": None}]
    ok("...and a `verify` still RUNNING is waited for, not counted",
       bool(judge_tags(_prod_req, _verify_pending)),
       "a check that had not finished was counted as finished", T)

    ok("the HARNESS lane does not demand `verify` it was never going to run",
       not judge_tags(["ci", "review", "drill"],
                      _rs([(n, "success") for n in
                           (*TAG_CHECKS["ci"], *TAG_CHECKS["drill"])])),
       "the harness lane was refused for a tag its own policy does not require — "
       "a lane system that demands every tag from every lane is not a lane system", T)

    ok("a tag nobody has defined evidence for is REFUSED, not assumed satisfied",
       bool(judge_tags(["ci", "some-future-tag"], _full)),
       "an unrecognised requirement passed by being unrecognised — the exact "
       "shape of `not_checked` reading as a pass", T)

    ok("a lane requiring NOTHING cannot merge on nothing",
       bool(judge_tags([], _full)),
       "an empty `requires:` list was treated as 'all requirements met'", T)

    # `CodeQL` only fires for main-targeted PRs, so demanding it on another base
    # is a refusal no author could ever clear — judge_check_runs already says
    # so in its own words and this cage must not contradict it.
    _no_codeql = [r for r in _full if r["name"] != "CodeQL"]
    ok("a check that cannot exist on this base is not demanded twice",
       not judge_tags(["security"], _no_codeql, base_ref="feat/something"),
       "the TAGS cage refused a non-main PR for a check that only runs on main, "
       "contradicting the PROOF cage about the same fact", T)

    # B19 — THE ANNOUNCEMENT MAY NOT OUTRUN THE ACT. This lineage has no
    #       `--merge` flag at all, so "Merged to production" was false on every
    #       single allowed PR, not merely on a dry run.
    _v = Verdict("PATHS", "pass", [], claim="no owner-lane file was touched")
    ok("the advisor does not tell the owner the PR was merged",
       "Merged to production" not in plain_english(
           1, {"title": "x: y", "files": 3, "lines": 10}, True, [_v]),
       "the cage announced a merge it cannot perform", ["plain_english"])

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

        # ── THE OWNER LANE STOPS THE MACHINE, ON THE SAME PERFECT PR ─────────
        # Same fake GitHub, same flawless PR, same baseline — the ONLY thing
        # that changes is the lane. If this ever goes green, `auto_merge: false`
        # has stopped meaning anything, and the four owner-lane path lists in
        # merge-policy.yml became decoration without a single test going red.
        allowed_owner, v_owner, _ = decide(
            1, {"drill": 3},
            {"lane": "product_owner", "auto_merge": False,
             "requires": ["ci", "review", "security", "verify", "ratchets"],
             "why": ["`backend/migrations/0007.sql` is irreversible against live data."]})
        ok("an owner lane refuses the machine even when NOTHING ELSE is wrong",
           not allowed_owner and any("may not decide it" in b for b in blocks_of(v_owner)),
           f"allowed={allowed_owner}: {blocks_of(v_owner)}", ["decide"])
        ok("...and the refusal NAMES the file that made it the owner's call",
           any("migrations" in b for b in blocks_of(v_owner)),
           "the owner was told to decide something without being told what — a "
           "refusal he cannot act on is how a gate gets switched off at 7am",
           ["decide"])

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

    # B17 — THE IMMEDIATE-MERGE CAPABILITY IS STILL GONE, and stays gone.
    # This case is unchanged by the 2026-08-24 wiring and that is the point.
    # `--queue` was added; `--merge` was NOT brought back. The difference is not
    # cosmetic:
    #   `--merge`  would merge NOW, on this file's judgement alone.
    #   `--queue`  asks GitHub to merge when `main-production-gate` is satisfied,
    #              so the ruleset still has to agree and GitHub performs the act.
    # If a future edit ever restores a straight merge, this case goes red, which
    # is exactly what it is for. Asserted end to end through main(), never by
    # grepping for the string — a self-test that greps stayed fully green here
    # after the only authorisation call had been deleted.
    # argparse signals a usage error by RAISING (`_Parser.error` -> SystemExit),
    # so this must catch it. Asserting on a return value alone would have made
    # this case blow the drill up rather than report — a self-test that crashes
    # is not a self-test that passed.
    try:
        rc_merge = main(["1", "--merge"])
    except SystemExit as exc:
        rc_merge = int(exc.code or 0)
    ok("`--merge` still does not exist: nothing here merges on its own judgement",
       rc_merge == EXIT_USAGE,
       f"exit {rc_merge} (expected {EXIT_USAGE}) — an immediate-merge flag came back",
       ["decide"])

    # ...AND `--queue` MAY NOT RUN BLIND. The lane is what decides whether a
    # machine is allowed to touch this change at all, so queuing without it is
    # the exact mistake the old lane-blind merge flag made — a migration and a
    # README treated as the same risk. Refused at the flag, before any API call.
    try:
        rc_queue = main(["1", "--queue"])
    except SystemExit as exc:
        rc_queue = int(exc.code or 0)
    ok("`--queue` without `--lane` is a usage error, not a lane-blind ship",
       rc_queue == EXIT_USAGE,
       f"exit {rc_queue} (expected {EXIT_USAGE}) — the cage would queue a PR without "
       f"knowing which lane it is in",
       ["decide"])

    # ...AND AN UNREADABLE LANE FILE BREAKS THE CAGE RATHER THAN BEING GUESSED.
    # `not_checked` reading as a pass is the hole this whole file exists to
    # avoid; a lane verdict that failed to parse is the same shape.
    try:
        rc_bad = main(["1", "--queue", "--lane", "no/such/lane.json"])
    except SystemExit as exc:
        rc_bad = int(exc.code or 0)
    ok("a lane verdict that cannot be read refuses instead of defaulting",
       rc_bad == EXIT_CAGE_BROKE,
       f"exit {rc_bad} (expected {EXIT_CAGE_BROKE}) — an unreadable lane was survivable",
       ["decide"])

    # ...AND `--auto` IS THE ENTIRE SAFETY ARGUMENT, SO IT GETS ITS OWN CASE.
    # Everything written about `--queue` rests on GitHub holding the PR until
    # `main-production-gate` is satisfied. Delete six characters from the
    # command in request_auto_merge and it becomes an immediate merge on this
    # file's judgement alone — B20 restored, silently, with every other case
    # still green. Caught by CodeRabbit on PR #380: the case above guards the
    # FLAG and nothing guarded the FLAG'S ARGUMENT. Asserted by capturing the
    # real argv rather than by reading the source, because a self-test that
    # greps its own file has already failed in this repo once.
    _seen_argv: list[list[str]] = []

    class _FakeProc:
        returncode = 0
        stdout = "queued"
        stderr = ""

    _saved_run = subprocess.run
    try:
        def _spy(cmd, *_a, **_k):  # noqa: ANN001, ANN202 - a drill stub
            _seen_argv.append(list(cmd))
            return _FakeProc()
        subprocess.run = _spy  # type: ignore[assignment]
        request_auto_merge(1)
    finally:
        subprocess.run = _saved_run  # type: ignore[assignment]
    _argv = _seen_argv[0] if _seen_argv else []
    ok("the queue request really passes `--auto`, so GitHub still holds the gate",
       "--auto" in _argv,
       f"the command was {_argv} — without `--auto` this is an immediate merge on this "
       f"file's judgement alone, which is exactly the capability B20 deleted",
       ["request_auto_merge"])
    ok("...and it squashes, matching the one merge shape this repo already uses",
       "--squash" in _argv, f"the command was {_argv}", ["request_auto_merge"])

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


def _swallowed_lane_probe() -> list[str]:
    """Re-create the state main was actually in: a fast lane denied outright.

    `scripts/**` sat in the `harness` lane as auto-mergeable while the cage
    denied every path under it. Nothing went red for the cage's whole life, so
    the guard that now catches it needs a witness of its own.
    """
    saved = list(ALLOW)
    try:
        ALLOW.append("scripts/**")   # the real historic contradiction, verbatim
        return check_lists()
    finally:
        ALLOW[:] = saved


def _bad_policy_probe() -> list[str]:
    """Feed `_load_lists()` broken policies; return the ones it did NOT refuse.

    Every shape here is a real YAML accident, and the last is the dangerous one:
    a quoted `"false"` is a non-empty string, so a truthy read would hand an
    OWNER lane's paths straight to ALLOW.
    """
    import tempfile

    cases = {
        "root is a list": "- not\n- a mapping\n",
        "lanes is a list": "lanes:\n  - harness\n",
        "lane is null": "lanes:\n  harness:\n",
        "paths is a bare string": 'lanes:\n  harness:\n    auto_merge: true\n    paths: "a/**"\n',
        "auto_merge is a quoted string": (
            'lanes:\n  owner_lane:\n    auto_merge: "false"\n    paths:\n      - "secret/**"\n'
        ),
    }
    global POLICY_PATH
    saved_path, survived = POLICY_PATH, []
    try:
        for label, text in cases.items():
            with tempfile.TemporaryDirectory() as d:
                probe = Path(d) / "merge-policy.yml"
                probe.write_text(text, encoding="utf-8")
                POLICY_PATH = probe
                try:
                    _load_lists()
                except SystemExit:
                    continue          # refused as a sentence — correct
                except Exception:     # a traceback is NOT a refusal
                    survived.append(f"{label} (raised, did not refuse cleanly)")
                    continue
                survived.append(label)
    finally:
        POLICY_PATH = saved_path
    return survived


def _stale_reason_probe() -> list[str]:
    """A refusal sentence whose pattern no lane lists any more."""
    key = "backend/src/this/path/is/in/no/lane/at/all.py"
    try:
        DENY_REASONS[key] = "a rule that no longer exists"
        return check_lists()
    finally:
        DENY_REASONS.pop(key, None)


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


def request_auto_merge(pr: int) -> tuple[bool, str]:
    """Hand the PR to GITHUB'S OWN auto-merge queue. Returns (ok, detail).

    WHY `--auto` AND NOT A STRAIGHT MERGE. `gh pr merge --squash` would merge
    right now, on this file's judgement alone. `--auto` asks GitHub to merge the
    PR when the ruleset it enforces is satisfied — so TWO INDEPENDENT
    AUTHORITIES have to agree before anything reaches production:

        this cage        the lane, the review threads, the ratchets, the tags
        GitHub           `main-production-gate`, its 11 required contexts

    Neither can be talked round by the other, and the act itself is performed by
    the one that is not written by an agent. If this file is ever wrong in the
    permissive direction, the ruleset is still standing.

    It also fails in the right direction on the way in: `--auto` needs
    `allow_auto_merge` on the repository, which is a setting only the owner can
    turn on. Until he does, this returns an error and the PR sits — a capability
    the owner has not granted cannot be assumed by writing code that wants it.

    This function re-decides NOTHING. Every judgement was made before it was
    called; its one job is to make the request and report truthfully whether the
    request was accepted, so the sentence the owner reads is chosen by what
    happened rather than by what was intended.
    """
    proc = subprocess.run(
        ["gh", "pr", "merge", str(pr), "--auto", "--squash", "--delete-branch"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "queued").strip()[:300]
    return False, (proc.stderr or proc.stdout or "no output").strip()[:300]


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
    ap.add_argument("--queue", action="store_true",
                    help="hand the PR to GitHub's auto-merge queue if every cage allows it "
                         "AND its lane says a machine may decide it. Requires --lane: queuing "
                         "without knowing the lane is shipping without knowing who gets hurt.")
    ap.add_argument("--lane", metavar="FILE",
                    help="the lane verdict JSON written by `python scripts/lane.py --json`")
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

        # THE LANE, LOADED BEFORE ANYTHING IS JUDGED.
        # `--queue` without `--lane` is a usage error, never a permissive
        # default. This is the whole lesson of the lineage before this one: its
        # merge flag knew nothing about lanes, so a migration and a README were
        # the same risk to it. A flag that can reach production must not be able
        # to run in ignorance of who it reaches.
        lane_verdict: dict | None = None
        if args.queue and not args.lane:
            ap.error("--queue requires --lane FILE (the output of `python scripts/lane.py "
                     "--json`). Queuing without the lane is shipping without knowing whether "
                     "a machine is allowed to decide this change at all.")
        if args.lane:
            try:
                lane_verdict = json.loads(Path(args.lane).read_text(encoding="utf-8"))
                if not isinstance(lane_verdict, dict) or "lane" not in lane_verdict:
                    raise ValueError("expected the JSON object written by scripts/lane.py")
            except Exception as exc:
                raise CageBroke(
                    f"--lane {args.lane} is not a lane verdict I can read ({exc}). A cage that "
                    f"cannot tell which lane a PR is in must not ship it. FIX: run "
                    f"`python scripts/lane.py --json lane.json <changed files>` first.") from exc

        allowed, verdicts, meta = decide(args.pr, base, lane_verdict)
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

        # THE ACT COMES BEFORE THE ANNOUNCEMENT, ALWAYS.
        queued = False
        queue_failed = False
        if args.queue and allowed:
            ok_queue, detail = request_auto_merge(args.pr)
            if ok_queue:
                queued = True
                print(f"QUEUED: PR #{args.pr} handed to GitHub's auto-merge queue — it will "
                      f"land when `main-production-gate` is satisfied. {detail}")
            else:
                # ALLOWED-AND-NOT-QUEUED IS NOT A REFUSAL. Every cage passed;
                # something outside this file said no. Printing "ask the owner"
                # here would blame the PR for a repository setting, and the
                # owner would go looking for a fault in the change.
                print(f"ALLOWED BUT NOT QUEUED: `gh pr merge --auto` was refused — {detail}\n"
                      f"  FIX: this is almost always `allow_auto_merge` being off on the "
                      f"repository, which is the owner's switch and nobody else's: "
                      f"`gh api -X PATCH repos/{REPO} -F allow_auto_merge=true`. "
                      f"The verdict above stands either way.", file=sys.stderr)
                # DEFERRED, NOT RETURNED. Returning here would skip Slack, the
                # advice file and --verdict-json — and this is the one case
                # where every cage PASSED, so it is the case a machine reader
                # most needs to be able to see. An early return in the arm's
                # failure branch would make "the cages passed but the queue
                # refused" the quietest outcome in the file, which is backwards.
                # (CodeRabbit, PR #380.)
                queue_failed = True

        if args.slack:
            chan = "ready-to-merge" if allowed else "needs-your-decision"
            if not slack(chan, plain_english(args.pr, meta, allowed, verdicts, queued)):
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

        # The arm's own failure is reported LAST, after every output above has
        # been produced. Same code as before, a later line.
        if queue_failed:
            return EXIT_CAGE_BROKE
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
