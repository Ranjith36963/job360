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
cd "$(dirname "$0")/backend" || exit 1
exec /d/dev/job360/backend/.venv/Scripts/python.exe main.py
