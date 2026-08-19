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

    product_owner  >  harness_owner  >  product  >  harness

One migration file in a 40-file docs PR makes the whole thing a product_owner PR. This
is deliberate and it is the opposite of a risk score. A score can be diluted --
add enough safe files and the dangerous one stops mattering. A door cannot.

THE OTHER RULE: AN UNKNOWN PATH IS NOT A SAFE PATH
--------------------------------------------------
A file matching no lane does not fall through to the fast lane. It escalates to
`product_owner` -- the strictest of the four -- and the reason names the file. This repo has shipped ten guards that
could not fire, every one of them a check that reported success about a question
it was not actually asking. `else: allow` is that bug in one word.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Reuse the cage's own glob matcher rather than writing a second one. Two
# implementations of "does this path match" is two answers to the same question,
# and the day they disagree is the day the cage means nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_cage import path_matches  # noqa: E402

POLICY_PATH = Path(__file__).resolve().parent.parent / ".github" / "merge-policy.yml"

# Most restrictive first. Order IS the precedence rule.
#
# TWO owner lanes, not one. "Who decides" and "who gets hurt" are separate
# questions; a single `owner` bucket collapsed them and quietly left hand-merged
# migrations with no post-merge watcher at all. product_owner outranks
# harness_owner because a user is downstream of it.
PRECEDENCE: tuple[str, ...] = ("product_owner", "harness_owner", "product", "harness")


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


def classify(files: list[str], policy: dict[str, Any]) -> dict[str, Any]:
    """Classify a whole changeset.

    Returns the lane, the requirements that lane demands, and -- always -- the
    specific files that caused the answer. A verdict you cannot trace to a file
    is a verdict nobody can argue with, which sounds good and is not.
    """
    if not files:
        return {
            "lane": "product_owner",
            "requires": policy["lanes"]["product_owner"].get("requires") or [],
            "auto_merge": False,
            "why": ["no files in the changeset -- refusing to guess"],
            "by_lane": {},
        }

    by_lane: dict[str, list[str]] = {}
    for f in files:
        by_lane.setdefault(lane_of_file(f, policy), []).append(f)

    # An unknown path takes the STRICTEST lane, and says which file made it so.
    if by_lane.get("unknown"):
        unknown = sorted(by_lane["unknown"])
        return {
            "lane": "product_owner",
            "requires": policy["lanes"]["product_owner"].get("requires") or [],
            "auto_merge": False,
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
                ],
                "by_lane": by_lane,
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
    check("harness: a workflow edit",
          classify([".github/workflows/uptime.yml"], policy)["lane"], "harness")
    check("harness: a doc edit", classify(["docs/README.md"], policy)["lane"], "harness")

    # 2. Product code takes the watched lane.
    check("product: a route", classify(["backend/src/api/routes/jobs.py"], policy)["lane"],
          "product")
    check("product: a component",
          classify(["frontend/src/components/JobCard.tsx"], policy)["lane"], "product")

    # 3. PRECEDENCE -- one dangerous file beats any number of safe ones. This is
    #    the case a risk SCORE gets wrong and a DOOR gets right.
    many_docs = [f"docs/note{i}.md" for i in range(40)]
    check("precedence: 40 docs + 1 migration is an OWNER pr",
          classify([*many_docs, "backend/migrations/007_x.sql"], policy)["lane"], "product_owner")
    check("precedence: product + harness is PRODUCT",
          classify(["docs/harness/a.md", "backend/src/main.py"], policy)["lane"], "product")

    # 4. The cage may not edit the cage.
    check("recursion: editing the policy file is owner-only",
          classify([".github/merge-policy.yml"], policy)["lane"], "harness_owner")
    check("recursion: editing merge_cage.py is owner-only",
          classify(["scripts/merge_cage.py"], policy)["lane"], "harness_owner")
    check("recursion: editing the verifier is owner-only",
          classify([".github/workflows/verify-live.yml"], policy)["lane"], "harness_owner")

    # 5. An UNKNOWN path must escalate, never fall through to fast.
    unknown = classify(["some/brand/new/place.py"], policy)
    check("unknown path escalates to the strictest lane", unknown["lane"], "product_owner")
    check("unknown path never auto-merges", unknown["auto_merge"], False)
    check("unknown path names the file",
          "some/brand/new/place.py" in " ".join(unknown["why"]), True)

    # 6. An empty changeset is refused, not waved through.
    check("empty changeset is product_owner", classify([], policy)["lane"], "product_owner")
    check("empty changeset never auto-merges", classify([], policy)["auto_merge"], False)

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
            a, cwd=tmp, capture_output=True, text=True, check=True
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
    mig = classify(["backend/migrations/007_x.sql"], policy)
    cage = classify(["scripts/merge_cage.py"], policy)
    check("product_owner is WATCHED even though you merge it by hand",
          bool(mig.get("watch_minutes")), True)
    check("harness_owner is NOT watched -- nothing live is downstream",
          bool(cage.get("watch_minutes")), False)
    check("product_owner can be rolled back on Railway", mig.get("rollback"), "railway")
    check("neither owner lane ever auto-merges",
          (mig["auto_merge"], cage["auto_merge"]), (False, False))

    # 11. THE LEAKY-GLOB TRAP. This repo's matcher lets `*` cross `/` -- the
    #     owner lane's `*.env*` and `*docker-compose*` depend on that. So a
    #     pattern like `*.md` in a FAST lane silently matches every markdown at
    #     every depth, and any unclassified document auto-merges. That is exactly
    #     what happened: `docs/product/product_design_rules.md` classified as `harness`,
    #     auto_merge True -- the canonical text of the owner's product rules,
    #     mergeable by a machine. No fast lane may carry a bare `*.ext` pattern.
    for fast in ("harness", "product"):
        for pattern in policy["lanes"][fast].get("paths") or []:
            check(f"fast lane `{fast}` has no depth-crossing glob: {pattern}",
                  pattern.startswith("*.") and "/" not in pattern, False)
    check("an unclassified nested doc escalates, never auto-merges",
          classify(["docs/somewhere/new.md"], policy)["auto_merge"], False)
    check("a root doc that IS classified still takes the fast lane",
          classify(["README.md"], policy)["lane"], "harness")


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
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    policy = load_policy()
    files, exempted = apply_data_exemptions(
        args.files, policy, args.base, args.head, args.repo
    )
    verdict = classify(files, policy)
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
