#!/usr/bin/env bash
# agent-gate.sh — the ONLY way to earn a commit.
# Runs the gates and writes a stamp binding the PASS to this exact tree state
# (HEAD + index + working tree). Any edit after the gate run invalidates the
# stamp, so "ran tests, then changed one more thing, then committed" is
# impossible. See agentic-loop/hardening.md.
#
# Tiered (2026-07-15):
#   default  — TARGETED backend tests: each changed backend/src file maps to
#              its tests/test_<name>.py; changed test files run as-is; ruff on
#              changed .py files. Falls back to the FULL suite when no changed
#              file maps to a test (conservative default).
#   --full   — the original canonical full backend suite (~90 min on Windows).
#              Use before merges/releases or when touching core plumbing.
# CI (ci-offline.yml) runs the full suite on Linux for every PR either way —
# the local gate is the fast shift-left check, CI is the final word.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

MODE="fast"
[ "${1:-}" = "--full" ] && MODE="full"

tree_fingerprint() {
  { git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin
}

CHANGED="$(git status --porcelain -- backend frontend | awk '{print $2}')"
BACKEND_CHANGED=$(echo "$CHANGED" | grep -c '^backend/' || true)
FRONTEND_CHANGED=$(echo "$CHANGED" | grep -c '^frontend/' || true)

if [ "$BACKEND_CHANGED" -gt 0 ]; then
  if [ "$MODE" = "full" ]; then
    echo "[gate] backend suite (full)..."
    (cd backend && python -m pytest -q -p no:randomly)
  else
    # Map each changed backend .py file to its test file by name.
    # backend/src/**/foo.py -> tests/test_foo.py; changed test files run as-is.
    TARGETS=""
    for f in $(echo "$CHANGED" | grep '^backend/' | grep '\.py$' || true); do
      base="$(basename "$f" .py)"
      case "$f" in
        backend/tests/test_*.py) [ -f "$f" ] && TARGETS="$TARGETS ${f#backend/}" ;;
        backend/tests/*) ;; # conftest/fixtures: no direct target; fall through
        *) [ -f "backend/tests/test_${base}.py" ] && TARGETS="$TARGETS tests/test_${base}.py" ;;
      esac
    done
    TARGETS="$(echo "$TARGETS" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')"
    LINT_FILES="$(echo "$CHANGED" | grep '^backend/.*\.py$' | sed 's|^backend/||' | while read -r p; do [ -f "backend/$p" ] && echo "$p"; done | tr '\n' ' ')"
    if [ -n "${TARGETS// /}" ]; then
      echo "[gate] backend targeted tests: $TARGETS"
      (cd backend && python -m pytest -q -p no:randomly $TARGETS)
      if [ -n "${LINT_FILES// /}" ]; then
        echo "[gate] ruff on changed files..."
        (cd backend && ruff check $LINT_FILES)
      fi
    else
      echo "[gate] no changed file maps to a test — running FULL backend suite (conservative fallback)"
      (cd backend && python -m pytest -q -p no:randomly)
    fi
  fi
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
echo "[gate] PASS ($MODE) — stamp written"
