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

# M7 — API-types drift guard. A backend API-model change OR a frontend change
# must keep frontend/src/lib/api-types.ts (generated from FastAPI's OpenAPI
# schema) in sync with the backend. Regenerate and fail if it differs from
# what's committed/staged. Guarded on the tool being installed so machines
# without the frontend toolchain can still gate backend-only work.
if { [ "$BACKEND_CHANGED" -gt 0 ] || [ "$FRONTEND_CHANGED" -gt 0 ]; } && [ -x "$ROOT/frontend/node_modules/.bin/openapi-typescript" ]; then
  echo "[gate] api-types drift check..."
  bash "$ROOT/scripts/gen-api-types.sh" >/dev/null
  git diff --exit-code -- frontend/openapi.json frontend/src/lib/api-types.ts \
    || { echo "[gate] FAIL: API types drifted from the backend schema — run 'npm run gen:types' in frontend/ and commit the result." >&2; exit 1; }
fi

if [ "$FRONTEND_CHANGED" -gt 0 ]; then
  echo "[gate] frontend gates..."
  (cd frontend && npm run -s test:unit && npm run -s type-check && npm run -s lint)
fi

mkdir -p "$ROOT/.claude"
tree_fingerprint > "$ROOT/.claude/gate-stamp"
echo "[gate] PASS — stamp written"
