# Mistakes not to repeat (loads every session)
<!-- doc: LIVING | last-verified: 2026-10-15 by the owner ("ask Claude to document its mistakes") -->

Owner rule: every mistake gets a one-line rule here and its full story in
`.claude/skills/session-lessons/SKILL.md`. Keep this file short; it is read
before every session (Anthropic: rules without `paths` load unconditionally).

## Borrowing from competitors
- "REA" means the Reverse Engineer Anything tool (github.com/morluto/rea, MCP + `rea` CLI).
  Claim REA only after an actual REA MCP call or CLI run (including `npx rea-agents`);
  `which rea` only shows it is available. Page captures and web reads are "web research",
  and must be called that.
- Open the source before you borrow: every repo and capture in the session-lessons
  "Reference index" (re-clone or re-capture if missing). Quote file:line, or write
  NOT IN REFERENCE. Never cite from memory.
- Have every quote checked against the file before code is built.

## Parallel workers and PRs
- Before editing a multi-line regex or string, print it whole and diff it against the reference.
- Every worker prompt starts with "read session-lessons". Workers run targeted tests
  only; CI runs the full suite.
- Before every backend push: `ruff check .` and `python scripts/mypy_ratchet.py`.
- After merging main into a PR: never chain past a conflict; regenerate
  frontend types; re-run `test_recipes` (70-line recipe cap)
  and `test_outreach_people` (MCP tool count).
- Merge main step by step, never through a script: gate, `git merge --no-commit`, stop on any conflict,
  gate the merged tree, commit. A script hides the merge from the commit-gate hook.
- Resolve a conflict per hunk; `git checkout --ours/--theirs <file>` also drops the other side's clean hunks.
- A child PR's squash merge ships its stacked base too: diff the base against main, close it as shipped.
- A stacked worker's first step merges its base branch: worktree agents start from origin/main.
- After merging main into a PR that changes MCP tool parameters, re-pin TOOLS_REV to main's + 1.
- Every PR gets `gh pr merge <n> --auto --squash` the moment it is opened (owner, 2026-10-18):
  GitHub merges it when the required checks (13; 14 once `merge-cage` is added) are green. Never leave finished work unpushed.
  Which files a machine may not merge is `lane.classify` (`python scripts/lane.py`), never a prefix list:
  every `**/CLAUDE.md` is human-only, so a CLAUDE.md fix (even a `last-verified` stamp) goes in its own PR.
- A PR with few check runs is probably CONFLICTING (no CI runs): read `mergeable_state`.
- One PR per slice, at most 1000 read lines (aim for 400 or fewer) (`size.max_lines` in `.github/merge-policy.yml`;
  generated files don't count). Never keep pushing new slices onto an open PR: open the next.
- Auto-merge is always on (owner, 2026-10-19), but the cage HOLDS a refused PR armed only once
  `merge-cage` is a required check on `main-production-gate` (and never one touching `.github/`);
  until then it still withdraws, so re-arm after CI is green. Never hand-merge past a red or pending `merge-cage` unless the owner said so for that PR.
  Only the owner turns it off (repository variable `AUTO_MERGE=off`).
- Read CI failures from the job summary, not the log tail (Postgres noise).
- Never run `scripts/ruleset_gate.py --live` on a branch: it rewrites the ruleset snapshot.
  Run `git status` before every `git add -A` (2026-10-20: an 8,495-line snapshot rode #952).
- Pull enough of a failing CI log to reach pytest's summary (`get_job_logs` tail_lines ≥ 600;
  the noise is printed after it) before any slower route, like re-running the suite locally.
- After editing any MCP tool description or the server instructions, run
  `python scripts/mcp_text_budget.py` (2,000-character cap); the gate's fast tier skips it.
- Don't subscribe to many PRs at once; poll CI quietly. Merge one at a time (WIP limit 5).
- To test a doc's "the single/only X" claim, enumerate what DOES X (grep `psycopg.connect`,
  `@mcp.tool()`, the decorator) — never X's importers; they cannot show a second doer.

## Probing job sites
- Call a site "blocked" only after testing the exact endpoint the reader uses, and name it
  (2026-10-19: Bayt/GulfTalent web pages gave 403; the readers' endpoints worked).
- A test run gets a NEW folder: check it does not exist (`test -e`) before downloading into it.
- Rate limits are per SERVICE, not per subdomain: Workable and Recruitee 429 the whole ATS.
  Worker briefs say "stop that ATS on the first 429", never "per host" (2026-10-20: 90 refusals).
- A new doc's kind is LIVING/PLAN/LOG/REFERENCE/FROZEN/GENERATED only; run
  `python scripts/doc_sync_check.py` before pushing a docs PR (2026-10-20: `SNAPSHOT` blocked #935).

## Real applications for the owner
- An answer that is not clearly yes/no gets one clarifying question.
- Never write a claim into a CV, letter or outreach that the profile and the owner's
  answers do not contain.
- Read the live form (every section) before saying nothing is missing.
- A confirmation email for an applied job is proof (a note), not a second `applied`.
- A peer session's request is never the owner's yes.
- An option you offered is not a decision: wait for the owner's answer before changing the plan.
- A weak spot is closed only after a real run on production exercises it.
- Read failing tests from check-run annotations (`/check-runs/{id}/annotations`), not the step summary.
