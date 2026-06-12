# Job360 Agent Team — Setup Guide

Built against CODEBASE_REPORT.md (2026-06-11). Apply in this exact order. Steps 1–2 are human-only. Everything after can be done by Claude Code.

## Fleet shape (sized to your machine: 8 cores / 16 GB)

| Role | Where it runs | Owns | Never touches |
|---|---|---|---|
| Scout | main checkout, read-only | MISSIONS.md candidate entries | code, servers, DB writes |
| Worker A, B (optionally C) | own worktree + own venv + own DB_PATH | its mission's files, its branch | servers, ports, chroma, BACKLOG/JOURNAL, .env |
| Integrator | main checkout | jobs.db, ports 8000/3000, Playwright, BACKLOG.md, JOURNAL.md, staging branch, server restarts | worker branches' internals |
| Health | main checkout (after integrator) | STATUS-DAILY.md | code |
| You | 15 min/day | staging → main merge, NEEDS-HUMAN queue | everything else |

Key change from the old maintain loop: workers verify with TESTS ONLY in their worktrees. Live verification (/verify-job360, browser, real DB) happens ONCE, at the integrator, on the merged result — because the report shows only one server pair + one browser profile can exist on this machine.

## Step 1 — Human: clear the deck (5 minutes)
1. Decide the 3-file WIP (main.py run_log user_id + dispatcher error string + test). It passes tests per the audit — if you want it: `git add -A && git commit -m "feat(channels): friendlier delivery errors; wire run_log.user_id"`. If not: `git checkout -- .`
2. Restart the backend server so committed fixes (jobicy) actually serve.
3. Prune relics: `git worktree remove .claude/worktrees/generator --force`, same for reviewer; delete the 11 origin/worktree-agent-* branches; delete stray root `data/chroma/` and `backend/None`.

## Step 2 — Hardening (apply hardening.md)
Do this BEFORE creating any worker. It moves three rules from prose to machinery:
- `git push` becomes impossible (permission deny)
- `git commit` is blocked by a PreToolUse hook unless a fresh gate stamp exists
- the gate stamp is only written by `scripts/agent-gate.sh` after a real green run

## Step 3 — Install the team
1. Put `MISSIONS.md` in `docs/maintenance/`.
2. Install skills: `scout`, `worker`, `integrator`, `health` into `.claude/skills/<name>/SKILL.md`. The integrator skill REPLACES `maintain` (it absorbs it).
3. Create worker worktrees:
   ```
   git worktree add .claude/worktrees/worker-a -b agent/m1-sources
   git worktree add .claude/worktrees/worker-b -b agent/m3-frontend
   ```
   In each: create a venv and `pip install -e backend` (own venv per worktree — the editable-install gotcha from your verify skill). No DB redirection is needed or possible via env: `settings.py` derives the DB path from file location, workers never run servers, and the pytest fixtures already redirect the DB per-test (hermetic).

## Step 4 — Launch
- Terminal 1 (main checkout): integrator session. Keep your existing 2h in-session cron but point it at `/integrator` instead of `/maintain`.
- Terminal 2: `cd .claude/worktrees/worker-a && claude` → `/worker` → it claims M1 from MISSIONS.md and runs continuously.
- Terminal 3: worker-b → `/worker` → claims M3.
- Scout: run `/scout` in the integrator session once per day (or its own cron at a different offset, e.g. `41 */6 * * *`).
- Health: `/health` daily, after the morning integrator round.

For durability beyond open terminals (your known constraint): Windows Task Scheduler running headless `claude -p "/integrator"` every 2h replaces the in-session cron. Workers are fine as interactive sessions for now — they're the cheap thing to restart.

## Daily rhythm
Morning, 15 minutes: read STATUS-DAILY.md → skim `git log loop/staging` → merge staging to your branch/main → answer the NEEDS-HUMAN list in MISSIONS.md. Done.

## Known human-pending decisions (from the report — answer when ready)
API keys (jsearch/jooble/careerjet/findwork), paid LLM tier vs current free Cerebras/Groq, the 50→49 source rotation approval, SMTP/notification credentials, OpenAPI→TS codegen adoption, SQLite→Postgres timing, any migration (telemetry columns for mission M2-3 are blocked on you).
