# Job360 — Deploy Runbook (Railway)
<!-- doc: LIVING -->

## 🔴 `main` IS PRODUCTION — deploys are AUTOMATIC

**Railway is GitHub-linked to `Ranjith36963/job360`, branch `main`. Every merge ships to real users.** There is no manual deploy step and no staging gate. Merging is a release — never merge "to tidy up".

Live at **job360.uk**.

### How to check what is actually deployed

```bash
railway deployment list --service backend --json   # read meta.commitHash, meta.branch
```

Verified 2026-07-27: `meta.commitHash` equalled `origin/main` HEAD exactly, and four deploys fired within 70 seconds as PRs merged.

> **`/api/health` cannot answer this.** It returns a hardcoded `{"status":"ok","version":"1.0.0"}` with no commit SHA. Timestamp correlation is the only alternative signal and it fails silently on a rollback or a failed build — so use the deploy API, not the clock.

### If you ever need to deploy WITHOUT the GitHub link

The manual `railway up` recipe further down still works, but it is the fallback path, not the normal one. Do not use it while the GitHub link is active — a manual upload and an auto-deploy can race and leave services on different commits.

---

> **Status: ✅ LIVE since 2026-07-02.** Railway Hobby active. Project `job360`.
> Which services exist and which commit each is on is only ever true when
> measured: `railway deployment list --service <svc> --json`.
> - **Custom domain:** https://job360.uk
> - **Frontend:** https://frontend-production-c608f.up.railway.app
> - **Backend API:** https://backend-production-80e8e.up.railway.app

## 🟢 What's already done (no action needed)
- Backend + frontend **Dockerfiles**, **`docker-compose.prod.yml`** — validated.
- App runs on **Postgres**. (Test count deliberately not quoted here — measure it:
  `cd backend && python -m pytest --collect-only -q | tail -1`. The
  merge-gate floor lives in one place, `CONTRIBUTING.md`, and only there.)
- **`/health`, `/livez`, `/readyz`** + **env validation at boot** (fail-fast on missing prod secrets).
- **Nightly encrypted DB backup, verified by restore** — `.github/workflows/db-backup.yml`; restore procedure in [`RUNBOOK-backups.md`](./RUNBOOK-backups.md).
- All prod env values staged in the local `.env` — the current names are the table in [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## First-time provisioning (HISTORICAL — already done 2026-07-02)

> These commands **created** the project, databases and services. They are kept as a
> record of how prod was built, and as the recipe if it ever has to be rebuilt from
> scratch. **They are NOT how you deploy a code change** — merging to `main` does
> that automatically (see the banner at the top).

```bash
# From repo root, logged in as rahulranjith369@gmail.com
railway init --name job360 --workspace "ranjith36963's Projects" --json
railway add --database postgres      # managed Postgres → DATABASE_URL

# Backend service (uploads backend/, builds backend/Dockerfile)
# Env names: the table in ARCHITECTURE.md, which is the only current list.
railway add --service backend
railway variables --service backend --set "DATABASE_URL=${{Postgres.DATABASE_URL}}" ...
cd backend && railway up --service backend --detach && cd ..
railway domain --service backend     # → public API URL

# Frontend service
railway add --service frontend
railway variables --service frontend --set "NEXT_PUBLIC_API_URL=https://<backend-url>" ...
cd frontend && railway up --service frontend --detach && cd ..
railway domain --service frontend    # → public site URL

# Smoke test: register → CV → bring a job on the live URL
```

## Verify after deploy
- `curl https://<backend-url>/livez` → 200; `/readyz` → `db:ok`
- Register + upload CV on the live frontend; confirm a row in the managed Postgres.
- Sentry receives a test error; PostHog receives a pageview.
