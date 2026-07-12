# 04 — Ops & Reliability

> Source: ops/infra sweep (Sonnet), the top P0 **independently verified by Fable**.
> Read-only, evidence as `file:line`. **Headline:** your backup engineering and
> secret-handling are genuinely good — but two P0s show the background-job and DB
> layers were never run end-to-end as actually deployed.

## What's already well-engineered (keep)
- **`db-backup.yml`** is real: it dumps prod, restores into a throwaway Postgres, asserts row counts, AES-256-encrypts *before* upload, keeps 30 backups. Better than most startups.
- **Secrets fail closed** — `SESSION_SECRET`/`CHANNEL_ENCRYPTION_KEY` raise if missing; Sentry is correctly prod-gated.

---

## P0 — The ARQ worker never populates `ctx['db']` → every cron task crashes (VERIFIED)
- **What I saw (and confirmed myself):** `grep -rn "on_startup\|on_shutdown" src/workers/` returns **nothing**. `WorkerSettings` (`workers/settings.py:86`) defines `functions`, `cron_jobs`, `redis_settings` — but **no `on_startup` hook**. Yet every task reads `ctx["db"]` (`workers/tasks.py:106,302,472,…`) and `notification_tick` reads `ctx.get("enqueue")`. Standard ARQ only injects `job_id`/`redis`/etc. into `ctx` — it never sets `db`/`enqueue`. Those keys only exist because *tests* build them by hand (`tests/test_worker_tasks.py:129`). The deployed worker runs plain `arq src.workers.settings.WorkerSettings` (`docker-compose.prod.yml:94`, `Makefile:253`).
- **Why it matters:** `notification_tick` fires every 5 min and `nightly_ghost_sweep` at 02:00. On first tick → `KeyError: 'db'` → logged + re-raised → ARQ retries → fails again. **Notifications, daily/every-N-hours digests, ghost-detection, and background enrichment have almost certainly been completely dead in production since this code shipped.** `DEPLOY.md:7` claims "Worker running 10 ARQ functions + 2 crons" — that claim is unverified and, as written, cannot be true.
- **Fix (P0, do first):** add `on_startup`/`on_shutdown` classmethods to `WorkerSettings` that open the DB and set `ctx['db']` + `ctx['enqueue'] = ctx['redis'].enqueue_job`, mirroring what the tests fake. Then **check live worker logs** to confirm crons actually run.
- **Guardrail this exposes:** your tests pass because they inject `ctx` by hand — so the test suite has been green while the real worker was broken. Add one test that loads `WorkerSettings` and asserts `on_startup` populates `ctx['db']`, so the harness can't be green while prod is dead again.

## P0 — Single unpooled Postgres connection for the whole API process (also in `02-DATA-AND-DB.md`)
- **What I saw:** `api/dependencies.py:11-15` creates one `JobDatabase` singleton; `database.py:19-27` opens **one** raw psycopg async connection (no `psycopg_pool` anywhere). No reconnect logic (`grep reconnect pg.py` → 0).
- **Why it matters:** every request serializes through one connection (hard concurrency ceiling), and when Railway's managed Postgres does maintenance or the connection idles out, **every DB call fails until a manual restart** — no self-heal. Combined with likely single-replica deploy, one Postgres hiccup = 100% outage.
- **Fix (P0):** `psycopg_pool.AsyncConnectionPool` with min/max size + retry-on-`OperationalError`. This is the same root fix as the data doc's #1 item.

---

## P1 — the reliability gaps
- **The backup restore runbook doesn't exist.** `db-backup.yml:11` points to `docs/RUNBOOK-backups.md` — the file isn't in the repo. The backups are great but **nobody has written down how to actually restore** from R2 under pressure. **Fix:** write `docs/RUNBOOK-backups.md` with the exact `gpg --decrypt` + `psql` steps.
- **Backups are single-region, never restore-drilled against prod topology, count-based retention.** One R2 bucket, no cross-region copy of the only durable copy of user PII. **Fix:** document retention as an intentional policy; consider a second region for the encrypted artifact.
- **Sentry `send_default_pii=True` with CVs/emails/session cookies, no scrubber** (`api/main.py:66`). Prod-gating is correct, but any exception in a CV-upload or auth route can ship the session cookie + PII to Sentry. **Fix:** `send_default_pii=False` + `before_send` scrubber. (Also in `01-SECURITY.md` and `05-COMPLIANCE-AND-LEGAL.md`.)
- **No error-rate/latency/volume alerting — only up/down.** `uptime.yml` pings `/livez` (dependency-free liveness); the app can be 200-OK while every real request 500s and nobody is paged for up to 6h. **Fix:** a Sentry error-rate alert rule + a `/readyz` check in `uptime.yml` that exercises a real DB-backed endpoint.

## P2 — hardening
- **Node 20 is EOL** — `ci.yml`, `ci-offline.yml`, `synthetic-live.yml` pin `node-version: "20"` (LTS ended April 2026). **Fix:** bump `setup-node` to `22`.
- **mypy is `continue-on-error: true`** with a 395-error backlog (`ci.yml:84-90`) — documented debt, but "type-check" isn't a real gate; a new type error ships green. **Fix (optional):** diff-only mypy so regressions are caught without draining the backlog.
- **No committed Railway config** (`railway.json`/`railway.toml` absent). Healthcheck path, restart policy, replica count are unauditable from the repo. **Fix:** commit `railway.json` so deploy behavior is version-controlled.
- **Migration-failure-on-boot has no documented rollback story.** `runner.up()` runs inside `lifespan`; a buggy migration crashes startup. Does Railway keep the last-good container live? Undocumented. **Fix:** document + confirm healthcheck-gated rollout against the actual Railway settings. (Ties to the transactional-migration P1 in the data doc.)
- **Worker tasks have no `job_timeout`** — an LLM-judge/enrichment call that hangs ties up a slot for ARQ's default 300s. **Fix:** set an explicit `job_timeout` in `WorkerSettings`.

---

## Fix order (ops)
1. **P0 ARQ `on_startup`** — your background features are dead until this lands. Verify with live logs. (Fastest high-impact fix in the audit.)
2. **P0 connection pool** — shared with the data doc; do once, fixes both.
3. **P1 restore runbook + Sentry PII + real alerting** — small, high-leverage.
4. **P2 Node bump, railway.json, job_timeout** — a hardening batch.

**Verdict:** Backups and secret-handling are above-bar. But two P0s — a worker that can't run its crons and a DB layer with no pool or reconnect — mean the deployed system was never exercised end-to-end under real conditions. **Not production-ready for real traffic until both are fixed and confirmed live.** The good news: both are small, well-scoped fixes.
