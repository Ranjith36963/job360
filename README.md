# Job360
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

**Job360 is the memory and context layer for the seeker's own AI agent.** Job boards find jobs. Agents (Claude Code, ChatGPT, Grok, Gemini, a browser agent) think and act — judge fit, write the CV, find the recruiter, read the inbox, fill the form. Job360 remembers: the structured profile, every artifact version, every typed event with its author, and the receipt of what was sent.

**We never source, rank or recommend jobs** (product rule 4). The user, or their agent, brings the job — a link or pasted text — and Job360 keeps everything that happens after the click. Read [`docs/product/VISION.md`](./docs/product/VISION.md) first; the work list with an issue per slice is [`docs/plans/2026-09-03-mission-roadmap.md`](./docs/plans/2026-09-03-mission-roadmap.md).

> **What is live on `main` today:** magic-link login, profile extraction (CV / LinkedIn / GitHub / preferences), `POST /api/jobs/bring`, the application spine (one Application object, typed events, versioned artifacts, append-only receipts), a CV tailor kept as the web fallback, and an MCP server at `/api/mcp`. Three Railway services: `backend`, `frontend`, `Postgres` (worker + Redis were deleted 2026-09-02, so nothing runs in the background — no notifications, no crons).
>
> **The sourcing era was deleted 2026-09-05** (slice 5, #483): the 40-source aggregator, the 0–100 scorer, the four-layer dedup, the search dashboard. **The per-user notification-channel system** (Apprise dispatcher, Slack/Discord/Telegram connect flows, digest queue) was deleted the same day. None of that code exists in this repo any more — git history is the record.

### API docs (auto-generated)

Once the backend is running (`cd backend && python main.py`), interactive API docs are served at **http://localhost:8000/docs** (Swagger UI) and **http://localhost:8000/redoc** (ReDoc). Both are generated from the FastAPI route decorators + Pydantic models — no separate maintenance.

## How an agent uses Job360

Point any MCP-capable client (Claude Code, Claude, ChatGPT, Grok) at `https://job360.uk/api/mcp` (or `http://localhost:8000/api/mcp` in dev). Two ways to authenticate:

- **OAuth 2.1** — the client discovers the authorization server from `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource` and runs the standard flow. This is how ChatGPT- and Grok-style connectors add Job360.
- **Personal token** — sign in to the web app, go to **Settings → Connect an agent**, and mint a token (`j360_…`). It is shown once; the backend stores only a hash. Good for CLI clients and Claude Code.

Once connected, the agent reads and writes the candidate's profile, brings a job, saves CV/cover-letter/answer versions, records typed events, and pulls `whats_new` — all through MCP tools backed by the same REST routes the web app uses. The agent still does the finding, judging and writing; Job360 only stores.

## What Job360 stores

Bringing a job (a link or pasted text) births one **Application**, status `considering`. Everything else hangs off it:

- **Job snapshot** — title, company, location, URL, the ad text as it read that day
- **Artifacts** — CV, cover letter, answers, outreach; every version kept, stamped with who/when and which profile version made it
- **Events** — an append-only, typed history (`brought`, `fit_judged`, `artifact_saved`, `applied`, `replied`, `interview_scheduled`, `offer`, `rejected`, and more); the current status is just the last status event
- **Contacts** — recruiter or hiring-manager details the agent found
- **Receipt** — frozen the moment "I applied" happens: artifact versions sent, fields filled, confirmation text, channel, timestamp — never edited afterwards

Nothing here is scored, ranked or recommended. The candidate profile (CV + LinkedIn + GitHub + preferences) is the one piece of context every application draws from; see [`docs/product/VISION.md`](./docs/product/VISION.md) for the full object model and the event-type list.

## Architecture

Two deployables share one Postgres database. The backend is a FastAPI app whose product path is `POST /api/jobs/bring` (`api/routes/bring.py`) → the application spine (`services/applications/spine.py`, append-only events/artifacts/receipts) → tailoring as a web fallback (`api/routes/tailor.py`) → the MCP server (`api/mcp_server.py`), with `services/profile/` feeding profile data into all of it. `src/repositories/pg.py` is the single DB door — an aiosqlite-shaped async driver that rewrites legacy SQLite SQL to Postgres at runtime; every module imports it as `from src.repositories import pg as aiosqlite`. The frontend is a Next.js app that is a thin screen over the same routes.

```
job360/
├── backend/
│   ├── main.py                  # FastAPI uvicorn entry (thin)
│   └── src/
│       ├── api/
│       │   └── routes/          # bring, receipts, applications, tailor, profile,
│       │                        # auth, tokens, oauth, well_known, health, client_log
│       ├── services/
│       │   ├── applications/    # the Application object + append-only event/artifact log
│       │   ├── profile/         # CV / LinkedIn / GitHub extraction
│       │   ├── fetch/           # the URL-fetch web fallback + its SSRF guard
│       │   └── tailoring/       # the CV/cover-letter tailor web fallback
│       ├── repositories/        # Postgres via psycopg3 (aiosqlite-shaped shim)
│       └── utils/
├── backend/migrations/          # forward/reverse SQL migration pairs + runner.py
└── frontend/
    └── src/
        ├── app/                 # Next.js App Router pages
        ├── components/
        └── lib/                 # fetch wrapper, generated API types, query keys
```

Every path above exists today; a fuller tree with the generated fact table (migration head, route/hard-rule counts, etc.) lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md) — read that for the deep reference, not this file.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Ranjith36963/job360.git
cd job360

# 2. Backend deps (installs the dev extra: pytest, ruff, mypy)
cd backend
pip install -e ".[dev]"

# 3. Local Postgres (pgvector image, host port 5433)
cd ..
docker compose -f docker-compose.dev.yml up -d postgres

# 4. Configure env
cp .env.example .env   # edit DATABASE_URL / DATABASE_PUBLIC_URL and any API keys

# 5. Run the backend — FastAPI on :8000, MCP at /api/mcp
cd backend
python main.py

# 6. Run the frontend — Next.js on :3000
cd ../frontend
npm run dev
```

## Profile setup

The CLI can bootstrap a single-tenant profile from the command line:

```bash
cd backend
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin-profile.pdf --github yourusername
```

`--cv`, `--linkedin` and `--github` are all optional and independent — run with just one, any combination, or none (the wizard still walks through preferences). Per-user profiles for signed-in accounts go through the web app at `/profile` or the `update_profile` MCP tool instead; the CLI always writes to the single dev tenant.

## Testing

```bash
# Backend — canonical pre-commit run, needs the dev Postgres up
cd backend
python -m pytest -q -p no:randomly
```

The suite runs against a real Postgres (not SQLite) via the shims in `tests/conftest.py`, schema-per-test, with HTTP mocked by `aioresponses` — it must run offline. **Never quote a test count from a doc — measure it**: `python -m pytest --collect-only -q -p no:randomly | tail -1`. See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the generated, code-verified counts.

```bash
# Frontend
cd frontend
npm run test:unit   # vitest
npm run test:e2e    # playwright
```

## Infrastructure

Live on Railway at job360.uk since 2026-07-02. Three services: `backend`, `frontend`, `Postgres`. The `worker` and `Redis` services were deleted 2026-09-02 — nothing runs in the background, so there are no scheduled jobs and no async notification delivery; anything that sends mail does it synchronously from the API process. Login is passwordless, by magic link, delivered through Resend on the verified `job360.uk` domain.

## Notifications

Job360 is **pull, not push** (VISION.md decision 11): the seeker reads `GET /whats-new` and the web home. There is no background delivery, no per-user channels, and no digest queue — that system was deleted 2026-09-05 with the sourcing era.

## Configuration

Copy `.env.example` to `.env` at the repo root and fill in `DATABASE_URL` / `DATABASE_PUBLIC_URL`, `FRONTEND_ORIGIN`, `SITE_BASE_URL`, and `RESEND_API_KEY` (system email — magic-link login and password reset — needs it). The full, current env-var table lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md) — do not trust an older list, several variables were retired with the sourcing era.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for branch naming, commit style, and the PR flow.

## History

The sourcing era (job search, scoring, dedup, enrichment) and the per-user notification-channel system were both deleted 2026-09-05 (slice 5, #483). Neither is archived in-tree — git history is the record. Never rebuild either.
