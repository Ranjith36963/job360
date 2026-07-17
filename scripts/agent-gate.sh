#!/usr/bin/env bash
# agent-gate.sh — the ONLY way to earn a commit.
#
# Runs the canonical gates and writes a stamp binding the PASS to this exact
# tree state (HEAD + index + working tree). Any edit after the gate run
# invalidates the stamp, so "ran tests, then changed one more thing, then
# committed" is impossible. See agentic-loop/hardening.md.
#
# CONTRACT (undocumented before — this is the order that actually works):
#   1. git add -A        <- stage FIRST. The api-types drift check compares the
#                           worktree against the INDEX, so correct-but-unstaged
#                           files read as "drifted".
#   2. bash scripts/agent-gate.sh
#   3. git commit         <- do NOT edit anything in between; the stamp is bound
#                            to the exact tree.
#
# Run it ONCE. It takes ~13 minutes and the backend suite is quiet for most of
# it. A heartbeat line prints every 30s so silence is distinguishable from
# death. Do NOT relaunch it because it "looks dead" — concurrent runs share one
# Postgres and poison each other (see the advisory lock below).
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

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
  echo "[gate] backend suite... (~12min, quiet; heartbeat every 30s)"
  (cd backend && python -m pytest -q -p no:randomly 2>&1 | tee -a "$GATE_LOG")
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
printf '%s' "$FP_END" > "$ROOT/.claude/gate-stamp"
echo "[gate] PASS — stamp written"
