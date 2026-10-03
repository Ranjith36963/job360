# Failure catalog — every bug class, its diagnosis, its antidote
<!-- doc: LIVING -->

**Rule (owner, 2026-10-02):** a failure is not closed when the instance is fixed.
It is closed when its **class** is named here and a **machine guard** catches the
whole class, so "if it comes in tomorrow, we know". Fixing instances one by one
just produces more bugs.

## How to add an entry (every time something breaks)

1. **Measure.** The real output, the exit code, the `file:line`. Not "looks fixed".
2. **Diagnose.** Name the class: the symptom you saw and the root cause underneath it.
3. **Sweep.** Grep for other instances of the same class. Fix them too.
4. **Antidote.** A guard (lint, test, hook, drill) that fails on the *class*.
   Prove it against the real buggy commit (red) and the fix (green).
5. **Register.** Declare the guard in `scripts/drill_registry.py` with a drill and
   a negative control, and wire it into a workflow. An undeclared guard is a red build.
6. **Record** it below. No guard yet? Write `OWED` and why. That debt stays visible.

## Catalog

| ID | Class | Symptom | Root cause | Antidote (guard) | First seen |
|---|---|---|---|---|---|
| FC-001 | Unrouted job result | Workflow ends **green** having done nothing, with no comment or relabel. | `case`/`if-elif` over `needs.<job>.result` handles only `success`/`failure`. A `timeout-minutes` hit is `cancelled`, so it falls through. | **Partial.** `scripts/check_job_result_routing.py`, run in `ci.yml`, checks bash `run:` steps against a fixture corpus. It catches buggy #679 `c40c58b` (2 findings) and passes the fix `18e890c`. **OWED** (3 `owed_*` fixtures): a `case` inside `$( )`, `else if` nesting, and non-bash steps (`actions/github-script`). | PR #679, 2026-10-02 (reviewer re-found it on 4 commits) |
| FC-002 | Failure path tells no human | A step fails red, but the issue/PR stays in its old state and nobody is told. A red Actions run notifies no one when the bot token started it. | The error path does `exit 1` (or `::warning::`) before or instead of commenting and relabelling. | **Partial:** `scripts/check_alert_paths.py` covers the Slack alert paths. **OWED:** a lint that every `exit 1` in a label-routing job is preceded by a relabel to `triage:needs-human`. Fixed by hand in #679 (triage dispatch, park path, CI approval, gate crash). | PR #679, 2026-10-02 (CodeRabbit, 4 findings) |
| FC-003 | Stray artifact blocks the gate | The stop quality gate blocks 3/3 on a file this session never touched, and tells it to `git add -A` the file into an unrelated PR. | The gate judges the **tree**, not authorship. A `TEMPORARY … delete after the run` spec was never deleted (5 days old). | `.claude/hooks/stop-quality-gate.sh`: untracked files untouched ≥12h are named as **STRAY** with their own cure (move it out of the tree, never stage it). Drilled by hand with a planted 72h-old file (flagged) and a fresh file (not flagged). | 2026-10-02 |
| FC-004 | One-off test file auto-joins the real suite | A temporary QA spec in `frontend/tests/e2e/` would be run by `npm run test:e2e` and CI (6 tests that assert nothing about the product). It writes to a hardcoded `C:/Users/<name>/…` path, which leaks the username into a public repo and drops junk files on Linux CI. | `playwright.config.ts` has only `testDir`, with no `testIgnore`, so a leading `_` does not exclude a file. The spec hardcoded its own `OUT_DIR`. | **OWED:** `testIgnore: ["**/_*.spec.ts"]` in `playwright.config.ts`, plus a lint rejecting absolute user-home paths in `frontend/tests/`. Instance handled 2026-10-02: reviewed (verdict: never commit), then kept out of the tree. | 2026-10-02 (`_phone_shots.spec.ts` review) |
| FC-005 | Guard parses lines, the language has statements | A lint keeps getting new findings: each review round finds another spelling it misses or wrongly flags. | The FC-001 guard read physical **lines** and patched one shape per review round. After 4 rounds, a local adversarial run still found **15/21 real bugs missed and 7/12 correct scripts flagged**. Patching shapes never converges. | (1) Read the language the way it runs **first**: `_statements()` handles `${{ }}` in the script, `\` joins, heredocs, quotes/escapes/comments, and `then`/`do`/`else` peeling. (2) **The fixtures are the spec:** `scripts/fixtures/job_result_routing/` (42 files: `bad_*` must flag, `good_*` must pass, `owed_*` = known gaps). The drill runs them all; every future finding becomes a fixture first. **Rule:** on a guard's 2nd shape-specific finding, stop patching. Fix how it reads, and turn the findings into a permanent corpus. | PR #709, 2026-10-02 |
| FC-007 | Workflow consumes a ref GitHub may not have built | Auto-merge went red 7 times in 13h (`couldn't find remote ref refs/pull/713/merge`, exit 128) while nothing was wrong with the lane. | `pick` never asked whether a PR merges; `arm` checks out `refs/pull/N/merge`, which GitHub builds only for a cleanly-merging PR. #713 conflicted from birth (opened 3 min after #709's squash); #712's first head conflicted in `docs/GENERATED.md` (proved with `git merge-tree`). | `auto-merge.yml` `pick` skips any PR whose `mergeable` is not `MERGEABLE`, and says so in the step summary. Sweep found the same class in `pr-advisor.yml` (run 36998888010: drill PR #694 closed at 11:09 before the queued run reached checkout at 11:13): its `precheck` now probes `state`+`mergeable` (retrying while UNKNOWN) and the two jobs that check out the ref are skipped, grey, on a definite no. **OWED:** a lint that every `refs/pull/*/merge` checkout sits behind such a probe. | 2026-10-03, runs 37065192803…37115071664, 36998888010 |

| FC-006 | Pre-execution gate on an unbounded command set | The Skill Law gate (`.claude/hooks/skill_law.py`) passed review on Edit/Write, but `reviewer-bugs` found a new Bash shape on each of 3 rounds: a heredoc into a non-zone file, a repro word anywhere (`sed -i … && curl`), and `rm`. Each one changed a guarded file while a law was owed. | The gate guessed **before** the command ran, by parsing it. But the set of commands that write a file is unbounded (`rm`, `python -c os.remove`, `truncate`, …), while the set of guarded files is finite. Same family as FC-005: patching shapes never converges. | Judge the **finite side, after the fact.** While a law is owed, PreToolUse snapshots the guarded files (mtime+size, no git, ~4 ms) and PostToolUse **blocks with a violation** if any file was created, changed or deleted, whatever command did it. The per-segment parser stays as a cheap first line. Corpus: every reviewer command is a test in `backend/tests/test_skill_law.py` (47 cases, with negative controls). Drill: `python .claude/hooks/skill_law.py --drill`. Mutation check: 9/9 mutants killed. | PR #712, 2026-10-02 (reviewer-bugs, 3 rounds) |

## Earlier classes (before this catalog)

The ten "guards that could not fire" behind `scripts/drill_registry.py` (see its
docstring) are the founding entries of this idea: a guard is trusted only after
someone **watched it go red**. Its antidote is the registry itself, plus each
guard's negative control.
