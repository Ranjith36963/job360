# 09 — Production Signals (what your REAL prod data shows)

> Not code review — this is what your live Sentry + PostHog actually report. Pulled
> 2026-07-12 from the `job360` Sentry org and PostHog project 213945. This is the
> "what's actually happening to real users" half the code audit couldn't see.

## The headline
Your production is **live but effectively pre-traffic** — Sentry shows only 2 issues
and ~2-3 users total in 30 days. That's the honest context for everything below: your
low error count reflects **low usage, not proven robustness**. The P0s in the code audit
(dead worker, single DB connection) haven't bitten yet because almost no one is using it.
They will, the moment traffic arrives.

---

## P0 (observability) — Your worker crashes are INVISIBLE to Sentry
> **STATUS: FIXED** — `272d29b`. `init_sentry(component='worker')` runs in `worker_startup`, sharing `core/observability.py` with the API.
- **What the data shows:** Sentry reports the app as "healthy" (2 minor issues). But `grep sentry src/workers/` returns **nothing** — Sentry is initialised only in the API's `lifespan` (`main.py`), never in the ARQ worker process.
- **Why it matters:** the dead-worker P0 (`04-OPS`) produces `KeyError: 'db'` on every cron tick — and **none of it reaches Sentry**, because the worker doesn't report. Your error tracker is blind exactly where your biggest bug lives. "Sentry is quiet" has been giving false comfort.
- **Fix:** call `sentry_sdk.init(...)` in the worker's `on_startup` (reuse `_init_sentry` from `main.py`). Then the dead-worker errors (and any future worker failure) actually page you. **Do this alongside the worker fix — otherwise you fix the worker but still can't see if it breaks again.**

## P1 (real bug, already fixed — just close it out) — Magic-link 500 on launch day
> **STATUS: CLOSED** — resolved live via the Sentry API (`PYTHON-FASTAPI-2`) with the explanation posted to the issue. Zero events in 11 days confirmed the code fix held.
- **What the data shows:** `PYTHON-FASTAPI-1` — `UniqueViolation: users_email_key` at `/api/auth/magic-link/consume`, 2026-07-02 (launch day), 1 user (your own test email), 1 occurrence.
- **Status:** **already fixed in current code** — `magic_link.py:173` now uses `INSERT OR IGNORE` (find-or-create atomic); the crash predates that fix. The Sentry issue is just stale/unresolved.
- **Fix:** confirm the fix is deployed to prod, then **resolve the Sentry issue** so it stops looking like an open problem. ⚠️ Note: the fix reactivates soft-deleted accounts on magic-link sign-in (`deleted_at = NULL`) — correct for UX, but conflicts with the GDPR-erasure item in `05-COMPLIANCE`. Reconcile the two.

## P2 — `/api/client-log` is generating Sentry errors (noise or signal?)
> **STATUS: FIXED** — `client_log.py` level-gates (error/warning/info) and `observability.py` `ignore_logger('job360.client')` keeps client noise out of Sentry.
- **What the data shows:** `PYTHON-FASTAPI-2` — `client_event` at `/api/client-log`, **30 events, 2 users, one day** (~7 days ago). This is the frontend→server log bridge (and where the synthetic-smoke test POSTs failures).
- **Why it matters:** either (a) real client-side errors you should look at, or (b) client logs being recorded as *backend* Sentry errors = noise that will bury real signal as traffic grows. 30 events in one day from 2 users is a burst worth explaining.
- **Fix:** investigate the 30 events; make sure client-log entries only reach Sentry when they're genuine errors, not routine client events (level-gate them).

---

## P1 (product) — You cannot measure your funnel: no product events instrumented
> **STATUS: FIXED** — `63964ec`. Six events: signup_completed, cv_uploaded, extraction_completed, search_run, job_viewed, application_created. NOTE: now gated on consent (fable/05 C3) — PostHog does not initialise until the user accepts.
- **What the data shows:** PostHog tracks only built-in events — `$pageview`, `$identify`, `$exception`, autocapture, `$web_vitals`. There are **zero** custom Job360 events: no `signup_completed`, `cv_uploaded`, `search_run`, `job_viewed`, `application_created`.
- **Why it matters:** you're **flying blind on the actual user journey.** You cannot answer "of users who sign up, how many upload a CV? see jobs? apply?" — the single most important question for a job-search product. Pageviews tell you *pages*, not *outcomes*. Every product decision is currently a guess.
- **Fix:** instrument ~6 key journey events with `posthog.capture()`: `signup_completed`, `cv_uploaded`, `extraction_completed`, `search_run`, `job_viewed`, `application_created`. Then build one funnel insight. This is small (a day) and turns your product from un-measurable to measurable — a genuine gate for "production-grade."

---

## What the signals DON'T show (and why that's not reassurance)
- **No performance/latency data** — traces sampled at 0.1 but no latency SLO or alert (`04-OPS`).
- **No error-rate alerting** — 2 issues sat unresolved for 7-9 days; nobody was paged.
- **The quiet is because of low traffic, not resilience.** With 2-3 users, the connection-pool ceiling and dead worker simply haven't been stressed. This is the calm before the load, not proof of stability.

## Fix order (production signals)
1. **P0 — Sentry in the worker** (bundle with the worker `on_startup` fix). Without it you can't even confirm the worker fix worked in prod.
2. **P1 — instrument 6 funnel events** in PostHog. Turns the product measurable.
3. **P1 — resolve the stale magic-link Sentry issue**; confirm the fix is live.
4. **P2 — level-gate `/api/client-log`** so it stops manufacturing Sentry noise.
5. Add an error-rate alert rule (`04-OPS`) so the next real spike pages you.

**Verdict:** Your production isn't on fire — but that's because almost no one is there yet.
The real finding is a **blind spot, not a blaze**: the worker is invisible to Sentry, the
funnel is invisible in PostHog, and nothing alerts on error rate. Fix the *visibility* now,
while traffic is low and mistakes are cheap — so that when users arrive, you can see what
they hit instead of learning about outages from a tweet.
