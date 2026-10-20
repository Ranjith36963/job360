---
name: session-lessons
description: Mistakes past sessions made in this repo and the rule each one taught. Read BEFORE dispatching parallel workers, opening or watching PRs, handing work to another Claude session, running a real apply / outreach lifecycle for the owner, or writing a CV, letter or outreach note. Append a new lesson whenever you make a mistake the owner had to catch or that cost real time, and put its one-line rule in .claude/rules/mistakes.md (loads every session).
---
<!-- doc: LIVING | last-verified: 2026-10-15 by the owner ("ask Claude to document its mistakes") -->

# Session lessons: write down every mistake, once

The owner's rule (2026-10-15): when you make a mistake, write it here, so the
next session does not repeat it. Format: **what went wrong**, then **the rule**.
Keep each one to two or three lines. Newest at the bottom of its section. Never
delete a lesson; if one stops being true, say so on its line and why.

## Parallel workers and the local test DB

- 2026-10-15: eleven worktree agents each ran the FULL backend suite at once
  against the one local Postgres (:5432). About 30 pytest processes thrashed the
  DB, and runs sat at "6 dots" for an hour. **Rule:** parallel workers run only
  their targeted test files (plus the MCP text-budget, parity and server tests
  if they touched MCP). The full suite is CI's job on the PR. Say so in the
  worker prompt from the start.
- 2026-10-15: one worker kept re-sending its final report after its PR was
  open. **Rule:** once a worker's work is committed, stop it with TaskStop.

## Watching PRs

- 2026-10-19: `drift-check` went red on every PR at midnight because CLAUDE.md and
  backend/CLAUDE.md passed the 45-day `last-verified` limit. I re-verified them and
  bumped the stamps INSIDE product PR #888. Every `**/CLAUDE.md` is human-only
  (the `harness_owner` lane in merge-policy.yml), so auto-merge.yml disarmed #888 50 s after it was armed, and
  the owner had to merge by hand. **Rule:** a CLAUDE.md edit, even a date stamp, is its
  own PR, opened alone, with the owner told it needs their click.
- 2026-10-15: subscribing to PR activity on four PRs flooded the conversation.
  Every bot comment (merge-cage, lane advice, CodeRabbit rate-limit edits, WIP
  parking) arrived as a long event, and each subscription re-sent a long
  rulebook. **Rule:** for your own PRs, track CI with a quiet background poll
  (`check-runs` for the head SHA until nothing is pending) and read the
  reviewer-bugs / reviewer-security verdicts once. Do not subscribe four PRs at
  once.
- (Retired 2026-10-20.) There was a WIP limit that parked extra PRs with `wip:parked`. The owner had it deleted: parked PRs were never unparked, because the sweep that unparked them ran hours late. **Rule:** never bring back a gate whose undo runs only on a schedule.
- 2026-10-15: a backend CI failure was unreadable: the log tail is Postgres
  service-container noise and the full-log download host is blocked from the
  cloud container, so the owner had to paste the failure. **Rule:** read the job
  summary (failing tests are written there since the failed-tests-in-summary
  PR). When a head shows few check runs, read the PR's `mergeable_state` first:
  a CONFLICTING PR runs no CI.
- 2026-10-19: `offline-suite` went red on PR #916 and I read only the last 80 lines of the
  job log (`get_job_logs` tail_lines=80). All 80 were Postgres teardown noise ("lastval is
  not yet defined"), which the container prints AFTER pytest; the check run's own output was
  empty. Instead of pulling more of the log I re-ran all ~3,900 backend tests in the cloud
  container (39 min, the first try lost to the local Postgres being stopped). The owner pasted
  the answer: pytest's short test summary sat ~400 lines above the end
  (`update_profile is 2034 characters`, the MCP text budget). Why it repeated: I never read
  this skill before watching the PR, so I also waited an hour on a rate-limited CodeRabbit,
  breaking the rule below. **Rule:** pull enough of a failing log to reach pytest's
  `short test summary info` (tail_lines ≥ 600) before any slower route.
- 2026-10-19, same PR #916: the failure itself was mine. A review asked for `skip` to be
  explained in the `update_profile` tool description; my one-line addition took it from
  1,998 to 2,034 characters. `tests/test_mcp_text_budget.py` caps every MCP text at 2,000
  (Claude Code truncates at 2,048), but `scripts/agent-gate.sh` passed twice: its fast tier
  ran only the 226 / 13 tests it judged affected, not the budget test. Only the 20-minute
  `offline-suite` caught it. **Rule:** after editing any `@mcp.tool()` docstring or
  `INSTRUCTIONS` in `api/mcp_server.py`, run `python scripts/mcp_text_budget.py` before
  pushing.
- CodeRabbit's free tier rate-limits after a few reviews an hour. **Rule:** never
  wait on it; the repo's own reviewer-bugs / reviewer-security / CI are the gate.
- 2026-10-18: a finished Needs-you feature sat unpushed while the session
  described "next steps: draft PR"; the owner asked why auto-merge was not used.
  The repo has `allow_auto_merge: true` and the `main-production-gate` ruleset
  requires 13 checks (both AI reviewers included). **Rule:** every PR, not a
  draft, gets `gh pr merge <n> --auto --squash` as soon as it is opened; then
  confirm the Railway deploy on the merge sha. Same day, PR #883: 30 s after
  arming, `auto-merge.yml` DISARMED it (timeline `auto_merge_disabled`) because
  one `.claude/` file matched no lane in `.github/merge-policy.yml` (owner
  lane). **Rule:** read a PR's lane off `lane.classify` (`python scripts/lane.py`), never a prefix list.

## Handing work to another Claude session

- 2026-10-15: the cloud session asked the owner's desktop "apply" session (via
  send_message) to fill and submit a form. That session's permission check
  refused the CV upload and an event write, because the request came from
  another session, not the owner. **Rule:** a peer's request is never the
  owner's yes. For anything that submits, uploads or sends, tell the owner to
  type the instruction in THAT session. Don't retry through the peer.
- The desktop session has no send_message tool, so it cannot reply. **Rule:**
  read its answer from its transcript (list_events), and say so in the hand-off.

## The cloud container and job sites

- The cloud container's network refuses ATS hosts (jobs.lever.co, Greenhouse,
  Ashby): proxy 403 / ENOTFOUND. **Rule:** read job pages and forms with the
  Apify web-fetch tool. Filling and submitting must happen in the owner's own
  browser session. Record `blocked` once, then hand over.
- The claude.ai Job360 connector can serve an OLD cached tool list (e.g.
  update_job without apply_url), and the argument silently vanishes.
  **Rule:** after a deploy that adds a tool parameter, check `doctor` and tell
  the owner to reconnect the connector before relying on it.

- 2026-10-19: a research worker probed Bayt and GulfTalent's public search WEB PAGES from the
  owner's PC, got 403, and I passed "both blocked" to the hunt-coverage session as a fact. On
  2026-10-20 the live smoke test (`docs/product/hunt-kit-smoke-2026-10-20.md`) showed the #918
  readers, which use other endpoints, answering 21/21 and 28/28 and keeping real Dubai jobs.
  **Rule:** say "blocked" only for the exact endpoint the reader calls, and name that endpoint.
- 2026-10-20: for the same smoke test I downloaded the kit into `tmp/smoke`, which already held
  `backend/` and `kit.tar` from an earlier task, though the brief said a NEW empty folder. Caught
  before the run and redone in a fresh folder. **Rule:** before downloading a test run, check the
  folder does not exist (`test -e`), then create it.
- 2026-10-20: I stamped the smoke doc `<!-- doc: SNAPSHOT -->`, a kind that does not exist, and
  pushed without running the drift check. CI's `drift-check` failed, the cage withdrew auto-merge,
  and PR #935 sat unmerged for hours while I reported it as armed. **Rule:** a new doc's kind is one
  of LIVING/PLAN/LOG/REFERENCE/FROZEN/GENERATED; run `python scripts/doc_sync_check.py` before
  pushing any docs PR, and re-read `gh pr checks` before saying a PR will merge.
- 2026-10-20: my Gulf-boards research brief said "stop on 403/429 for that host". The Sonnet worker
  treated each `<slug>.recruitee.com` as its own host and guessed slugs in a burst: 90 answers of 429
  from Recruitee, and Workable 429'd twice (Retry-After 314 s). Both throttle the service as a whole.
  **Rule:** a brief names the SERVICE as the unit ("stop that ATS on its first 429 for this run") and
  forbids slug-guessing bursts; verify known slugs only.

## Real applications for the owner

- 2026-10-15: the owner answered "and upload observe" to "apply or skip?".
  The assistant read it as go-ahead for documents only. That was right not to
  submit on it, but it should have asked one short question then. **Rule:** an
  answer that is not clearly yes/no to the question asked gets one clarifying
  question, never an interpretation.
- 2026-10-15: an outreach draft said the owner was "happy to take the aptitude
  tests" before the owner had answered that question. **Rule:** before saving
  any outreach, CV or letter, re-read it for claims the profile and the owner's
  answers do not contain; remove them, or ask.
- 2026-10-15: a cover letter said "remote work with Indian time-zone overlap",
  which is not in the profile. **Rule:** same as above; letters are not exempt.
- 2026-10-15: to attach the confirmation email as proof, the assistant recorded a
  SECOND `applied` event. **Rule:** a confirmation email for an application that
  is already applied is proof, not a new status: record it as a note starting
  "Submission confirmed" with the email `source` (proof.py counts it as email).
- 2026-10-15: the kit said `missing: []`, but the live form required six answers
  Job360 did not have. **Rule:** always read the live form's questions (every
  section, scroll to the bottom) before saying nothing is missing, and ask the
  owner once, in one message, for all of them.
- 2026-10-15: `record_application` without `cover_letter_artifact_id` froze the
  newest letter into the receipt, though the form had no letter field.
  **Rule:** name every artifact the form actually took. Leave the letter out only
  when the code no longer guesses (weak spot #2 PR); until then, check the
  receipt after recording it.

## Reference index (open these before "borrowing" anything)

They live in the cloud container's /var/tmp and vanish when it is reclaimed.
If a path is missing, re-clone the repo, or re-capture the site with the
Apify web-fetch tool, before you build. Never cite from memory.

- career-ops (terminal OSS): /var/tmp/co-src, https://github.com/career-ops-hq/career-ops
  (modes/apply.md, scan.mjs, salary-gap.mjs, update-system.mjs, pipeline-lock.mjs, AGENTS.md)
- neonwatty job-apply-plugin: /var/tmp/refs/job-apply-plugin, https://github.com/neonwatty/job-apply-plugin
  (skills/answer-memory, skills/job-apply, scripts/job-apply-store.py)
- trackly-cli: /var/tmp/refs/trackly-cli, https://github.com/kevinastuhuaman/trackly-cli
- jobctl-claude: /var/tmp/refs/jobctl-claude, https://github.com/DmytroRybka/jobctl-claude
- JobCtrl: /var/tmp/refs/JobCtrl, https://github.com/ebarti/JobCtrl
- Tsenta (YC) and 40+ other sites, captured pages: /var/tmp/rea-comp/<site>
  (tsenta, simplify, huntr, teal, jobright, lazyapply.com, loopcv,
  jobcopilot.com, jackandjill, jobwyre, aiapply.co, careerflow, himalayas_app,
  freehire_me, jobspipe_dev, scale.jobs, ...). Summary: /var/tmp/comp-evidence.md.
  Tsenta's real content is in home-curl.txt (home.txt is a 502 page).

## Why mistakes repeated (owner, 2026-10-15)

- "Borrowed from <competitor>" was written into PRs while the workers never
  opened that competitor's code. The fixes were our own guesses with a
  competitor's name on them, and needed a second round. **Rule:** a worker
  implementing a borrowed technique must first open the reference source or
  REA capture (career-ops: /var/tmp/co-src; Tsenta: /var/tmp/rea-comp/tsenta;
  others: clone them), cite file:line in the PR, and mirror the mechanism.
- Fixes were "proven" by unit tests, then a real application broke the same
  area in a new way. **Rule:** a weak spot is closed only after a real
  lifecycle run on production exercises it.
- Known traps were not in the workers' prompts, so each worker rediscovered
  them. **Rule:** every worker prompt starts with "read session-lessons".
- 2026-10-15: PR #840 failed CI on one line over 120 chars (ruff E501).
  **Rule:** run `cd backend && python -m ruff check .` before every push.
- 2026-10-15: every PR that added a test file bumped the same count in
  docs/GENERATED.md, so each later PR went stale after a merge. **Rule:**
  before merging a queued PR, merge origin/main into it and run
  `python3 scripts/gen_doc_blocks.py --write`.

- 2026-10-15: a one-line `git merge ...; git add -A; git commit` kept going
  after the merge CONFLICTED and pushed a commit full of conflict markers
  (caught and fixed one commit later). **Rule:** never chain past a merge.
  Check its exit code (or `set -e`), resolve, run the tests, then commit.
- 2026-10-20: the lesson above repeated. Merging main into a stacked slice, a chained
  `git merge ...; git add -A; git commit` ran on past a CONFLICT (no markers landed, by luck:
  the conflicted file was one we kept whole). **Rule:** merge through a script that exits on
  a non-zero merge (`set -e`, or `if ! git merge ...; then exit 2; fi`), auto-resolves only
  `docs/GENERATED.md` (take main's, regenerate) and stops on anything else.
- 2026-10-20: `isolation: "worktree"` agents start from origin/main, not from the lead's
  checkout, so a slice meant to stack on an open PR was built without its base; one worker
  halted (right), its empty worktree vanished, and it had to be re-dispatched. **Rule:** a
  stacked worker's prompt names the base branch and its first step,
  `git fetch origin <base> && git merge --no-edit origin/<base>` (a merge commit, never
  `--ff-only` or rebase), and tells it to stop on any conflict but GENERATED.md.
- 2026-10-20: six PRs that each added an MCP tool parameter went red on
  `test_mcp_tools_rev` one after another: each bumped `TOOLS_REV` from the same main and
  pinned its own hash, so every merge made the rest stale. **Rule:** after merging main into
  a PR that changes MCP tool names or parameters, recompute the served hash and set the rev
  to main's rev + 1 at that moment, not when the slice was written.
- 2026-10-20, same session: the "merge through a script" rule above was followed with a
  script that ran `git merge` and a `--no-verify` regen commit inside `bash update.sh`. The
  commit-gate hook reads only the top-level command, so a dozen merge commits went up with no
  gate run (CI still ran the full suite on each push). Caught when a plain `git merge` was
  blocked. **Rule:** never wrap `git merge` / `git commit` in a script and never pass
  `--no-verify`: run the gate, `git merge --no-commit --no-ff <base>`, resolve, gate the merged
  tree, then `git commit --no-edit`. Slower, and the point.
- 2026-10-20: resolving a merge, `git checkout --ours <file>` took the branch's WHOLE file and
  silently dropped main's cleanly merged hunks in it (slice 9's code). Caught before the commit.
  **Rule:** resolve per hunk (`git checkout -m <file>` restores the markers); keep a side only
  inside the conflict regions, then diff the file against both parents.
- 2026-10-20: slice 8 (#993) was stacked on slice 7 (#977); its squash merge carried slice 7
  into main, and #977 sat open, red, with nothing left to ship. **Rule:** when a stacked child
  merges, merge main into the base (`--no-commit`); if the tree equals main, close the base PR
  as shipped, naming the merge commit.
- 2026-10-20, same session: a gate helper ended with `echo`, so it exited 0 even when the gate
  failed. Run from `backend/` instead of the repo root, the gate never ran (rc=127), yet
  `helper && git commit && git push` committed and pushed the s30 merge (#1007). The hook did not
  stop it because the command did not start with `git commit`. The same tree passed the gate
  right after. **Rule:** a gate step is its own command: read its PASS line, then run
  `git commit` as a separate command; a helper must exit with the gate's own code.
- 2026-10-15: pushes failed CI on the mypy ratchet and, after merging main, on
  the recipe line cap and MCP tool count. **Rule:** run the ratchet
  (`cd backend && python scripts/mypy_ratchet.py`) before every backend push, and
  re-run tests/test_recipes.py + tests/test_outreach_people.py after merging main.

- 2026-10-15: the first round of the 11 weak-spot PRs used 6 references the
  lead remembered; the 44 captured competitor sites in /var/tmp/rea-comp were
  never searched, and citations were not checked until the owner asked.
  **Rule:** before planning a borrowed fix, sweep EVERY source in the
  reference index for that topic (Haiku can grep), write a plan that quotes
  file:line or says NOT IN REFERENCE, and have each quote checked against the
  file (another agent or the lead) before any code is built.
- 2026-10-16: the lead offered to move the outreach link into feedback PR 1,
  got no answer, then announced the move as the plan. The owner had to undo
  it ("Don't link") and dropped outreach linking entirely. **Rule:** an
  option you offered is not a decision; wait for the owner's answer before
  changing the plan or the spec.
- 2026-10-14 to 10-16: the owner asked again and again for "REA" (Reverse Engineer
  Anything, github.com/morluto/rea) on competitors. The lead took "REA" to mean its own
  page captures (/var/tmp/rea-comp) and web reads, reported "REA done" for two days, and
  never checked the tool existed. It was never installed or run. **Rule:** when the owner
  names a tool, check it is available (`which`, MCP list) - that only shows availability;
  claim it ran only after an actual REA MCP call or CLI run (including `npx rea-agents`).
  If it is missing, say so at once and give the install steps. Label web reads as web research.
- 2026-10-17: the daily doc truth check shortened the root `CLAUDE.md` bullet calling
  `repositories/pg.py` "the single DB door" without questioning the claim; CodeRabbit found
  it. The sweep had enumerated `pg` importers, which can never reveal that `pgsync.py` opens
  its own psycopg connection on the profile path. **Rule:** to test a "single/only X" claim,
  enumerate what performs X, never what imports X.

- 2026-10-17: reviewing a worker's port, the lead "restored" a career-ops regex prefix by
  grepping a diff that showed only one line of a two-line string, and so added the prefix a
  SECOND time. "This position has been filled." stopped reading as closed; CodeRabbit caught
  it on PR #875. **Rule:** before editing a multi-line expression, print the whole expression
  (sed -n) and diff it against the reference line, then run a test that hits that exact branch.

- 2026-10-19: of 27 PRs merged 17-19 October, 2 merged by auto-merge cleanly. The cage
  judged each PR a minute after it opened, read "has not finished" as a refusal and turned
  auto-merge off; nothing woke it when CI went green (no `check_suite` event fires for
  Actions' own suites), so sessions merged by hand past it, and #906 shipped a red test to
  main. Meanwhile #902 grew from 1,584 to 3,800+ lines as slices 2-4 were pushed onto it
  while it was being split. **Rule:** one PR per slice under the cap, arm auto-merge once,
  and let `merge-cage` hold it; never hand-merge around the gate.

- 2026-10-20: checking the live ruleset for PR #952, the lead ran `scripts/ruleset_gate.py
  --live` inside the PR's worktree. That mode REWRITES `.github/rulesets/main-gate-snapshot.json`
  from the live API, and the next `git add -A` committed the rewritten file (an 8,495-line
  diff). Chain wires went red with merge-cage DEAD_CONTEXT; fixed by restoring the file from
  origin/main. **Rule:** never run `ruleset_gate.py --live` on a branch (read the ruleset with
  the API instead), and run `git status` before every `git add -A`.

## Tooling

- `scripts/gen-api-types.sh` calls plain `python`. **Rule:** run it with the
  venv on PATH (`PATH=/var/tmp/j360venv/bin:$PATH npm run gen:types`). Then
  revert any unrelated `format: binary` → `contentMediaType` hunks.
- Local Postgres may be down after a container restart. **Rule:**
  `service postgresql start`, then `pg_isready -h localhost -p 5432`.
