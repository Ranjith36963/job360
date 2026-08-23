# health-daily.ps1 — scheduled wrapper for the Job360 `health` skill (Windows).
# Archives yesterday's report, then runs the health skill headless to write a fresh one.
# Register it with Windows Task Scheduler — see docs/harness/maintenance/HEALTH-SCHEDULE.md.
#
# Requires: `claude` CLI on PATH. Best run with the backend + frontend servers up
# (otherwise Pillars 1/3 report UNKNOWN, which is honest, not a failure).

$ErrorActionPreference = "Stop"

# Repo root = two levels up from this script (scripts/ -> repo root).
$Repo   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Daily  = Join-Path $Repo "docs\maintenance\STATUS-DAILY.md"
$History = Join-Path $Repo "docs\maintenance\STATUS-HISTORY.md"

Set-Location $Repo

# 1. Archive the existing report into history (skill rule: overwrite daily, keep yesterday).
if (Test-Path $Daily) {
    Add-Content -Path $History -Value "`n`n---`n# Archived $(Get-Date -Format 'yyyy-MM-dd HH:mm')`n"
    Get-Content $Daily | Add-Content -Path $History
}

# 2. Run the health skill headless. The skill writes the new STATUS-DAILY.md itself.
$prompt = "Run the /health skill: do the daily three-pillar system check and overwrite docs/harness/maintenance/STATUS-DAILY.md with the report. Collect verbatim evidence. Mark any check that cannot run as UNKNOWN with the reason."
claude -p $prompt

Write-Host "health-daily: done — see $Daily"
