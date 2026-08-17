#!/usr/bin/env python3
"""review_debt.py — CodeRabbit is an ADVISOR with a debt meter, never an approval.

WHY IT CAN NEVER BE A GATE HERE — three measured reasons
---------------------------------------------------------
1. ITS TICK CANNOT SAY NO AT ANY SETTING. `.coderabbit.yaml` sets
   `fail_commit_status: false`, but flipping that would not help: the documented
   scope of that key is REVIEW ERRORS — it makes the tick red when CodeRabbit
   CRASHES, never when CodeRabbit finds a bug. There is no configuration in
   which the green tick means "clean". Measured live: on PR #336 the `CodeRabbit`
   commit status read success / "Review completed" while 15 threads were open.

2. MAKING IT ABLE TO SAY NO MEANS BUYING AUTO-APPROVAL. The switch is
   `request_changes_workflow: true`, and CodeRabbit's own configuration
   reference describes it primarily as "Automatically approve once CodeRabbit's
   comments are resolved and no pre-merge checks are failing." You cannot buy
   the blocking half without buying a bot that approves production deploys on
   its own assessment of its own comments. Measured on PR #336: 6 of 6 thread
   resolutions were performed by coderabbitai[bot] ITSELF.

3. IT IS SLOWER THAN THE DEPLOY. Measured queued->completed: 6m50s and 5m50s.
   Merge->live here is 2m14s. A CodeRabbit-gated merge is strictly slower than
   shipping, and it sits behind a per-developer hourly review budget.

So: NOTHING WAITS ON IT. This file has no merge path, no approval path, and no
blocking exit code. It counts.

THE FOUR RULES THAT MAKE THE COUNT HONEST
------------------------------------------
A. OUTDATED IS NOT RESOLVED. `isOutdated` only means the code moved under the
   comment, which is the normal state of an UNFIXED finding after any later
   commit. Excluding it would hand out a one-line bypass: push whitespace, every
   finding clears itself. Measured on PR #336: 3 threads were outdated AND still
   unresolved.

B. SELF-CLEARED IS NOT ACCEPTED. A thread whose `resolvedBy.login` is
   coderabbitai[bot] is the reviewer clearing its own finding, not a human
   deciding anything. Counted, and counted SEPARATELY — never folded into the
   number that reads as "somebody looked at this".

C. SILENCE IS NOT A PASS. If a head SHA has no CodeRabbit status and no threads
   once the budget has expired, that SHA is recorded `NOT DELIVERED`. Measured:
   1 of 8 commits on PR #336 received no CodeRabbit status at all. The failure
   mode of this vendor is silence, not red, and silence must never be readable
   as either "clean" or "still thinking". The absence is itself a finding —
   "CodeRabbit did not review 3 of the last 20 head SHAs" is exactly the kind of
   fact this repo has lost ten times by treating no-answer as a pass.

D. AN UNRECOGNISED FINDING IS `unknown`, NOT DROPPED. Severity comes from
   CodeRabbit's own body markers, which will change. A classifier that silently
   discards what it does not recognise reports a smaller number every time the
   vendor renames a label, and the number only ever moves in the reassuring
   direction. The buckets must add up to the total, and the drill asserts it.

WHY GRAPHQL AND NOT REST
------------------------
REST `pulls/{n}/comments` has NO resolution field at all — verified: its keys
are (_links, author_association, body, commit_id, created_at, diff_hunk,
html_url, id, line, ...) and not one of them expresses resolution. Only
`reviewThreads{isResolved}` does. CodeRabbit's own Claude Code plugin reads
GraphQL for the same reason; its public REST API is deprecated, Pro-only, can
take ten minutes, and returns markdown prose.

    python scripts/review_debt.py                 # the number, for a human
    python scripts/review_debt.py --slack         # ...and post it
    python scripts/review_debt.py --drill         # break the meter on purpose
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = os.environ.get("GH_REPO", "Ranjith36963/job360")
REVIEWER = "coderabbitai"
REVIEWER_BOT = "coderabbitai[bot]"
STATUS_CONTEXT = "CodeRabbit"

# A HARD budget. Nothing in this repo waits on a third party whose own status
# page lists the OpenAI and Anthropic APIs as dependencies. When the budget is
# gone the sweep stops and reports what it has; the unread PRs are named as
# unread, never as clean.
DEFAULT_BUDGET_S = 900

# Exit codes. There is deliberately no "findings exist" code: findings are the
# POINT of this file, and a non-zero exit would eventually be wired into
# something that blocks, which is the thing three measurements say not to do.
EXIT_OK = 0
EXIT_METER_BROKE = 1

# CodeRabbit's own body markers, newest spellings first. Order matters: a body
# can carry more than one and the strongest wins.
SEVERITY_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("critical", ("potential issue", "🛑", "critical")),
    ("major", ("refactor suggestion", "🛠️", "major")),
    ("minor", ("nitpick", "🧹", "minor")),
]
SEVERITIES = ("critical", "major", "minor", "unknown")


def classify_severity(body: str) -> str:
    """CodeRabbit's severity, or `unknown` — never nothing.

    Rule D. Dropping what we do not recognise makes the meter read lower every
    time the vendor renames a label, and only ever in the reassuring direction.
    """
    low = (body or "").lower()
    for name, markers in SEVERITY_MARKERS:
        if any(m in low for m in markers):
            return name
    return "unknown"


@dataclass
class PrDebt:
    """One PR's review debt. Every field is a count of something real."""

    number: int
    merged: bool = False
    delivery: str = "delivered"  # "delivered" | "NOT DELIVERED" | "in progress"
    open_by_severity: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SEVERITIES, 0))
    open_total: int = 0
    self_cleared: int = 0  # resolved by coderabbitai[bot] — the bot grading itself
    human_cleared: int = 0
    outdated_open: int = 0  # unresolved AND the code moved under it

    @property
    def has_debt(self) -> bool:
        return self.open_total > 0 or self.delivery == "NOT DELIVERED"


def judge_delivery(threads: list[dict], statuses: list[dict], budget_expired: bool) -> str:
    """Did CodeRabbit actually answer for this head SHA?

    Rule C. The three outcomes are deliberately not two: "in progress" is a real
    state (measured: pending/"Review queued" -> pending/"Review in progress" ->
    success/"Review completed", 6m50s end to end), and collapsing it into either
    neighbour is a lie in one direction or the other. Once the budget is gone,
    "in progress" becomes NOT DELIVERED — a review that never finishes is a
    review that did not happen, and this file will not sit and wait for it.
    """
    ours = [s for s in statuses if s.get("context") == STATUS_CONTEXT]
    if any(s.get("state") == "success" for s in ours):
        return "delivered"
    if threads:
        # It commented. The status is a mirror of the review, not the review.
        return "delivered"
    if ours:  # pending, and only pending
        return "NOT DELIVERED" if budget_expired else "in progress"
    return "NOT DELIVERED" if budget_expired else "in progress"


def judge_threads(threads: list[dict]) -> tuple[dict[str, int], int, int, int, int]:
    """(open by severity, open total, self-cleared, human-cleared, outdated-open).

    Rules A and B live here, and both are the difference between a number that
    means something and a number that flatters.
    """
    by_sev = dict.fromkeys(SEVERITIES, 0)
    open_total = self_cleared = human_cleared = outdated_open = 0
    for t in threads:
        comments = ((t.get("comments") or {}).get("nodes") or [])
        author = ((comments[0].get("author") or {}).get("login") if comments else "") or ""
        if REVIEWER not in author.lower():
            continue  # someone else's thread; this meter is about the reviewer bot
        if t.get("isResolved"):
            who = ((t.get("resolvedBy") or {}).get("login")) or ""
            if who.lower() in (REVIEWER.lower(), REVIEWER_BOT.lower()):
                self_cleared += 1  # rule B: the bot grading its own homework
            else:
                human_cleared += 1
            continue
        # RULE A: still open. `isOutdated` is recorded, never used to excuse.
        open_total += 1
        if t.get("isOutdated"):
            outdated_open += 1
        body = comments[0].get("body", "") if comments else ""
        by_sev[classify_severity(body)] += 1
    return by_sev, open_total, self_cleared, human_cleared, outdated_open


def judge_pr(number: int, merged: bool, threads: list[dict], statuses: list[dict],
             budget_expired: bool) -> PrDebt:
    by_sev, total, self_c, human_c, outdated = judge_threads(threads)
    return PrDebt(
        number=number, merged=merged,
        delivery=judge_delivery(threads, statuses, budget_expired),
        open_by_severity=by_sev, open_total=total,
        self_cleared=self_c, human_cleared=human_c, outdated_open=outdated,
    )


def summarise(debts: list[PrDebt], unread: list[int]) -> str:
    """The one number the owner should watch. It is allowed to be non-zero."""
    open_total = sum(d.open_total for d in debts)
    on_merged = sum(d.open_total for d in debts if d.merged)
    by_sev = {s: sum(d.open_by_severity.get(s, 0) for d in debts) for s in SEVERITIES}
    self_cleared = sum(d.self_cleared for d in debts)
    human_cleared = sum(d.human_cleared for d in debts)
    outdated = sum(d.outdated_open for d in debts)
    not_delivered = [d.number for d in debts if d.delivery == "NOT DELIVERED"]
    in_progress = [d.number for d in debts if d.delivery == "in progress"]

    lines = [
        f"*Review debt — {open_total} open CodeRabbit finding(s) across {len(debts)} PR(s)*",
        "  by severity: " + ", ".join(f"{s} {by_sev[s]}" for s in SEVERITIES),
        f"  {on_merged} of them sit on ALREADY-MERGED PRs — those are not gate failures, "
        f"they are an unread inbox.",
        f"  {outdated} are outdated-but-unresolved (the code moved; the finding did not).",
        f"  cleared: {human_cleared} by a human, {self_cleared} by coderabbitai[bot] itself "
        f"(the reviewer clearing its own findings is not a decision).",
    ]
    if not_delivered:
        lines.append(
            f"  :warning: NO REVIEW DELIVERED for PR(s) {not_delivered}. This is a FINDING, "
            f"not a pass — silence from this vendor must never read as clean.")
    if in_progress:
        lines.append(f"  still reviewing: {in_progress} (inside budget, nothing is waiting).")
    if unread:
        lines.append(
            f"  :hourglass: budget expired before PR(s) {unread} were read — they are "
            f"UNMEASURED, not clean.")
    lines.append("  Nothing merges or blocks on this number. It is a meter.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────


def gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      merged
      headRefOid
      reviewThreads(first:100){
        pageInfo{hasNextPage}
        nodes{
          isResolved isOutdated
          resolvedBy{login}
          comments(first:1){nodes{author{login} body}}
        }
      }
    }
  }
}
"""


def read_pr(number: int, budget_expired: bool) -> PrDebt:
    owner, _, name = REPO.partition("/")
    raw = gh(["api", "graphql", "-f", f"query={_QUERY}", "-f", f"owner={owner}",
              "-f", f"name={name}", "-F", f"number={number}"])
    pr = json.loads(raw)["data"]["repository"]["pullRequest"]
    threads = (pr.get("reviewThreads") or {}).get("nodes") or []
    sha = pr.get("headRefOid") or ""
    statuses: list[dict] = []
    if sha:
        statuses = json.loads(
            gh(["api", f"repos/{REPO}/commits/{sha}/status"])).get("statuses", [])
    return judge_pr(number, bool(pr.get("merged")), threads, statuses, budget_expired)


def sweep(limit: int, budget_s: int) -> tuple[list[PrDebt], list[int]]:
    """Read recent PRs inside a HARD budget. Returns (measured, unread).

    The unread list is returned, not swallowed. A sweep that quietly stopped
    early would report a smaller debt number and look like progress.
    """
    raw = gh(["pr", "list", "--state", "all", "--limit", str(limit), "--json", "number"])
    numbers = [int(p["number"]) for p in json.loads(raw)]
    started = time.monotonic()
    measured: list[PrDebt] = []
    unread: list[int] = []
    for n in numbers:
        if time.monotonic() - started > budget_s:
            unread.append(n)
            continue
        try:
            measured.append(read_pr(n, budget_expired=False))
        except Exception as exc:  # a PR we could not read is UNREAD, never clean
            print(f"  could not read PR #{n} ({type(exc).__name__}: {exc})", file=sys.stderr)
            unread.append(n)
    return measured, unread


def slack(channel: str, text: str) -> bool:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK: no SLACK_BOT_TOKEN — nothing was sent.", file=sys.stderr)
        return False
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "text": text}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"})
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


# ─────────────────────────────────────────────────────────────────────────────
# The drill. Every break below is a shape measured on this repo.
# ─────────────────────────────────────────────────────────────────────────────


def _thread(resolved: bool = False, outdated: bool = False, by: str | None = None,
            body: str = "_⚠️ Potential issue_ something", author: str = "coderabbitai") -> dict:
    return {"isResolved": resolved, "isOutdated": outdated,
            "resolvedBy": ({"login": by} if by else None),
            "comments": {"nodes": [{"author": {"login": author}, "body": body}]}}


def self_drill() -> int:  # noqa: C901 - a drill is a list, not a branch tree
    print("DRILL — making the review meter flatter itself on purpose. It must refuse to.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def ok(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, "" if passed else detail))

    done_status = [{"context": STATUS_CONTEXT, "state": "success"}]
    pending_status = [{"context": STATUS_CONTEXT, "state": "pending"}]

    # RULE A — an OUTDATED unresolved thread still counts. Push whitespace and
    # every finding would clear itself otherwise. Measured: 3 such threads on #336.
    d = judge_pr(1, False, [_thread(outdated=True)], done_status, False)
    ok("an OUTDATED unresolved finding still counts as debt (no whitespace bypass)",
       d.open_total == 1 and d.outdated_open == 1,
       f"open={d.open_total} outdated={d.outdated_open}")

    # RULE B — the bot resolving its own thread is NOT a human accepting it.
    d = judge_pr(1, False, [_thread(resolved=True, by=REVIEWER_BOT)], done_status, False)
    ok("a thread the bot resolved itself is counted as SELF-CLEARED, not accepted",
       d.self_cleared == 1 and d.human_cleared == 0 and d.open_total == 0,
       f"self={d.self_cleared} human={d.human_cleared}")
    d = judge_pr(1, False, [_thread(resolved=True, by="Ranjith36963")], done_status, False)
    ok("a thread a HUMAN resolved is counted separately from the bot's own",
       d.human_cleared == 1 and d.self_cleared == 0,
       f"self={d.self_cleared} human={d.human_cleared}")

    # RULE C — silence. Measured: 1 of 8 commits on PR #336 got no status at all.
    d = judge_pr(1, False, [], [], budget_expired=True)
    ok("no status and no threads past the budget is NOT DELIVERED, never clean",
       d.delivery == "NOT DELIVERED" and d.has_debt, f"delivery={d.delivery}")
    d = judge_pr(1, False, [], pending_status, budget_expired=True)
    ok("a review still 'pending' past the budget is NOT DELIVERED, not 'still thinking'",
       d.delivery == "NOT DELIVERED", f"delivery={d.delivery}")
    d = judge_pr(1, False, [], pending_status, budget_expired=False)
    ok("a review pending INSIDE the budget is 'in progress', not yet a finding",
       d.delivery == "in progress" and not d.has_debt, f"delivery={d.delivery}")

    # RULE D — an unrecognised body is `unknown`, and the buckets must add up.
    d = judge_pr(1, False, [_thread(body="something the vendor renamed"),
                            _thread(body="_🧹 Nitpick (assertive)_ x"),
                            _thread(body="_⚠️ Potential issue_ y")], done_status, False)
    ok("an unrecognised finding becomes `unknown` and is never dropped",
       d.open_by_severity["unknown"] == 1
       and sum(d.open_by_severity.values()) == d.open_total == 3,
       f"buckets={d.open_by_severity} total={d.open_total}")

    # The tick is not the review. A green CodeRabbit status with open threads is
    # the exact live state of PR #336 and must produce DEBT, not reassurance.
    d = judge_pr(336, False, [_thread(), _thread()], done_status, False)
    ok("a GREEN CodeRabbit status with open threads is still debt (the tick is not the review)",
       d.delivery == "delivered" and d.open_total == 2 and d.has_debt,
       f"delivery={d.delivery} open={d.open_total}")

    # Debt on an ALREADY-MERGED PR is the headline number here: 30 of the 46 open
    # findings in this repo sit on PRs that already shipped.
    merged_debt = judge_pr(331, True, [_thread(), _thread()], done_status, False)
    text = summarise([merged_debt], [])
    ok("debt on an already-merged PR is reported as an unread inbox, not a gate failure",
       "ALREADY-MERGED" in text and "2 of them" in text, text[:200])

    # A PR the sweep never got to must be named as UNMEASURED, never as clean.
    text = summarise([], [999])
    ok("a PR the budget ran out on is reported UNMEASURED, not clean",
       "UNMEASURED" in text and "999" in text, text[:200])

    # THE ONE THAT MATTERS MOST — this file must have no way to act on a PR.
    # Asserted through the real command-line surface, NOT by grepping the source:
    # the first version of this case DID grep, and it went red against its own
    # assertion strings while the code was perfectly correct. A self-test that
    # greps is the exact antipattern that left this repo's authorisation check
    # fully green after the only call it guarded was deleted.
    rejected = []
    for flag in ("--merge", "--approve", "--request-changes"):
        try:
            main([flag])
            rejected.append(f"{flag} was ACCEPTED")
        except SystemExit as exc:
            if int(exc.code or 0) == 0:
                rejected.append(f"{flag} exited 0")
    ok("the meter has no merge, approve or request-changes surface at all",
       not rejected, f"this file gained a way to act on a PR: {rejected}")
    ok("findings never produce a non-zero exit (nothing can be wired to block on it)",
       main(["--fixture", json.dumps({"prs": [
           {"number": 1, "merged": False, "threads": [{"isResolved": False, "isOutdated": False,
            "resolvedBy": None, "comments": {"nodes": [{"author": {"login": "coderabbitai"},
                                                        "body": "_⚠️ Potential issue_ x"}]}}],
            "statuses": []}]})]) == EXIT_OK,
       "a run WITH findings exited non-zero — that is a gate, and this may not be one")

    # NEGATIVE CONTROL — a genuinely clean PR must produce silence, or the meter
    # is a wall and its number stops meaning anything.
    d = judge_pr(1, False, [_thread(resolved=True, by="Ranjith36963")], done_status, False)
    ok("NEGATIVE CONTROL (a delivered review with every thread humanly resolved is no debt)",
       not d.has_debt and d.open_total == 0 and d.delivery == "delivered",
       f"clean PR reported debt: open={d.open_total} delivery={d.delivery}")

    # NEGATIVE CONTROL — someone else's review thread is not CodeRabbit debt.
    d = judge_pr(1, False, [_thread(author="Ranjith36963")], done_status, False)
    ok("NEGATIVE CONTROL (a human's own review thread is not counted as CodeRabbit debt)",
       d.open_total == 0, f"counted a non-CodeRabbit thread: open={d.open_total}")

    print()
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if not passed and detail:
            print(f"         {detail[:220]}")
    n = sum(1 for _, p, _ in results if p)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The meter flattered itself. Its number cannot be trusted.")
        return 1
    print("Every way of counting less than the truth was refused; the clean case stayed quiet.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", action="store_true", help="break the meter on purpose")
    ap.add_argument("--limit", type=int, default=30, help="how many recent PRs to read")
    ap.add_argument("--budget-seconds", type=int, default=DEFAULT_BUDGET_S,
                    help="hard wall-clock budget; unread PRs are named, never assumed clean")
    ap.add_argument("--slack", action="store_true", help="post the number to Slack")
    ap.add_argument("--channel", default="needs-your-decision")
    ap.add_argument("--fixture", help="judge a recorded JSON payload instead of the live API")
    args = ap.parse_args(argv)

    if args.drill:
        return self_drill()

    try:
        if args.fixture:
            payload = json.loads(args.fixture)
            debts = [judge_pr(int(p["number"]), bool(p.get("merged")), p.get("threads") or [],
                              p.get("statuses") or [], bool(p.get("budget_expired")))
                     for p in payload.get("prs", [])]
            unread: list[int] = list(payload.get("unread") or [])
        else:
            debts, unread = sweep(args.limit, args.budget_seconds)
    except Exception as exc:
        # The METER broke — distinct from "there is debt". Only this exits non-zero.
        print(f"review_debt: could not measure ({type(exc).__name__}: {exc}). "
              f"No number was produced, and no number is NOT zero.", file=sys.stderr)
        return EXIT_METER_BROKE

    text = summarise(debts, unread)
    print(text)
    for d in sorted(debts, key=lambda x: -x.open_total):
        if d.has_debt:
            print(f"  #{d.number:>4} {'merged' if d.merged else 'open  '} "
                  f"open={d.open_total} self-cleared={d.self_cleared} "
                  f"delivery={d.delivery}")
    if args.slack and not slack(args.channel, text):
        print("review_debt: the number was produced but could not be delivered.",
              file=sys.stderr)
        return EXIT_METER_BROKE
    # ALWAYS zero when a number was produced. Findings are the point.
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
