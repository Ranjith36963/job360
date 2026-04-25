<!--
Generator commit template — v1.

Every batch commit produced by the generator worktree MUST be accompanied
by a fully-filled copy of this template (saved at the branch root as
.claude/generator-commit.md).

Required sections are checked structurally by scripts/review_batch.sh
and machine-greppable by `make verify-batch`. Do NOT delete sections,
even if empty — write "none" or "n/a" instead.

Why this template exists:
  Step-1.5 shipped a sentinel that the reviewer round-tripped against
  a different scope (--ignore=tests/test_main.py) than the generator
  used. Round-trip diff: 1086p/0f/4s vs 1087p/0f/17s. Forcing the
  generator to declare scope up-front makes that drift impossible.
-->

# Generator Commit — <batch name / SHA>

## Files changed

<!-- One bullet per file with a 3–6 word note on the nature of the change.
     Group by directory if >10 files. Cite paths from repo root. -->

- `path/to/file.py` — short purpose
- `path/to/other.ts` — short purpose

## Why

<!-- 2–4 sentences. State the goal of this commit and the linked
     blocker / plan section. Avoid restating the diff — the diff is the
     "what". This section is the "why". -->

## Tests added

<!-- One bullet per new or significantly-modified test. Cite the file
     and the test name. If a test is parametrized, name the param scope.
     Write "none" if this commit is docs / config only. -->

- `backend/tests/test_X.py::test_Y` — what it asserts

## Verification command

<!-- The EXACT command the generator ran locally before claiming green.
     Copy-paste-runnable. Single line preferred; multi-line OK with `&&`.
     If multiple gates were run, list each on its own line. -->

```
cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly
```

## Verification scope

<!-- Explicit declaration of any narrowing applied to the verification
     command above. The reviewer will fail loud if their scope diverges.

     Required fields:
       - --ignore flags: list every excluded path, or "none"
       - --select flags: list every restricted path, or "none"
       - env vars set: e.g. ARQ_TEST_MODE=1, JOB360_DB=:memory:
       - skipped suites: list any test file or marker NOT touched by
         the verification command, with a one-line reason
-->

- `--ignore` flags: `tests/test_main.py` (live-HTTP leak — pre-existing)
- `--select` flags: none
- env vars set: `ARQ_TEST_MODE=1`
- skipped suites: none beyond the --ignore above

## Sentinel claim

<!-- The exact pass/fail/skip counts the generator observed under the
     scope above. Format: NNNNp / Nf / Ns. The reviewer will compare
     this number against their own re-run.

     If the count differs by ANY amount, the reviewer marks
     sentinel_claim_verified: stale and BLOCKS the merge.
-->

- Counts: `1086p / 0f / 4s`
- Wall-clock: `~210s`
- Commit verified: `<git rev-parse HEAD>`

## Known regressions or deferrals

<!-- Honesty section. List anything intentionally not closed in this
     commit but flagged for follow-up. Each entry: one sentence + the
     follow-up batch / issue / plan section it should land in.

     Write "none" if the batch is fully closed. -->

- none
