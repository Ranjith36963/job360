# REVIEW.md — how a change is reviewed before a human merges it

<!-- doc: LIVING -->

`main` is production. This file says what a review pass looks for, what counts
as blocking, and what to leave alone. Reviewers — human or agent
(`.claude/agents/reviewer-bugs.md`, `reviewer-conventions.md`, `verifier.md`) —
follow it. Adapted 2026-09-02 from
https://claude.com/blog/the-ai-native-sdlc-playbook ("Review the diff, not the
PR description"; "Important vs Nit"; "human approval stays separate").

## The three passes

Run each as its own read of the diff. One pass, one question.

| Pass | Question | Who |
|---|---|---|
| **Bugs** | Does this code do something wrong? Logic, edge cases, async/DB, silent failures, IDOR. | `reviewer-bugs` |
| **Security & rules** | Does it break a numbered hard rule (`.claude/skills/hard-rules/SKILL.md`), leak data across users, take unbounded input, or import a heavy dep at module top? | `reviewer-conventions` |
| **Compliance with spec** | Does the diff do what `docs/plans/<slice>/spec.md` says — every R-number covered, nothing extra shipped silently? Is `plan.md` "Diff vs plan" filled in and honest? | integrator (the session that wrote it, reading as a stranger) |

Then the **verifier** walks it in a browser (`.claude/agents/verifier.md`). Its
table is pasted into the PR. A review without the verifier's table is a code
read, not a review.

## Important vs Nit

Every finding is one or the other. No third bucket.

**Important** — blocks merge. One of:
- Broken behaviour a user can hit (the verifier saw it, or a test proves it).
- Data leak or cross-user access (rules #12/#25) — including "probably fine".
- A hard-rule breach (schema, catalog, scoring, auth, notifications, extraction).
- Unbounded input reaching the DB or an LLM (no max length, no count cap).
- A test that was weakened to pass ("fix code, not the test").
- Spec requirement not met, or behaviour shipped that the spec did not ask for and
  `plan.md` did not declare.

**Nit** — never blocks. Naming, wording, ordering, a comment, a style choice.
**Max 5 nits per review.** Past five, the reviewer is tidying, not reviewing.
Nits are fixed only if the author is already editing that line.

## What to skip

- Generated files: `frontend/src/lib/api-types.ts`, `frontend/openapi.json`. The
  gate's `check:types-drift` owns them.
- Anything CI already enforces: ruff, mypy ratchet, eslint, type-check, the
  drill registry, the offline test suite. Don't re-lint by hand.
- Formatting. Prettier and ruff format won.
- Reworking the design. If the design is wrong, the finding is "spec C-n is wrong",
  raised against `spec.md`, not against the diff.

## Evidence rules

- Every Important finding names `file:line` and says what input breaks it.
- "I think" is a Nit at most. A Bug needs a reproduction: an input and what happens.
- The reviewer reads the **diff** and the code it calls — not the PR description.
  The description is the author's claim; the diff is the fact.
- If a fix changes a frozen test, that is itself an Important finding.

## The human gate

Agents review. Agents never merge. The owner merges after:
1. CI green (the full suite, on Linux — not the Windows targeted gate).
2. The verifier's table in the PR with screenshots.
3. Zero open Important findings.
4. `plan.md` "Diff vs plan" filled in.

After merge: check prod is on the new commit
(`railway deployment list --service backend --json` → `meta.commitHash`), drive
the real flow once on job360.uk, check Sentry for the next hour. Every prod bug
found becomes a test before it becomes a fix.
