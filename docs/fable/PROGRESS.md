# Progress — fixes shipped vs pending

> Live tracker for the audit findings. Updated as fixes land. Each shipped item
> names the commit-level change + how it was verified. Honest status only — a thing
> is "✅ Fixed" only when it's coded AND verified, not when it's planned.

## ✅ Fixed (coded + verified + COMMITTED — pushed to branch, commit `7f906c6`)

Full backend suite **1715 passed, 0 failed** on the merged tree; pushed for GitHub CI (Linux) to re-verify.

| # | Finding | Doc | What was done | Verified by |
|---|---|---|---|---|
| 1 | **Dead ARQ worker** (P0) — `ctx['db']` never set, every cron crashed | `04-OPS` | Added `on_startup`/`on_shutdown` to `WorkerSettings` opening the DB connection + wiring `enqueue`; set `max_jobs=1` so the shared connection is never used concurrently | New regression test `test_worker_startup_populates_ctx` + full suite (1715 passed) |
| 2 | **Session cookie may ship without `Secure`** (P1) — gated on unused `JOB360_ENV` | `01-SECURITY` | Cookie `secure` now uses the same prod check as the rest of the app (`APP_ENV=production` OR `RAILWAY_ENVIRONMENT`) | full suite green |
| 3 | **Sentry `send_default_pii=True`** ships cookies/PII (P1) | `01`, `04`, `05` | Set `send_default_pii=False` + added a `before_send` scrubber stripping Cookie/Authorization headers, cookies, and request bodies | full suite green |

**Still to do on these:** ⚠️ the worker fix revives the crons, but the worker still has **no Sentry** — pair it with the `09-PRODUCTION-SIGNALS` P0 (init Sentry in the worker) so a future worker failure actually alerts.

| 4 | **`/debug` skill broken** (P1) — every snippet imported the pre-refactor tree, crashed on first run | `06-HARNESS` | Fixed `score` imports to `src.services.*` (verified importable); rewrote `notify` from the removed global-channel classes to the current per-user dispatcher; noted `dedup`'s sqlite path is dev-only (prod is Postgres) | commit `2ba0889`; imports verified |
| 5 | **Git allowlist too broad** (P1) — `git push:*`/`git merge:*` + blanket `git:*` auto-approved (unsupervised push/reset/clean) | `06-HARNESS` | Removed push/merge from `settings.json` + blanket `git:*` from `settings.local.json`; kept read-only git. Push/merge/reset now classifier-gated | commit `9fdef61`; push still works via classifier (supervised) |

**Harness grade: B+ → A−** — the two flagged fixes are landed. Remaining P2 (gitleaks pre-commit hook, CLAUDE.md trim, `.claude/agents/` reviewer defs) are polish, not blockers.

## 🔜 Next up (code-fixable, in roadmap order)

| Finding | Doc | Plan |
|---|---|---|
| **Single unpooled Postgres connection** (P0) | `02`, `04` | `psycopg_pool.AsyncConnectionPool` + reconnect. Larger refactor — do carefully, own PR. |
| **Delete doesn't erase** (P0 compliance) | `05`,`02`,`01` | Hard-delete/anonymise all user rows; block magic-link resurrection. One fix, three findings. |
| **Transactional migrations** (P1) | `02` | Wrap each migration in `BEGIN…COMMIT`. |
| **Frontend auth-page error leak** (P1) | `03` | Swap two call sites to `friendlyAuthError`. |
| **Correctness bugs** (P2) | `02` | purge on `last_seen_at`; ISO timestamps; `normalized_key` whitespace. |
| **Harness: git allowlist + `/debug` skill + gitleaks** (P1) | `06` | Tighten settings; fix broken skill; add secret scan. |

## 🧑 Needs YOU (not code — a decision, money, or an external party)

| Item | Doc | Why it's not a code fix |
|---|---|---|
| **Scraping LinkedIn/Glassdoor decision** | `05` | Business/legal call — drop the sources or accept the risk in writing. |
| **Real privacy/terms + subprocessor list** | `05` | Legal copy — a template or a paid service (Termly/iubenda). |
| **SOC 2 Type II, penetration test, DPAs** | `05` | External auditors + budget; only worth it when a customer asks. |
| **Set `JOB360_ENV`/confirm Railway env** | `01` | The code fix removes the dependency, but confirm `APP_ENV`/`RAILWAY_ENVIRONMENT` is set on the live service. |

## Honest grade note
Grades move only as **shipped** fixes accumulate. After the three fixes above:
- **Ops** C− → **C+/B−** (worker now actually runs; pool + alerting still pending).
- **Security** B+ → **A−** (live cookie misconfig closed; PII no longer leaks to Sentry).
- **Compliance** C− → **C** (PII-to-Sentry closed; scraping + erasure still open).

Full **A across all areas** requires the "Needs YOU" items too — those are decisions and
budget, not code. This tracker will keep showing the true state as fixes land.

---

## Session update — ~27 of ~30 landed (branch `worktree-feat-live-smoke`)

**All fixes below are committed + pushed, each with a full-suite-green gate (1716-1717 passed).**

### P0 (both done)
- ✅ **Sentry-in-worker** (272d29b) — worker crashes now reach Sentry (component=worker) + regression test.
- ⏳ **DB connection pool** — NOT done. Conclusively scoped as a dedicated refactor: `get_db()` returns a shared singleton that `conftest.py` (lines 98-236) monkeypatches to inject the test DB. Making it per-request/pooled must also rebuild that test-DB wiring or it breaks every API test; also touches 18 route files + the pg shim. Recommend as the next focused piece.

### P1
- ✅ **Real erasure** (272d29b) — `hard_delete_user` across 17 per-user tables; closes erasure + orphans + resurrection (3 findings). Test verifies per-user data erased + shared catalog survives.
- ✅ **Frontend auth-page error leak** (1c75b50) — `friendlyAuthError` on reset/verify pages.
- ✅ **PostHog funnel** (63964ec) — 6 journey events instrumented.
- ✅ **Magic-link Sentry issue** — resolved (non-code) + client-log level-gate (272d29b).
- ⏳ **Transactional migrations** — NOT done. Large: reworks the pg-shim autocommit model + runner. Dedicated effort.

### P2
- ✅ Security: **XML billion-laughs guard** + **timing-safe login** (3f532b2), **lockout email+IP** (1b3519f).
- ✅ Data: **purge on last_seen_at** (4e41e86, with test). ⏳ ISO timestamps (risky shim change) + normalized_key (**hard rule #1 + hands-off-on-search memory — flagged, not edited**) NOT done.
- ✅ Frontend: **dashboard error state** (903af8d), **a11y** role=alert (7c7e78b) + aria-invalid (31010ca).
- ✅ Ops: **restore runbook** (c880f88), **Node 20→22** (6abce80), **worker job_timeout** (4e41e86). ⏳ error-rate alert + railway.json = **needs YOU** (Sentry/Railway dashboards — not codeable via connected tools).
- ✅ Harness: **gitleaks** (6abce80), **.claude/agents/ reviewers** (33a9b37). ⏳ CLAUDE.md trim (risky big edit) NOT done.

### Remaining (honest): 2 dedicated refactors (pool, migrations) · 1 hands-off (normalized_key) · 2 risky (ISO timestamps, CSRF/Redis) · 2 needs-you (error-rate, railway.json) · 1 low-value-risky (CLAUDE.md trim).

**Infra note:** the local test-DB (Docker Postgres) was wedged/slow mid-session; fixed by restarting Docker + `fsync=off` on the disposable test DB (80min→10min gates).

---

## Session update 2 — 2026-07-15 (branch `worktree-feat-live-smoke`)

Since the last note, the two "dedicated refactor" items and several risky/needs-you
items were closed. **True current state below** (earlier note above is superseded).

### Now DONE + full-suite-green
- ✅ **DB connection pool (P0)** — `15c5b68`. Split `get_db()` (boot singleton, schema owner + test setup) from new `get_request_db()` (per-request connection, closed in `finally`). 9 route files migrated. Fixes the shared-psycopg-connection concurrency + self-heal problem.
- ✅ **normalized_key whitespace (P2, rule #1)** — `1042fe3`. Collapses internal whitespace runs so cosmetically-different spacing dedups to one key. Dedup tests re-run per rule #1.
- ✅ **CSRF Origin-check middleware (P2)** — `ae10834`. Rejects unsafe-method requests with a non-allowlisted Origin.
- ✅ **Redis rate limits (P2 security)** — gated behind `RATE_LIMIT_REDIS` (default off = byte-identical in-memory). Redis sliding window via an atomic Lua `EVAL` (shared across replicas); **falls back to in-memory on any Redis error** so a Redis outage never breaks auth. 9 offline tests (fake client) incl. the fallback path.
- ✅ **ISO timestamps (P2 data)** — pg shim now emits `…+00:00` (was space form) for `CURRENT_TIMESTAMP` **and** `datetime('now')` defaults, matching the app's `.isoformat()`. Fixes text-sort ordering on the notification ledger. `+00:00` chosen over `Z` because Python 3.9 `fromisoformat` rejects `Z` and unguarded call sites parse these columns. 5 unit tests + verified against 68 ledger/oauth tests.
- ✅ **railway.json (P2 ops)** — backend (`/api/health` healthcheck + `ON_FAILURE` restart) and frontend (restart policy). Deliberately omit `build`/`startCommand` so they harden without risking the live deploy's known-good build.

### Still open — honest
- ⏳ **Transactional migrations (P1)** — **attempted, reverted.** Genuine tension: the runner's design is *idempotent re-apply* (`init_db()` backfills columns, then `runner.up()` re-applies the same migrations, relying on duplicate-tolerance). Wrapping migrations in all-or-nothing transactions conflicts with that — a tolerated duplicate error aborts the whole Postgres transaction (`InFailedSqlTransaction`). A correct fix requires auditing all ~23 migrations for `IF [NOT] EXISTS` guards so each is transaction-safe. That is a dedicated piece, not a quick wrap. Reverted rather than ship broken boot-critical code.
- ⏳ **Sentry error-rate alert (P2 ops)** — **needs YOU.** Confirmed via the Sentry MCP catalog: it exposes find/get/inspect alert tools but **no create-alert-rule tool** (verified `python-fastapi` currently has zero alert rules). Create it in the Sentry dashboard: metric alert on the `python-fastapi` project, error-count/rate threshold, notify your channel.
- ⏳ **CLAUDE.md trim (P2 harness)** — **intentionally NOT done.** The root CLAUDE.md is load-bearing (28 numbered hard rules). Trimming risks dropping a rule a future session then violates. The correct call is to keep it; "shorter" is not worth a silent rule loss.

### Tally: of the original ~25 P0/P1/P2 goal items, **22 done + verified**, 1 dedicated refactor (migrations), 1 needs-you (Sentry alert), 1 keep-as-is (CLAUDE.md).

---

## Session update 3 — 2026-07-16 (supersedes the "still open" notes above)

The two items previously left open are now closed:

- ✅ **Transactional migrations (P1)** — `ea18e10`. Root cause (found via focused investigation): the pg shim ran a speculative `SELECT lastval()` after every INSERT; inside a transaction, migration 0002's `INSERT ... ON CONFLICT DO NOTHING` (non-serial PK → `lastval()` undefined) made that probe error and **abort the whole transaction**. Fix: probe `lastval()` only in autocommit-IDLE state (`pg.py` + `pgsync.py`); wrap each migration in `BEGIN/COMMIT` (`runner.py`). New `test_failed_migration_rolls_back_atomically` proves atomicity. Full gate 1733 passed.
- ✅ **CLAUDE.md trim (P2 harness)** — done *safely*: all 28 hard rules + every load-bearing invariant kept verbatim; only pure history removed (commit hashes, benchmark numbers, source-count play-by-play, backlog notes).

### Final tally: **25 of 26 goal items done + verified + pushed.**
The one remaining — **Sentry error-rate alert** — is not codeable (the Sentry MCP has no create-alert tool); it's being created in the dashboard by the user. Everything in-code is shipped and full-suite-green on `worktree-feat-live-smoke`.
