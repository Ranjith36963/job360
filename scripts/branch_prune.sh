#!/usr/bin/env bash
# branch_prune.sh — conservative local-branch cleanup.
#
# WHY THIS EXISTS
#   The 2026-07-26 hygiene audit found 76 local branches (only ~14 holding real
#   work), 14 worktrees, and 5 weeks of drift since the repo was last clean.
#   Root cause: nothing tears a branch/worktree down when its PR lands. This is
#   the safe way to catch up without ever risking unmerged work.
#
# SAFETY CONTRACT — a branch is only ever deleted when ALL of these hold:
#   1. it is fully merged into origin/main  (git branch --merged origin/main)
#   2. it is not the branch you are standing on
#   3. it is not checked out in ANY git worktree (another session may own it)
#   4. it is not on the protected list below
#   Deletion always uses `git branch -d` (lowercase) — git itself refuses to
#   delete anything that is not truly merged. `-D` is never used, anywhere.
#
# USAGE
#   scripts/branch_prune.sh            # dry run — lists candidates, deletes nothing
#   scripts/branch_prune.sh --apply    # actually delete the listed branches
set -euo pipefail

APPLY=false
[ "${1:-}" = "--apply" ] && APPLY=true

# Never delete these, even if merged.
PROTECTED="main master develop"

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
  echo "Recovery note: even after deletion, tips stay in 'git reflog' for ~90 days."
  exit 0
fi

echo
failed=0
for b in "${candidates[@]}"; do
  # -d (never -D): git refuses if the branch is not actually merged.
  git branch -d "$b" || { echo "  ! kept $b (git refused — not fully merged)"; failed=$((failed+1)); }
done
echo
echo "Done. Deleted $(( ${#candidates[@]} - failed )) branch(es); kept $failed that git refused."

# Worktrees whose directory is gone: prune the admin entries only.
# This never deletes a directory that still exists on disk.
echo
echo "Pruning worktree metadata for directories that no longer exist…"
git worktree prune -v || true
