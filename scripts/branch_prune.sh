#!/usr/bin/env bash
# branch_prune.sh — conservative local-branch cleanup.
#
# ⚠️ SUPERSEDED FOR AUTOMATIC USE (2026-09-02). Use scripts/worktree_reaper.py.
#
#   This script is still SAFE — nothing below deletes unmerged work. It is simply
#   BLIND on this repo, because check #1 asks the wrong oracle:
#
#       git branch --merged origin/main   ->    1 branch
#       gh pr list --state merged         ->  343 branches
#
#   The repo SQUASH-merges. A squash rewrites the commits, so a shipped branch is
#   never an ancestor of main and never appears in `--merged`. That is why this
#   file ran for five weeks and collected nothing while the estate went from 14 to
#   98 worktrees: it was not broken, it was looking at a number that is always 1.
#
#   scripts/worktree_census.py computes the right answer with `git cherry`
#   (patch-ids, which survive a squash), and scripts/worktree_reaper.py acts on it
#   from a SessionStart hook. Keep this for the manual, deliberately conservative
#   case; do not wire it into automation expecting it to find anything.
#
# WHY THIS EXISTS
#   The 2026-07-26 hygiene audit found 76 local branches (only ~14 holding real
#   work), 14 worktrees, and 5 weeks of drift since the repo was last clean.
#   Root cause: nothing tears a branch/worktree down when its PR lands. This is
#   the safe way to catch up without ever risking unmerged work.
#
# SAFETY CONTRACT — a branch is only ever deleted when ALL of these hold:
#   1. it is listed by `git branch --merged origin/main`
#   2. its tip is an ANCESTOR of origin/main, re-verified per branch immediately
#      before deletion (`git merge-base --is-ancestor`) — see WARNING below
#   3. it is not the branch you are standing on
#   4. it is not checked out in ANY git worktree (another session may own it)
#   5. it is not on the protected list below
#   6. its tip SHA has been appended to docs/maintenance/REAPED.log first
#
# ⚠️ TWO CORRECTIONS (2026-07-27 adversarial review — both sandbox-verified):
#
#   `git branch -d` IS NOT A SAFETY NET. Git deletes a branch that is merged into
#   its UPSTREAM even when it is NOT merged into HEAD — it prints a warning and
#   exits 0. Any pushed-but-unmerged branch sails straight through. That is why
#   check #2 above exists: the explicit ancestor test is the real guard, not `-d`.
#
#   REFLOG IS NOT A 90-DAY UNDO. `git branch -d` deletes that branch's own reflog
#   with it, and HEAD reflogs are per-worktree — so a branch never checked out
#   here has no reflog here at all. The real recovery index is REAPED.log (the
#   tip SHAs, committed to git), plus the object grace period (gc.pruneExpire
#   default: 2 weeks), NOT 90 days.
#
# USAGE
#   scripts/branch_prune.sh            # dry run — lists candidates, deletes nothing
#   scripts/branch_prune.sh --apply    # actually delete the listed branches
set -euo pipefail

APPLY=false
[ "${1:-}" = "--apply" ] && APPLY=true

# Never delete these, even if merged.
PROTECTED="main master develop"

# Kill switch — if this file exists, the script is inert.
ROOT="$(git rev-parse --show-toplevel)"
if [ -f "$ROOT/docs/maintenance/REAPER-OFF" ]; then
  echo "REAPER-OFF present — doing nothing."
  exit 0
fi

# Single-execution lock: a merge hook and the weekly cron must never run together.
LOCK="$ROOT/.git/reaper.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "Another reaper run holds $LOCK — exiting."
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# Runaway cap: an unexpectedly large sweep reports instead of deleting.
MAX_DELETE=10
REAPED_LOG="$ROOT/docs/maintenance/REAPED.log"

echo "Fetching + pruning stale remote refs…"
git fetch origin --prune --quiet

current="$(git rev-parse --abbrev-ref HEAD)"

# Every branch currently checked out in a worktree (main repo + .claude/worktrees/*).
# Deleting one of these would break another session's working directory.
worktree_branches="$(git worktree list --porcelain | sed -n 's#^branch refs/heads/##p')"

merged="$(git branch --merged origin/main --format='%(refname:short)')"

candidates=()
skipped_worktree=()
for b in $merged; do
  [ "$b" = "$current" ] && continue
  case " $PROTECTED " in *" $b "*) continue ;; esac
  if printf '%s\n' "$worktree_branches" | grep -qx -- "$b"; then
    skipped_worktree+=("$b")
    continue
  fi
  candidates+=("$b")
done

if [ ${#skipped_worktree[@]} -gt 0 ]; then
  echo
  echo "Skipped (merged, but owned by a live worktree — another session may be using it):"
  printf '  - %s\n' "${skipped_worktree[@]}"
fi

if [ ${#candidates[@]} -eq 0 ]; then
  echo
  echo "Nothing to prune. No merged, unowned local branches found."
  exit 0
fi

echo
echo "Merged into origin/main and owned by no worktree — ${#candidates[@]} candidate(s):"
printf '  - %s\n' "${candidates[@]}"

if [ "$APPLY" != true ]; then
  echo
  echo "DRY RUN — nothing deleted. Re-run with --apply to delete the above."
  echo "Recovery: tip SHAs are written to docs/maintenance/REAPED.log BEFORE deletion."
  echo "Do NOT rely on reflog — 'git branch -d' deletes the branch's own reflog with it,"
  echo "and HEAD reflogs are per-worktree (a branch never checked out here has none)."
  exit 0
fi

# Runaway guard: a sweep this large is a bug or a surprise. Report, don't delete.
if [ ${#candidates[@]} -gt "$MAX_DELETE" ]; then
  echo
  echo "REFUSING: ${#candidates[@]} candidates exceeds the cap of $MAX_DELETE."
  echo "This is deliberate — a sweep this size should be read by a human first."
  echo "Delete them in smaller batches, or raise MAX_DELETE knowingly."
  exit 1
fi

echo
mkdir -p "$(dirname "$REAPED_LOG")"
failed=0
deleted=0
for b in "${candidates[@]}"; do
  tip="$(git rev-parse "$b")"

  # THE REAL GUARD. `-d` is not one: git deletes a branch merged into its UPSTREAM
  # even when it is not merged into HEAD (warning + exit 0). Re-verify ancestry per
  # branch, immediately before deleting — not from the batch snapshot above.
  if ! git merge-base --is-ancestor "$tip" origin/main; then
    echo "  ! kept $b — tip $tip is NOT an ancestor of origin/main (would have been lost)"
    failed=$((failed+1))
    continue
  fi

  # Recovery index is written BEFORE the deletion, never after.
  printf '%s  %s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$tip" "$b" >> "$REAPED_LOG"

  if git branch -d "$b" >/dev/null 2>&1; then
    deleted=$((deleted+1))
  else
    echo "  ! kept $b (git refused)"
    failed=$((failed+1))
  fi
done
echo
echo "Done. Deleted $deleted branch(es); kept $failed."
echo "Recovery index: $REAPED_LOG (commit it — it is the only reliable undo)."

# Worktrees whose directory is gone: prune the admin entries only.
# This never deletes a directory that still exists on disk.
echo
echo "Pruning worktree metadata for directories that no longer exist…"
git worktree prune -v || true
