# 01 — Security

> Source: backend security sweep (Opus). Read-only audit, evidence as `file:line`.
> **Headline:** backend is well above average for a solo build — clean IDOR/tenant
> scoping, parameterized SQL, fail-closed secrets, argon2 + hashed single-use tokens.
> One finding matters for the *live* Railway deploy. The rest is hardening.

## What's already strong (don't touch, just know)
- **IDOR discipline is excellent.** Every per-user route uses `Depends(require_user/require_verified_user)` and scopes by `user.id`; no route accepts `user_id` from path/body. OAuth state nonces enforce ownership (`channels.py:305`). This is the #1 thing most apps get wrong — you got it right.
- **SQL is parameterized** everywhere; the only f-string SQL uses internal column/table constants, never user input.
- **Secrets fail closed** — `SESSION_SECRET` (`auth_deps.py:37`) and `CHANNEL_ENCRYPTION_KEY` (`crypto.py:28`) raise if missing; no insecure committed defaults.
- **argon2id** params are OWASP-grade; tokens are 256-bit, SHA256-hashed at rest.

---

## P1 — Session cookie may ship WITHOUT `Secure` on the live Railway deploy
> **STATUS: FIXED** — `auth.py:_set_session_cookie` now gates on `APP_ENV==production OR RAILWAY_ENVIRONMENT` (the check the rest of the app uses). NOTE: confirm `APP_ENV` is actually set on the live service.
- **What I saw:** `backend/src/api/routes/auth.py:109` sets `secure = os.environ.get("JOB360_ENV") == "prod"`. But every other prod check uses a *different* variable — `middleware.py:36`, `main.py:57`, `settings.py:277` test `APP_ENV == "production"` OR `RAILWAY_ENVIRONMENT`. `JOB360_ENV` appears **only** at `auth.py:109`, nowhere else.
- **Why it matters:** On Railway, `RAILWAY_ENVIRONMENT` is set but `JOB360_ENV` almost certainly isn't. So HSTS turns on, but the **30-day session cookie is issued without `Secure`** — it can ride a plain-HTTP request (first visit before HSTS pins, an http→https redirect, a non-HSTS subdomain) and be sniffed. This is your live production app.
- **Fix (P1, 5 min):** Set `JOB360_ENV=prod` in Railway **today** as the stop-gap, then unify the check on the same helper the rest of the app uses (`APP_ENV`/`RAILWAY_ENVIRONMENT`) so it can't drift again. Also confirm the cookie sets `HttpOnly` + `SameSite=Lax` (it does).

## P2 — Auth rate-limits & brute-force lockout are per-process in-memory
> **STATUS: FIXED (opt-in)** — `5cfe292`. Redis-backed sliding window behind `RATE_LIMIT_REDIS` (atomic Lua `EVAL`, shared across replicas), falling back to in-memory on ANY Redis error. Default OFF = byte-identical to before; set `RATE_LIMIT_REDIS=true` + `REDIS_URL` to activate in prod.
- **What I saw:** `backend/src/services/auth/rate_limit.py:25-30` — module-level dicts guarded by a `threading.Lock`. Drives login lockout, magic-link, password-reset, verify-email limits. Docstring admits "not race-safe across processes … a restart wipes the buckets."
- **Why it matters:** With multiple uvicorn workers or Railway replicas, an attacker's attempts fan out across processes → effective limit is `N × configured`. Every deploy resets all counters. Weakens brute-force and SMTP-amplification guards.
- **Fix:** Back the limiter with **Redis** (already a dependency via ARQ; `REDIS_URL` exists). The `check_and_record` API was designed for exactly this swap.

## P2 — Login leaks account existence (timing + duplicate-register)
> **STATUS: PARTIAL** — the timing side-channel is FIXED (`3f532b2`: `_DUMMY_PW_HASH` makes every login run exactly one argon2 verify). The duplicate-register 409 REMAINS and is deliberate — you cannot let someone register without telling them the address is taken.
- **What I saw:** `auth.py:191` — `if row is None or not verify_password(...)`. Python short-circuits: unknown email returns instantly (no argon2), a real email always runs the ~50-100 ms verify. Register also 409s on duplicate email (`auth.py:136-140`).
- **Why it matters:** Timing the response enumerates which emails have accounts — undoing the no-enumeration care taken in the magic-link/reset flows.
- **Fix:** When `row is None`, run `verify_password` against a fixed dummy argon2 hash so both paths cost the same. Consider a generic message on duplicate-register.

## P2 — Sentry `send_default_pii=True` can ship cookies/passwords to a third party
> **STATUS: FIXED** — `core/observability.py` sets `send_default_pii=False` plus a `_scrub_pii` before_send hook, shared by API and worker.
- **What I saw:** `backend/src/api/main.py:63-67` — `sentry_sdk.init(..., send_default_pii=True)` with auto FastAPI integration.
- **Why it matters:** Every captured event attaches request headers (the `Cookie:` with the session token) + client IP; an exception inside `/auth/login` can forward the submitted password body to Sentry. Secrets crossing the trust boundary. (Also a compliance issue — see `05-COMPLIANCE-AND-LEGAL.md`.)
- **Fix:** Set `send_default_pii=False` and add a `before_send` scrubber stripping `Cookie`/`Authorization` headers and `/auth/*` bodies.

## P2 — Untrusted XML feeds parsed with stdlib ElementTree (billion-laughs DoS)
> **STATUS: FIXED** — `sources/base.py::_sanitize_xml` strips `<!DOCTYPE>` / `<!ENTITY>` before parsing, closing the entity-expansion vector for all 11 feed/ATS sources.
- **What I saw:** `ET.fromstring(...)` in 11 feed/ATS sources (e.g. `sources/feeds/nhs_jobs.py:46`, `ats/personio.py:51`). The sanitizer `sources/base.py:23-29` escapes bare `&` and strips control chars but does **not** remove `<!DOCTYPE>`/`<!ENTITY>`.
- **Why it matters:** expat expands internal entities → a malicious/compromised upstream feed can hang or OOM the fetch worker. Bounded by the 60 s fetch timeout, so moderate.
- **Fix:** Parse with `defusedxml.ElementTree`, or strip any `DOCTYPE` block before `fromstring`.

## P2 — CSRF defence rests entirely on `SameSite=Lax`; a few GETs mutate state
> **STATUS: FIXED (both halves)** — `ae10834` added `OriginCheckMiddleware` (403 on unsafe methods carrying a non-allowlisted Origin); `4af1c7b` converted the side-effecting `GET /tailor/.../download` to **POST** so the middleware actually covers it.
- **What I saw:** cookie is `samesite="lax"` (`auth.py:110-117`); no CSRF token or Origin/Referer check anywhere. But `GET /tailor/{job_id}/{doc_kind}/download` (`tailor.py:240`) marks the doc KEPT + triggers pattern-learning — a side-effecting GET.
- **Why it matters:** `Lax` sends the cookie on top-level GET navigation, so an attacker link can trigger those GET side effects (only on the victim's own data — low impact, but zero defence-in-depth).
- **Fix:** Make state-changing endpoints non-GET, and add an Origin allowlist check (reuse `FRONTEND_ORIGIN`) on unsafe methods.

## P2 — Lockout DoS + IP-only reset limit enable targeted harassment
> **STATUS: FIXED** — `1b3519f`. Lockout keyed `login:<email>:<ip>`, so an attacker only locks their own (email, IP) bucket — the real user on another IP still signs in.
- **What I saw:** lockout keyed on **email only** (`rate_limit.py:90-119`); password-reset limited per **IP hash** 1/min (`auth.py:386-390`).
- **Why it matters:** (a) an attacker can keep any victim permanently locked out by spamming failures for their email; (b) IP-only reset key is bypassed by IP rotation → email-bomb a victim's inbox.
- **Fix:** Add an IP dimension to lockout (email+IP); key reset throttling on email as well as IP.

## Informational
- **Soft-deleted accounts resurrect on magic-link consume** — `services/auth/magic_link.py:182-186` sets `deleted_at = NULL`. A GDPR-deleted user's row un-deletes if a link for that email is later consumed. Requires the deleted user to click, so low risk, but conflicts with the Article-17 delete intent (`auth.py:238`). See compliance doc.

---

## Fix order (security)
1. **P1 cookie `Secure`** — set `JOB360_ENV=prod` on Railway now; unify the env check this week.
2. **P2 Sentry PII** — one-line flag flip + scrubber (also compliance-relevant).
3. **P2 Redis-backed rate limits** — before you scale past one replica.
4. Remaining P2s (timing, XML, CSRF, lockout) — batch into a hardening PR.

**Verdict:** No open doors. One live misconfiguration (cookie Secure) worth fixing today; everything else is defence-in-depth that a solo founder can schedule, not scramble on.
