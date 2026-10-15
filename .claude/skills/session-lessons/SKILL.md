---
name: session-lessons
description: Mistakes past sessions made in this repo and the rule each one taught. Read BEFORE dispatching parallel workers, opening or watching PRs, handing work to another Claude session, running a real apply / outreach lifecycle for the owner, or writing a CV, letter or outreach note. Append a new lesson whenever you make a mistake the owner had to catch or that cost real time.
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

- 2026-10-15: subscribing to PR activity on four PRs flooded the conversation.
  Every bot comment (merge-cage, lane advice, CodeRabbit rate-limit edits, WIP
  parking) arrived as a long event, and each subscription re-sent a long
  rulebook. **Rule:** for your own PRs, track CI with a quiet background poll
  (`check-runs` for the head SHA until nothing is pending) and read the
  reviewer-bugs / reviewer-security verdicts once. Do not subscribe four PRs at
  once.
- The WIP limit is 2 open PRs. Extras get `wip:parked`. **Rule:** merge one at a
  time, then remove the label from the next PR.
- CodeRabbit's free tier rate-limits after a few reviews an hour. **Rule:** never
  wait on it; the repo's own reviewer-bugs / reviewer-security / CI are the gate.

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

- 2026-10-15: the first round of the 11 weak-spot PRs used 6 references the
  lead remembered; the 44 captured competitor sites in /var/tmp/rea-comp were
  never searched, and citations were not checked until the owner asked.
  **Rule:** before planning a borrowed fix, sweep EVERY source in the
  reference index for that topic (Haiku can grep), write a plan that quotes
  file:line or says NOT IN REFERENCE, and have each quote checked against the
  file (another agent or the lead) before any code is built.

## Tooling

- `scripts/gen-api-types.sh` calls plain `python`. **Rule:** run it with the
  venv on PATH (`PATH=/var/tmp/j360venv/bin:$PATH npm run gen:types`). Then
  revert any unrelated `format: binary` → `contentMediaType` hunks.
- Local Postgres may be down after a container restart. **Rule:**
  `service postgresql start`, then `pg_isready -h localhost -p 5432`.
