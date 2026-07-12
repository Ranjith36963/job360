# Progress — fixes shipped vs pending

> Live tracker for the audit findings. Updated as fixes land. Each shipped item
> names the commit-level change + how it was verified. Honest status only — a thing
> is "✅ Fixed" only when it's coded AND verified, not when it's planned.

## ✅ Fixed (coded + verified)

| # | Finding | Doc | What was done | Verified by |
|---|---|---|---|---|
| 1 | **Dead ARQ worker** (P0) — `ctx['db']` never set, every cron crashed | `04-OPS` | Added `on_startup`/`on_shutdown` to `WorkerSettings` opening the DB connection + wiring `enqueue`; set `max_jobs=1` so the shared connection is never used concurrently | New regression test `test_worker_startup_populates_ctx` + full `test_worker_tasks.py` (15 passed) |
| 2 | **Session cookie may ship without `Secure`** (P1) — gated on unused `JOB360_ENV` | `01-SECURITY` | Cookie `secure` now uses the same prod check as the rest of the app (`APP_ENV=production` OR `RAILWAY_ENVIRONMENT`) | ruff + parse; no test depended on `JOB360_ENV` |
| 3 | **Sentry `send_default_pii=True`** ships cookies/PII (P1) | `01`, `04`, `05` | Set `send_default_pii=False` + added a `before_send` scrubber stripping Cookie/Authorization headers, cookies, and request bodies | ruff + parse; prod-gated init unchanged |

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
