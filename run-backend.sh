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
# Try both spellings: many Linux and macOS hosts ship `python3` only, and the
# old single `python` check exited below while a usable interpreter was on PATH.
for name in python python3; do
  if command -v "$name" >/dev/null 2>&1; then
    echo "run-backend.sh: no .venv found under $ROOT/backend — using $name from PATH" >&2
    exec "$name" main.py
  fi
done

# We only reach here when NEITHER `python` nor `python3` is on PATH, so the first
# line of the advice cannot be a python command — it has to be "install one".
# The rest is anchored to $ROOT (copied from a subdirectory it would build the
# venv in the wrong place) and split by platform: the venv puts its interpreter
# in bin/ on POSIX and Scripts/ on Windows, and there is no `pip` binary to glob
# for on Windows — it is `pip.exe`. Calling the venv's own python with -m pip
# sidesteps both.
echo "run-backend.sh: no Python interpreter found on PATH (tried python, python3)." >&2
echo "" >&2
echo "  1. Install Python 3.9 or newer (backend/pyproject.toml: requires-python >=3.9):" >&2
echo "     https://www.python.org/downloads/" >&2
echo "  2. Then create the venv:" >&2
echo "" >&2
echo "     cd \"$ROOT\"" >&2
echo "     # POSIX" >&2
echo "     python3 -m venv backend/.venv && backend/.venv/bin/python -m pip install -e 'backend[dev]'" >&2
echo "     # Windows" >&2
echo "     py -3 -m venv backend/.venv && backend/.venv/Scripts/python.exe -m pip install -e backend[dev]" >&2
exit 1
