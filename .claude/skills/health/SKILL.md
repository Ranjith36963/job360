---
name: health
description: Job360 health: daily system check of all three pillars against GREEN/AMBER/RED definitions, written to docs/harness/maintenance/STATUS-DAILY.md with verbatim evidence. Use for the daily health report.
---
<!-- doc: LIVING -->

# Health — daily system check (.claude/skills/health/SKILL.md)

You are the Job360 health checker. You ignore the backlog and missions entirely — you test the SYSTEM against each pillar's definition of healthy, once per day, and write ONE report the human reads in 5 minutes. Read-only on code; you may run servers/probes via the integrator session's resources (run only when the integrator is idle — check the lock).

**Schedule: NOT RUNNING — this skill is invoked by hand.** `docs/harness/maintenance/HEALTH-SCHEDULE.md` records NOT RUNNING as measured 2026-08-24; the earlier `schtasks /query /tn "Job360 Health"` check on 2026-07-27 also found no such task. No workflow runs it either. Do not trust a schedule claim in a doc; check the scheduler.

**Do not run `scripts/health-daily.{ps1,sh}` — both are BROKEN.** Reports live under `docs/harness/maintenance/`, but the `.sh` writes `$HISTORY` to `docs/maintenance/` (its `$DAILY` is right) and the `.ps1` gets BOTH wrong. The `.sh` archive append is guarded by `if [[ -f "$DAILY" ]]`, so it only fails when a previous report exists — which it does today, and `set -euo pipefail` then aborts it before `claude -p`. Invoke this skill directly until they are fixed.

**MODEL POLICY (owner-mandated):** health CHECKS run on **Sonnet** (`model: "sonnet"` explicit when dispatched); the final verdict paragraph in STATUS-DAILY.md is written by the integrator session. Clerical formatting → Haiku if dispatchable. Degradation under constrained usage: step down one tier and note it in the report header.

## Checks (run all, collect verbatim evidence)

> **Mission (2026-09-03, `docs/product/VISION.md`):** Job360 no longer sources or ranks jobs. "Healthy" now means the memory layer works: a job can be brought, a receipt saved, the MCP server answers. The old Pillar 1/2 checks below are **retired** — do not trigger `/api/search`, do not grade a feed, do not count productive sources.

### Pillar 1 — Bring + remember (the product path)
- `POST /api/jobs/bring` with pasted text as the test user → 2xx, a job row exists.
- Tailor the CV for it (web fallback), then `POST /api/receipts/{job_id}` → 201; `GET /api/receipts` lists it with the frozen ad + documents.
- MCP: call the `/api/mcp` mount (streamable HTTP, not a `@router` route) with a `j360_…` bearer → `tools/list` returns the tool set; `get_profile` returns the test user's profile.
- GREEN: all 2xx, receipt readable after the job row changes. RED: any 5xx, a receipt that lost its documents, MCP 401 with a valid token.

### Pillar 2 — Profile
- Upload the test CV → extraction completes; profile fields are non-empty for what the CV contains (value-presence, rule #21); an unset preference is absent, not zero (rule #29).
- GREEN: extraction returns within 120s and the profile shows the CV's skills. RED: extraction throws or an empty preference shows as a default.

### Retired (sourcing era — do not run)
- ~~Trigger one fresh pipeline run (POST /api/search), count productive sources, grade ranking against `llm_fit_score`, run the accuracy harness.~~ Slice 5 (#483) deletes this code.

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
