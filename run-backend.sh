#!/usr/bin/env bash
# Local backend for the design pass.
#
# MUST be `python main.py`, not `uvicorn main:app`. On Windows, Python defaults
# to the ProactorEventLoop and psycopg refuses to run async on it
# ("Psycopg cannot use the 'ProactorEventLoop'"). backend/main.py:17-30 exists
# precisely to install a SelectorEventLoop first; invoking uvicorn directly
# skips that and the app dies during startup with an InterfaceError.
#
# That also pins the port to 8000 (main.py:27), so this script does not choose one.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

# Resolve the interpreter from THIS checkout rather than a hardcoded path, so
# the script works from any clone and on POSIX hosts, not only the machine it
# was written on. VIRTUAL_ENV wins if one is already active.
for candidate in \
  "${VIRTUAL_ENV:-}/bin/python" \
  "${VIRTUAL_ENV:-}/Scripts/python.exe" \
  "$ROOT/backend/.venv/bin/python" \
  "$ROOT/backend/.venv/Scripts/python.exe" \
  "$ROOT/.venv/bin/python" \
  "$ROOT/.venv/Scripts/python.exe"
do
  if [ -x "$candidate" ]; then
    exec "$candidate" main.py
  fi
done

# Nothing checkout-local: fall back to whatever python is on PATH, and say so
# rather than failing with an obscure "command not found" later.
if command -v python >/dev/null 2>&1; then
  echo "run-backend.sh: no .venv found under $ROOT/backend — using python from PATH" >&2
  exec python main.py
fi

echo "run-backend.sh: no Python interpreter found. Create one with:" >&2
echo "  python -m venv backend/.venv && backend/.venv/*/pip install -e 'backend[dev]'" >&2
exit 1
