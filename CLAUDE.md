# CLAUDE.md
<!-- doc: LIVING | last-verified: 2026-09-03 -->
<!-- SIZE BUDGET: <= 1,100 words (was 2,000; halved 2026-08-25 when the hard
     rules moved to a skill -- a budget left above the real size is slack, not
     headroom). This file is auto-loaded before every session,
     so it is POINTERS + CRITICAL GOTCHAS ONLY. Long-form history belongs in
     docs/harness/IMPLEMENTATION_LOG.md, reference tables in ARCHITECTURE.md, recipes in
     .claude/skills/. If you are about to add a paragraph here, add it there and
     leave a one-line pointer. CI enforces this (doc_sync_check.py). -->

<!-- Tone rules live in ~/.claude/CLAUDE.md (loads in every project);
     duplicating them here cost a paragraph per session for nothing. -->

## 🔴 `main` IS PRODUCTION. MERGING SHIPS TO REAL USERS.

Railway is GitHub-linked to `Ranjith36963/job360`, branch `main`. **Every merge auto-deploys** — no manual step, no staging gate. Re-provable any time with `railway deployment list --service backend --json` (read `meta.commitHash`). `/api/health` is **useless** here — it returns a hardcoded `"version": "1.0.0"`, so the deploy API is the only trustworthy source.

- Never merge "to tidy up" — merging is a release.
- When the owner reports a problem, he is describing LIVE PRODUCTION. Diagnose against prod first, then read code.
- Check prod is on the commit you are reading; several sessions merge here and `main` moves under you.

**You can read production directly — do it, don't ask the owner to paste things.**

| Source | How |
|---|---|
| Errors | Sentry MCP — `organizationSlug: "job360"`, `regionUrl: "https://de.sentry.io"` |
| Logs (backend, frontend, Postgres) | `railway logs --service <name>` |
| Database | `railway run -s Postgres python <script>` using `DATABASE_PUBLIC_URL` (plain `DATABASE_URL` only resolves inside Railway) |
| Product analytics | PostHog MCP (EU) |
| Deploys | `railway deployment list --service <svc> --json` |

**Two traps that cost real time:** `railway logs` *streams*, so piping to `tail` returns nothing and looks exactly like "no access" — always `| head -N` with a `timeout`. And **never print secret values** (`railway variables` once dumped a live API key into a transcript); filter to key NAMES only.

## 🎯 The mission (decided 2026-09-03 — read `docs/product/VISION.md` first)

**Job360 is the memory and context layer for the seeker's own AI agent.** The agent finds the job, judges fit, writes the CV, reads Gmail, applies. We store the profile, every artifact version, every typed event, the receipt. **We never source, rank or recommend jobs** (product rule 4). New feature test (rule 5): *could Claude Code / ChatGPT do this with its own tools? Then expose a store tool, not a do tool.* Work list + issue per slice: `docs/plans/2026-09-03-mission-roadmap.md`. The old search pipeline was deleted in slice 5 (#483, 2026-09-05).

## Quick Orientation

- Branch: `main`. Multi-commit work demands a preflight: verify `git branch --show-current`, clean tree, and `git fetch origin <branch>` HEAD alignment. Halt and surface on divergence — never silent rebase.
- **Canonical pre-commit verification:** `cd backend && python -m pytest -q -p no:randomly`. **Never quote a test count from a doc — measure it** (`python -m pytest --collect-only -q | tail -1`); three docs once disagreed by 400–800 tests. Runs against a **real Postgres** (docker-compose.dev.yml, port 5433) via the `sqlite3`/`aiosqlite` shims in `tests/conftest.py`, schema-per-test. HTTP is mocked with `aioresponses`; the suite must run offline. Never add `--ignore=` for a test file; fix or delete the test.
- Two deployables: `backend/` (Python 3.10+, FastAPI, Postgres via psycopg3) and `frontend/` (Next.js 16, React 19). Runtime data in `backend/data/`. Live on Railway at job360.uk since 2026-07-02; three services: `backend`, `frontend`, `Postgres` — `worker` + `Redis` were deleted 2026-09-02, so nothing runs in the background (no notifications, no crons; Redis-unreachable log lines are expected).
- What automation is actually running: the GitHub Actions harness — 23 workflows in `.github/workflows/` (triage, doc-sync, ci, ci-offline, codeql, security, uptime, db-backup, pr-shepherd, branch-reaper…; measure with `ls`). The old agent loop (scout/worker/integrator) was disabled 2026-06-21 and its files deleted 2026-09-05 — there is no background agent to wait on.
- What surprises new sessions: the sourcing era (sources, scorer, dedup, enrichment, ARQ worker, dashboard) was **deleted 2026-09-05** (slice 5, #483) and the notification/channel/Kanban stack followed in the cleanup audit — git history is the only record; never rebuild them. Heavy deps must be lazy-imported (top-level imports cost every CLI run and every pytest collection). Next.js 15 made `params` a Promise and 16 removed synchronous access — await it. MCP tools call route *functions*, so any new route gate must be re-applied in `mcp_server.py` (parity test enforces). Migrations auto-apply on boot: `api.dependencies.init_db()`, called by `api.main.lifespan`.

## Hard Rules

The numbered invariants that break production or corrupt data when violated —
schema, the application spine, MCP, auth, extraction. They live in
`.claude/skills/hard-rules/SKILL.md` and load when the work touches them.

Read them before editing any of those areas. Full product rules:
`docs/product/product_design_rules.md`.

## Commands

```bash
# Backend — run from backend/
python main.py                                       # FastAPI on :8000 (+ MCP at /api/mcp)
python -m src.cli setup-profile --cv cv.pdf --linkedin li.pdf --github user
python -m pytest -q -p no:randomly                   # canonical run (needs Postgres up)
python -m pytest tests/test_receipts.py::test_name -v  # single test
python -m migrations.runner up | status | down       # migrations

# Frontend — run from frontend/  (⚠️ Next.js 16: `params` is a Promise — async since 15, sync access gone in 16; await it)
npm run dev | build | lint | type-check | test:unit | test:e2e
```

## Architecture (one paragraph + pointers)

**Product path:** `POST /jobs/bring` (`api/routes/bring.py`) stores the ad as a `jobs` row and births one Application (`services/applications/spine.py`: `applications` + append-only `application_events` / `application_artifacts` / `application_receipts`) → tailoring as web fallback (`api/routes/tailor.py`) → MCP server `api/mcp_server.py` (18 tools — measure with `grep -c "@mcp.tool()"`; bearer tokens `j360_…` or OAuth 2.1; `/api/mcp`). Nothing scores, ranks, dedups or enriches a job — that pipeline was deleted in slice 5 (#483).

- `src/repositories/pg.py` is the single DB door — an `aiosqlite`-shaped async driver whose `translate()` rewrites legacy SQLite SQL to Postgres at runtime. It is production-critical, not test-only (guard: `tests/test_pg_translate.py`). Every module does `from src.repositories import pg as aiosqlite`.
- `ARCHITECTURE.md` — system overview, full directory tree, DB schema, dependency table, and the canonical environment-variable table.
- `docs/README.md` — index of every surviving doc (product, operations, harness). History lives in git log, not in docs.
- `docs/product/product_design_rules.md` — the owner's product rules in full (rules 4–6 are the mission).
- `STATUS.md` — current phase, what is live on main, what is next. `CONTRIBUTING.md` — branch/commit/PR conventions. `backend/README.md` / `frontend/README.md` — install + run.
- Second brain: older project memory lives at `D:\second-brain\wiki\projects\job360\`.
