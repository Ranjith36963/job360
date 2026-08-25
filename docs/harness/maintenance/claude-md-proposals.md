# CLAUDE.md drift proposals — INBOX (append-only)
<!-- doc: LOG -->

**What this file is.** A scratch inbox for drift found in `CLAUDE.md`: a stale count, a
renamed path, a retired system still described as live, a flag whose default moved. Any
session that trips over one drops a short, evidence-backed proposal here.

**Why it exists.** CLAUDE.md drift is this repo's chronic disease — it once described a
disabled agent loop as "running", and its test count was ~17% stale. Drift gets
discovered constantly and written down never, because at the moment of discovery the
session is busy doing something else. This file makes recording it a 5-line job.

**Who writes here.** The `Stop` hook `.claude/hooks/claude-md-proposal.sh` asks the model
once per substantial session. Humans and any session may append by hand too.

## Rules

1. **APPEND ONLY.** Add new entries at the bottom. Never reorder, reword or delete
   someone else's entry. ~6 Claude Code sessions and up to 14 worktrees run in parallel
   on this repo and have already clobbered each other three times — concurrent appends
   merge cleanly, concurrent edits do not.
2. **Never edit `CLAUDE.md` from a session that is doing something else.** Proposing and
   applying are separate jobs, on purpose.
3. **Evidence or it does not count.** Every entry names a `file:line` or the exact command
   that was run. Do not file anything you merely suspect — an unverified proposal is worse
   than no proposal, because the next session will trust it.
4. **One entry per finding, max 5 lines.** If a session found several, it files the one
   that would mislead the next session most.
5. **Applying an entry means DELETING it from here.** This file is an inbox, not a log.
   A designated session reviews the accumulated entries, re-verifies each one against the
   current code, edits `CLAUDE.md`, opens a **single PR** for the batch, and removes the
   applied entries in that same PR. Empty file = no known drift.
6. **Wrong entries get deleted too**, with a one-line note in the PR body saying why.

## Entry format

```
### YYYY-MM-DD — <short topic>
- CLAIM: "<the wrong line, quoted from CLAUDE.md>"
- ACTUALLY: <what is true>
- EVIDENCE: <file:line, or the command run + its result>
- REPLACE WITH: "<the exact replacement line for CLAUDE.md>"
```

---

<!-- Append new entries below this line. Nothing pending. -->
