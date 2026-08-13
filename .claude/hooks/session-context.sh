#!/usr/bin/env bash
# session-context.sh — SessionStart hook. Tells the model WHERE IT IS, at t=0.
#
# WHY THIS EXISTS
#   Three facts decide whether a session's work is safe here, and all three were
#   prose the model had to remember:
#     1. WHICH branch/worktree am I in, and does another session own it?
#        (2026-07-16: a cross-worktree merge clobbered another session's work.
#         branch-owner-guard.sh now catches it — but only at the first WRITE.
#         By then the session has already planned around the wrong assumption.)
#     2. HAS origin/main MOVED under me? `main` auto-deploys, several sessions
#        push to it, and an audit once ran to completion on a branch 124 commits
#        behind — every conclusion in it was wrong.
#     3. IS THE TREE DIRTY? The repo mandates a clean-tree preflight before any
#        multi-commit batch.
#   statusline-cockpit.js already computes most of this — for the HUMAN's eyes.
#   The model never sees it. This closes that gap.
#
# CONTRACT
#   - READ ONLY. Never writes, never blocks. Pure context.
#   - FAILS OPEN. Any error exits 0 with no output: a broken context hook must
#     never stop a session from starting.
#   - Cheap. No network. Bounded git calls only.
set -uo pipefail

# Never let this hook be the reason a session dies — or waits.
trap 'exit 0' ERR

# HARD WALL-CLOCK BOUND. Measured 2026-08-11: ~1.6 s here, and `git worktree list`
# alone is 432 ms across 30 worktrees on Windows — that cost grows with the pile.
# Re-exec under `timeout` so the ceiling holds no matter how big the repo gets.
# If timeout is unavailable, run unwrapped rather than not at all.
if [ -z "${_SESSION_CTX_BOUNDED:-}" ] && command -v timeout >/dev/null 2>&1; then
  export _SESSION_CTX_BOUNDED=1
  timeout 5s bash "$0" "$@" || exit 0
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$ROOT" ] || exit 0
cd "$ROOT" 2>/dev/null || exit 0

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
SHORT="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

OUT=""
add() { OUT="${OUT}$1
"; }

add "## Where this session is (SessionStart hook, read-only)"
add ""
add "- **cwd**: \`$ROOT\`"
add "- **branch**: \`$BRANCH\` @ \`$SHORT\`"

# --- Is this a worktree, and does someone else own this branch? ------------
WT_COUNT="$(git worktree list 2>/dev/null | wc -l | tr -d ' ')"
if [ "${WT_COUNT:-1}" -gt 1 ]; then
  add "- **worktrees live**: $WT_COUNT — another session may own a branch you touch."
  OWNER="$(git worktree list --porcelain 2>/dev/null \
    | awk -v b="refs/heads/$BRANCH" '
        /^worktree /{w=$2}
        /^branch /{ if ($2==b) print w }' \
    | head -1)"
  if [ -n "${OWNER:-}" ] && [ "$OWNER" != "$ROOT" ]; then
    add "- ⚠️ **\`$BRANCH\` is checked out in a DIFFERENT worktree**: \`$OWNER\`."
  fi
fi

# --- Has main moved under me? ---------------------------------------------
# No fetch: a SessionStart hook must not add network latency or fail offline.
# This reports the last KNOWN state of origin/main, which is the honest answer.
if git rev-parse --verify -q origin/main >/dev/null 2>&1; then
  BEHIND="$(git rev-list --count "HEAD..origin/main" 2>/dev/null || echo '?')"
  AHEAD="$(git rev-list --count "origin/main..HEAD" 2>/dev/null || echo '?')"
  add "- **vs origin/main (last fetch)**: behind $BEHIND, ahead $AHEAD"
  if [ "${BEHIND:-0}" != "?" ] && [ "${BEHIND:-0}" -gt 20 ] 2>/dev/null; then
    add "- 🛑 **$BEHIND commits behind origin/main.** Do NOT audit the repo, judge CI,"
    add "  or describe \"what production does\" from this tree. \`git fetch origin main\`"
    add "  and read \`origin/main\`. An audit once ran to completion 124 commits behind"
    add "  and every conclusion in it was wrong."
  fi
fi

# --- Dirty tree? -----------------------------------------------------------
DIRTY="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if [ "${DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  add "- **uncommitted changes**: $DIRTY path(s). The repo's preflight wants a clean"
  add "  tree before a multi-commit batch — check whose they are before you commit."
fi

# --- main IS production ----------------------------------------------------
if [ "$BRANCH" = "main" ]; then
  add ""
  add "- 🔴 **You are ON \`main\`, which IS production and auto-deploys.** Branch before"
  add "  you edit."
fi

printf '%s' "$OUT"
exit 0
