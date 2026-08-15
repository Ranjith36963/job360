#!/usr/bin/env bash
# harness_capture_diff.sh — print a git diff, truncated, WITHOUT killing the caller.
#
# WHY THIS EXISTS (the bug it replaces, measured 2026-08-15)
# ---------------------------------------------------------
# The blind checker in repair.yml and pr-repair.yml built its input with:
#
#     set -euo pipefail
#     ...
#     git diff origin/main...HEAD -- backend/ frontend/ | head -800
#
# `head` exits the moment it has its 800 lines and closes the read end of the
# pipe. `git` then writes into a closed pipe, takes SIGPIPE and exits 141.
# `pipefail` promotes 141 to the pipeline's status, and `set -e` kills the step.
#
# So the blind checker was STRUCTURALLY DEAD on exactly the pull requests that
# most needed reviewing: any repair whose diff exceeded 800 lines. Small diffs
# survived only because they fit in the 64 KB pipe buffer, so git finished
# writing before head ever closed it. That is why it looked fine.
#
# THE FIX, and why this one
# -------------------------
# Options considered:
#   `| head -800 || true`   — swallows genuine git failures too; a broken `git
#                             diff` would silently yield an EMPTY diff and the
#                             checker would approve a PR it never saw.
#   `set +o pipefail` around it — fixes the symptom, but leaves a pipe whose
#                             upstream status is discarded, same blind spot.
#   `head -c` / `sed 800q`  — same pipe, same SIGPIPE.
#   THIS: capture to a temp file, then `head` the FILE.
#                             There is no pipe at all, so SIGPIPE is impossible
#                             rather than merely handled. `git diff`'s own exit
#                             status is still checked, so a real git failure is
#                             still loud. And because we hold the whole diff we
#                             can tell the reader HOW MUCH was cut — the old
#                             form truncated silently, so the checker could not
#                             know it was judging a fragment.
#
# Usage:  bash scripts/harness_capture_diff.sh <max-lines> <git-diff args...>
# e.g.    bash scripts/harness_capture_diff.sh 800 origin/main...HEAD -- backend/ frontend/
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <max-lines> <git diff args...>" >&2
  exit 2
fi

max="$1"
shift

case "$max" in
  '' | *[!0-9]*)
    echo "$0: max-lines must be a positive integer, got '$max'" >&2
    exit 2
    ;;
esac

full="$(mktemp)"
# shellcheck disable=SC2064  # expand $full now, on purpose
trap "rm -f '$full'" EXIT

# NO PIPE. If git fails, set -e stops us here and the caller sees a real error
# instead of an empty diff that looks like "nothing changed".
git diff "$@" > "$full"

total="$(wc -l < "$full" | tr -d '[:space:]')"

head -n "$max" "$full"

if [ "$total" -gt "$max" ]; then
  printf '\n[diff truncated: showing the first %s of %s lines. Anything below this point was NOT reviewed.]\n' \
    "$max" "$total"
fi
