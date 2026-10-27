---
name: run-job360
description: How to build, launch and sign in to Job360 locally (Postgres, backend on :8000, frontend on :3000, session cookie, j360_ token for MCP). Launch facts for Anthropic's /run and /verify.
---
<!-- doc: LIVING -->

# Run Job360 locally (launch facts)

## Prerequisites
- Postgres: `docker compose -f docker-compose.dev.yml up -d postgres` (host port **5433**; the cloud sandbox and CI use **5432**, so there set `DATABASE_URL=postgresql://job360:job360dev@localhost:5432/job360`). The default DSN is the 5433 one.
- Backend: Python 3.12 venv, then from `backend/`: `pip install -e ".[dev]"`.
- Pitfall: the editable install resolves `import src` to whichever checkout installed it (often a git worktree). In a standalone script `sys.path.insert(0, "<this checkout>/backend")` and `os.chdir` there; check with `python -c "import src; print(src.__file__)"`.
- `backend/.env` (read by `load_dotenv()`) needs `SESSION_SECRET` (any `python -c "import secrets;print(secrets.token_urlsafe(64))"`); sessions fail without it.
- Local `REQUIRE_EMAIL_VERIFICATION=false` skips the verified-email gate (token mint, assistant settings).
- Frontend: `cd frontend && npm ci` first.

## Start
- Backend, from `backend/`: `python main.py` (127.0.0.1:8000; migrations auto-apply on boot). Not bare `uvicorn`. `./run-backend.sh` wraps it.
- Frontend dev: `cd frontend && npm run dev` (:3000; `/api/*` proxies to `BACKEND_ORIGIN`, default `http://localhost:8000`).
- Frontend production build: `npm run build && npm run start`.
- `npm run dev` rewrites tracked `frontend/AGENTS.md`; run `git checkout frontend/AGENTS.md` afterwards.
- Health: `curl http://127.0.0.1:8000/api/health` (its version field is hardcoded; `/api/readyz` checks the DB).

## Get a session
- `POST /api/auth/register` `{"email","password"}` (password 8+ chars) returns **201 and sets no cookie**.
- Then `POST /api/auth/login` `{"email","password"}` sets the `job360_session` cookie (`curl -c jar -b jar`). Login locks per (email, IP) after repeated failures; use a throwaway email for lockout tests.
- Browser: `/login` shows the magic-link form first; click **Use password instead**.
- Browser driving: Playwright via node `require('playwright')`, or Claude in Chrome.

## MCP token
- With the session cookie: `POST /api/tokens` `{"name":"x"}` returns 201 with the `j360_...` token once (session only; a token cannot mint a token).
- MCP endpoint: `http://127.0.0.1:8000/api/mcp` (streamable HTTP, `Authorization: Bearer j360_...`).

## What /verify should do per change
- Frontend: real browser, walk the changed page signed in, screenshot, read console errors.
- Backend: real HTTP call to the route, then read the resulting row in Postgres.
- Migration: `cd backend && python -m migrations.runner up`, then one request that touches the new table or column.
- MCP: a real tool call with a `j360_` token (`tools/list`, then the changed tool).
