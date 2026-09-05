#!/usr/bin/env bash
# worktree-reaper.sh — SessionStart hook. LAUNCHES the reaper; never IS the reaper.
#
# WHY THIS EXISTS
#   Nothing tears a worktree down when its PR lands, so they reached 98.
#   `scripts/branch_prune.sh` was written for exactly this in July 2026 and is
#   wired into no workflow and no hook, which is the same as not existing. This
#   file is the entrance it never got.
#
# WHY IT ONLY LAUNCHES
#   A SessionStart hook sits on the critical path of every session opening, and
#   the work does not fit that budget. Measured on this repo:
#       gh pr list ................ 2.16 s
#       git worktree list ......... 0.43 s across 30 worktrees
#       one `worktree remove` ..... 10-30 s (each carries node_modules + .venv)
#   So this forks a detached child and returns. Inline, a session start would pay
#   several minutes.
#
# WHY THERE IS ALMOST NO LOGIC HERE
#   .claude/hooks/ is NOT scanned by scripts/drill_registry.py, so anything
#   decided in this file is decided somewhere nothing can drill. A throttle that
#   silently never fires looks identical to one that works. So the throttle, the
#   report and every safety gate live in worktree_reaper.py, which has 20 drill
#   cases; this file only resolves paths and forks.
#
# CONTRACT
#   - FAILS OPEN. Any error exits 0. A cleanup hook must never stop a session.
#   - THROTTLED (--hook-tick, default 6 h). ~14 sessions open against this repo;
#     unthrottled, that is 14 concurrent `gh` calls, i.e. a rate limit not a sweep.
#   - CAPPED (--max 3). It converges over a few sessions instead of blocking one.
#
# KILL SWITCH
#   touch docs/maintenance/REAPER-OFF   (the child re-checks it too)
set -uo pipefail
trap 'exit 0' ERR

# Two caps, because the two jobs cost wildly different amounts. Removing a
# worktree is a recursive delete of node_modules + .venv (10-30 s each); deleting
# a branch is one ref write. Measured 2026-09-02: 0 worktrees were reapable and
# 170 local branches were, so a shared cap would have throttled the free half.
MAX_PER_RUN="${JOB360_REAP_MAX:-3}"
MAX_BRANCHES="${JOB360_REAP_MAX_BRANCHES:-50}"
MIN_IDLE_HOURS="${JOB360_REAP_MIN_IDLE:-24}"
THROTTLE_HOURS="${JOB360_REAP_EVERY_HOURS:-6}"

# The MAIN repo, not this worktree: only it can remove or prune the others.
# (`--show-toplevel` inside a worktree returns that worktree, which would make
# the reaper operate on the wrong repository root.)
COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
[ -n "$COMMON" ] || exit 0
ROOT="$(dirname "$COMMON")"
[ -d "$ROOT" ] || exit 0

[ -f "$ROOT/docs/maintenance/REAPER-OFF" ] && exit 0
[ -f "$ROOT/scripts/worktree_reaper.py" ] || exit 0

STATE="$COMMON/reaper"
mkdir -p "$STATE" 2>/dev/null || exit 0

# Prints what the LAST run did (the child is detached, so this is the only way
# anyone ever learns it ran) and tells us whether another run is due.
# The `if !` matters: a bare call returning 3 would trip the ERR trap above and
# exit before this line ever read it. Same outcome by luck is not the same thing.
if ! python "$ROOT/scripts/worktree_reaper.py" \
     --hook-tick "$STATE" --throttle-hours "$THROTTLE_HOURS" 2>/dev/null; then
  exit 0   # exit 3 = throttled; anything else = something is wrong, do nothing
fi

touch "$STATE/last-run" 2>/dev/null || true

# `--apply` is safe unattended ONLY because of gate 4 (a MERGED pull request,
# asked of GitHub rather than of git ancestry) and gate 6 (the idle floor).
# Without the idle floor this would delete a clean, shipped worktree that another
# session is simply sitting in without typing.
(
  cd "$ROOT" || exit 0
  nohup python scripts/worktree_reaper.py \
    --apply --json \
    --max "$MAX_PER_RUN" \
    --max-branches "$MAX_BRANCHES" \
    --min-idle-hours "$MIN_IDLE_HOURS" \
    >"$STATE/last-run.json" 2>"$STATE/last-run.err" &
) >/dev/null 2>&1 &

exit 0
