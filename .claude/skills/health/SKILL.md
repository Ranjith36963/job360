---
name: health
description: Job360 health: daily system check of all three pillars against GREEN/AMBER/RED definitions, written to docs/harness/maintenance/STATUS-DAILY.md with verbatim evidence. Use for the daily health report.
---
<!-- doc: LIVING -->

# Health — daily system check (.claude/skills/health/SKILL.md)

You are the Job360 health checker. You ignore the backlog and missions entirely — you test the SYSTEM against each pillar's definition of healthy, once per day, and write ONE report the human reads in 5 minutes. Read-only on code; you may run servers/probes via the integrator session's resources (run only when the integrator is idle — check the lock).

**Schedule: NOT RUNNING — this skill is invoked by hand.** The Windows scheduled task documented in `docs/harness/maintenance/HEALTH-SCHEDULE.md` was **never registered** — verified 2026-07-27: `schtasks /query /tn "Job360 Health"` returns "cannot find the file specified". Do not trust a schedule claim in a doc; check the scheduler.

**Do not run `scripts/health-daily.{ps1,sh}` — both are BROKEN.** They point their archive path at `docs/maintenance/`, a directory that does not exist (the reports live in `docs/harness/maintenance/`). Under `set -euo pipefail` the `.sh` dies on the archive append and never reaches its `claude -p` line; the `.ps1` has the same wrong path for *both* the daily and history files. Invoke this skill directly until they are fixed.

**MODEL POLICY (owner-mandated):** health CHECKS run on **Sonnet** (`model: "sonnet"` explicit when dispatched); the final verdict paragraph in STATUS-DAILY.md is written by the integrator session. Clerical formatting → Haiku if dispatchable. Degradation under constrained usage: step down one tier and note it in the report header.

## Checks (run all, collect verbatim evidence)

### Pillar 1 — Ingestion
- Trigger one fresh pipeline run (POST /api/search as the test user). Record: sources_queried, returned>0 count, errored count, duration, new jobs.
- GREEN: ≥20 productive sources, 0 errors from sources not on the known-broken list, duration <300s.
- AMBER: productive 15–19 OR new errored source not previously known.
- RED: productive <15 OR any previously-working source newly dead OR latest job in DB older than 48h.

### Pillar 2 — Matching
- Confirm judged coverage: user_feed rows with llm_matched_at vs shortlist size for the test user.
- Spot-check ranking: GET /api/jobs authed — is rank 1 the highest llm_fit_score? Do any intern/mismatch roles outrank senior fits?
- Run the accuracy harness sample if cheap (<2 min).
- GREEN: full judged coverage on the shortlist, ranking honors the judge, no engine flag drift (MATCHER/ENRICHMENT/SEMANTIC flags match documented intent).
- RED: ranking ignores the judge, judged coverage 0 on a fresh feed, or any engine throwing.

### Pillar 3 — Delivery
- Auth round-trip: POST /api/auth/register (new account) **then always** POST /api/auth/login,
  and only then GET /api/auth/me carrying the `job360_session` cookie.
  - `/register` deliberately does **not** log you in — `backend/src/api/routes/auth.py:208-212`
    ("NEITHER path sets one — the user signs in next"), and it returns `RegisterResponse()`
    with no `_set_session_cookie`. The only cookie-setter on this flow is `/login`
    (`backend/src/api/routes/auth.py:296`). Skipping the login step gets a 401 from `/me`, which reads as auth
    broken on a perfectly healthy system.
  - The route is `/api/auth/me` — `backend/src/api/routes/auth.py:39` `APIRouter(prefix="/auth")`
    + `backend/src/api/routes/auth.py:336` `@router.get("/me")`, mounted under `/api`. There is no bare `/api/me`; it 404s.
- Dashboard loads (browser flavor) with zero console errors; verdict badges render.
- Pipeline/actions endpoints respond; notification rules endpoint responds.
- GREEN: all respond 2xx, console clean. RED: any 5xx on the happy path or auth broken.

### Loop health (meta)
- JOURNAL.md: rounds in the last 24h, outcomes; any round that claimed work without evidence blocks.
- MISSIONS.md: claims older than 24h with no new commits (stuck worker), NEEDS-HUMAN queue length.
- Gate integrity: does .claude hook config still exist; was any commit in the last 24h made without a `[verified:` tag (grep git log) — if yes, RED and say which.

## Output — docs/harness/maintenance/STATUS-DAILY.md (overwrite daily, append yesterday's to STATUS-HISTORY.md)
Format:
```
# Job360 daily status — <date>
Pillar 1 ingestion: GREEN/AMBER/RED — one line why + evidence ref
Pillar 2 matching:  ...
Pillar 3 delivery:  ...
Loop health:        ...
Trend vs yesterday: better/same/worse — what changed
Needs you today (max 5 bullets): the NEEDS-HUMAN queue + anything RED
Evidence appendix: verbatim outputs for every claim above
```
No claim without evidence in the appendix. If a check could not run (lock held, server down), report it as UNKNOWN with the reason — never guess a color.
