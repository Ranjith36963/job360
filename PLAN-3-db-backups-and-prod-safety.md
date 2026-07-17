# PLAN 3 — Wire database backups + close the remaining prod-safety gaps

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rank: 3 of 5.**
Why: the app is live on Railway with a real Postgres database and **no scheduled backup anywhere** — a working `pg_dump` script exists (`backend/scripts/backup_db.py`) but a repo-wide grep confirms nothing references it: no cron, no Railway job, no CI schedule, no docs. One bad migration or fat-fingered delete and the data is gone. This plan wires the existing script to a nightly scheduler, proves restore actually works (a backup you've never restored is a hope, not a backup), and closes three verified smaller gaps.

**Goal:** nightly automated Postgres backups with retention + a documented, rehearsed restore procedure + correct container healthchecks + an inbound request-timeout middleware.

**Architecture:** GitHub Actions nightly job runs the existing `backup_db.py` against prod `DATABASE_URL` (secret) and stores the gzipped dump as a private artifact. Healthcheck paths corrected to the probes that already exist. One new middleware (the only genuinely new code).

**Tech Stack:** GitHub Actions, pg_dump/psql, FastAPI/Starlette middleware, pytest.

---

## Verified facts (checked 2026-07-07 — several "Step 4" items already exist; do NOT rebuild them)

- `backend/scripts/backup_db.py` EXISTS and works: reads `DATABASE_URL` (errors if unset), streams `pg_dump --no-owner --no-privileges` into gzip, names files `job360-{UTC}.sql.gz`, output dir `./backups` or `$BACKUP_DIR`, retention via `--keep`/`$BACKUP_KEEP` (default 14). Restore is documented in its docstring: `gunzip -c file.sql.gz | psql "$DATABASE_URL"`. It is referenced by NOTHING else in the repo.
- `/api/livez` and `/api/readyz` ALREADY EXIST and are tested (`backend/src/api/routes/health.py:35-98`, `backend/tests/test_health_probes.py`). readyz checks Postgres (`SELECT 1`) and Redis (skipped when `REDIS_URL` unset). Do not build probes.
- `SecurityHeadersMiddleware` ALREADY EXISTS (`backend/src/api/middleware.py:109-133`, registered in `backend/src/api/main.py:106`): nosniff, X-Frame-Options DENY, Referrer-Policy, CSP, HSTS in prod. Do not build it.
- Healthcheck path bugs: `backend/Dockerfile:40-41` HEALTHCHECK hits `/api/health` (works, but it's the trivial no-dependency endpoint); `docker-compose.prod.yml` backend healthcheck hits `/health` (NO `/api` prefix — that path does not exist, so the compose healthcheck can never pass).
- Inbound request timeout: DOES NOT EXIST. `REQUEST_TIMEOUT`/`SOURCE_FETCH_TIMEOUT*` in `backend/src/core/settings.py` are OUTBOUND (job-source fetch) timeouts only. The FastAPI middleware stack (`backend/src/api/main.py:94-113`) is CORS → SecurityHeaders → AccessLog → RequestId; no timeout middleware.
- `.env.example` does NOT document `DATABASE_URL`, `SENTRY_DSN`, `BACKUP_DIR`, `BACKUP_KEEP`. `backend/scripts/check_env_example.py` is the parity gate.
- Deploy shape: no railway.json/Procfile — Railway builds `backend/Dockerfile` (API) and `backend/Dockerfile.worker` (ARQ worker via `RAILWAY_DOCKERFILE_PATH`). Prod DB is Railway Postgres; its `DATABASE_URL` lives in the Railway dashboard.
- App runs Postgres-only through the `pg.py` psycopg3 shim; connections are autocommit, no pool.

## Files to touch

- Create: `.github/workflows/db-backup.yml`
- Create: `docs/RUNBOOK-backups.md`
- Modify: `backend/Dockerfile` (healthcheck path), `docker-compose.prod.yml` (healthcheck path)
- Modify: `backend/src/api/middleware.py` (new `RequestTimeoutMiddleware`), `backend/src/api/main.py` (register it), `backend/src/core/settings.py` (timeout setting)
- Create: `backend/tests/test_request_timeout.py`
- Modify: `.env.example`

---

### Task 0: Preflight

- [ ] **Step 0.1:**

```bash
git fetch origin main
git checkout -b feat/prod-safety-net origin/main
git status --porcelain   # must be empty
```

- [ ] **Step 0.2: Privacy gate for artifact backups.** Database dumps contain user emails and CVs — they must never land in a public repo's artifacts:

```bash
gh repo view --json visibility
```

If visibility is not `PRIVATE`: STOP the backup-workflow task (Tasks 1–2), report, and do only Tasks 3–5. (Fallback for a public repo would be Railway's own backup add-on or an encrypted-at-rest bucket — owner decision.)

### Task 1: Nightly backup workflow

- [ ] **Step 1.1: Read the script first** (`backend/scripts/backup_db.py`) and confirm: env names (`DATABASE_URL`, `BACKUP_DIR`, `BACKUP_KEEP`), CLI flags, and that it shells out to `pg_dump` (so the runner needs the Postgres client installed).

- [ ] **Step 1.2: Create `.github/workflows/db-backup.yml`:**

```yaml
# Nightly Postgres backup of the live Railway database.
# Dump lands as a workflow artifact (repo is private; artifacts inherit repo ACL).
# Restore procedure: docs/RUNBOOK-backups.md
name: db-backup
on:
  schedule:
    - cron: "17 2 * * *" # 02:17 UTC nightly (odd minute avoids top-of-hour load spikes)
  workflow_dispatch: {}

jobs:
  backup:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - name: Install Postgres client
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q postgresql-client
          pg_dump --version
      - name: Dump database
        env:
          DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}
          BACKUP_DIR: ./backups
          BACKUP_KEEP: "2"
        run: python backend/scripts/backup_db.py
      - name: Sanity-check the dump is non-trivial
        run: |
          ls -l backups/
          FILE=$(ls backups/*.sql.gz | head -1)
          SIZE=$(stat -c%s "$FILE")
          echo "dump size: $SIZE bytes"
          test "$SIZE" -gt 10000   # a real dump of a live DB is far bigger than 10 KB
          gunzip -t "$FILE"        # gzip integrity
      - name: Upload backup artifact
        uses: actions/upload-artifact@v4
        with:
          name: db-backup-${{ github.run_id }}
          path: backups/
          retention-days: 14
          if-no-files-found: error
```

- [ ] **Step 1.3: HUMAN STEP — set the secret.** The prod `DATABASE_URL` lives in the Railway dashboard (Postgres service → Variables → the PUBLIC/external connection URL, not the `.railway.internal` one — GitHub runners are outside Railway's network). Owner runs:

```bash
gh secret set PROD_DATABASE_URL
```

- [ ] **Step 1.4: Commit.** Note in the PR that the workflow only activates after merge to main + secret set, then fire once with `gh workflow run db-backup.yml` and confirm green + artifact present.

```bash
git add .github/workflows/db-backup.yml
git commit -m "feat(ops): nightly pg_dump backup workflow wiring the existing backup_db.py"
```

### Task 2: Restore drill + runbook

- [ ] **Step 2.1: Do a real restore locally** against the dev Postgres (default DSN `postgresql://job360:job360dev@localhost:5433/job360`). Use a LOCAL dump for the drill (run the script against your local DB) so no prod secret is needed on the workstation:

```bash
cd backend
set DATABASE_URL=postgresql://job360:job360dev@localhost:5433/job360   # PowerShell: $env:DATABASE_URL="..."
python scripts/backup_db.py
# restore into a fresh database, NEVER the live one:
psql "postgresql://job360:job360dev@localhost:5433/postgres" -c "DROP DATABASE IF EXISTS job360_restore_drill;"
psql "postgresql://job360:job360dev@localhost:5433/postgres" -c "CREATE DATABASE job360_restore_drill;"
# Windows has no gunzip -c by default; python does it portably:
python -c "import gzip,sys,glob; f=sorted(glob.glob('backups/*.sql.gz'))[-1]; sys.stdout.buffer.write(gzip.open(f,'rb').read())" | psql "postgresql://job360:job360dev@localhost:5433/job360_restore_drill"
# verify shape:
psql "postgresql://job360:job360dev@localhost:5433/job360_restore_drill" -c "SELECT count(*) FROM users; SELECT count(*) FROM jobs; SELECT max(id) FROM _schema_migrations;"
```

Expected: counts match the source DB; `_schema_migrations` max id equals the highest migration number applied. Record the actual numbers.

- [ ] **Step 2.2: Write `docs/RUNBOOK-backups.md`** containing, concretely: where backups live (Actions → db-backup → artifacts, 14-day retention), how to download one (`gh run download <run-id>`), the exact restore commands from Step 2.1 adapted for prod (restore into a NEW Railway database service, repoint `DATABASE_URL`, never in-place), the drill log (date + counts from Step 2.1), and a "test the restore quarterly" note.

- [ ] **Step 2.3: Commit:**

```bash
git add docs/RUNBOOK-backups.md
git commit -m "docs(ops): backup restore runbook + first restore drill log"
```

### Task 3: Fix the healthcheck paths

- [ ] **Step 3.1:** In `backend/Dockerfile` HEALTHCHECK line, change `/api/health` → `/api/livez` (liveness is the correct semantic for a container healthcheck — see edge cases for why NOT readyz).
- [ ] **Step 3.2:** In `docker-compose.prod.yml` backend healthcheck, change `http://localhost:8000/health` → `http://localhost:8000/api/livez` (the current path lacks the `/api` prefix and 404s forever).
- [ ] **Step 3.3: Verify + commit:**

```bash
docker build -t job360-hc-test backend/   # optional if docker available; else skip build, the path change is textual
git add backend/Dockerfile docker-compose.prod.yml
git commit -m "fix(ops): container healthchecks hit /api/livez (compose path was 404ing)"
```

### Task 4: Inbound request-timeout middleware (the one genuinely new piece)

- [ ] **Step 4.1: Failing test first.** Create `backend/tests/test_request_timeout.py`:

```python
"""Inbound request-timeout middleware — slow handlers must 504, fast ones pass."""
import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware import RequestTimeoutMiddleware


def _app_with_timeout(timeout: float, exempt=()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RequestTimeoutMiddleware, timeout_seconds=timeout, exempt_prefixes=exempt
    )

    @app.get("/fast")
    async def fast():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.5)
        return {"ok": True}

    @app.get("/exempt/slow")
    async def exempt_slow():
        await asyncio.sleep(0.5)
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_fast_request_passes():
    app = _app_with_timeout(0.2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/fast")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_slow_request_gets_504():
    app = _app_with_timeout(0.2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/slow")
    assert resp.status_code == 504
    assert resp.json()["detail"] == "request timed out"


@pytest.mark.asyncio
async def test_exempt_prefix_is_not_timed_out():
    app = _app_with_timeout(0.2, exempt=("/exempt",))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/exempt/slow")
    assert resp.status_code == 200
```

Run: `cd backend && python -m pytest tests/test_request_timeout.py -v -p no:randomly` — expected: ImportError (middleware doesn't exist yet).

- [ ] **Step 4.2: Implement** in `backend/src/api/middleware.py` (append; match the file's existing style — it already has BaseHTTPMiddleware subclasses to copy the shape from):

```python
class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Cap inbound request duration. LLM-heavy routes are exempted by prefix —
    tailoring/extraction legitimately run longer than an API roundtrip should."""

    def __init__(self, app, timeout_seconds: float = 60.0, exempt_prefixes: tuple = ()):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds
        self.exempt_prefixes = tuple(exempt_prefixes)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in self.exempt_prefixes):
            return await call_next(request)
        try:
            return await asyncio.wait_for(
                call_next(request), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504, content={"detail": "request timed out"}
            )
```

Add the needed imports at the top of `middleware.py` if missing (`asyncio`, `JSONResponse` from `fastapi.responses`). Keep Python 3.9-compatible typing (no `tuple[str, ...]` in signatures unless the file already uses `from __future__ import annotations`).

- [ ] **Step 4.3: Find the routes that legitimately run long BEFORE choosing exemptions.** These call LLMs or parse files inline:

```bash
cd backend && grep -rn "async def" src/api/routes/tailor.py src/api/routes/profile.py | head -20
```

Starting exemption list (verify each actually does inline slow work by reading the route bodies): `("/api/tailor", "/api/profile")`. If a route only ENQUEUES background work (returns fast), it does NOT need exemption.

- [ ] **Step 4.4: Register in `backend/src/api/main.py`** next to the existing middleware registrations (order note: Starlette runs `add_middleware` calls LIFO — add it AFTER the CORS registration so CORS headers still apply to 504 responses; concretely, place the `add_middleware(RequestTimeoutMiddleware, ...)` line BEFORE `app.add_middleware(CORSMiddleware, ...)` in source order… **verify empirically**: run the app, hit a route, confirm 504s carry `Access-Control-Allow-Origin` when sent with an Origin header):

```python
app.add_middleware(
    RequestTimeoutMiddleware,
    timeout_seconds=float(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "60")),
    exempt_prefixes=("/api/tailor", "/api/profile"),
)
```

- [ ] **Step 4.5: Declare the setting + env doc:** add to `backend/src/core/settings.py`:

```python
# Inbound API request ceiling (seconds). Outbound source-fetch ceilings are
# SOURCE_FETCH_TIMEOUT / SOURCE_FETCH_TIMEOUT_ATS — different knob, do not merge.
API_REQUEST_TIMEOUT_SECONDS = float(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "60"))
```

(and import/use it in `main.py` instead of the inline `os.getenv` if `main.py` conventionally imports from settings — READ `main.py` and match its style). Add `API_REQUEST_TIMEOUT_SECONDS=` to `.env.example`.

- [ ] **Step 4.6: Run everything + commit:**

```bash
cd backend
python -m pytest tests/test_request_timeout.py -v -p no:randomly
python -m pytest -q -p no:randomly
python scripts/check_env_example.py
git add -A && git commit -m "feat(ops): inbound request-timeout middleware (504), LLM routes exempt"
```

Expected: new tests pass; full suite green (one run — Windows double-run crash is environmental).

### Task 5: Document the missing env vars

- [ ] **Step 5.1:** Add to `.env.example` (with one-line comments each): `DATABASE_URL=`, `SENTRY_DSN=`, `BACKUP_DIR=`, `BACKUP_KEEP=`. Run `python backend/scripts/check_env_example.py` — if the gate flags direction mismatches, fix per its output.

- [ ] **Step 5.2: Commit, push, PR:**

```bash
git add .env.example
git commit -m "docs(env): document DATABASE_URL, SENTRY_DSN, backup vars"
git push -u origin feat/prod-safety-net
gh pr create --title "feat(ops): nightly DB backups + restore drill + healthcheck fixes + request timeouts" --body "Wires the existing (orphaned) backup_db.py to a nightly Actions job with artifact retention; documents + rehearses restore; fixes compose healthcheck 404; adds inbound 504 timeout middleware with LLM-route exemptions. HUMAN steps: set PROD_DATABASE_URL secret (Task 1.3), fire db-backup once after merge."
```

---

## Edge cases a weaker model would miss

1. **Don't rebuild what exists.** livez/readyz, security headers, and the backup script are DONE. The work is wiring and correcting, not creating. If you find yourself writing a new probe or a new pg_dump wrapper, re-read the Verified facts.
2. **Container healthcheck must be livez, not readyz.** readyz fails when Redis blips; a failing container healthcheck makes Docker/Railway restart the API — turning a Redis hiccup into an API outage. Liveness for the container, readiness for deploy gating/load balancing (Railway's dashboard healthcheck can use `/api/readyz` — note that as a human option in the PR, it's dashboard config, not repo config).
3. **Internal vs public Railway DSN.** The `DATABASE_URL` inside Railway services is often `postgres.railway.internal` — unreachable from GitHub runners. The secret must be the EXTERNAL connection string, or the workflow fails with DNS errors that look like auth problems.
4. **pg_dump client vs server version.** `pg_dump` refuses to dump a server NEWER than itself. ubuntu-latest ships a recent client; if the workflow fails with "server version mismatch," install the matching `postgresql-client-NN` (add the PGDG apt repo). Put this in the runbook.
5. **`asyncio.wait_for` + BaseHTTPMiddleware caveat:** cancelling `call_next` abandons the handler mid-flight; DB writes in this codebase are autocommit single statements (per `pg.py`), so a cancelled handler cannot leave an open transaction — but a cancelled route that was mid-way through a multi-statement sequence may leave partial state. That's acceptable for a timeout backstop; do NOT try to "fix" it with transactions here (that's a bigger change; out of scope).
6. **Exemptions by prefix, not exact path** — `/api/tailor` must also cover `/api/tailor/{id}/...` subroutes. But keep the prefixes tight: exempting `/api` would neuter the whole middleware.
7. **Backup file size check matters.** `pg_dump` against a wrong-but-reachable DSN (e.g., an empty default DB) exits 0 and produces a tiny valid dump. The `test "$SIZE" -gt 10000` step is what catches "backing up the wrong database for six months."
8. **Windows shell for the restore drill:** `gunzip -c` doesn't exist in PowerShell; the plan's python one-liner is the portable path. Also `set VAR=` vs `$env:VAR=` differ — both shown in Step 2.1.
9. **Artifacts inherit repo visibility.** The Step 0.2 gate is not optional; a dump artifact in a public repo is a data breach.

## Acceptance criteria

- [ ] After merge + secret set: one green `db-backup` run with a `.sql.gz` artifact > 10 KB (`gh run list --workflow db-backup.yml`).
- [ ] `docs/RUNBOOK-backups.md` exists with a logged restore drill (real row counts from Step 2.1).
- [ ] `docker-compose.prod.yml` and `backend/Dockerfile` healthchecks point at `/api/livez`.
- [ ] `python -m pytest tests/test_request_timeout.py -p no:randomly` → 3 passed.
- [ ] Full backend suite green (one run); `check_env_example.py` passes.
- [ ] `curl -i` against a locally-running API on a normal route shows no behavior change; an artificially slow route (temporarily add `await asyncio.sleep(70)` to a test route, or trust the unit tests) → 504.

## STOP conditions

- Repo is public (Step 0.2) — skip Tasks 1–2, do the rest, report.
- `backup_db.py` has materially changed from the Verified-facts description.
- Adding the middleware breaks existing tests in a way that isn't a simple ordering/import fix — report rather than reshuffling the whole middleware stack.
