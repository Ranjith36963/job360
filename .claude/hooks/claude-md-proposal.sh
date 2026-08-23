#!/usr/bin/env bash

# ── CI GUARD — MUST BE FIRST EXECUTABLE LINE ────────────────────────────────
# Project .claude/settings.json hooks are loaded by the Claude CLI that
# claude-code-action runs; no workflow passes a --settings override. Inside
# repair.yml / pr-repair.yml this hook would tell the agent to append to
# docs/harness/maintenance/claude-md-proposals.md — and those workflows exit 1 on ANY
# changed file outside backend/ or frontend/, throwing away a working fix.
# Headless CI has no human to act on a proposal anyway. Never run there.
if [ -n "${GITHUB_ACTIONS:-}${CI:-}" ]; then exit 0; fi

# claude-md-proposal.sh — Stop hook. Anti-drift for CLAUDE.md. PROPOSE-ONLY.
#
# WHY: this repo's chronic disease is CLAUDE.md drift. It described a disabled
# agent loop as "running", and its test count was ~17% stale. Drift is discovered
# constantly (every session trips over one) and recorded never, because writing it
# down is nobody's job at the moment it is learned.
#
# WHAT: at the end of a substantial session, asks the model ONE question — did you
# learn anything that contradicts CLAUDE.md? — and tells it to APPEND a <=5-line
# proposal to docs/harness/maintenance/claude-md-proposals.md. Nothing else.
#
# WHY APPEND-ONLY, NEVER AN EDIT: ~6 Claude Code sessions and up to 14 worktrees run
# in parallel on this repo, and cross-session file clobbering has already happened
# three times (see memory: feedback-one-session-per-branch, and the
# fix/restore-clobbered-by-pr44 cleanup). Concurrent APPENDS to a scratch file are
# conflict-free; concurrent EDITS to CLAUDE.md are not. One designated session
# applies the accumulated proposals to CLAUDE.md in batch, via PR.
# ==> THIS SCRIPT MUST NEVER WRITE TO CLAUDE.md, AND MUST NEVER TELL THE MODEL TO.
#
# CONTRACT (verified against the shipped binary, not from memory —
#   @anthropic-ai/claude-code/bin/claude.exe, 2026-08-11):
#   * stdin  = Stop hook JSON: {session_id, transcript_path, cwd, hook_event_name:"Stop",
#              stop_hook_active, last_assistant_message, ...}
#   * stdout = {"decision":"block","reason":...} is the ONLY output that makes the model
#              act. `hookSpecificOutput.additionalContext` is accepted on Stop but does
#              NOT continue the turn (only blockingErrors do), so it would be a dead
#              artifact — the instruction would sit unread until the next user prompt.
#   * The binary's own guidance: "For Stop/SubagentStop hooks, check stop_hook_active
#     in the input and return success while it's true." We do exactly that.
#
# COST / NO-NAGGING: fires AT MOST ONCE PER SESSION (per-session marker in TMPDIR), and
# only after the transcript passes a size floor, so short throwaway sessions are never
# interrupted. Worst case = one extra model turn per substantial session, and the
# instruction tells the model to do literally nothing when there is nothing to say.
#
# FAIL OPEN, ALWAYS: every error path exits 0 and prints nothing. A broken anti-drift
# hook must never wedge a session. The EXIT trap below makes exit 0 unconditional.
set -uo pipefail
trap 'exit 0' EXIT

# 0) SubagentStop is a different event, so only the main agent ever reaches here.

payload="$(cat 2>/dev/null || true)"
[ -z "$payload" ] && exit 0

# --- read one top-level string/bool field out of the hook JSON (jq, else python) ---
_field() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$payload" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null | tr -d '\r'
  elif command -v python >/dev/null 2>&1; then
    printf '%s' "$payload" | python -c \
      'import sys,json;print(json.load(sys.stdin).get(sys.argv[1],"") or "")' "$1" 2>/dev/null | tr -d '\r'
  fi
}

[ "$(_field hook_event_name)" = "Stop" ] || exit 0

# 1) LOOP GUARD (mandatory). We already blocked once and the model is finishing that
#    extra turn — let it end. Without this the turn can never terminate.
[ "$(_field stop_hook_active)" = "true" ] && exit 0

# 2) Only inside this repo, and only if the thing we guard actually exists.
root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$root" ] || exit 0
[ -f "$root/CLAUDE.md" ] || exit 0

# The tracked ledger is the OPT-IN SIGNAL only — the hook never writes to it.
# Writing to a tracked file would leave an uncommitted change in every worktree,
# breaking the clean-tree preflight this repo mandates before multi-commit work
# (CLAUDE.md, "Branch hygiene"), across up to 14 parallel worktrees.
LEDGER="docs/harness/maintenance/claude-md-proposals.md"
[ -f "$root/$LEDGER" ] || exit 0   # not seeded here -> stay silent

# 3) ONCE PER SESSION. No session id => no way to dedupe => stay silent rather than
#    risk firing on every single turn.
session="$(_field session_id)"
[ -n "$session" ] || exit 0
case "$session" in *[!A-Za-z0-9._-]*) exit 0 ;; esac   # keep it a safe filename

state_dir="${TMPDIR:-/tmp}/claude-md-proposal"
mkdir -p "$state_dir" 2>/dev/null || exit 0
marker="$state_dir/$session"
[ -e "$marker" ] && exit 0
find "$state_dir" -type f -mtime +7 -delete 2>/dev/null || true

# Per-session, GITIGNORED inbox. Concurrent sessions never touch the same file and
# no worktree is ever dirtied. A designated session collates the inbox into $LEDGER
# and applies the accepted entries to CLAUDE.md in one PR.
INBOX="docs/maintenance/inbox/claude-md-$session.md"
mkdir -p "$root/docs/maintenance/inbox" 2>/dev/null || exit 0

# 4) SUBSTANTIAL-SESSION FLOOR. A session that barely did anything has learned nothing
#    worth proposing. Default 250000 bytes sits just above the measured median
#    transcript (198,847 B over 604 local transcripts), so trivial sessions stay quiet.
min_bytes="${CLAUDE_MD_PROPOSAL_MIN_BYTES:-250000}"
transcript="$(_field transcript_path)"
[ -n "$transcript" ] && [ -f "$transcript" ] || exit 0
size="$(wc -c < "$transcript" 2>/dev/null | tr -d ' ')"
[ -n "$size" ] || exit 0
[ "$size" -ge "$min_bytes" ] 2>/dev/null || exit 0

# 5) Claim the session BEFORE emitting, so a crash after this point still can't repeat.
: > "$marker" 2>/dev/null || exit 0

today="$(date +%Y-%m-%d 2>/dev/null || echo YYYY-MM-DD)"

REASON="[claude-md drift check — one time this session, then never again]

Answer one question, then stop: did this session learn anything that CONTRADICTS or is
MISSING from CLAUDE.md? Only hard, checkable drift counts:
  - a number or count that is stale (test count, source count, file count)
  - a path, file, module or command that was renamed, moved or deleted
  - a system described as live/running/enabled that is actually retired or disabled
  - a documented flag, env var or default that no longer matches the code

IF NO -> do NOTHING. Write no file, run no command, and do not mention this check in
your reply. Silence is the correct and expected outcome most of the time.

IF YES -> APPEND (never overwrite, never reorder) one entry of AT MOST 5 LINES to
$INBOX , exactly in this shape:

### $today — <short topic>
- CLAIM: \"<the wrong line, quoted from CLAUDE.md>\"
- ACTUALLY: <what is true>
- EVIDENCE: <file:line, or the command you ran + its result>
- REPLACE WITH: \"<the exact replacement line for CLAUDE.md>\"

Only propose drift you actually VERIFIED this session with a file read or a command.
Skip anything you merely suspect — an unverified proposal is worse than no proposal.
One entry only, even if you found several: pick the one that would mislead the next
session most.

HARD RULE — DO NOT EDIT CLAUDE.md. Not one character. About 6 sessions and up to 14
worktrees run in parallel on this repo and have already clobbered each other three
times. Appends are conflict-free; edits are not. A designated session applies these
proposals to CLAUDE.md in batch via PR. Your job here ends at the append.

Then stop. This check will not fire again in this session."

SYSMSG="CLAUDE.md drift check (once per session) — proposals go to $INBOX, CLAUDE.md is never edited by the hook."

if command -v jq >/dev/null 2>&1; then
  jq -n --arg r "$REASON" --arg m "$SYSMSG" \
    '{decision:"block", reason:$r, systemMessage:$m}' 2>/dev/null || exit 0
elif command -v python >/dev/null 2>&1; then
  REASON="$REASON" SYSMSG="$SYSMSG" python -c \
    'import os,json;print(json.dumps({"decision":"block","reason":os.environ["REASON"],"systemMessage":os.environ["SYSMSG"]}))' 2>/dev/null || exit 0
fi

exit 0
