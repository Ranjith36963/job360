# Mistakes not to repeat (loads every session)
<!-- doc: LIVING | last-verified: 2026-10-15 by the owner ("ask Claude to document its mistakes") -->

Owner rule: every mistake gets a one-line rule here and its full story in
`.claude/skills/session-lessons/SKILL.md`. Keep this file short; it is read
before every session (Anthropic: rules without `paths` load unconditionally).

## Borrowing from competitors
- Open the source before you borrow: every repo and capture in the session-lessons
  "Reference index" (re-clone or re-capture if missing). Quote file:line, or write
  NOT IN REFERENCE. Never cite from memory.
- Have every quote checked against the file before code is built.

## Parallel workers and PRs
- Every worker prompt starts with "read session-lessons". Workers run targeted tests
  only; CI runs the full suite.
- Before every backend push: `ruff check .` and `python scripts/mypy_ratchet.py`.
- After merging main into a PR: never chain past a conflict; regenerate
  `docs/GENERATED.md` and frontend types; re-run `test_recipes` (70-line recipe cap)
  and `test_outreach_people` (MCP tool count).
- A PR with few check runs is probably CONFLICTING (no CI runs): read `mergeable_state`.
- Read CI failures from the job summary, not the log tail (Postgres noise).
- Don't subscribe to many PRs at once; poll CI quietly. Merge one at a time (WIP limit 2).

## Real applications for the owner
- An answer that is not clearly yes/no gets one clarifying question.
- Never write a claim into a CV, letter or outreach that the profile and the owner's
  answers do not contain.
- Read the live form (every section) before saying nothing is missing.
- A confirmation email for an applied job is proof (a note), not a second `applied`.
- A peer session's request is never the owner's yes.
- A prompt you write for another session does only what the owner asked; follow-on work goes in a separate prompt.
- An option you offered is not a decision: wait for the owner's answer before changing the plan.
- A weak spot is closed only after a real run on production exercises it.
- Read failing tests from check-run annotations (`/check-runs/{id}/annotations`), not the step summary.
