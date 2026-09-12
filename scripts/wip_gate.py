#!/usr/bin/env python3
"""THE WIP LIMIT — refuse a 3rd open PR by PARKING it, never by losing it.

OWNER DECISION (2026-09-08): "refuse a 3rd open PR (WIP 2)". Refuse does NOT
mean discard. A pull request opened while `wip.limit` (2) other real PRs are
already open gets PARKED, a comment says why and how to undo it, and the
owner is told in Slack once.

PARKING IS A LABEL (`wip:parked`), NOT DRAFT STATUS -- CHANGED 2026-09-12.
---------------------------------------------------------------------------
The original version of this file converted a PR to a draft (`gh pr ready
--undo`). Measured on PR #560, pr-advisor run 34664353220, job 103473145164:
the gate decided correctly ("2 other open ... PRs ahead: #558, #559") and
then the "Park it — convert to draft" step failed with

    GraphQL: Resource not accessible by integration (convertPullRequestToDraft)

The workflow's `GITHUB_TOKEN` cannot convert a PR to draft -- that mutation
needs a permission GitHub does not grant the default token. The step exited
1, which put a RED check on the PR, which woke `finding-watch.yml` (it reads
failed checks as findings), which dispatched `pr-repair.yml` three times --
each of which refused at its own gate (nothing to repair; the "finding" was
this workflow's own parking step failing). Three wasted repair runs from one
parking decision.

So parking is now a LABEL, `wip:parked`, applied with `gh pr edit --add-
label` -- a mutation the default `GITHUB_TOKEN` genuinely has. A labeled PR
is exempted from the WIP count and filtered out of `auto-merge.yml`'s
candidate list exactly like a draft, so the refusal mechanism (invisible to
the merge queue) is unchanged; only the marker changed. And because a
parking decision must never again produce a red check that wakes the fixer,
the calling workflow step never fails the job for a parking outcome -- see
`.github/workflows/pr-advisor.yml`'s `wip` job for how that is now enforced.

WHAT COUNTS TOWARD THE LIMIT, AND WHAT DOES NOT
------------------------------------------------
Only OTHER open, non-draft, non-parked PRs whose author is not dependabot
and whose branch is not an emergency revert count against the limit:

  * a draft already opted itself out of the merge queue -- counting it would
    park a PR because of one that was never competing for a slot
  * a PR labeled `wip:parked` is, for exactly the same reason, already
    parked -- it is not competing for a slot either, so it must not count
    against one
  * dependabot has its OWN loop (dependabot-auto.yml) and its own cadence;
    folding it into the human WIP count would park a human's PR because a
    bot opened three dependency bumps overnight
  * `revert/*` branches are emergency undos. Parking one delays the exact
    thing that puts a broken deploy back -- the one PR that must never wait

THIS PR'S OWN EXEMPTIONS
-------------------------
The PR being judged is *itself* never parked if it is a dependabot PR, a
revert, or ALREADY carries `wip:parked` -- same reasoning as above, stated
the other way round: the PR that would be exempt from the COUNT would also
be senseless to PARK (again), and a PR already parked has nothing left to do.

THE POLICY, AND WHY A MISSING BLOCK REFUSES RATHER THAN DEFAULTS
------------------------------------------------------------------
The limit itself lives in `.github/merge-policy.yml` under a `wip:` block, so
the owner turns a dial there, never edits this file. Following
`scripts/lane.py`'s `load_policy()`: a MISSING block raises rather than
defaulting to some number -- `else: allow` reads as "no limit" and every
guard in this repo that ever defaulted open did so quietly. But the SAFE
direction here is not "no limit", it is also not "assume the limit and park
someone's PR on a misread" -- parking is a visible, disruptive action taken
against a stranger's work, and doing it on a policy this script guessed at is
worse than not doing it at all. So a missing/malformed `wip:` block is a LOUD
refusal to decide anything: exit 2, no park, say why. The gate stands down
the same way `pr-advisor.yml`'s `precheck` job does when the cage itself
is not on the default branch yet -- SKIPPED/REFUSED, never a false verdict.

USAGE
  python scripts/wip_gate.py --pr N --json out.json   # decide for real PR N
  python scripts/wip_gate.py --drill                  # prove the classifier can go red
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a gate problem
    print("wip_gate: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / ".github" / "merge-policy.yml"

# Both spellings are real: the REST API reports "app/dependabot" for some
# tokens and "dependabot[bot]" for others (the same confusion chain_check.py's
# W12/W13 exist to catch for a different bot). Treat either as dependabot.
DEPENDABOT_AUTHORS = {"dependabot[bot]", "app/dependabot"}
REVERT_PREFIX = "revert/"
PARKED_LABEL = "wip:parked"


class PolicyError(RuntimeError):
    """The policy file is missing, unreadable, or has no usable `wip:` block."""


def load_wip_limit(path: Path = POLICY_PATH) -> int:
    """Read `wip.limit` from `.github/merge-policy.yml`.

    Raises rather than defaulting -- see the module docstring for why a
    missing block must not read as "no limit". Mirrors `lane.py.load_policy`.
    """
    if not path.exists():
        raise PolicyError(f"policy file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"policy file does not parse to a mapping: {path}")
    wip = data.get("wip")
    if not isinstance(wip, dict) or "limit" not in wip:
        raise PolicyError(
            f"policy file has no `wip: {{limit: N}}` block ({path}) -- "
            "refusing to guess a limit and refusing to park anything on a misread"
        )
    limit = wip["limit"]
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise PolicyError(f"wip.limit must be a positive integer, got {limit!r}")
    return limit


def _is_exempt_author(author: str | None) -> bool:
    return author in DEPENDABOT_AUTHORS


def _is_revert(head_ref: str | None) -> bool:
    return bool(head_ref) and head_ref.startswith(REVERT_PREFIX)


def _is_parked(labels: Any) -> bool:
    """`labels` is the normalised list of label name strings `fetch_open_prs`
    produces (see there for why it is flattened out of gh's `{name: ...}`
    objects). Missing/None reads as "not parked", never an error -- a PR with
    no labels field at all is exactly a PR with no `wip:parked` label."""
    return bool(labels) and PARKED_LABEL in labels


def decide(open_prs: list[dict[str, Any]], this_pr: int, limit: int) -> dict[str, Any]:
    """PURE decision function -- the thing the drill breaks.

    `open_prs` items carry: number (int), isDraft (bool), author (the login
    string), headRefName (str), labels (list[str], optional). Returns a dict
    with at least `park` (bool), `count` (int, OTHER PRs counted against the
    limit) and `why` (str).
    """
    this = next((p for p in open_prs if p.get("number") == this_pr), None)
    this_author = (this or {}).get("author")
    this_head = (this or {}).get("headRefName") or ""
    this_labels = (this or {}).get("labels") or []

    # THIS PR'S OWN EXEMPTIONS -- checked first, BEFORE any counting.
    #
    # A PR opened AS A DRAFT (`opened` fires with isDraft: true too) is
    # already parked in every sense that matters -- there is nothing left to
    # park. Checked before the dependabot/revert/label exemptions and before
    # the counting loop: whatever else is true about this PR, there is
    # nothing left to do.
    if bool((this or {}).get("isDraft")):
        return {
            "park": False,
            "count": 0,
            "why": f"PR #{this_pr} is already a draft -- nothing to park.",
        }
    # A PR already carrying `wip:parked` is, for the same reason, already
    # parked -- re-labelling it would be a no-op dressed up as a decision.
    if _is_parked(this_labels):
        return {
            "park": False,
            "count": 0,
            "why": f"PR #{this_pr} is already parked ({PARKED_LABEL}).",
        }
    # An emergency revert or a dependabot PR is never parked, no matter how
    # many other PRs are open.
    if _is_exempt_author(this_author):
        return {
            "park": False,
            "count": 0,
            "why": f"PR #{this_pr} is exempt: its author (`{this_author}`) is "
            "dependabot, which has its own loop and is never parked.",
        }
    if _is_revert(this_head):
        return {
            "park": False,
            "count": 0,
            "why": f"PR #{this_pr} is exempt: its branch (`{this_head}`) is an "
            "emergency revert, and an emergency revert must never be parked.",
        }

    blockers: list[dict[str, Any]] = []
    for p in open_prs:
        if p.get("number") == this_pr:
            continue
        if p.get("isDraft"):
            continue
        if _is_parked(p.get("labels")):
            continue
        if _is_exempt_author(p.get("author")):
            continue
        if _is_revert(p.get("headRefName")):
            continue
        blockers.append(p)

    count = len(blockers)
    names = ", ".join(f"#{p.get('number')}" for p in sorted(blockers, key=lambda p: p.get("number") or 0))

    if count >= limit:
        return {
            "park": True,
            "count": count,
            "why": (
                f"WIP limit is {limit}; {count} other open, non-draft, non-parked, "
                f"non-dependabot, non-revert PR(s) are already ahead of PR #{this_pr}: {names}."
            ),
        }
    return {
        "park": False,
        "count": count,
        "why": (
            f"{count} other open, non-draft, non-parked, non-dependabot, non-revert "
            f"PR(s) counted{f' ({names})' if names else ''}; under the WIP limit of {limit}."
        ),
    }


def fetch_open_prs() -> list[dict[str, Any]]:
    """`gh pr list`, normalised to the flat shape `decide()` expects.

    Never prints a token or any secret -- this only ever reads public PR
    metadata (number, draft state, author login, branch name).
    """
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json",
         "number,isDraft,author,headRefName,labels", "--limit", "100"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {out.stderr.strip()[:300]}")
    raw = json.loads(out.stdout or "[]")
    normalized: list[dict[str, Any]] = []
    for p in raw:
        author_field = p.get("author")
        if isinstance(author_field, dict):
            login = author_field.get("login")
        else:
            login = author_field
        # `gh`'s `labels` field is a list of {name, ...} objects; flatten to
        # bare names so `decide()` (and its drill fixtures) deal in plain
        # strings, not gh's object shape.
        labels = [lbl.get("name") for lbl in (p.get("labels") or []) if isinstance(lbl, dict) and lbl.get("name")]
        normalized.append({
            "number": p.get("number"),
            "isDraft": bool(p.get("isDraft")),
            "author": login,
            "headRefName": p.get("headRefName") or "",
            "labels": labels,
        })
    return normalized


def _drill() -> int:
    """Break the pure classifier on purpose. Needs no gh, no network, no policy file."""
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + ("" if ok else f"   got={got!r} want={want!r}"))

    def pr(number: int, author: str = "alice", draft: bool = False,
           head: str = "feature/x", labels: list[str] | None = None) -> dict[str, Any]:
        return {
            "number": number, "isDraft": draft, "author": author,
            "headRefName": head, "labels": labels or [],
        }

    print("wip_gate.py --drill")

    # 1. NEGATIVE CONTROL FIRST. A repo with zero open PRs must never park.
    v = decide([], this_pr=1, limit=2)
    check("negative control: no open PRs at all -> never park", v["park"], False)
    check("...and counts zero", v["count"], 0)

    # 2. Under the limit -> no park.
    v = decide([pr(1), pr(2)], this_pr=1, limit=2)
    check("one other open PR, limit 2 -> under the limit, no park", v["park"], False)
    check("...counts exactly the one other PR", v["count"], 1)

    # 3. At the limit -> park.
    v = decide([pr(1), pr(2), pr(3)], this_pr=1, limit=2)
    check("two other open PRs, limit 2 -> AT the limit, park", v["park"], True)
    check("...counts both", v["count"], 2)

    # 4. Over the limit -> still park (not just == , >= ).
    v = decide([pr(1), pr(2), pr(3), pr(4)], this_pr=1, limit=2)
    check("three other open PRs, limit 2 -> over the limit, park", v["park"], True)

    # 5. Dependabot PRs don't count.
    v = decide(
        [pr(1), pr(2, author="dependabot[bot]"), pr(3, author="app/dependabot")],
        this_pr=1, limit=2,
    )
    check("two dependabot PRs (either spelling) don't count toward the limit",
          v["park"], False)
    check("...count is zero", v["count"], 0)

    # 6. revert/ branches don't count.
    v = decide(
        [pr(1), pr(2, head="revert/abc"), pr(3, head="revert/def")],
        this_pr=1, limit=2,
    )
    check("two revert/ branches don't count toward the limit", v["park"], False)
    check("...count is zero", v["count"], 0)

    # 7. Drafts don't count.
    v = decide([pr(1), pr(2, draft=True), pr(3, draft=True)], this_pr=1, limit=2)
    check("two draft PRs don't count toward the limit", v["park"], False)
    check("...count is zero", v["count"], 0)

    # 8. THIS PR ITSELF is already a draft -- an `opened` event can fire with
    #    isDraft: true (someone opened it as a draft from the start). Even at
    #    the limit, there is nothing to park: `gh pr ready --undo` on a PR
    #    that is not ready would error and fail the gate step for a PR that
    #    was never a real WIP-limit incident.
    v = decide([pr(1, draft=True), pr(2), pr(3)], this_pr=1, limit=2)
    check("this PR is already a draft, even AT the limit -> never parked",
          v["park"], False)
    check("...the reason names the already-a-draft exemption",
          "already a draft" in v["why"], True)
    check("...and counts zero (checked before counting)", v["count"], 0)

    # 9. THIS PR being dependabot -> never parked, however many others are open.
    v = decide(
        [pr(1, author="dependabot[bot]"), pr(2), pr(3), pr(4), pr(5), pr(6)],
        this_pr=1, limit=2,
    )
    check("this PR is dependabot -> never parked even with 5 others open",
          v["park"], False)
    check("...the reason names the dependabot exemption", "dependabot" in v["why"], True)

    # 10. THIS PR being a revert/ branch -> never parked.
    v = decide(
        [pr(1, head="revert/hotfix"), pr(2), pr(3), pr(4)],
        this_pr=1, limit=2,
    )
    check("this PR is a revert/ branch -> never parked even with others open",
          v["park"], False)
    check("...the reason names the revert exemption", "revert" in v["why"], True)

    # 11. A PR labeled `wip:parked` does not count toward the limit -- same
    #     reasoning as a draft: it is already sitting out of the queue, so
    #     counting it would park a PR on the strength of one that was never
    #     competing for a slot.
    v = decide([pr(1), pr(2, labels=["wip:parked"]), pr(3, labels=["wip:parked"])],
                this_pr=1, limit=2)
    check("two labeled-parked PRs don't count toward the limit", v["park"], False)
    check("...count is zero", v["count"], 0)

    # 12. THIS PR already carries `wip:parked` -- nothing to do. Re-labelling
    #     an already-parked PR is a no-op dressed up as a decision, and it
    #     must never happen even AT the limit.
    v = decide([pr(1, labels=["wip:parked"]), pr(2), pr(3)], this_pr=1, limit=2)
    check("this PR is already labeled wip:parked, even AT the limit -> not parked again",
          v["park"], False)
    check("...the reason names the already-parked exemption",
          "already parked" in v["why"], True)
    check("...and counts zero (checked before counting)", v["count"], 0)

    # 13. The verdict NAMES the blocking PRs -- a park you cannot trace to a
    #     PR number is a park nobody can argue with, which sounds good and is
    #     not (same principle as lane.py's classify()).
    v = decide([pr(1), pr(2), pr(3)], this_pr=1, limit=2)
    check("the verdict names PR #2", "#2" in v["why"], True)
    check("the verdict names PR #3", "#3" in v["why"], True)

    # 14. Mixed exemptions stack: a pile of exempt PRs plus exactly `limit`
    #     real ones still parks on the real ones alone.
    v = decide(
        [pr(1), pr(2), pr(3), pr(4, author="dependabot[bot]"),
         pr(5, draft=True), pr(6, head="revert/x"), pr(7, labels=["wip:parked"])],
        this_pr=1, limit=2,
    )
    check("mixed exemptions: only the 2 real PRs count -> parks", v["park"], True)
    check("...count ignores the 4 exempt PRs", v["count"], 2)

    # 15. A missing/malformed `wip:` block raises, never silently returns a
    #     limit -- the "safe direction" documented at the top of this file.
    import tempfile
    broken = Path(tempfile.mkdtemp(prefix="wip-gate-drill-")) / "merge-policy.yml"
    broken.write_text("version: 1\nlanes: {}\n", encoding="utf-8")
    raised = False
    try:
        load_wip_limit(broken)
    except PolicyError:
        raised = True
    check("a policy with no `wip:` block raises, does not default to a limit",
          raised, True)

    missing = Path(tempfile.mkdtemp(prefix="wip-gate-drill-")) / "does-not-exist.yml"
    raised_missing = False
    try:
        load_wip_limit(missing)
    except PolicyError:
        raised_missing = True
    check("a missing policy file raises, does not default to a limit",
          raised_missing, True)

    bad_limit = Path(tempfile.mkdtemp(prefix="wip-gate-drill-")) / "merge-policy.yml"
    bad_limit.write_text("wip:\n  limit: 0\n", encoding="utf-8")
    raised_bad = False
    try:
        load_wip_limit(bad_limit)
    except PolicyError:
        raised_bad = True
    check("wip.limit of 0 (or less) raises rather than being treated as 'no limit'",
          raised_bad, True)

    # 16. The real policy file (this repo's) must load and produce the
    #     documented owner limit -- proves the file this PR edits actually
    #     parses, not just that the pure function is correct in the abstract.
    try:
        real_limit = load_wip_limit(POLICY_PATH)
        check("this repo's .github/merge-policy.yml has a usable wip.limit",
              isinstance(real_limit, int) and real_limit >= 1, True)
    except PolicyError as exc:
        check(f"this repo's .github/merge-policy.yml has a usable wip.limit ({exc})",
              False, True)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decide whether to park a PR under the WIP limit (owner decision 2026-09-08).",
    )
    ap.add_argument("--pr", type=int, help="the PR number being judged")
    ap.add_argument("--json", metavar="FILE", help="write the decision JSON here")
    ap.add_argument("--drill", action="store_true", help="break the classifier on purpose")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    if args.pr is None:
        print("wip_gate: --pr is required outside --drill", file=sys.stderr)
        return 2

    try:
        limit = load_wip_limit()
    except PolicyError as exc:
        # LOUD REFUSAL, per the module docstring: a misread policy must never
        # park a real PR. Exit 2 is "the gate could not judge", not "no park".
        print(f"wip_gate: {exc}", file=sys.stderr)
        print("wip_gate: refusing to decide anything on a misread policy "
              "-- no PR will be parked by this run", file=sys.stderr)
        return 2

    try:
        open_prs = fetch_open_prs()
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"wip_gate: could not read open PRs: {exc}", file=sys.stderr)
        return 2

    verdict = decide(open_prs, args.pr, limit)
    text = json.dumps(verdict, indent=2, sort_keys=True)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - fail LOUD, never silently green
        print(f"wip_gate crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
