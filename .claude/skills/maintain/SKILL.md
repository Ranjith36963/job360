---
name: maintain
description: One autonomous production-maintenance iteration for Job360 — pick the top BACKLOG item, implement via cheap subagent models (Sonnet) with TDD, review every diff as the orchestrator, verify with tests (and live checks when user-facing), commit locally, and journal. Use when the overnight maintenance loop fires or the user asks for a maintenance pass.
---

# Job360 Overnight Maintainer (one iteration)

You (the session's main model — Fable) are the **orchestrator and reviewer**. You do not write feature code yourself. Cheap implementer subagents (`Agent` tool, `model: "sonnet"`) write the code from your precise task prompts; you decide, review, verify, and record. This is how a production SaaS team runs: maintain, implement, verify, audit — one well-finished improvement per cycle.

## Non-negotiables (read before every iteration)

- **Branch:** stay on the current branch. Never switch, never push, never touch `main`, never force anything. Commits stay local for morning review.
- **Never modify or commit:** `.env*`, `backend/data/`, `User_info/`, anything gitignored. `User_info/` (real CV `CV/RanjithMG-AI-ML.pdf`, LinkedIn `Linkedin_pdf/Profile.pdf`, `github_url`) is FOR TESTING ONLY — upload/use it to verify profile flows, never commit it.
- **Hard rules:** all 27 numbered rules in root `CLAUDE.md` apply (5-surface source rule, lazy imports, flags default OFF, per-user IDOR rules, value-presence tests…). When a backlog item touches sources, scoring, or auth — re-read the relevant rule first.
- **TDD always:** failing test → minimal code → green. The canonical gate must pass before ANY commit: `cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py`. Frontend touched → also `npm run test:unit`, `npm run type-check`, `npm run lint` from `frontend/`.
- **ONE backlog item per iteration.** Small and finished beats big and half-done. Too big? Split it in the backlog and do the first slice.
- **Token discipline:** read `JOURNAL.md` tail + `BACKLOG.md` first — never re-derive project state from scratch. Targeted reads/greps only. One implementer agent per iteration (two only when a fix genuinely spans backend+frontend). No exploratory wandering.
- **Test account for live checks:** ranjith.demo@gmail.com / RanjithPass123 (user id e34aeb69e9bf4680bd143e1f3756140a) — profile already loaded from `User_info`. Backend runs via `cd backend && python main.py` (:8000); frontend `cd frontend && npm run dev` (:3000); kill stale listeners on :8000 before restarting.

## State files (the loop's memory between iterations)

- `docs/maintenance/BACKLOG.md` — the prioritized work queue. Item statuses: `TODO` / `DOING` / `DONE (sha)` / `BLOCKED(reason)`.
- `docs/maintenance/JOURNAL.md` — append-only. One entry per iteration: timestamp, item worked, commit SHAs, test results, problems discovered, next hint.

## Iteration algorithm

1. **Lock.** If `docs/maintenance/.lock` exists and is younger than 3 hours, journal nothing and EXIT (previous iteration still running). Otherwise write the current timestamp to it. ALWAYS delete the lock at the end, even on failure.
2. **Recall.** Read the last 2 entries of `JOURNAL.md` and the whole `BACKLOG.md`.
3. **Preflight.** `git branch --show-current` + `git status --short`. If the tree has uncommitted changes you didn't make (the user's work-in-progress), journal "skipped: dirty tree" and EXIT — never stash or commit someone else's work.
4. **Pick** the highest-priority `TODO`. Mark it `DOING` (write the file immediately so a crashed iteration is visible).
5. **Investigate** just enough to write a precise implementer prompt: targeted Read/Grep; an `Explore` agent only for genuinely broad questions.
6. **Dispatch** an implementer: `Agent(subagent_type: "general-purpose", model: "sonnet")` with the FULL task text — exact files, exact test commands, TDD steps (failing test first), the commit message format ending `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`, and the do-not-touch list. Model escalation: sonnet → opus only if sonnet reports BLOCKED twice or the task needs deep design judgment.
7. **Review the diff yourself** (`git show <sha>`): spec compliance, code quality, hard-rule compliance, Python-3.9 runtime compat (no module-level `X | Y` unions — annotations only), no heavy top-level imports. Fix one-liners yourself with a follow-up commit; re-dispatch the same agent for anything bigger.
8. **Verify.** Targeted tests, then the canonical gate (+ frontend gates if touched). For user-facing behavior, do ONE cheap live proof (curl the route / query `backend/data/jobs.db` / check a log line) — full browser verification only for UI changes.
9. **Record.** Mark the item `DONE (sha)`. Append the journal entry. Any NEW problem you noticed goes into `BACKLOG.md` as a `TODO` with priority — do NOT fix it this iteration.
10. **Audit sweep** (only when no implementable `TODO` remains): one cheap discovery pass — `python -m ruff check .` (backend), `npm run lint` (frontend), grep `TODO|FIXME|XXX` in src, read the latest `backend/data/logs/` run log for failing sources (HTTP 4xx/5xx, JSON errors), check `STATUS.md` fragile-source table against reality. Convert findings into prioritized backlog items, journal, exit.

## Stop conditions

- Backlog empty after an audit sweep → journal "all clear" and exit quietly.
- LLM providers hard-down / pytest infrastructure broken → mark the item `BLOCKED(reason)`, journal, exit. Never burn tokens retrying a dead provider inside the loop.

## Self-improvement

When an iteration pays a debugging cost that the next iteration shouldn't repay (a gotcha, a flaky source's quirk, a fixture trick), append it to the gotchas in `.claude/skills/verify-job360/SKILL.md` or this file — whichever fits. Repeatable multi-step workflows you find yourself re-writing belong in a new skill under `.claude/skills/`.
