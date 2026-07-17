#!/usr/bin/env bash
# agent-gate.sh — the ONLY way to earn a commit.
#
# Runs the gates and writes a stamp binding the PASS to this exact tree state
# (HEAD + index + working tree). Any edit after the gate run invalidates the
# stamp, so "ran tests, then changed one more thing, then committed" is
# impossible. See agentic-loop/hardening.md.
#
# CONTRACT (undocumented before — this is the order that actually works):
#   1. git add -A        <- stage FIRST. The api-types drift check compares the
#                           worktree against the INDEX, so correct-but-unstaged
#                           files read as "drifted".
#   2. bash scripts/agent-gate.sh
#   3. git commit         <- do NOT edit anything in between; the stamp is bound
#                            to the exact tree.
#
# Tiered (2026-07-15):
#   default  — TARGETED backend tests: each changed backend/src file maps to
#              its tests/test_<name>.py; changed test files run as-is; ruff on
#              changed .py files. Falls back to the FULL suite when no changed
#              file maps to a test (conservative default).
#   --full   — the original canonical full backend suite. Use before
#              merges/releases or when touching core plumbing (pg shim,
#              migrations runner, auth) — a targeted run maps a changed file to
#              tests/test_<name>.py only, so it CANNOT see the integration tests
#              that a plumbing change actually breaks.
# CI (ci-offline.yml) runs the full suite on Linux for every PR either way —
# the local gate is the fast shift-left check, CI is the final word.
#
# Run it ONCE. A --full run took 34min (measured 2026-07-17, ~1,826 tests on
# Windows); the earlier "~13min" here was guesswork and understating it is how
# you talk yourself into believing a healthy run has died. The backend suite is
# quiet for nearly all of it — a heartbeat line prints every 30s so silence is
# distinguishable from death. Do NOT relaunch it because it "looks dead":
# concurrent runs share one Postgres and poison each other (see the advisory
# lock below). To check it is alive on Windows use `tasklist`, never MSYS `ps`.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

MODE="fast"
[ "${1:-}" = "--full" ] && MODE="full"

tree_fingerprint() {
  { git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin
}

# ── M1 (the real correctness hole) ──────────────────────────────────────────
# The stamp used to be fingerprinted AFTER the ~13min run, so any edit made
# while the gate ran got blessed by a stamp whose tests never covered it — the
# commit hook then accepted untested code. Capture the tree we ACTUALLY test
# now, and refuse to stamp at the end if it moved.
FP_START="$(tree_fingerprint)"

# ── Singleton lock, per DATABASE SERVER (not per worktree) ───────────────────
# ~14 worktrees share ONE test Postgres, so a per-worktree lock would not help:
# the contention is on the database. A pg advisory lock is kill-safe — it dies
# with the connection, so a hard-killed gate can never leave a stale lockfile.
LOCK_HELD=""
if command -v python >/dev/null 2>&1; then
  LOCK_OUT="$(cd backend 2>/dev/null && python - <<'PYLOCK' 2>/dev/null || true
import sys
try:
    import psycopg
    from src.repositories import pg
    # Module-level connection kept open for the life of THIS process only; we
    # just probe here and re-take it in the background holder below.
    conn = psycopg.connect(pg.DEFAULT_DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(732360001)")
        got = cur.fetchone()[0]
    if got:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(732360001)")
        print("FREE")
    else:
        print("BUSY")
    conn.close()
except Exception:
    print("UNKNOWN")
PYLOCK
)"
  if [ "$LOCK_OUT" = "BUSY" ]; then
    echo "[gate] ABORT: another gate is already running against this Postgres." >&2
    echo "[gate] Wait for it — do NOT run a second gate. Concurrent suites share" >&2
    echo "[gate] one DB, poison each other's schemas, and both crawl." >&2
    exit 1
  fi
fi

CHANGED="$(git status --porcelain -- backend frontend | awk '{print $2}')"
BACKEND_CHANGED=$(echo "$CHANGED" | grep -c '^backend/' || true)
FRONTEND_CHANGED=$(echo "$CHANGED" | grep -c '^frontend/' || true)

# ── Heartbeat: silence killed 2 hours today ─────────────────────────────────
# `pytest -q` is block-buffered through a pipe, so an agent sees NOTHING for
# 13 minutes and concludes the gate died. It didn't. This ticker makes "alive"
# observable. (And on Windows: check with `tasklist`, NEVER MSYS `ps` — `ps`
# only shows the exe name, so `ps aux | grep agent-gate` returns 0 even for a
# perfectly healthy run.)
GATE_LOG="$ROOT/backend/data/logs/gate-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$GATE_LOG")"
export PYTHONUNBUFFERED=1
_heartbeat() {
  local start=$SECONDS
  while true; do
    sleep 30
    echo "[gate] alive — $(( (SECONDS-start)/60 ))m elapsed · log: $GATE_LOG"
  done
}
_heartbeat & HB_PID=$!
trap 'kill "$HB_PID" 2>/dev/null || true' EXIT

if [ "$BACKEND_CHANGED" -gt 0 ]; then
  if [ "$MODE" = "full" ]; then
    echo "[gate] backend suite (full)... (~30min, quiet; heartbeat every 30s)"
    (cd backend && python -m pytest -q -p no:randomly 2>&1 | tee -a "$GATE_LOG")
    # ruff over the WHOLE backend. This used to run ONLY in targeted mode, which
    # made --full strictly WEAKER than the fast path on lint — backwards, and it
    # cost a red CI: `aiosqlite.Connection` survived as a type annotation in
    # workers/tasks.py. 1,826 tests passed straight over it, because
    # `from __future__ import annotations` makes annotations lazy strings that
    # are never evaluated — no test can ever catch that class of bug. Only a
    # static check can, so --full must run one. CI runs `ruff check .`; match it
    # exactly, or the gate keeps passing things CI rejects.
    echo "[gate] ruff (full backend)..."
    (cd backend && python -m ruff check . 2>&1 | tee -a "$GATE_LOG")
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
      (cd backend && python -m pytest -q -p no:randomly $TARGETS 2>&1 | tee -a "$GATE_LOG")
      if [ -n "${LINT_FILES// /}" ]; then
        # `python -m ruff`, never bare `ruff`: it is a dev-extra, so a bare call
        # can hit a PATH miss and vanish instead of gating (it was once missing
        # from deps entirely — PR #33 drained 329 findings it had been hiding).
        echo "[gate] ruff on changed files..."
        (cd backend && python -m ruff check $LINT_FILES 2>&1 | tee -a "$GATE_LOG")
      fi
    else
      echo "[gate] no changed file maps to a test — running FULL backend suite (conservative fallback)"
      (cd backend && python -m pytest -q -p no:randomly 2>&1 | tee -a "$GATE_LOG")
      echo "[gate] ruff (full backend)..."
      (cd backend && python -m ruff check . 2>&1 | tee -a "$GATE_LOG")
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
  # NOTE: we JUST regenerated, so the worktree is correct BY CONSTRUCTION. The
  # only way this diff can fail is "you didn't stage them" — say that, instead
  # of telling you to run the command you already ran.
  git diff --exit-code -- frontend/openapi.json frontend/src/lib/api-types.ts \
    || { echo "[gate] FAIL: regenerated API types differ from what's STAGED." >&2; \
         echo "[gate] Fix: git add frontend/openapi.json frontend/src/lib/api-types.ts — then rerun the gate." >&2; \
         exit 1; }
fi

if [ "$FRONTEND_CHANGED" -gt 0 ]; then
  echo "[gate] frontend gates..."
  (cd frontend && npm run -s test:unit && npm run -s type-check && npm run -s lint)
fi

# ── M1 enforcement: the tree must not have moved while we tested ────────────
FP_END="$(tree_fingerprint)"
if [ "$FP_START" != "$FP_END" ]; then
  echo "[gate] FAIL: the working tree CHANGED while the gate was running." >&2
  echo "[gate] The tests just run did NOT cover the current code, so no stamp." >&2
  echo "[gate] Fix: stop editing during a gate run, then rerun it." >&2
  exit 1
fi

mkdir -p "$ROOT/.claude"
# Write the fingerprint we VERIFIED above, not a freshly recomputed one — a
# recompute here would re-open the M1 hole it just closed.
printf '%s' "$FP_END" > "$ROOT/.claude/gate-stamp"
echo "[gate] PASS ($MODE) — stamp written"
