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
        "docs/product/product_design_rules.md — the canonical text of owner rules #29/#30/#31, "
        "which CLAUDE.md names as the authority — could ship unsupervised, while "
        "CLAUDE.md itself was correctly denied. The rule delegated; the delegation was "
        "not protected.",
        repro="check_paths(['docs/product/product_design_rules.md'])   -> [] (allowed)",
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
        drill="an exception nobody anticipated becomes a REFUSE, not a traceback",
        severity="too-permissive",
    ),
    Blocker(
        id="B18",
        met="2026-08-17",
        what="THE CAGE COULD NOT SAY YES, AND 44 DRILLS DID NOT NOTICE. Every drill proved "
        "a refusal; not one proved an ALLOW was reachable. In production it was not: "
        "auto-merge.yml:114 ran `merge_cage.py \"$pr\" --slack $merge_flag` with no "
        "`--baseline`, `check_ratchets(None)` returns `not_checked`, and `not_checked` "
        "blocks — so 100% of PRs were refused for a reason about the WIRING, identically, "
        "regardless of the PR. auto-merge.yml:53 also checked out `ref: main`, which "
        "ground_problem then refuses as the wrong tree for a tree-scoped ratchet, so the "
        "lane had two independent reasons to never say yes.",
        repro="python scripts/merge_cage.py 342 ; python scripts/merge_cage.py 343 ; "
        "python scripts/merge_cage.py 346   # all three: 'no baseline was given, so no "
        "number was compared to anything'",
        rule="A GATE THAT CANNOT SAY YES IS THE SAME DEFECT CLASS AS A GUARD THAT CANNOT "
        "SAY NO, and it is worse in one way: it gets switched off, and then it protects "
        "nothing. Every cage must carry a drill proving its PASS is reachable end to end, "
        "not only that its FAIL fires. The wiring itself is now guarded by "
        "scripts/gate_wiring_check.py rules G1 and G4, so forgetting the baseline is a red "
        "build rather than a silent constant refusal.",
        drill="ALLOW IS REACHABLE (a perfect docs PR with a baseline is allowed end to end)",
        severity="too-strict",
    ),
    Blocker(
        id="B19",
        met="2026-08-17",
        what="REQUIRED_CHECKS WAS NOT BASE-AWARE, so every stacked PR was refused for a "
        "check that structurally cannot fire there. codeql.yml declares "
        "`pull_request: branches: [\"main\"]`, so a PR based on a feature branch never "
        "produces a `CodeQL` run at all — measured on #343/#344/#345/#346, all four "
        "missing it. Then the first fix introduced the mirror-image bug: the guard read "
        "`if base_ref and base_ref != 'main'`, so an UNREADABLE base short-circuited into "
        "the branch that judges against the full list. The permissive guess restored, on "
        "paper, the one check that reads new security alerts from the diff.",
        repro="decide() with a stubbed `pulls/1` returning base.ref = "
        "'feat/every-guard-declares-its-drill', then again with base.ref absent",
        rule="AN UNKNOWN VALUE IS NEVER THE PERMISSIVE VALUE. A check that cannot exist on "
        "this base is named and refused, never silently dropped — dropping it would make "
        "'branch off a feature branch' a way to stop the security question being asked.",
        drill="a PR whose base ref could not be read is refused, not assumed to be main",
        severity="too-permissive",
    ),
    Blocker(
        id="B20",
        met="2026-08-17",
        what="THE MERGE CAPABILITY WAS A DEFAULT, NOT AN ABSENCE. `--merge` existed and was "
        "held off by an unset repo variable (`AUTO_MERGE_ENABLED`) — one click, in a "
        "settings page with no diff and no review, from an agent merging to a branch that "
        "auto-deploys to real users in 2m14s. Separately measured: replaying the cage's own "
        "PATH+SIZE rules over merged PRs #250-338 allows 2 of 60 (3%), and both are "
        "documentation. The lane was never worth the blast radius.",
        repro="python scripts/merge_cage.py 1 --merge   # used to be accepted",
        rule="DELETE THE CAPABILITY, DO NOT DEFAULT IT OFF. `--merge` is gone from this "
        "file; the cage now only advises, and `--advise` writes a PR comment naming which "
        "file is the decision. Anthropic's own most autonomous shipped mode still ends at a "
        "pull request, and its auto-mode classifier names 'running production deploys' as a "
        "blocked action. Merging here IS deploying.",
        drill="`--merge` no longer exists: the cage cannot merge anything at all",
        severity="too-permissive",
    ),
    Blocker(
        id="B21",
        met="2026-08-17",
        what="A RATCHET THAT DOES NOT EXIST YET READ AS A RATCHET THAT BROKE. "
        "`measure_ratchets` answered 'a number, or None', and None meant both 'the command "
        "blew up' and 'the command is not in this tree'. Measured: "
        "`git ls-tree origin/main scripts/drill_registry.py` is EMPTY — the `guards never "
        "watched failing` ratchet is not on main at all. So on every main-based PR it was "
        "unmeasurable on BOTH sides, and an unmeasurable ratchet refuses. Round 1 of the "
        "live run: 27 PRs judged, 0 allowed, and this was one of the two reasons on all 27.",
        repro="python scripts/merge_cage.py 337   # after a correct two-checkout baseline: "
        "'ratchet `guards never watched failing` could not be measured'",
        rule="THREE ANSWERS, NOT TWO: ok / absent / error. `absent` on BOTH sides is NOT "
        "APPLICABLE and is named in the verdict. `error` always refuses. base=ok with "
        "head=absent — a PR deleting a ratchet — refuses. And zero comparisons is never a "
        "pass, or the not-applicable arm becomes a way for the whole cage to evaporate.",
        drill="a ratchet absent from BOTH base and PR is named as not-compared, "
        "and does not block",
        severity="too-strict",
    ),
    Blocker(
        id="B22",
        met="2026-08-17",
        what="THE ONLY WAY TO MEASURE A PR'S OWN NUMBERS WAS TO RUN THE PR'S OWN COPY OF THE "
        "CAGE. `repo_root()` ties ROOT to this file's checkout and every ratchet is "
        "tree-scoped, so pr-advisor.yml had to check out `refs/pull/N/merge` at the "
        "workspace root and run the cage from there — the thing being judged supplying the "
        "judge. Worse, it then measured the baseline by running "
        "`scripts/merge_cage.py --measure` inside a checkout of `main`, where THIS FILE DOES "
        "NOT EXIST (measured: `git ls-tree origin/main scripts/merge_cage.py` is empty), so "
        "the step could only ever fail on a main-based PR.",
        repro="cd _base && python scripts/merge_cage.py --measure   # on a checkout of main: "
        "can't open file 'scripts/merge_cage.py'",
        rule="SEPARATE THE TREE THAT SUPPLIES THE RULES FROM THE TREE THAT SUPPLIES THE "
        "NUMBERS. `--tree DIR` measures DIR while the rules stay in this file's own "
        "checkout. A junk --tree is a usage error and a non-git --tree breaks the cage — "
        "neither may fall back to 'here', because falling back to here is how a ratchet "
        "compares a tree to itself.",
        drill="a --tree that is not a git checkout breaks the cage instead of being measured",
        severity="too-permissive",
    ),
    Blocker(
        id="B23",
        met="2026-08-17",
        what="`--replay` PRINTED AN AGREEMENT RATE IT COULD NOT COMPUTE. It looped "
        "`decide(pr)` with no baseline; `check_ratchets(None)` returns `not_checked` and "
        "`not_checked` blocks, so the answer was 0/N by construction, for every N, forever. "
        "The '13 of 245 (5.3%)' figure quoted from it cannot have come from this code.",
        repro="python scripts/merge_cage.py --replay 60   # used to print 0/60 and call it "
        "a measurement",
        rule="AN INSTRUMENT THAT CANNOT COMPUTE ITS ANSWER MUST REFUSE TO ANSWER. A "
        "convincing wrong number is worse than no number. The real replay needs a checkout "
        "per PR and lives in scripts/cage_replay.py; this flag now says so and exits USAGE.",
        drill="--replay refuses to answer instead of printing an agreement rate "
        "it cannot compute",
        severity="too-permissive",
    ),
    Blocker(
        id="B24",
        met="2026-08-17",
        what="THE INSTRUMENT WAS THERE AND THE QUESTION WAS NOT. B21 probed whether a "
        "ratchet's SCRIPT exists in the tree. `backend/scripts/mypy_ratchet.py` IS on main "
        "— but `--count` was added by this branch, so main answers "
        "`usage: mypy_ratchet.py [-h] [--update]` and exits 2. The file-existence probe read "
        "a MISSING instrument as a BROKEN one, and broken always refuses. Round 2 of the "
        "live run: 16 of 16 merged PRs refused with an argparse usage message as the "
        "explanation, which is a reason no owner can act on.",
        repro="git show origin/main:backend/scripts/mypy_ratchet.py | grep add_argument   "
        "# only --update; then python scripts/cage_replay.py --merged 16",
        rule="PROBE THE CAPABILITY, NOT THE FILE. `needs` is {file, supports}: the file must "
        "exist AND contain the flag the ratchet is about to pass. The probe is static — "
        "deciding a refusal by parsing an argparse usage message is a guess, and both ways "
        "of being wrong here fail safe (a differently-spelled flag reads absent, which "
        "refuses via 'nothing compared'; a flag that appears only in a comment runs, fails, "
        "and refuses).",
        drill="a script that exists but does not carry the flag reads ABSENT, not ERROR",
        severity="too-strict",
    ),
    Blocker(
        id="B25",
        met="2026-08-17",
        what="A FLAG THAT DID NOT EXIST WAS ACCEPTED ANYWAY. argparse abbreviates unique "
        "prefixes by default, so `cage_replay.py --merge` was silently accepted as "
        "`--merged`. Found by that file's own drill on its first run — the drill asserts "
        "the runner has no merge surface, and the surface existed, spelled by accident. "
        "merge_cage.py had the same setting and only escaped because none of its flags "
        "happen to start with `--merge`.",
        repro="python scripts/cage_replay.py 1 --merge   # 'argument --merged: expected one "
        "argument', i.e. ACCEPTED as --merged rather than rejected",
        rule="allow_abbrev=False ON EVERY PARSER IN THE MERGE PATH. On a repo where merging "
        "is deploying, a flag that means something other than what it says is a trapdoor, "
        "not a convenience.",
        drill="an abbreviated flag is rejected, not silently expanded into a different mode",
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
