#!/usr/bin/env bash
# branch-owner-guard.sh — PreToolUse(Bash) guard.
#
# WHY: this repo runs ~14 worktrees, several of them LIVE parallel agent sessions.
# On 2026-07-16 a session reached into `worktree-feat-live-smoke` (checked out in
# ANOTHER worktree) to "help" land a PR. It clobbered work badly enough that a third
# session opened `fix/restore-clobbered-by-pr44` to undo it, left the owning session
# stranded on a stale commit, and later staged 165 of their files on a one-file
# `git add`. See memory: feedback-one-session-per-branch.
#
# WHAT: blocks git WRITE commands when this worktree's current branch is also checked
# out in a DIFFERENT worktree (i.e. someone else owns it). Reads are always allowed.
# Fails OPEN on any internal error — a broken guard must never wedge the session.
#
# Contract: stdin = hook JSON; exit 0 = allow; exit 2 = block (stderr shown to model).
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null | tr -d '\r')"
[ -z "$cmd" ] && exit 0

# Only care about git commands that MUTATE branch/commit state.
printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]' || exit 0
printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+(commit|merge|rebase|push|reset|checkout|switch|cherry-pick|revert|am|apply|stash)([[:space:]]|$)' || exit 0

# --- resolve state; any failure => allow (fail open) ---
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit 0
[ -z "$branch" ] && exit 0
[ "$branch" = "HEAD" ] && exit 0   # detached: nothing owned

here="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
wt="$(git worktree list --porcelain 2>/dev/null)" || exit 0

# Find a worktree holding this same branch, excluding THIS one.
owner="$(printf '%s\n' "$wt" | awk -v want="refs/heads/$branch" -v here="$here" '
  /^worktree /  { path = substr($0, 10) }
  /^branch /    { if (substr($0, 8) == want) {
                    gsub(/\\/, "/", path); h = here; gsub(/\\/, "/", h);
                    if (tolower(path) != tolower(h)) { print path; exit }
                  } }
')"

[ -z "$owner" ] && exit 0   # nobody else owns it -> allow

cat >&2 <<EOF
BLOCKED — branch '$branch' is checked out in ANOTHER worktree:
    $owner

That worktree is very likely a live parallel agent session. Writing here (commit/
merge/push/reset/checkout/rebase) can strand it on a stale commit, clobber its
in-flight work, or stage its files under your changes — this exact mistake already
forced a 'fix/restore-clobbered-by-pr44' cleanup on 2026-07-16.

Do instead:
  * Work on your OWN branch:   git checkout -B <my-branch> origin/main
  * Hand work over via main, or prepare a patch for the owning session.
  * Read-only git (status/log/diff/show) is always allowed.
EOF
exit 2
