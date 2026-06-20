#!/usr/bin/env bash
# health-daily.sh — scheduled wrapper for the Job360 `health` skill (Linux/Mac/Git-Bash).
# Archives yesterday's report, then runs the health skill headless to write a fresh one.
# Register it with cron — see docs/maintenance/HEALTH-SCHEDULE.md.
#
# Requires: `claude` CLI on PATH. Best run with the backend + frontend servers up
# (otherwise Pillars 1/3 report UNKNOWN, which is honest, not a failure).
set -euo pipefail

# Repo root = two levels up from this script (scripts/ -> repo root).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAILY="$REPO/docs/maintenance/STATUS-DAILY.md"
HISTORY="$REPO/docs/maintenance/STATUS-HISTORY.md"

cd "$REPO"

# 1. Archive the existing report into history (skill rule: overwrite daily, keep yesterday).
if [[ -f "$DAILY" ]]; then
  { printf '\n\n---\n# Archived %s\n\n' "$(date '+%Y-%m-%d %H:%M')"; cat "$DAILY"; } >> "$HISTORY"
fi

# 2. Run the health skill headless. The skill writes the new STATUS-DAILY.md itself.
claude -p "Run the /health skill: do the daily three-pillar system check and overwrite docs/maintenance/STATUS-DAILY.md with the report. Collect verbatim evidence. Mark any check that cannot run as UNKNOWN with the reason."

echo "health-daily: done — see $DAILY"
