#!/usr/bin/env bash
# review-stamp.sh — records "code + security review done" for the CURRENT tree.
# Run it only after the reviews ran and every real finding was fixed.
# Same fingerprint as scripts/agent-gate.sh, so any later edit invalidates it.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
git add -A -- backend frontend
FP="$({ git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin)"
mkdir -p .claude
printf '%s' "$FP" > .claude/review-stamp
echo "[review-stamp] recorded for tree ${FP:0:12}"
