# Health schedule — wiring the daily `health` skill to a real cron
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

> ## ⚠️ NOT RUNNING — measured 2026-08-24
>
> This document describes the wiring in the present tense. Nothing is running.
>
> `STATUS-DAILY.md` still reads **"Job360 daily status — 2026-06-19 … First
> report."** It has produced exactly ONE report, 66 days ago. A later commit
> (#348, 2026-08-21) touched the file, but that was the structure refactor
> MOVING it — `git log` on a path shows moves, not content, which is how this
> looked alive from the outside.
>
> So the schedule below is a PLAN, not a description. Either register it or
> delete the skill; what must not continue is a doc that reads as though a
> watchdog is watching. That is worse than no watchdog, because it stops anyone
> from looking.
>
> This is the repo's own law arriving again: an artifact with no notifier dies.

The `health` skill writes `STATUS-DAILY.md` (a 5-minute three-pillar system check). It is
only useful if it runs on a schedule. This is how to wire it. The skill is NOT deleted —
it is wired.

## What runs

`scripts/health-daily.ps1` (Windows) / `scripts/health-daily.sh` (Linux/Mac):
1. archives the current `STATUS-DAILY.md` into `STATUS-HISTORY.md`, then
2. runs the `health` skill headless (`claude -p ...`), which overwrites `STATUS-DAILY.md`.

**Run it best with the servers up** (`python main.py` + `npm run dev`). Without them,
Pillars 1 and 3 honestly report UNKNOWN instead of a guessed color.

## Register the real schedule

### Windows (Task Scheduler) — run once, in an elevated PowerShell

Runs every day at **08:37** local. Off-peak minute on purpose (not :00).

```powershell
schtasks /create /tn "Job360 Health" /sc daily /st 08:37 /tr `
  "powershell -NoProfile -ExecutionPolicy Bypass -File `"D:\dev\job360\scripts\health-daily.ps1`"" /f
```

Verify / run-now / remove:

```powershell
schtasks /query  /tn "Job360 Health"      # confirm it exists
schtasks /run    /tn "Job360 Health"      # fire it now (smoke test)
schtasks /delete /tn "Job360 Health" /f   # OFF-SWITCH: stop the daily run
```

### Linux / Mac (crontab)

```bash
# 08:37 daily — edit with: crontab -e
37 8 * * * /path/to/job360/scripts/health-daily.sh >> /path/to/job360/docs/maintenance/health-cron.log 2>&1
```

Off-switch: `crontab -e` and delete the line.

## In-session alternative (no OS task)

If you keep a Claude Code session open, you can instead ask it to schedule the check for
the lifetime of that session. Note: harness cron jobs are session-bound and recurring ones
auto-expire after 7 days — they are NOT a permanent schedule. The OS task above is the
permanent one.

## Decision: schedule or delete?

The loop is currently PAUSED (M1–M8 owner-confirmed complete, 2026-06-13). A daily health
run still has value as an early-warning tripwire even with the loop paused — it catches a
dead source, a stale catalog, or a broken auth path before you notice. Keep it scheduled.
If you stop running the system entirely, use the off-switch above rather than letting the
skill rot unwired.
