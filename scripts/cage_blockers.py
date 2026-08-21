#!/usr/bin/env python3
"""THE BLOCKER LOG — every dead end the merge cage hit, and the rule it produced.

THE OWNER'S ASK, VERBATIM
-------------------------
    "Every time you meet the blockers, the dead end, make sure you note that and
     make it stronger. Make it efficient. Cover all the issues and the security
     and quality, and you can know what PATTERN is happening every time so that
     you can overcome those hurdles or blockers."

WHY THIS IS A PYTHON MODULE AND NOT A MARKDOWN CHANGELOG
--------------------------------------------------------
Because a changelog is read by nobody, and this repo has already paid for that
lesson: "an artifact with no notifier dies — 0 of 4 survived." A log that only a
human could read would rot in exactly the way the ten dead guards rotted.

So the log is CODE, and `scripts/merge_cage.py --drill` IMPORTS it and enforces
two things on every single run:

  1. Every blocker below names a `drill` — the exact drill case that goes RED if
     the fix is removed. A blocker with no drill FAILS the drill.
  2. Every named drill must actually exist in the drill's results. Rename or
     delete a drill case and the log notices, because the log is checked against
     the drill, not against itself.

That makes this file the one thing a blocker log usually is not: unignorable.
A future run does not have to choose to read it. It cannot run the cage without
it.

THE PATTERN THESE ALL SHARE (the answer to "what keeps happening")
------------------------------------------------------------------
Every entry below is the same bug wearing a different coat:

    A CHECK REPORTS CONFIDENTLY ABOUT A DIFFERENT QUESTION THAN THE ONE THAT
    MATTERS, AND ITS SELF-TEST AGREES WITH IT BECAUSE THE SELF-TEST ASKS THE
    SAME WRONG QUESTION.

  * B04/B05 answered "how many lines are in this file?" while printing "how many
    type errors are there?".
  * B07 answered "did the bot ever comment?" while printing "are there unresolved
    comments?".
  * B06 answered "does this file exist?" while printing "is the code healthy?".
  * B03 answered "did the process exit non-zero?" while the workflow read it as
    "did the owner need to decide?".

The structural cure, applied in merge_cage.py, is that a verdict line may only be
printed by the evidence object that produced it. A cage that returned early can
say "NOT CHECKED" and nothing else. It is no longer possible to write a sentence
claiming a check that did not run, because the sentence does not exist unless the
check produced it.

THE PROCESS CURE (why rounds 2, 3 and 4 each found dead guards inside the
previous round's fix): a drill must be written by feeding the guard a REAL
failing input end to end, never by asserting on its internals. Every defect
below survives an internal assertion and dies instantly on a real input.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Blocker:
    """One dead end, with the evidence that it was real and the rule it bought."""

    id: str
    met: str  # date the cage hit it
    what: str  # what went wrong, in one sentence
    repro: str  # the exact command or input that caused it
    rule: str  # the rule the blocker produced
    drill: str  # name of the drill case in merge_cage.self_drill() that goes red
    severity: str  # "crash" | "wrong-verdict" | "too-permissive" | "too-strict" | "too-vague"


# ─────────────────────────────────────────────────────────────────────────────
# THE LOG. Newest last. Never delete an entry — a blocker that stops being true
# is history, and history is the only thing that stops round five.
# ─────────────────────────────────────────────────────────────────────────────

BLOCKERS: list[Blocker] = [
    Blocker(
        id="B01",
        met="2026-08-16",
        what="A malformed --baseline killed the cage with a traceback instead of "
        "refusing. json.loads sat outside every try in main().",
        repro="python scripts/merge_cage.py 315 --baseline 'not-json'",
        rule="CRASHING IS WORSE THAN REFUSING. Refusing is a defined safe state; "
        "crashing is undefined. Every input surface is parsed inside the guard, "
        "and main() converts any escaping Exception into a REFUSAL that says why.",
        drill="a malformed --baseline refuses cleanly, naming the flag, instead of crashing",
        severity="crash",
    ),
    Blocker(
        id="B02",
        met="2026-08-16",
        what="Any OSError or timeout from `gh` escaped decide(). Its handler caught "
        "(RuntimeError, JSONDecodeError, KeyError); gh() can raise FileNotFoundError "
        "(gh not on PATH) and subprocess.TimeoutExpired (the API does hang).",
        repro="monkeypatch subprocess.run inside decide(315) to raise "
        "FileNotFoundError(2, 'x', 'gh') -> UNCAUGHT traceback, no verdict",
        rule="A cage may fail, but never silently and never ambiguously. Handlers "
        "catch Exception — not a hand-picked tuple that goes stale the moment a new "
        "call is added. (Not BaseException: that swallows KeyboardInterrupt and "
        "SystemExit, and argparse exits via SystemExit.)",
        drill="gh vanishing mid-judgement refuses instead of crashing",
        severity="crash",
    ),
    Blocker(
        id="B03",
        met="2026-08-16",
        what="Crash and refuse shared exit code 1, so auto-merge.yml's "
        "`*) merge_cage crashed` arm was unreachable dead code — a crash-detector "
        "that could not fire, shipped inside the PR written to end that class. "
        "Worse and unreported at the time: argparse errors exit 2, which the "
        "workflow maps to 'could not reach Slack — stopping', so a bad flag aborted "
        "the whole sweep while blaming Slack.",
        repro="python scripts/merge_cage.py 315 --baseline 'not-json'; echo $?  -> 1\n"
        "python scripts/merge_cage.py 999999; echo $?                        -> 1\n"
        "python scripts/merge_cage.py --badflag; echo $?                     -> 2",
        rule="EXIT CODES ARE A TYPED CHANNEL, NEVER OVERLOADED. 0 allow, 1 refuse, "
        "2 could-not-tell-the-owner, 3 the-cage-broke, 4 usage. Each caller arm can "
        "then be reached by a real input, which is what makes it a guard.",
        drill="crash, refuse, slack-failure and usage have four different exit codes",
        severity="crash",
    ),
    Blocker(
        id="B04",
        met="2026-08-16",
        what="Cage 3 (RATCHET) never compared anything in production — nothing passes "
        "--baseline, so check_ratchets hit `if base_values is None: continue` for every "
        "ratchet. Meanwhile the ALLOW message told the owner 'no quality number went "
        "backwards' on every allow. A sentence asserting a check that never ran.",
        repro="git grep -n -- '--baseline' .github scripts   # only the argparse definition",
        rule="A VERDICT LINE MAY ONLY BE PRINTED BY THE EVIDENCE THAT PRODUCED IT. "
        "Each cage returns a Verdict carrying its own claim; plain_english prints the "
        "claims of cages that PASSED and nothing else. A cage that did not run is "
        "NOT CHECKED, and NOT CHECKED is a refusal — an unmeasured ratchet is not a "
        "passing one.",
        drill="an un-baselined ratchet is NOT CHECKED and refuses, and no allow text claims it",
        severity="too-permissive",
    ),
    Blocker(
        id="B05",
        met="2026-08-16",
        what="The mypy ratchet reported 4 errors against a true 0. It counted non-blank "
        "lines of backend/mypy_baseline.txt — a file of four comments whose last line "
        "reads '# total errors: 0'. The real consumer skips '#' lines and sums the "
        "leading count column, because one line can carry N errors.",
        repro="python scripts/merge_cage.py --measure   # {'mypy errors': 4, ...}\n"
        "python backend/scripts/mypy_ratchet.py --count  # 0",
        rule="A RATCHET MUST NEVER RE-IMPLEMENT ITS CONSUMER'S PARSER. It shells out to "
        "the script CI already runs and reads its number. One file, one function that "
        "turns it into an integer. (Reading the '# total errors: N' comment was "
        "considered and rejected: that is a second regex over prose nobody is obliged "
        "to keep.)",
        drill="the cage's mypy number equals the number its consumer reports",
        severity="wrong-verdict",
    ),
    Blocker(
        id="B06",
        met="2026-08-16",
        what="Run from outside the repo the ratchets failed OPEN: mypy read 0 (the best "
        "possible value) because the inline command ended `if p.exists() else 0`, and "
        "the drill ratchet degraded to its own error sentinel 999, which no real value "
        "can ever exceed. Judging a real PR from that cwd produced zero ratchet "
        "complaints — the cage could not tell it was measuring an empty tree.",
        repro="cd C:/Users/Ranjith && python <worktree>/scripts/merge_cage.py --measure\n"
        "# {'mypy errors': 0, 'guards never watched failing': 999, ...}, exit 0",
        rule="UNKNOWN IS NEVER A VALUE, AND ASSERT THE GROUND FIRST. No `else 0`, no "
        "sentinel: a ratchet either prints a number it measured or exits non-zero, and "
        "unmeasurable REFUSES. Before judging anything the cage resolves the git root "
        "from its own __file__ and refuses if it is not standing in the repo it judges.",
        drill="the cage refuses when it is not standing in the repo it judges",
        severity="too-permissive",
    ),
    Blocker(
        id="B07",
        met="2026-08-16",
        what="check_review printed 'N unresolved review comment(s)' from REST "
        "pulls/{pr}/comments, an endpoint with no resolution field at all. It was wrong "
        "in BOTH directions: PR #336 showed 4 'unresolved' when GraphQL says all four "
        "threads are isResolved=true, and PR #258 showed 4 when GraphQL shows 5 "
        "unresolved (it never saw the github-advanced-security thread). Because "
        "resolving in the UI does not delete the REST comment, the block was "
        "UNCLEARABLE — no action the owner could take would ever satisfy it.",
        repro="gh api graphql -f query='{repository(owner:\"Ranjith36963\",name:\"job360\")"
        "{pullRequest(number:336){reviewThreads(first:50){nodes{isResolved}}}}}'"
        "   # all four true, while the cage said 4 unresolved",
        rule="IF A CHECK'S WORDING CLAIMS A STATE, THE API IT READS MUST BE ABLE TO "
        "EXPRESS THAT STATE. Resolution lives only in GraphQL reviewThreads, so that is "
        "what the cage reads, counting isResolved == false. isOutdated is deliberately "
        "NOT excluded: outdated only means the code moved under the comment, and "
        "excluding it would hand out a one-line bypass — push a whitespace commit and "
        "every open finding clears itself.",
        drill="a resolved review thread does not block; an unresolved one does",
        severity="wrong-verdict",
    ),
    Blocker(
        id="B08",
        met="2026-08-16",
        what="'`x.py` — not on the allow list; nobody has decided this path is safe' "
        "tells a human nothing at 7am. Neither does 'Chain wires did not run', whose "
        "real cause was 'this PR's base predates the check — rebase it'. A verdict that "
        "is technically defensible and operationally useless is how a gate gets "
        "switched off.",
        repro="python scripts/merge_cage.py 326   # three bullets, no action in any of them",
        rule="EVERY REFUSAL ENDS IN AN IMPERATIVE THE READER CAN EXECUTE, or says "
        "plainly that nothing will and the owner must merge it by hand. Enforced "
        "mechanically: every reason string must contain a FIX: clause.",
        drill="every refusal reason says what would make it mergeable",
        severity="too-vague",
    ),
    Blocker(
        id="B09",
        met="2026-08-16",
        what="Two of the twelve ALLOW entries were unreachable — "
        "backend/src/services/profile/storage.py and github_enricher.py sit under the "
        "DENY glob backend/src/services/profile/*, and DENY beats ALLOW. 17% of the "
        "allow list was dead configuration that reads as permission. Nothing noticed; "
        "the drill was 7/7 green with them dead.",
        repro="check_paths(['backend/src/services/profile/storage.py'])"
        "  -> \"changes what is extracted from a user's CV\"",
        rule="DEAD CONFIGURATION IS A HARD ERROR, NOT A SILENT NO-OP. Before judging, "
        "the cage asserts no LITERAL allow entry is fully shadowed by a deny pattern, "
        "and refuses to run at all if one is. Only literals: `*.md` overlapping the "
        "CLAUDE.md deny is the documented precedence rule working, not a bug.",
        drill="an allow entry shadowed by a deny pattern stops the cage dead",
        severity="too-strict",
    ),
    Blocker(
        id="B10",
        met="2026-08-16",
        what="The allow list matched only frontend/src/**/__tests__/*, missing the 24 "
        "co-located *.test.tsx / *.test.ts files — 55% of this repo's frontend unit "
        "tests. A pure unit test, the safest change that exists, was refused as an "
        "unknown path. Real PRs hit it: #324 and #331 both carry "
        "PreferencesForm.*.test.tsx refusals.",
        repro="check_paths(['frontend/src/components/profile/PreferencesForm.test.tsx'])"
        "  -> 'not on the allow list'",
        rule="THE ALLOW LIST MUST MATCH THE REPO AS IT IS, NOT AS IT WAS IMAGINED. Test "
        "files are allowed by what they ARE (*.test.tsx, *.spec.ts) rather than by where "
        "someone hoped they would live.",
        drill="NEGATIVE CONTROL (a co-located frontend unit test passes the path cage)",
        severity="too-strict",
    ),
    Blocker(
        id="B11",
        met="2026-08-16",
        what="frontend/package.json and package-lock.json were on the ALLOW list and the "
        "cage has no semver awareness, so it would have allowed PR #291 — a MAJOR bump, "
        "motion 12.42.2 -> 13.0.0 — that dependabot-auto.yml on main already routes to a "
        "human. The new cage was strictly weaker than the gate it sits above. Package "
        "manifests also carry postinstall scripts, so allowing them is allowing "
        "arbitrary code on the build host.",
        repro="gh pr view 291 --json title   # '...in the npm-major group...'\n"
        "grep -n major .github/workflows/dependabot-auto.yml   # majors go to a human",
        rule="THE CAGE MAY NEVER BE MORE PERMISSIVE THAN A GATE ALREADY ON MAIN. "
        "Dependency manifests are denied and the refusal names the gate that owns that "
        "decision, so the two cannot disagree.",
        drill="a dependency manifest is refused and points at dependabot-auto",
        severity="too-permissive",
    ),
    Blocker(
        id="B12",
        met="2026-08-16",
        what="`docs/*` and `*.md` were allowed wholesale, so "
        "docs/product_design_rules.md — the canonical text of owner rules #29/#30/#31, "
        "which CLAUDE.md names as the authority — could ship unsupervised, while "
        "CLAUDE.md itself was correctly denied. The rule delegated; the delegation was "
        "not protected.",
        repro="check_paths(['docs/product_design_rules.md'])   -> [] (allowed)",
        rule="IF A DENIED FILE DELEGATES ITS AUTHORITY TO ANOTHER FILE, THAT FILE "
        "INHERITS THE DENIAL. The documents that ARE decisions are denied by name.",
        drill="a document that is itself a product decision is refused",
        severity="too-permissive",
    ),
    Blocker(
        id="B13",
        met="2026-08-16",
        what="`pat.replace('*','**')` and `p.replace('**','*')` in check_paths were "
        "no-ops: fnmatch has no glob-star, `*` already crosses `/`. The code read like "
        "glob-star support and was not, so `frontend/**/x` looked meaningful and was "
        "not — a trap for whoever writes the next pattern.",
        repro="fnmatch.translate('scripts/*') == fnmatch.translate('scripts/**')  # True",
        rule="A PATTERN LANGUAGE MEANS WHAT IT LOOKS LIKE. The matcher now implements "
        "real glob semantics — `*` stops at a path separator, `**` crosses it, a "
        "pattern with no slash matches the basename — and every recursive deny was "
        "rewritten to `**` so the migration could not loosen anything.",
        drill="a nested file under a recursive deny is still refused, and for the right reason",
        severity="too-vague",
    ),
    Blocker(
        id="B14",
        met="2026-08-16",
        what="The files and check-runs calls took page 1 only, no pagination. Latent "
        "today because MAX_CHANGED_FILES is 40, but raise that cap above 100 and the "
        "101st file — say auth.py — becomes invisible to the path cage while the cage "
        "reports a confident verdict about the 100 it saw.",
        repro="gh api repos/<repo>/pulls/<n>/files?per_page=100   # page 1, silently",
        rule="A FULL PAGE IS NOT A COMPLETE ANSWER. When a listing comes back at the "
        "page limit, the cage refuses rather than judging what it happened to see.",
        drill="a full page of results refuses instead of judging a partial list",
        severity="too-permissive",
    ),
    Blocker(
        id="B15",
        met="2026-08-16",
        what="'DRILL RESULT: 7/7 passed' reads as 'the cage works'. It meant 'the path "
        "list works': six of the seven cases called check_paths and one called is_worse. "
        "check_proof, check_review, check_ratchets, measure_ratchets, slack and "
        "plain_english were never drilled — and every defect in this log lived in that "
        "undrilled 60%.",
        repro="python scripts/merge_cage.py --drill   # 7/7, with four live defects",
        rule="A DRILL MUST PRINT ITS COVERAGE, NOT JUST ITS SCORE. It enumerates every "
        "function the decision path calls and FAILS if any is undrilled, so the number "
        "at the bottom can never again mean less than it looks like.",
        drill="COVERAGE (every function on the decision path is drilled)",
        severity="too-permissive",
    ),
    Blocker(
        id="B16",
        met="2026-08-16",
        what="backend/src/api/routes/profile.py was on the ALLOW list, so PR #315 — "
        "+45 lines in a per-user API route — would have merged on the strength of its "
        "FILENAME. check_paths never sees a diff. The identical PR with "
        "Depends(require_user) deleted gets the identical verdict, and rules #12/#25 "
        "exist because a review found three real IDORs in exactly these routes.",
        repro="gh api repos/<repo>/pulls/315/files -q '.[].filename'"
        "   # backend/src/api/routes/profile.py, allowed on name alone",
        rule="A PATH ALLOW-LIST CANNOT DISTINGUISH A LOGGING FIX FROM AN AUTH REMOVAL, "
        "SO IT MUST NOT TRY. Per-user API routes are denied. (A grep-level auth "
        "assertion over the merged content was proposed and REJECTED: it false-positives "
        "on client_log.py, which is deliberately public, and cannot see router-level "
        "dependencies. An auth invariant belongs in a test that walks app.routes.)",
        drill="a per-user API route is refused on its path",
        severity="too-permissive",
    ),
    Blocker(
        id="B17",
        met="2026-08-16",
        what="THE DRILL FOR THE FIXES WAS ITSELF WRONG — round five, inside the round-four "
        "fix, exactly as predicted. Mutation-testing the new rules (break one on purpose, "
        "watch the drill) found three of them green against a real break: the nested-deny "
        "case COUNTED refusals instead of reading them, so narrowing `scripts/**` back to "
        "`scripts/*` still gave four refusals — the file merely fell through to the generic "
        "'no rule covers this path'; the coverage case was defeated by a mutation that "
        "removed one function reference out of several; and deleting the catch-all around "
        "main() left every crash case green, because they are all caught one layer lower.",
        repro="python scratch_mutate.py   # 20 deliberate breaks; 3 did not turn the drill red",
        rule="A DRILL IS NOT PROVEN BY PASSING. It is proven by breaking the thing it "
        "guards and watching it go red — every rule in this file was mutation-tested, and "
        "three had to be rewritten before they caught anything. Corollary now enforced: "
        "assert on the REASON a check gave, never on the COUNT of reasons; a refusal for "
        "the wrong reason is a guard that has already stopped knowing what it protects.",
        # B17 RECORDS THREE DEFECTS AND USED TO NAME A DRILL COVERING ONE.
        # The named drill guarded only the deleted catch-all, so reverting the
        # nested-deny case to counting refusals -- the defect the `rule` field
        # above actually states -- left this entry green. Every other entry here
        # names the drill that DIES WITH ITS FIX; this one did not, which is the
        # same "guard that cannot go red" this whole file exists to record.
        # Now names the case that goes red for the reason-versus-count rule.
        # (CodeRabbit, PR #336.)
        drill="a nested file under a recursive deny is still refused, and for the right reason",
        severity="too-permissive",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────


def undrilled() -> list[Blocker]:
    """Blockers that named no drill. Each one is a rule that could silently die."""
    return [b for b in BLOCKERS if not b.drill.strip()]


def render() -> str:
    """The log, for a human. `python scripts/cage_blockers.py`."""
    out = [f"THE BLOCKER LOG — {len(BLOCKERS)} dead ends the cage hit, and what each bought", "=" * 78]
    for b in BLOCKERS:
        out += [
            "",
            f"{b.id}  [{b.severity}]  met {b.met}",
            f"  WHAT   {b.what}",
            "  REPRO  " + b.repro.replace("\n", "\n         "),
            f"  RULE   {b.rule}",
            f"  DRILL  {b.drill or '*** NONE — this rule can die unnoticed ***'}",
        ]
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover - human entrance
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(render())
    raise SystemExit(1 if undrilled() else 0)
