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

## Tooling

- `scripts/gen-api-types.sh` calls plain `python`. **Rule:** run it with the
  venv on PATH (`PATH=/var/tmp/j360venv/bin:$PATH npm run gen:types`). Then
  revert any unrelated `format: binary` → `contentMediaType` hunks.
- Local Postgres may be down after a container restart. **Rule:**
  `service postgresql start`, then `pg_isready -h localhost -p 5432`.
