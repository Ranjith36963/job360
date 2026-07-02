# Job360 — Deploy Runbook (Railway)

> **Status: ✅ LIVE (2026-07-02).** Railway Hobby active. Project `job360`, 5 services all Online.
> - **Frontend (the site):** https://frontend-production-c608f.up.railway.app
> - **Backend API:** https://backend-production-80e8e.up.railway.app
> - Verified live: `/readyz` → `{db:ok, redis:ok}`, security headers, full register→login→/me auth flow.
> - Worker running 10 ARQ functions + 2 crons. Managed Postgres + Redis attached.
>
> To redeploy after code changes: `cd backend && railway up --service backend --detach` (and `--service worker`), `cd frontend && railway up --service frontend --detach`. Env vars already set per-service in Railway.

## 🔴 What YOU must do (one time, ~2 min)
The Railway **free trial has expired**. To deploy, activate a plan + payment:
1. Go to **https://railway.com/account/plans** (logged in as `rahulranjith369@gmail.com`).
2. Pick **Hobby** (~$5/mo; includes usage credit that covers Postgres + Redis + the app at small scale).
3. Add a payment method.
4. Tell me "Railway plan active" — I run the deploy below and give you the live URL.

*(Alternative if you'd rather not pay Railway: say the word and I'll target a free host — Render/Fly for the API, Vercel for the frontend. That needs an account on that host, though.)*

## 🟢 What's already done (no action needed)
- Backend + frontend **Dockerfiles**, **`docker-compose.prod.yml`** (5 services) — validated.
- App runs on **Postgres** (1608 tests green).
- **`/health`, `/livez`, `/readyz`** + **env validation at boot** (fail-fast on missing prod secrets).
- **DB backup script** (`backend/scripts/backup_db.py`).
- All prod env values staged in the local `.env` (LLM keys, `SESSION_SECRET`, `CHANNEL_ENCRYPTION_KEY`, `SENTRY_DSN`, `POSTHOG_KEY`).

## Deploy commands (I run these once the plan is active)
```bash
# From repo root, logged in as rahulranjith369@gmail.com
railway init --name job360 --workspace "ranjith36963's Projects" --json
railway add --database postgres      # managed Postgres → DATABASE_URL
railway add --database redis         # managed Redis → REDIS_URL

# Backend service (uploads backend/, builds backend/Dockerfile)
railway add --service backend
railway variables --service backend --set "DATABASE_URL=${{Postgres.DATABASE_URL}}" \
  --set "REDIS_URL=${{Redis.REDIS_URL}}" --set "SESSION_SECRET=..." \
  --set "CHANNEL_ENCRYPTION_KEY=..." --set "GEMINI_API_KEY=..." --set "APP_ENV=production" ...
cd backend && railway up --service backend --detach && cd ..
railway domain --service backend     # → public API URL

# Worker service (same image, ARQ start command)
railway add --service worker
# set start command: arq src.workers.settings.WorkerSettings + same env

# Frontend service
railway add --service frontend
railway variables --service frontend --set "NEXT_PUBLIC_API_URL=https://<backend-url>" ...
cd frontend && railway up --service frontend --detach && cd ..
railway domain --service frontend    # → public site URL

# Smoke test: register → CV → search on the live URL
```

## Verify after deploy
- `curl https://<backend-url>/livez` → 200; `/readyz` → `{db:ok, redis:ok}`
- Register + upload CV on the live frontend; confirm a row in the managed Postgres.
- Sentry receives a test error; PostHog receives a pageview.
