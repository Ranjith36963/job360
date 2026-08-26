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
import re
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
# migrations with no post-merge watcher at all. product_owner outranks
# harness_owner because a user is downstream of it.
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
    check("harness: a workflow edit is the OWNER's (blanket .github/**)",
          classify([".github/workflows/uptime.yml"], policy)["lane"], "harness_owner")
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
    mig = classify(["backend/migrations/007_x.sql"], policy)
    cage = classify(["scripts/merge_cage.py"], policy)
    check("product_owner is WATCHED even though you merge it by hand",
          bool(mig.get("watch_minutes")), True)
    check("harness_owner is NOT watched -- nothing live is downstream",
          bool(cage.get("watch_minutes")), False)
    check("product_owner can be rolled back on Railway", mig.get("rollback"), "railway")
    check("neither owner lane ever auto-merges",
          (mig["auto_merge"], cage["auto_merge"]), (False, False))

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
    for decision in ("docs/product/product_design_rules.md",
                     "docs/product/plans/batch-2-decisions.md"):
        verdict = classify([decision], policy)
        check(f"a document that IS a decision still needs you: {decision}",
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
