#!/usr/bin/env bash
# gate-fresh-db.sh — run the commit gate against a FRESH, throwaway Postgres
# database, the way CI does. The shared dev DB carries data + partial migration
# state that breaks empty-DB-assuming tests; a clean per-run DB avoids that.
#
# Usage:  bash scripts/gate-fresh-db.sh
# Result: on success, writes the same .claude/gate-stamp scripts/agent-gate.sh
#         does, so the commit hook is satisfied.
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

PGADMIN="${PGADMIN_URL:-postgresql://job360:job360dev@localhost:5433/postgres}"
DB="job360_gate_$$"                       # unique per run
GATE_URL="postgresql://job360:job360dev@localhost:5433/${DB}"

_psql_admin() { python -c "import psycopg,sys;psycopg.connect('${PGADMIN}',autocommit=True,connect_timeout=10).execute(sys.argv[1])" "$1"; }

cleanup() { _psql_admin "DROP DATABASE IF EXISTS ${DB} WITH (FORCE)" 2>/dev/null || true; }
trap cleanup EXIT

echo "[fresh-gate] creating throwaway DB ${DB}"
_psql_admin "DROP DATABASE IF EXISTS ${DB} WITH (FORCE)"
_psql_admin "CREATE DATABASE ${DB} OWNER job360"

echo "[fresh-gate] running the gate against the fresh DB..."
DATABASE_URL="${GATE_URL}" bash "${ROOT}/scripts/agent-gate.sh"
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "[fresh-gate] PASS — stamp written; safe to commit."
else
  echo "[fresh-gate] FAIL — gate did not pass (rc=${rc})." >&2
fi
exit "$rc"
