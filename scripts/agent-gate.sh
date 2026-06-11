#!/usr/bin/env bash
# agent-gate.sh — the ONLY way to earn a commit.
# Runs the canonical gates and writes a stamp binding the PASS to this exact
# tree state (HEAD + index + working tree). Any edit after the gate run
# invalidates the stamp, so "ran tests, then changed one more thing, then
# committed" is impossible. See agentic-loop/hardening.md.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

tree_fingerprint() {
  { git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin
}

CHANGED="$(git status --porcelain -- backend frontend | awk '{print $2}')"
BACKEND_CHANGED=$(echo "$CHANGED" | grep -c '^backend/' || true)
FRONTEND_CHANGED=$(echo "$CHANGED" | grep -c '^frontend/' || true)

if [ "$BACKEND_CHANGED" -gt 0 ]; then
  echo "[gate] backend suite..."
  (cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py)
fi
if [ "$FRONTEND_CHANGED" -gt 0 ]; then
  echo "[gate] frontend gates..."
  (cd frontend && npm run -s test:unit && npm run -s type-check && npm run -s lint)
fi

mkdir -p "$ROOT/.claude"
tree_fingerprint > "$ROOT/.claude/gate-stamp"
echo "[gate] PASS — stamp written"
