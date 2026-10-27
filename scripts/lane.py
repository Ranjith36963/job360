#!/usr/bin/env python3
"""Decide which LANE a pull request belongs to, and therefore which gate it gets.

WHY THIS EXISTS
---------------
The previous cage had ONE gate for everything, so it had to be as careful as its
most dangerous case. Measured over 29 real PRs it allowed 0, and over the last 60
merges only 2 (3%) would have passed. A gate that refuses everything is not a
gate, it is an off switch.

The mistake was treating all change as one kind of risk. It is not:

Two questions, not one -- WHO GETS HURT, and WHO DECIDES. Crossing them gives a
2x2, and the first version of this file collapsed it into a list of three:

                      a machine may merge      only the owner merges
    HARNESS       |   harness                |  harness_owner        |  hurts YOU
    PRODUCT       |   product                |  product_owner        |  hurts a USER

The collapse was not cosmetic. It is what happens AFTER the merge: a hand-merged
migration still reaches live users, so it still needs the 15-minute watcher and a
confirmed rollback -- and under one `owner` bucket it got neither. Measured over
the last 60 merges that bucket held 7 product decisions, 6 harness decisions and
5 that were both.

THE PRECEDENCE RULE, AND WHY IT IS NOT A SCORE
----------------------------------------------
A PR is classified by its MOST RESTRICTIVE file, never by a majority or an
average:

    harness_owner  >  product_owner  >  product  >  harness

One migration file in a 40-file docs PR makes the whole thing a product_owner PR. This
is deliberate and it is the opposite of a risk score. A score can be diluted --
add enough safe files and the dangerous one stops mattering. A door cannot.

REORDERED 2026-09-17. `product_owner` used to be first, because it was the
strictest lane: a human merged it AND a user was downstream. The owner flipped it
to `auto_merge: true` that day, which means it is no longer strict in the sense
precedence exists to serve -- "does a machine get to decide this". Under the old
order a PR holding one `.github/workflows/*.yml` file and one migration
classified `product_owner`, reported `auto_merge: true`, and the human-only file
would have ridden along. `harness_owner` is first now because it is the only
`auto_merge: false` lane left. The tuple itself lives in `merge_cage.LANES`.

THE OTHER RULE: AN UNKNOWN PATH IS NOT A SAFE PATH
--------------------------------------------------
A file matching no lane does not fall through to the fast lane. It escalates to
`harness_owner` -- the only lane a machine may not merge -- and the reason names
the file. (It escalated to `product_owner` until 2026-09-17 for exactly the same
stated reason, "the strictest of the four"; when that lane started auto-merging,
an unchanged escalation target would have quietly turned "nobody has decided this
is safe" into "ship it". The TARGET was never the point -- "a machine does not
decide this" was.) This repo has shipped ten guards that
could not fire, every one of them a check that reported success about a question
it was not actually asking. `else: allow` is that bug in one word.

THE NEW-PACKAGE TRIPWIRE (owner, 2026-10-20)
--------------------------------------------
A path cannot say "this manifest adds a package". `content_pins()` compares a
dependency manifest at base and head, and a change that ADDS a package name
moves that file to `product_human` (the owner merges it, it is still watched).
A version bump of a name already there keeps the file's own lane. A pin can
only TIGHTEN a lane, so with no revisions, or a file it cannot read, it fires.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# Reuse the cage's own glob matcher rather than writing a second one. Two
# implementations of "does this path match" is two answers to the same question,
# and the day they disagree is the day the cage means nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_cage import LANES, path_matches  # noqa: E402

POLICY_PATH = Path(__file__).resolve().parent.parent / ".github" / "merge-policy.yml"

# Most restrictive first. Order IS the precedence rule.
#
# TWO owner lanes, not one. "Who decides" and "who gets hurt" are separate
# questions; a single `owner` bucket collapsed them and quietly left hand-merged
# migrations with no post-merge watcher at all.
#
# `harness_owner` outranks `product_owner` since 2026-09-17, which is the
# opposite of what it said here before. The old ranking was "a user is
# downstream, so it is more serious", and that was a statement about BLAST
# RADIUS. Precedence is not about blast radius; it is about WHO DECIDES. Once
# `product_owner` began auto-merging, ranking it above the one human-only lane
# meant a single harness_owner file could be out-voted by a product_owner file
# beside it and machine-merged. The harness lane is also the one whose blast
# radius includes the watcher that would have caught the other one.
# IMPORTED, NOT RETYPED. `merge_cage.LANES` is the one definition; this file
# used to keep its own copy, and a lane in one list but not the other is the
# same "two readers, two answers" bug the ALLOW/DENY merge was written to end.
# merge_cage also REFUSES a policy whose lane set is not exactly this.
PRECEDENCE: tuple[str, ...] = LANES


class PolicyError(RuntimeError):
    """The policy file is missing, unreadable, or does not describe the lanes."""


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    """Read `.github/merge-policy.yml`.

    Raises rather than defaulting. A missing policy must never read as "no
    restrictions" -- that is the `else: allow` bug wearing a different hat.
    """
    if not path.exists():
        raise PolicyError(f"policy file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or "lanes" not in data:
        raise PolicyError(f"policy file has no `lanes:` section: {path}")
    for lane in PRECEDENCE:
        if lane not in data["lanes"]:
            raise PolicyError(f"policy file is missing the `{lane}` lane")
    return data


def lane_of_file(path: str, policy: dict[str, Any]) -> str:
    """Return the lane a single file belongs to.

    Checked most-restrictive-first, so a file listed in two lanes gets the
    stricter one without the caller having to think about it.
    """
    lanes = policy["lanes"]
    for lane in PRECEDENCE:
        for pattern in lanes[lane].get("paths") or []:
            if path_matches(path, pattern):
                return lane
    return "unknown"


def apply_data_exemptions(
    files: list[str],
    policy: dict[str, Any],
    base: str | None,
    head: str | None,
    repo: str = ".",
) -> tuple[list[str], list[str]]:
    """Drop files whose change was DATA ONLY, and say which were dropped.

    Only runs when a base and head are supplied — without two revisions there is
    nothing to compare, and an unverifiable exemption must never be granted. The
    default with no revisions is therefore the STRICT answer, which is the safe
    direction for a rule that can only ever loosen a lane.

    Anything that cannot be measured (unparseable file, missing revision) keeps
    the file, so a broken instrument reads as "still owner", never as "fine".
    """
    exemptions = policy.get("data_exemptions") or {}
    if not exemptions or not base or not head:
        return files, []

    from data_only import NotParseableError, git_show, is_data_only

    kept: list[str] = []
    exempted: list[str] = []
    for f in files:
        rule = exemptions.get(f)
        if not rule:
            kept.append(f)
            continue
        before = git_show(base, f, Path(repo))
        after = git_show(head, f, Path(repo))
        if not before or not after:
            kept.append(f)  # added or deleted outright -- not a data edit
            continue
        try:
            if is_data_only(before, after, str(rule.get("data_name", ""))):
                exempted.append(f)
                continue
        except NotParseableError:
            pass  # unmeasurable -> keep it, stay strict
        kept.append(f)
    return kept, exempted


# ── CONTENT PINS: THE NEW-PACKAGE TRIPWIRE (owner, 2026-10-20) ──────────────
# The mirror of the data exemption above: that one can only LOOSEN a lane, a
# pin can only TIGHTEN one. The policy's `content_pins:` names the manifests and
# this compares each at base and head. An agent can invent a package name, and a
# typo-squat runs its install scripts on the build host (slopsquatting).
#
# THE STRICT DEFAULT IS THE OPPOSITE OF THE EXEMPTION'S, ON PURPOSE. With no
# revisions, an unreadable revision or an unparseable file, an exemption is
# withheld -- and a pin FIRES. Both are the safe direction for what they do.
PIN_KINDS = ("new_dependency",)
MANIFEST_KINDS = ("pyproject", "package_json", "package_lock", "requirements")
_PACKAGE_JSON_TABLES = ("dependencies", "devDependencies", "optionalDependencies",
                        "peerDependencies")
_REQ_NAME = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
# `--break-checker pins` sets this, so the drill can be watched going red.
_PINS_BLINDED = False


class PinUnreadableError(RuntimeError):
    """git could not answer for a revision -- the pin fires rather than guess."""


def _pins_config(policy: dict[str, Any]) -> dict[str, Any]:
    """`content_pins:` from the policy, shape-checked. Absent means no pins."""
    cfg = policy.get("content_pins")
    if cfg is None:
        return {}
    if not isinstance(cfg, dict) or set(cfg) - set(PIN_KINDS):
        raise PolicyError(f"`content_pins:` must be a mapping of {', '.join(PIN_KINDS)}")
    for kind, block in cfg.items():
        lane = (block or {}).get("lane") if isinstance(block, dict) else None
        if lane not in policy["lanes"]:
            raise PolicyError(f"content_pins.{kind}.lane `{lane}` is not a lane in the policy")
        # A pin into a lane a machine may merge is a pin that changes nothing.
        if policy["lanes"][lane].get("auto_merge") is not False:
            raise PolicyError(f"content_pins.{kind} pins to `{lane}`, which a machine may merge")
    for pattern, kind in ((cfg.get("new_dependency") or {}).get("manifests") or {}).items():
        if kind not in MANIFEST_KINDS:
            raise PolicyError(f"content_pins manifest `{pattern}` has unknown kind `{kind}`")
    return cfg


def _norm_package(name: str, kind: str) -> str:
    # PEP 503 for Python, so `Foo_Bar` -> `foo-bar` is a bump, not a new name.
    if kind in ("pyproject", "requirements"):
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _requirement_names(text: str) -> list[str]:
    """Names in a pip requirements file (pip-compile/uv lock files included).

    An OPTION line (`--extra-index-url`, `-r`, `-e`) and a bare URL are kept
    verbatim as a pseudo-name, so adding a new index or source fires too.
    `--hash` lines are continuations of a requirement and are joined first.
    """
    names: list[str] = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.startswith("-") or ("://" in line and " @ " not in line):
            names.append("source:" + " ".join(line.split()))
            continue
        names.extend(_pep508(line))
    return names


# A SOURCE SWAP IS A NEW PACKAGE TOO (reviewer P2 on #998). `next` -> a git
# repo keeps the name and changes what installs, so a dependency whose spec is
# not a registry version is recorded again as `name@<source>`: a new or changed
# source fires the pin, a version/range/tag bump does not.
_DIRECT_REF = re.compile(r"^[^@;]*?@\s*(\S+)")


def _pep508(req: str) -> list[str]:
    """`[name]`, plus `name@<url>` for a PEP 508 direct reference."""
    m = _REQ_NAME.match(req)
    if not m:
        raise ValueError(f"cannot read a package name from {req[:60]!r}")
    ref = _DIRECT_REF.match(req)
    return [m.group(1)] + ([f"{m.group(1)}@{ref.group(1)}"] if ref else [])


def _npm_source(name: str, spec: object) -> list[str]:
    """`name@<spec>` when the spec is not a registry range (git, github:, npm:, file:, a path)."""
    if isinstance(spec, str) and ":" not in spec and "/" not in spec:
        return []
    return [f"{name}@{json.dumps(spec, sort_keys=True)}"]


def dependency_names(text: str, kind: str) -> set[str]:
    """Every package NAME a manifest declares. Raises ValueError if unreadable."""
    names: list[str] = []
    if kind in ("package_json", "package_lock"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{kind} is not an object")
        tables = [data]
        if kind == "package_lock":
            packages = data.get("packages")
            if not isinstance(packages, dict):
                raise ValueError("no `packages` table (a v1 lockfile is not read)")
            # Every installed name, transitive ones included: `npm ci` installs
            # what the lock says, so a name added only here is still a new one.
            for key, meta in packages.items():
                if not key:
                    continue
                name = key.rsplit("node_modules/", 1)[-1]
                names.append(name)
                got = (meta or {}).get("resolved") if isinstance(meta, dict) else None
                if got and not str(got).startswith("https://registry.npmjs.org/"):
                    names.append(f"{name}@{got}")
            tables = [packages.get("") or {}]
        for table_owner in tables:
            for table in _PACKAGE_JSON_TABLES:
                deps = table_owner.get(table) or {}
                if not isinstance(deps, dict):
                    raise ValueError(f"`{table}` is not an object")
                for name, spec in deps.items():
                    names += [name, *_npm_source(name, spec)]
    elif kind == "requirements":
        names = _requirement_names(text)
    elif kind == "pyproject":
        try:
            import tomllib  # noqa: PLC0415 - 3.11+, and only this path needs it
        except ImportError as exc:  # pragma: no cover - CI runs 3.12
            raise ValueError("no TOML parser on this Python") from exc
        data = tomllib.loads(text)
        project = data.get("project") or {}
        reqs: list[object] = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            reqs.extend(group)
        for group in (data.get("dependency-groups") or {}).values():
            reqs.extend(r for r in group if not isinstance(r, dict))  # skip {include-group}
        reqs.extend((data.get("build-system") or {}).get("requires") or [])
        for req in reqs:
            if not isinstance(req, str):
                raise ValueError(f"cannot read a package name from {req!r}")
            names.extend(_pep508(req))
        # uv sources and poetry tables: every name, and any non-version spec
        # (git, url, path, a named source) as `name@<spec>`.
        tool = data.get("tool") or {}
        poetry = tool.get("poetry") or {}
        tables = [(tool.get("uv") or {}).get("sources") or {},
                  poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {},
                  *((g or {}).get("dependencies") or {}
                    for g in (poetry.get("group") or {}).values())]
        for table in tables:
            for name, spec in table.items():
                if name.lower() != "python":
                    names += [name] + ([] if isinstance(spec, str) and table is not tables[0]
                                       else [f"{name}@{json.dumps(spec, sort_keys=True)}"])
    else:
        raise ValueError(f"unknown manifest kind {kind!r}")
    return {_norm_entry(n, kind) for n in names}


def _norm_entry(entry: str, kind: str) -> str:
    """Normalise the NAME only; a `name@<source>` keeps its source verbatim."""
    if entry.startswith("source:"):
        return entry
    cut = entry.find("@", 1)            # skip a scoped npm name's leading `@`
    if cut < 0:
        return _norm_package(entry, kind)
    return _norm_package(entry[:cut], kind) + entry[cut:]


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _read_side(repo: str, rev: str, path: str) -> str | None:
    """The file at `rev`, or None when it is absent there. Raises if git cannot say."""
    listed = _git(repo, "ls-tree", rev, "--", path)
    if listed.returncode != 0:
        raise PinUnreadableError(f"git ls-tree {rev} failed: {listed.stderr.strip()[:120]}")
    if not listed.stdout.strip():
        return None
    shown = _git(repo, "show", f"{rev}:{path}")
    if shown.returncode != 0:
        raise PinUnreadableError(f"git show {rev}:{path} failed: {shown.stderr.strip()[:120]}")
    return shown.stdout


def content_pins(
    files: list[str],
    policy: dict[str, Any],
    base: str | None,
    head: str | None,
    repo: str = ".",
) -> dict[str, tuple[str, str]]:
    """file -> (lane, reason) for every manifest that gains a package name."""
    cfg = _pins_config(policy)
    if _PINS_BLINDED or not cfg:
        return {}
    dep = cfg.get("new_dependency") or {}
    todo: list[tuple[str, str]] = []   # (file, manifest kind)
    for f in files:
        kind = next((k for pat, k in (dep.get("manifests") or {}).items()
                     if path_matches(f, pat)), None)
        if kind:
            todo.append((f, kind))
    if not todo:
        return {}
    lane = dep["lane"]
    if not base or not head:
        return {f: (lane, "no base/head revisions to compare, so this change cannot be shown "
                          "to add no package -- the owner's") for f, _k in todo}
    for rev in (base, head):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").returncode != 0:
            return {f: (lane, f"revision `{rev}` is unreadable, so nothing could be compared")
                    for f, _k in todo}

    pins: dict[str, tuple[str, str]] = {}
    for f, kind in todo:
        try:
            before = _read_side(repo, base, f)
            after = _read_side(repo, head, f)
        except PinUnreadableError as exc:
            pins[f] = (lane, f"could not read it to compare ({exc})")
            continue
        if after is None:
            continue                      # a deleted manifest adds nothing
        try:
            was = dependency_names(before, kind) if before is not None else set()
            now = dependency_names(after, kind)
        except ValueError as exc:         # json/toml errors are ValueErrors too
            pins[f] = (lane, f"could not read its dependency tables ({exc})")
            continue
        added = sorted(now - was)
        if added:
            pins[f] = (lane, f"adds a NEW package ({', '.join(added[:5])}) -- a name nobody "
                             f"has installed here before is the owner's call (slopsquatting)")
    return pins


def classify(
    files: list[str],
    policy: dict[str, Any],
    pins: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Classify a whole changeset.

    Returns the lane, the requirements that lane demands, and -- always -- the
    specific files that caused the answer. A verdict you cannot trace to a file
    is a verdict nobody can argue with, which sounds good and is not.

    `pins` (from `content_pins()`) can only move a file to a STRICTER lane: the
    stricter of its own lane and the pin's wins, and an unknown file still
    escalates.
    """
    pins = pins or {}
    # THE ESCALATION TARGET, NAMED ONCE. Both fall-throughs below send a
    # changeset nobody has classified to this lane, and the property that has to
    # hold is `auto_merge: false`, not the lane's name. It was `product_owner`
    # until 2026-09-17, when that lane started auto-merging and the same line of
    # code silently became "ship whatever we do not recognise". Pinned by two
    # drill cases that assert the name AND the flag.
    escalate = "harness_owner"
    # ...AND THE FLAG IS READ FROM THE POLICY, NOT TYPED HERE.
    #
    # Both escalation arms used to return a literal `"auto_merge": False`. That
    # is a sentence about a lane, written somewhere the lane cannot contradict
    # it, and on 2026-09-17 it was caught doing exactly what this repo's dead
    # guards all do: pointing `escalate` at a lane that auto-merges left the
    # verdict still saying False, so the drill case "unknown path never
    # auto-merges" stayed GREEN over the mutation it exists to catch. It could
    # not go red, because it was reading a constant.
    #
    # Downstream it would have been an internally inconsistent verdict too —
    # `lane: product_owner, auto_merge: false` handed to a reader that also
    # knows product_owner auto-merges is two answers to one question, and this
    # whole file exists because that is how a cage stops meaning anything.
    #
    # Reading the real flag makes the mutation visible. What keeps it SAFE is
    # not this line but merge_cage's drill case "the escalation target is a lane
    # a machine may not merge", which asserts against the live policy that
    # `harness_owner` is still `auto_merge: false`.
    escalate_auto = bool(policy["lanes"][escalate].get("auto_merge", False))

    if not files:
        return {
            "lane": escalate,
            "requires": policy["lanes"][escalate].get("requires") or [],
            "auto_merge": escalate_auto,
            "why": ["no files in the changeset -- refusing to guess"],
            "by_lane": {},
        }

    by_lane: dict[str, list[str]] = {}
    for f in files:
        own = lane_of_file(f, policy)
        if f in pins and own != "unknown":
            own = min(own, pins[f][0], key=PRECEDENCE.index)
        by_lane.setdefault(own, []).append(f)

    # An unknown path takes the lane no machine may merge, and says which file
    # made it so.
    if by_lane.get("unknown"):
        unknown = sorted(by_lane["unknown"])
        return {
            "lane": escalate,
            "requires": policy["lanes"][escalate].get("requires") or [],
            "auto_merge": escalate_auto,
            "why": [
                f"{len(unknown)} file(s) match no lane in .github/merge-policy.yml, "
                f"so they escalate to the owner lane: {', '.join(unknown[:5])}"
                + (" ..." if len(unknown) > 5 else ""),
                "FIX: add the path to a lane in .github/merge-policy.yml (owner merges that change).",
            ],
            "by_lane": by_lane,
        }

    for lane in PRECEDENCE:
        if by_lane.get(lane):
            cfg = policy["lanes"][lane]
            hits = sorted(by_lane[lane])
            pinned = {f: pins[f][1] for f in hits if f in pins and pins[f][0] == lane}
            return {
                "lane": lane,
                "requires": cfg.get("requires") or [],
                "auto_merge": bool(cfg.get("auto_merge", False)),
                "watch_minutes": cfg.get("watch_minutes"),
                "rollback": cfg.get("rollback"),
                "why": [
                    f"lane `{lane}` -- {cfg.get('description', '')}".rstrip(" -"),
                    f"decided by {len(hits)} file(s), e.g. {', '.join(hits[:4])}"
                    + (" ..." if len(hits) > 4 else ""),
                    *(f"`{f}` is pinned here: {why}" for f, why in sorted(pinned.items())),
                ],
                "by_lane": by_lane,
                "pinned": pinned,
            }

    raise PolicyError("unreachable: every file classified but no lane matched")


def _drill() -> int:  # noqa: C901 - a drill is a list of cases, not a branch tree
    """Break the classifier on purpose. A guard nobody has watched go red is a claim."""
    policy = load_policy()
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + ("" if ok else f"   got={got!r} want={want!r}"))

    print("lane.py --drill")

    # 1. Pure harness work takes the fast lane.
    #
    # THIS TEST USED TO WANT `harness` FOR A WORKFLOW, AND IT WAS NEVER TRUE.
    # `merge_cage.py` has denied `.github/**` outright since it was written, and
    # DENY beats ALLOW, so `.github/workflows/uptime.yml` reached the owner every
    # single time while this line said otherwise. The two files were written down
    # as one on 2026-08-26 and the blanket deny was kept — changing a security
    # boundary is not a side effect of a de-duplication. The test now asserts
    # what the system does.
    #
    # The narrower rule is still available and is the owner's to take:
    # `harness_owner` names the caged workflows individually (auto-merge.yml,
    # verify-live.yml, revert-main.yml, ...), and deleting the three blanket
    # lines in merge-policy.yml hands the rest back to this fast lane.
    #
    # TAKEN 2026-10-18, owner: "let PRs touching .claude/**, .github/** or
    # scripts/** auto-merge". An ordinary workflow, script or `.claude/` note is
    # now fast-lane; the NAMED cage and the six check factories stay the owner's.
    for fast in (".github/workflows/uptime.yml", "scripts/stale_path_check.py",
                 ".claude/rules/mistakes.md"):
        v_fast = classify([fast], policy)
        check(f"harness: an ordinary harness file auto-merges: {fast}",
              (v_fast["lane"], v_fast["auto_merge"]), ("harness", True))
    for factory in (".github/workflows/ci.yml", ".github/workflows/ci-offline.yml",
                    ".github/workflows/security.yml", ".github/workflows/codeql.yml",
                    ".github/workflows/review-bugs.yml", ".github/workflows/review-security.yml"):
        v_fac = classify([factory], policy)
        check(f"a workflow that PRODUCES a required check is still the owner's: {factory}",
              (v_fac["lane"], v_fac["auto_merge"]), ("harness_owner", False))
    check("an ordinary workflow beside a check factory is still harness_owner",
          classify([".github/workflows/uptime.yml", ".github/workflows/review-bugs.yml"],
                   policy)["lane"], "harness_owner")
    check("harness: a doc edit", classify(["docs/README.md"], policy)["lane"], "harness")

    # 2. Product code takes the watched lane.
    check("product: a route", classify(["backend/src/api/routes/jobs.py"], policy)["lane"],
          "product")
    check("product: a component",
          classify(["frontend/src/components/JobCard.tsx"], policy)["lane"], "product")

    # 3. PRECEDENCE -- one dangerous file beats any number of safe ones. This is
    #    the case a risk SCORE gets wrong and a DOOR gets right.
    # THE WITNESSES HAD TO BE REAL DOCS, AND THEY WERE NOT. This list was
    # `docs/note{i}.md` — paths in NO lane, which escalate. So the case passed
    # because 40 files were unclassified, not because one migration outranked 40
    # harmless ones, and it would have stayed green with the precedence rule
    # deleted. It only showed up on 2026-09-17 when the escalation target moved
    # to `harness_owner` and the answer changed without the premise changing.
    # `docs/harness/**` is a real fast lane, so the safe files are now genuinely
    # safe and the migration is genuinely the thing doing the work.
    many_docs = [f"docs/harness/note{i}.md" for i in range(40)]
    check("precedence: 40 docs + 1 models.py change is a PRODUCT_OWNER pr",
          classify([*many_docs, "backend/src/models.py"], policy)["lane"], "product_owner")
    check("precedence: 40 docs + 1 migration is a PRODUCT_HUMAN pr (owner, 2026-10-20)",
          classify([*many_docs, "backend/migrations/007_x.sql"], policy)["lane"], "product_human")
    check("precedence: product + harness is PRODUCT",
          classify(["docs/harness/a.md", "backend/src/main.py"], policy)["lane"], "product")

    # 3b. THE CASE THE 2026-09-17 REORDER EXISTS FOR.
    #
    #     `product_owner` auto-merges now. `harness_owner` does not. Under the
    #     OLD order (`product_owner` first, because it used to be the strictest)
    #     this changeset classified `product_owner` and reported auto_merge TRUE
    #     -- so the workflow file, which a machine may never merge, would have
    #     ridden into main on the migration's ticket. Put the old tuple back in
    #     `merge_cage.LANES` and this goes red; that is the whole point of it.
    #
    #     Asserted on BOTH the lane name and the flag on purpose. The name alone
    #     would stay green if someone later gave harness_owner `auto_merge: true`;
    #     the flag alone would stay green if the changeset escalated for some
    #     unrelated reason. The defect this guards is "the machine merged a file
    #     it may not merge", and it takes both halves to say that.
    mixed = classify(["backend/migrations/0042_x.up.sql", ".github/workflows/ci.yml"], policy)
    check("precedence: a migration AND a workflow file is harness_owner",
          mixed["lane"], "harness_owner")
    check("precedence: ...and is therefore never machine-merged",
          mixed["auto_merge"], False)
    check("precedence: the workflow file is what pinned it, by the RULE",
          ".github/workflows/ci.yml" in (mixed.get("by_lane") or {}).get("harness_owner", []),
          True)
    # ...and the negative control, or the case above is bought by refusing
    # everything: the migration ALONE is the watched lane and a machine may merge
    # it (owner decision 2026-09-17).
    settings_only = classify(["backend/src/models.py"], policy)
    check("NEGATIVE CONTROL: a models.py change alone is product_owner and machine-mergeable",
          (settings_only["lane"], settings_only["auto_merge"]), ("product_owner", True))
    # OWNER 2026-10-20: migrations and auth wait for his hand, and stay watched.
    for f in ("backend/migrations/0042_x.up.sql", "backend/src/api/routes/auth.py",
              "backend/src/services/auth/tokens.py", "backend/src/api/routes/account_delete.py"):
        got = classify([f], policy)
        check(f"product_human: {f} waits for the owner and is still watched",
              (got["lane"], got["auto_merge"], bool(got.get("watch_minutes")), got.get("rollback")),
              ("product_human", False, True, "railway"))

    # 2026-10-26: settings.py left the owner's hand; it is watched like product_owner.
    got = classify(["backend/src/core/settings.py"], policy)
    check("product_owner: settings.py is machine-merged under the 15-minute watch",
          (got["lane"], got["auto_merge"], got.get("watch_minutes")), ("product_owner", True, 15))

    # 4. The cage may not edit the cage.
    check("recursion: editing the policy file is owner-only",
          classify([".github/merge-policy.yml"], policy)["lane"], "harness_owner")
    check("recursion: editing merge_cage.py is owner-only",
          classify(["scripts/merge_cage.py"], policy)["lane"], "harness_owner")
    check("recursion: editing the verifier is owner-only",
          classify([".github/workflows/verify-live.yml"], policy)["lane"], "harness_owner")

    # 5. An UNKNOWN path must escalate, never fall through to fast — and since
    #    2026-09-17 "escalate" means `harness_owner`, the only lane a machine may
    #    not merge. Both halves are asserted: the NAME, because a lane that
    #    auto-merges is not an escalation whatever it is called, and the FLAG,
    #    because the flag is the property that actually stops the arm.
    unknown = classify(["some/brand/new/place.py"], policy)
    check("unknown path escalates to the only human-only lane",
          unknown["lane"], "harness_owner")
    check("unknown path never auto-merges", unknown["auto_merge"], False)
    check("unknown path names the file",
          "some/brand/new/place.py" in " ".join(unknown["why"]), True)

    # 6. An empty changeset is refused, not waved through. This is not a
    #    hypothetical: pr-advisor.yml was reading an empty file list off a
    #    broken `gh api` call on real 11-file PRs, and this arm is what kept
    #    those out of the machine's hands.
    check("empty changeset is harness_owner", classify([], policy)["lane"], "harness_owner")
    check("empty changeset never auto-merges", classify([], policy)["auto_merge"], False)
    # ...and the flag really comes from the policy, not from a literal. Proven by
    # asking the policy the same question directly: if these two ever disagree,
    # the verdict is describing a lane that does not exist.
    check("the escalation verdict's flag is the escalation lane's real flag",
          classify([], policy)["auto_merge"],
          bool(policy["lanes"][classify([], policy)["lane"]].get("auto_merge", False)))

    # 7. Every lane demands at least one tag. A lane with `requires: []` would
    #    merge on nothing at all, which is the failure this whole file prevents.
    for lane in PRECEDENCE:
        req = policy["lanes"][lane].get("requires") or []
        check(f"lane `{lane}` demands at least one tag", len(req) >= 1, True)

    # 8. A broken policy file must raise, never default to permissive.
    broken = Path(__file__).resolve().parent / "_drill_broken_policy.yml"
    try:
        broken.write_text("lanes:\n  harness: {}\n", encoding="utf-8")
        raised = False
        try:
            load_policy(broken)
        except PolicyError:
            raised = True
        check("a policy missing lanes raises, does not default open", raised, True)
    finally:
        broken.unlink(missing_ok=True)

    missing_raised = False
    try:
        load_policy(Path(__file__).resolve().parent / "_no_such_policy.yml")
    except PolicyError:
        missing_raised = True
    check("a missing policy raises, does not default open", missing_raised, True)

    # 9. DATA EXEMPTIONS. A new power needs its own drill, and the case that
    #    matters is not "does it loosen" but "does it refuse to loosen when it
    #    cannot prove the change was data". Built on a real throwaway git repo
    #    so `git show` is genuinely exercised, not stubbed.
    import subprocess
    import tempfile

    law = "def check(root):\n    return []\n"
    reg_a = 'REGISTRY = {"a": 1}\n'
    reg_b = 'REGISTRY = {"a": 1, "b": 2}\n'
    target = "scripts/drill_registry.py"

    def _mini_repo(after: str) -> tuple[str, str, str]:
        """A repo with the file before -> after. Returns (repo, base_sha, head_sha)."""
        tmp = tempfile.mkdtemp(prefix="lane-drill-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=tmp, capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace",
        )
        run("git", "init", "-q")
        run("git", "config", "user.email", "drill@local")
        run("git", "config", "user.name", "drill")
        (Path(tmp) / "scripts").mkdir()
        (Path(tmp) / target).write_text(reg_a + law, encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "before")
        base = run("git", "rev-parse", "HEAD").stdout.strip()
        (Path(tmp) / target).write_text(after, encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "after")
        head = run("git", "rev-parse", "HEAD").stdout.strip()
        return tmp, base, head

    repo, base, head = _mini_repo(reg_b + law)  # data changed, law untouched
    kept, exempted = apply_data_exemptions([target], policy, base, head, repo)
    check("exemption: a registry-only edit stops pinning the PR", (kept, exempted), ([], [target]))
    check("exemption: and the PR then leaves the owner lane",
          classify(kept or ["docs/harness/x.md"], policy)["lane"], "harness")

    repo, base, head = _mini_repo(reg_b + "def check(root):\n    return ['x']\n")
    kept, exempted = apply_data_exemptions([target], policy, base, head, repo)
    check("exemption: a law edit is NOT exempted", (kept, exempted), ([target], []))
    check("exemption: so the PR stays harness_owner", classify(kept, policy)["lane"], "harness_owner")

    # The direction that matters most: with nothing to compare, do not loosen.
    kept, exempted = apply_data_exemptions([target], policy, None, None, ".")
    check("exemption: no revisions -> nothing exempted (strict default)",
          (kept, exempted), ([target], []))

    # A file the policy never exempted must be untouched by any of this.
    kept, _ = apply_data_exemptions(["backend/migrations/9.sql"], policy, base, head, repo)
    check("exemption: a non-exempt file is never dropped", kept, ["backend/migrations/9.sql"])

    # 10. THE WHOLE POINT OF SPLITTING owner IN TWO. If both owner lanes get
    #     identical treatment the split is decoration, so pin the difference:
    #     a hand-merged migration still reaches users and must still be watched;
    #     a hand-merged cage edit has nobody downstream and must not be.
    mig = classify(["backend/src/models.py"], policy)  # migrations, auth, settings are product_human since 2026-10-20
    cage = classify(["scripts/merge_cage.py"], policy)
    check("product_owner is WATCHED even though you merge it by hand",
          bool(mig.get("watch_minutes")), True)
    check("harness_owner is NOT watched -- nothing live is downstream",
          bool(cage.get("watch_minutes")), False)
    check("product_owner can be rolled back on Railway", mig.get("rollback"), "railway")

    # REWRITTEN 2026-09-17. This line used to read "neither owner lane ever
    # auto-merges" and it was the single sentence the owner's decision
    # contradicted, so it is stated as the new asymmetry rather than deleted:
    #
    #   product_owner  auto-merges, BECAUSE it is watched and can be rolled back.
    #   harness_owner  does not, because the watcher and the rollback are FILES
    #                  IN IT -- there is no undo to borrow speed against.
    #
    # The second half is the load-bearing one and it is asserted on its own
    # below so that flipping harness_owner can never be a side effect of
    # anything.
    check("product_owner auto-merges — the 15-minute watcher is the argument",
          mig["auto_merge"], True)
    check("harness_owner NEVER auto-merges — it owns the watcher, so it has no undo",
          cage["auto_merge"], False)
    check("...and the watched lane really is watched, or the flip has no argument",
          (mig.get("watch_minutes"), mig.get("rollback")), (15, "railway"))

    # 11. THE LEAKY-GLOB TRAP -- and why this section was rewritten 2026-08-20.
    #
    #     The version that stood here asked `pattern.startswith("*.")`. That is
    #     the SHAPE of the one example someone happened to find (`*.md`), not the
    #     bug. So it printed
    #         ok   fast lane `harness` has no depth-crossing glob: README.md
    #     while that exact pattern was reaching `docs/product/pillars/README.md`
    #     and classifying it `harness`, auto_merge TRUE. A green sentence that was
    #     false. It also repeated the wrong MECHANISM -- `*` does not cross `/`,
    #     it compiles to `[^/]*` -- and a drill written from a wrong mechanism
    #     tests the wrong thing by construction.
    #
    #     These checks ask about BEHAVIOUR instead: they build a real nested path
    #     and classify it. A syntax rule can only catch the shapes it was taught;
    #     an outcome check catches any pattern that produces the wrong outcome,
    #     including ones nobody has invented yet.

    # 11a. THE INVARIANT, STATED AS AN OUTCOME — AND NARROWED 2026-08-26.
    #
    #      Wave 1 asserted that NOTHING under docs/product/ may be merged by a
    #      machine, at any depth. The owner has since decided that ordinary
    #      product prose is prose: it reaches no user, and the `verify` tag —
    #      watching production for 15 minutes — cannot say anything true about a
    #      markdown file. `docs/product/**` is in the `harness` lane now.
    #
    #      What survives is the part that was always the real invariant: a
    #      document that IS a decision still goes to a human. Both files are
    #      named individually in `product_owner`, and both are cited BY NUMBER
    #      from CLAUDE.md, which is what makes them rules rather than prose.
    #
    #      The depth cases stay, because the mechanism they were written to catch
    #      has not changed: a pattern that fails to cross `/` would classify a
    #      nested file wrongly, and only a real nested path can prove it does not.
    #      ASSERT THE LANE, NOT `auto_merge` — §11c below found this the hard way
    #      and these checks were written breaking its rule. The concrete miss:
    #      delete BOTH `docs/product/**` from `harness` and
    #      `product_design_rules.md` from `product_owner`, and the file matches
    #      nothing, escalates to `product_owner`, and reports auto_merge False.
    #      An auto_merge assertion goes GREEN over two deleted rules. Escalation
    #      can fake that answer; it cannot fake the lane NAME.
    for depth in ("docs/product/x.md",
                  "docs/product/pillars/README.md",
                  "docs/product/plans/deep/er/still.md",
                  "docs/product/research/nested/notes.md"):
        check(f"ordinary product prose is in the harness lane at depth: {depth}",
              classify([depth], policy)["lane"], "harness")

    # 11b. ...and the two documents that are DECISIONS still are not, at any
    #      depth and whatever else is in the changeset with them. Same rule:
    #      the lane name, because `product_owner` reached by ESCALATION and
    #      `product_owner` reached by the RULE are different facts that
    #      `auto_merge` renders identically.
    #
    #      2026-09-17: the CLAIM these cases make is narrower now. They used to
    #      say "still needs you"; since the product_owner flip they say "still
    #      separated from ordinary prose" -- the two decision documents sit in
    #      the watched lane, get the 15-minute watch, and do not ride the
    #      fast lane with a README. They no longer stop for a hand. Moving these
    #      two lines to `harness_owner` in the policy is how the owner takes
    #      that back, and it is two lines.
    for decision in ("docs/product/product_design_rules.md",
                     "docs/product/plans/batch-2-decisions.md"):
        verdict = classify([decision], policy)
        check(f"a document that IS a decision is not ordinary prose: {decision}",
              verdict["lane"], "product_owner")
        check(f"...by the RULE, not by escalation: {decision}",
              decision in (verdict.get("by_lane") or {}).get("product_owner", []), True)
        check(f"...even beside ordinary prose: {decision}",
              classify(["docs/product/pillars/README.md", decision], policy)["lane"],
              "product_owner")

    # 11b. EVERY FAST-LANE PATTERN, WITH A REAL NESTED WITNESS.
    #
    #      THE FIRST VERSION OF THIS WAS VACUOUS AND I SHIPPED IT AS THE FIX FOR
    #      EXACTLY THIS BUG CLASS. It built the witness as `pretend-dir/{pattern}`
    #      and asserted the pattern did not match it. But the matcher is
    #      ROOT-ANCHORED, so `docs/README.md` can never match
    #      `pretend-dir/docs/README.md` no matter how broken it becomes. Every
    #      case asserted False == False. It could not go red for anything.
    #      (CodeRabbit, PR #357.)
    #
    #      The witness now comes from REAL TRACKED FILES: take a file the pattern
    #      actually matches, push it one directory deeper, and require the
    #      pattern to STOP matching. That is the depth-leak question, asked
    #      somewhere the answer can actually be no.
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout.split()
    for fast in ("harness", "product"):
        for pattern in policy["lanes"][fast].get("paths") or []:
            if "**" in pattern:
                continue          # asked for depth in writing; that is allowed
            hit = next((f for f in tracked if path_matches(f, pattern)), None)
            if hit is None:
                continue          # nothing in the tree exercises it today
            head, _, tail = hit.rpartition("/")
            deeper = f"{head}/pretend-dir/{tail}" if head else f"pretend-dir/{tail}"
            check(f"fast-lane pattern does not reach one level deeper: {pattern}",
                  path_matches(deeper, pattern), False)

    # 11c. THE DIRECTIONS ARE NOT SYMMETRIC, so the deny side gets the opposite
    #      question. Deleting the basename fallback LOOSENED every slash-free
    #      deny at a stroke; each was rewritten with `**/` in the same commit and
    #      every one is pinned here by a real nested file that exists in this repo.
    #      If someone drops a `**/`, the file stops being owner-lane and this goes red.
    #      ASSERT THE LANE, NOT `auto_merge`. Found by drilling this drill: revert
    #      `**/CLAUDE.md` to `CLAUDE.md` and `backend/CLAUDE.md` then matches
    #      nothing, escalates to `product_owner`, and reports auto_merge False --
    #      so an auto_merge assertion goes GREEN over a deny that stopped working.
    #      Escalation can fake that answer. It cannot fake the lane name.
    for nested, want_lane in (("backend/Dockerfile", "product_owner"),
                              ("backend/railway.json", "product_owner"),
                              ("frontend/package.json", "product_owner"),
                              ("frontend/package-lock.json", "product_owner"),
                              ("backend/pyproject.toml", "product_owner"),
                              ("frontend/.env.local.example", "product_owner"),
                              ("backend/CLAUDE.md", "harness_owner")):
        check(f"still owner-lane at depth after the `**/` rewrite: {nested}",
              classify([nested], policy)["lane"], want_lane)

    # 11d. THE CAGE MAY NOT EDIT THE CAGE -- derived, not listed. merge-policy.yml
    #      names the cage's files by hand, and a hand-list is wrong the moment
    #      someone adds a seventh. So this asks the CODE: whatever merge_cage.py
    #      imports at run time is part of the cage, and must be owner-lane.
    #      `cage_blockers.py` was found exactly this way -- merge_cage.py does
    #      `from cage_blockers import BLOCKERS, undrilled`, so an edit to it
    #      changes what the cage refuses, while merge_cage.py sat owner-locked.
    cage_src = (Path(__file__).resolve().parent / "merge_cage.py").read_text(encoding="utf-8")
    siblings = {f.stem for f in Path(__file__).resolve().parent.glob("*.py")}

    # A SECOND WAY IN, AND THE FIRST VERSION OF THIS CHECK MISSED IT.
    # Deriving cage membership from IMPORTS alone finds `cage_blockers` and stops.
    # But merge_cage also reaches code by EXECUTING it: every RATCHET is a
    # subprocess whose printed number the cage then trusts. `merge_cage.py:367`
    # runs `backend/scripts/mypy_ratchet.py --count` and `:379` runs
    # `drill_registry.py --count-owed` -- so a machine able to merge either file
    # can make a ratchet report a false number while merge_cage.py itself sits
    # owner-locked. Same escape as the import route, through a different door.
    #
    # Found by running the cage over a real PR: `backend/scripts/mypy_ratchet.py`
    # came back "no rule covers this path" while merge-policy.yml's `backend/
    # scripts/**` put it in the FAST lane -- two systems, two answers, and this
    # drill agreeing with neither because it never looked.
    executed = set(re.findall(r'"cmd":\s*\[sys\.executable,\s*"([^"]+\.py)"', cage_src))
    imported = {
        name for name in siblings
        if re.search(rf"^[ \t]*(?:from {name} import|import {name})\b", cage_src, re.M)
    }
    #      A DERIVATION THAT FINDS NOTHING IS NOT A PASS. The first version of
    #      this check had a doubled escape in its regex, so it derived zero
    #      imports, printed zero lines, and reported green -- committing, inside
    #      its own body, the exact failure it exists to catch. An empty
    #      derivation is now a red.
    check("the cage-membership derivation actually found something",
          len(imported) > 0 and len(executed) > 0, True)
    for name in sorted(imported):
        check(f"the cage may not edit the cage -- merge_cage imports {name}",
              classify([f"scripts/{name}.py"], policy)["lane"], "harness_owner")
    for path in sorted(executed):
        check(f"the cage may not edit the cage -- merge_cage executes {path}",
              classify([path], policy)["auto_merge"], False)

    # 11e. THE UNDO. The product lane's speed is explicitly borrowed against
    #      being able to roll back, so the rollback must not be machine-mergeable.
    #      These two are what the enforcement workflows actually execute.
    for gear in ("scripts/rollback_gear.py", "scripts/revert_gear.py"):
        check(f"the undo is not machine-mergeable: {gear}",
              classify([gear], policy)["auto_merge"], False)

    # ASSERT THE LANE TOO, from 2026-09-17. `auto_merge == False` alone was a
    # true sentence about a moving target: it stayed green through the
    # product_owner flip while the lane it named quietly became machine-
    # mergeable. The name is the fact that cannot be faked by escalation.
    nested_doc = classify(["docs/somewhere/new.md"], policy)
    check("an unclassified nested doc escalates to harness_owner",
          nested_doc["lane"], "harness_owner")
    check("an unclassified nested doc escalates, never auto-merges",
          nested_doc["auto_merge"], False)
    check("a root doc that IS classified still takes the fast lane",
          classify(["README.md"], policy)["lane"], "harness")

    # 12. THE `.claude` CARVE-OUT (owner, 2026-09-17: "merge all daily docs").
    #
    #     The daily truth-check PR edits `.claude/skills/**/*.md`. The blanket
    #     `.claude/**` in `harness_owner` made every one of them a hand-merge,
    #     for prose an agent reads.
    #
    #     Both directions are pinned, because this carve-out is only safe as a
    #     PAIR: the reading matter merges, the executing, configuring and
    #     JUDGING parts do not. Widen `.claude/skills/**` to `.claude/**` and the
    #     second group goes red instantly.
    for readable in (".claude/skills/hard-rules/SKILL.md",
                     ".claude/skills/run-job360/references/methodology.md",
                     ".claude/skills/debug/SKILL.md"):
        v_read = classify([readable], policy)
        check(f"the words an agent reads take the fast lane: {readable}",
              (v_read["lane"], v_read["auto_merge"]), ("harness", True))
    #     `.claude/agents/**` WAS IN THE LIST ABOVE FOR ONE COMMIT. reviewer-bugs
    #     raised it as a P0 on PR #582: that directory defines the reviewers this
    #     gate relies on (`bugs` IS the reviewer-bugs check), so machine-
    #     mergeable it lets a PR soften its own reviewer and then be merged by
    #     the softened reviewer. All three files are named, not just the one the
    #     P0 cited — the rule is about the directory, and `verifier.md` is the
    #     one nobody would think to check.
    for guarded in (".claude/hooks/commit-gate.sh",
                    ".claude/settings.json",
                    ".claude/settings.local.json",
                    ".claude/agents/reviewer-bugs.md",
                    ".claude/agents/reviewer-conventions.md",
                    ".claude/agents/verifier.md"):
        v_guard = classify([guarded], policy)
        check(f"the half that RUNS, CONFIGURES or JUDGES is still yours: {guarded}",
              (v_guard["lane"], v_guard["auto_merge"]), ("harness_owner", False))
    #     ...and a skill beside an agent definition is the owner's, because
    #     precedence is a door. Without this the skills carve-out is a way in.
    check("a skill doc beside a reviewer definition is still harness_owner",
          classify([".claude/skills/hard-rules/SKILL.md",
                    ".claude/agents/verifier.md"], policy)["lane"], "harness_owner")
    # ...and a skill beside a hook is a harness_owner PR, because precedence is a
    # door. Without this, the carve-out could be used as a way in.
    check("a skill doc beside a hook is still harness_owner",
          classify([".claude/skills/debug/SKILL.md",
                    ".claude/hooks/commit-gate.sh"], policy)["lane"], "harness_owner")

    # 13. THE NEW-PACKAGE TRIPWIRE (owner, 2026-10-20). A manifest that ADDS a
    #     package name is `product_human`; a version bump keeps its own lane.
    #     Built on real throwaway repos so `git show` is exercised, as in 9.
    #     `--break-checker pins` blinds `content_pins()` and these go red.
    human = ("product_human", False)

    def lane_auto(paths: list[str], pins: dict[str, tuple[str, str]] | None = None) -> tuple:
        v = classify(paths, policy, pins)
        return v["lane"], v["auto_merge"]

    def pinned_lane(rel: str, before: dict[str, str], after: dict[str, str]) -> tuple:
        tmp = tempfile.mkdtemp(prefix="lane-pin-drill-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=tmp, capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace",
        )
        run("git", "init", "-q")
        run("git", "config", "user.email", "drill@local")
        run("git", "config", "user.name", "drill")
        shas = []
        for files in (before, after):
            for p, text in files.items():
                (Path(tmp) / p).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / p).write_text(text, encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "x", "--allow-empty")
            shas.append(run("git", "rev-parse", "HEAD").stdout.strip())
        return lane_auto([rel], content_pins([rel], policy, shas[0], shas[1], tmp))

    check("content pin `new_dependency` exists and pins to a lane a machine may not merge",
          policy["lanes"][((policy.get("content_pins") or {}).get("new_dependency") or {})
                          .get("lane", "product")].get("auto_merge"), False)
    pkg = "frontend/package.json"
    pj = lambda deps, dev: json.dumps({"name": "x", "dependencies": deps,  # noqa: E731
                                       "devDependencies": dev})
    base_pj = pj({"next": "16.0.0"}, {"vitest": "3.0.0"})
    check("package.json: a version BUMP stays product_owner",
          pinned_lane(pkg, {pkg: base_pj}, {pkg: pj({"next": "16.1.0"}, {"vitest": "3.0.0"})}),
          ("product_owner", True))
    check("package.json: a NEW dependency is the owner's",
          pinned_lane(pkg, {pkg: base_pj},
                      {pkg: pj({"next": "16.0.0", "reqeusts": "1.0.0"}, {"vitest": "3.0.0"})}), human)
    check("package.json: a REMOVED dependency is not a new one",
          pinned_lane(pkg, {pkg: base_pj}, {pkg: pj({}, {"vitest": "3.0.0"})}), ("product_owner", True))
    check("package.json: unparseable after the change -> the owner's (strict)",
          pinned_lane(pkg, {pkg: base_pj}, {pkg: "{ not json"}), human)
    lock = "frontend/package-lock.json"
    pl = lambda pk: json.dumps({"lockfileVersion": 3, "packages": pk})  # noqa: E731
    base_pl = pl({"": {"dependencies": {"next": "16"}}, "node_modules/next": {"version": "16.0.0"}})
    check("package-lock.json: a version BUMP stays product_owner",
          pinned_lane(lock, {lock: base_pl}, {lock: pl({"": {"dependencies": {"next": "16"}},
                                                         "node_modules/next": {"version": "16.1.0"}})}),
          ("product_owner", True))
    check("package-lock.json: a NEW installed name (even transitive) is the owner's",
          pinned_lane(lock, {lock: base_pl},
                      {lock: pl({"": {"dependencies": {"next": "16"}}, "node_modules/next": {},
                                 "node_modules/next/node_modules/@evil/pkg": {}})}), human)
    req = "backend/requirements.lock.txt"
    base_rq = "fastapi==0.115.0 \\\n    --hash=sha256:aa\nPython_Dotenv==1.0  # via x\n"
    check("requirements lock: a version BUMP (and a PEP 503 respelling) stays product_owner",
          pinned_lane(req, {req: base_rq},
                      {req: "fastapi==0.120.0 \\\n    --hash=sha256:bb\npython-dotenv==1.1\n"}),
          ("product_owner", True))
    check("requirements lock: a NEW package is the owner's",
          pinned_lane(req, {req: base_rq}, {req: base_rq + "fastapi-helpers-pro==1.0\n"}), human)
    check("requirements: a NEW index URL is the owner's too",
          pinned_lane("backend/requirements-dev.txt", {"backend/requirements-dev.txt": "pytest\n"},
                      {"backend/requirements-dev.txt": "--extra-index-url https://x.example\npytest\n"}),
          human)
    pyp = "backend/pyproject.toml"
    tm = lambda deps, dev: (  # noqa: E731
        "[project]\nname = \"x\"\ndependencies = [" + ", ".join(f'"{d}"' for d in deps) + "]\n"
        "[project.optional-dependencies]\ndev = [" + ", ".join(f'"{d}"' for d in dev) + "]\n")
    base_tm = tm(["fastapi>=0.115", "psycopg[binary,pool]>=3.2"], ["pytest>=8"])
    check("pyproject: a version BUMP stays product_owner",
          pinned_lane(pyp, {pyp: base_tm},
                      {pyp: tm(["fastapi>=0.120", "psycopg[binary,pool]>=3.3"], ["pytest>=9"])}),
          ("product_owner", True))
    check("pyproject: a NEW optional (dev) dependency is the owner's",
          pinned_lane(pyp, {pyp: base_tm},
                      {pyp: tm(["fastapi>=0.115", "psycopg[binary,pool]>=3.2"],
                               ["pytest>=8", "pytest-magic"])}), human)
    # A SOURCE SWAP keeps the name and changes what installs (reviewer P2, #998).
    check("package.json: a range bump (^16.0.0 -> ^16.1.0) stays product_owner",
          pinned_lane(pkg, {pkg: pj({"next": "^16.0.0"}, {})}, {pkg: pj({"next": "^16.1.0"}, {})}),
          ("product_owner", True))
    check("package.json: a SOURCE swap of an existing name is the owner's",
          pinned_lane(pkg, {pkg: base_pj},
                      {pkg: pj({"next": "github:evil/next"}, {"vitest": "3.0.0"})}), human)
    check("package.json: an npm: alias of an existing name is the owner's",
          pinned_lane(pkg, {pkg: base_pj},
                      {pkg: pj({"next": "npm:nexxt@16.0.0"}, {"vitest": "3.0.0"})}), human)
    check("pyproject: a direct reference (`pkg @ git+https`) is the owner's",
          pinned_lane(pyp, {pyp: base_tm},
                      {pyp: tm(["fastapi @ git+https://x.example/fastapi",
                                "psycopg[binary,pool]>=3.2"], ["pytest>=8"])}), human)
    check("pyproject: a new [tool.uv.sources] entry is the owner's",
          pinned_lane(pyp, {pyp: base_tm},
                      {pyp: base_tm + "[tool.uv.sources]\nfastapi = { git = \"https://x.example\" }\n"}),
          human)
    check("requirements: a `pkg @ https://` line for an existing name is the owner's",
          pinned_lane(req, {req: base_rq},
                      {req: base_rq.replace("fastapi==0.115.0", "fastapi @ https://x.example/f.whl")}),
          human)
    check("NEGATIVE CONTROL: a harmless neighbour of a manifest keeps its lane",
          pinned_lane("frontend/src/lib/api.ts", {"frontend/src/lib/api.ts": "a"},
                      {"frontend/src/lib/api.ts": "b"}), ("product", True))
    no_rev = classify([pkg], policy, content_pins([pkg], policy, None, None))
    check("no revisions -> the pin FIRES (strict, the opposite of the exemption)",
          (no_rev["lane"], no_rev["auto_merge"], sorted(no_rev.get("pinned") or {})),
          ("product_human", False, [pkg]))
    check("a pin never LOOSENS: a cage file pinned to product_human stays harness_owner",
          lane_auto([".github/workflows/ci.yml"],
                    {".github/workflows/ci.yml": ("product_human", "drill")}), ("harness_owner", False))
    check("the verdict NAMES the pin, so the owner knows what to read",
          any("NEW package" in w for w in classify(
              [pkg], policy, {pkg: ("product_human", "adds a NEW package (x)")})["why"]), True)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Classify a changeset into a merge lane.")
    ap.add_argument("files", nargs="*", help="changed file paths")
    ap.add_argument("--json", metavar="FILE", help="write the verdict as JSON")
    ap.add_argument("--drill", action="store_true", help="break the classifier on purpose")
    ap.add_argument("--base", help="revision before the change (enables data exemptions)")
    ap.add_argument("--head", help="revision after the change (enables data exemptions)")
    ap.add_argument("--repo", default=".", help="repository root")
    # SEPARATE FROM --base/--head ON PURPOSE (2026-10-20). Those also switch on
    # the data exemptions, which only LOOSEN; pr-advisor and the arm withhold
    # them. A pin only tightens, so a caller can hand it revisions without
    # granting an exemption. Defaults to --base/--head when those are given.
    ap.add_argument("--pin-base", help="revision before the change, for the content pins")
    ap.add_argument("--pin-head", help="revision after the change, for the content pins")
    ap.add_argument("--pin-repo", help="repository the pin revisions live in (default --repo)")
    ap.add_argument("--break-checker", choices=["pins"],
                    help="drill only: blind the content pins, so the drill must go red")
    args = ap.parse_args(argv)

    if args.break_checker and not args.drill:
        ap.error("--break-checker is a drill flag")
    if args.drill:
        if args.break_checker == "pins":
            global _PINS_BLINDED
            _PINS_BLINDED = True
        return _drill()

    policy = load_policy()
    files, exempted = apply_data_exemptions(
        args.files, policy, args.base, args.head, args.repo
    )
    pins = content_pins(files, policy, args.pin_base or args.base, args.pin_head or args.head,
                        args.pin_repo or args.repo)
    verdict = classify(files, policy, pins)
    if exempted:
        verdict["data_exempted"] = exempted
        verdict["why"].append(
            f"{len(exempted)} owner-lane file(s) changed only their declared DATA, "
            f"not their law, so they did not pin this PR: {', '.join(exempted)}"
        )
    text = json.dumps(verdict, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
