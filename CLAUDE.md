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

**Job360 is the memory and context layer for the seeker's own AI agent.** The agent finds the job, judges fit, writes the CV, reads Gmail, applies. We store the profile, every artifact version, every typed event, the receipt. **We never source, rank or recommend jobs** (product rule 4). New feature test (rule 5): *could Claude Code / ChatGPT do this with its own tools? Then expose a store tool, not a do tool.* Work list + issue per slice: `docs/plans/2026-09-03-mission-roadmap.md`. The search pipeline below is legacy — hidden, then deleted (slice 5).

## Quick Orientation

- Branch: `main`. Multi-commit work demands a preflight: verify `git branch --show-current`, clean tree, and `git fetch origin <branch>` HEAD alignment. Halt and surface on divergence — never silent rebase.
- **Canonical pre-commit verification:** `cd backend && python -m pytest -q -p no:randomly`. **Never quote a test count from a doc — measure it** (`python -m pytest --collect-only -q | tail -1`); three docs once disagreed by 400–800 tests. Runs against a **real Postgres** (docker-compose.dev.yml, port 5433) via the `sqlite3`/`aiosqlite` shims in `tests/conftest.py`, schema-per-test. HTTP is mocked with `aioresponses`; the suite must run offline. `test_main.py` is in the canonical run — do **not** re-add `--ignore=tests/test_main.py`.
- Two deployables: `backend/` (Python 3.10+, FastAPI, Postgres via psycopg3) and `frontend/` (Next.js 16, React 19). Runtime data in `backend/data/`. Live on Railway at job360.uk since 2026-07-02; three services: `backend`, `frontend`, `Postgres` — `worker` + `Redis` were deleted 2026-09-02, so nothing runs in the background (no notifications, no crons; Redis-unreachable log lines are expected).
- What automation is actually running: the GitHub Actions harness — 30 workflows in `.github/workflows/` (repair, triage, doc-sync, ci, ci-offline, codeql, security, uptime, live-e2e, journey, product-health, db-backup, pr-shepherd…). The old agent loop (`docs/harness/maintenance/MISSIONS.md`) is DORMANT — disabled 2026-06-21. Do not wait on it.
- What surprises new sessions: `SOURCE_REGISTRY` has 41 entries but 40 unique source classes (`indeed` + `glassdoor` alias `JobSpySource`) — legacy, never add a source; measure it, never quote it. Heavy deps must be lazy-imported (top-level imports cost every CLI run and every pytest collection). Next.js 15 made `params` a Promise and 16 removed synchronous access — await it. MCP tools call route *functions*, so any new route gate must be re-applied in `mcp_server.py` (parity test enforces). Migrations auto-apply on boot: `api.dependencies.init_db()`, called by `api.main.lifespan`.

## Hard Rules

31 numbered invariants that break production or corrupt data when violated —
schema, sources, scoring, auth, notifications, extraction. They live in
`.claude/skills/hard-rules/SKILL.md` and load when the work touches them.

Read them before editing any of those areas. Full product rules:
`docs/product/product_design_rules.md`.

## Commands

```bash
# Backend — run from backend/
python main.py                                       # FastAPI on :8000 (+ MCP at /api/mcp)
python -m src.cli setup-profile --cv cv.pdf --linkedin li.pdf --github user
python -m pytest -q -p no:randomly                   # canonical run (needs Postgres up)
python -m pytest tests/test_scorer.py::test_name -v  # single test
python -m migrations.runner up | status | down       # migrations

# Frontend — run from frontend/  (⚠️ Next.js 16: `params` is a Promise — async since 15, sync access gone in 16; await it)
npm run dev | build | lint | type-check | test:unit | test:e2e
```

## Architecture (one paragraph + pointers)

**Product path (current):** `POST /jobs/bring` (`api/routes/bring.py`) → `jobs` row + application receipt (`api/routes/receipts.py`, append-only) → tailoring as web fallback (`api/routes/tailor.py`) → MCP server `api/mcp_server.py` (8 tools, bearer tokens `j360_…`, `/api/mcp`). Slice 2 turns this into one Application object + event log.
**Legacy path (hidden, slice 5 deletes):** `src/main.py` `run_search()` → `SOURCE_REGISTRY` sources → `skill_matcher.JobScorer` → `deduplicator.py` → feed.

- `src/repositories/pg.py` is the single DB door — an `aiosqlite`-shaped async driver whose `translate()` rewrites legacy SQLite SQL to Postgres at runtime. It is production-critical, not test-only (guard: `tests/test_pg_translate.py`). Every module does `from src.repositories import pg as aiosqlite`.
- `docs/product/pillars/` — code-verified architecture reference for the *sourcing era* (User pillar still true; Search & Match / Job Providers are legacy). Cross-check it for any specific claim about that code.
- `ARCHITECTURE.md` — system overview, full directory tree, DB schema, dependency table, and the canonical environment-variable table.
- `docs/README.md` — index of every plan/design/eval doc.
- `docs/harness/IMPLEMENTATION_LOG.md` — append-only batch history (read FIRST when picking up unfamiliar work).
- `docs/product/product_design_rules.md` — the owner's product rules in full (rules 4–6 are the mission).
- `STATUS.md` — current phase, what is live on main, what is next. `CONTRIBUTING.md` — branch/commit/PR conventions. `backend/README.md` / `frontend/README.md` — install + run.
- Second brain: older project memory lives at `D:\second-brain\wiki\projects\job360\`.
