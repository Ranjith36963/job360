# 03 — Frontend & Auth-UI
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

> Source: frontend sweep (Sonnet), Next.js 16 + React 19. Read-only, evidence as `file:line`.
> **Headline:** core is clean — JSON-LD escaping, `safeUrl`, the API client, CSRF/cookie
> handling, and the Next-16 async-API usage all checked out. Two real fixes; the rest is
> UX/accessibility polish. The scary-sounding E2E bypass is **currently safe**.

## What checked out clean (don't worry about these)
- Next 16 async-API usage (params/cookies awaited correctly) — no violations found.
- No client-bundle secret leakage; no unsafe `dangerouslySetInnerHTML`.
- `tailor-cv.spec.ts` fixme is **test debt, not a product bug** — the UI moved to an inline `<TailorSection>` (`JobDetailClient.tsx:492-494`); the spec still expects the old button+dialog. Rewrite the spec eventually; nothing to fix in product code.

---

## P1 — Reset-password & verify-email pages leak raw `API error <status>: <detail>` to users
> **STATUS: FIXED** — `1c75b50`. Both pages route through `friendlyAuthError`.
- **What I saw:** `reset-password/page.tsx:62-70` and `verify-email/page.tsx:41-45` render `err.message`. But `api-error.ts:17` builds that message as the literal `"API error 400: <backend detail>"`. The files' *own comments* say "show a generic message — don't help an attacker distinguish cases." Login/register already fixed this by calling `friendlyAuthError` (`api-error.ts:59-87`); these two pages roll their own `instanceof Error` check that bypasses it.
- **Why it matters:** any 400/500 from the reset/verify endpoints shows `"API error 400: <detail>"` to the user. If the backend's `detail` ever differs between "token not found / expired / already used" (plausible in dev builds or a future change), this becomes a **token-enumeration side channel** — the exact thing the code says it's preventing.
- **Fix (P1, small):** replace both call sites with `friendlyAuthError(err, fallback)` (already imported elsewhere), or `apiErrorMessage(err, fallback)`.

## P1/P2 — E2E_TEST_MODE auth bypass: correctly gated today, but a single-env-var trust chain
> **STATUS: IT IS FIXED (2026-07-23, F2, PR #107).** The description below is now
> historical. By the time this was revisited the code had actually been simplified
> to gate SOLELY on `E2E_TEST_MODE=1` (the `NODE_ENV` half was dropped because CI
> e2e runs a prod build) — so the risk had grown, not shrunk. Fixed by additionally
> requiring `!process.env.RAILWAY_ENVIRONMENT` (`middleware.ts`): Railway injects
> that into every deployed service and CI/local never have it, so an accidental
> `E2E_TEST_MODE=1` on a real deploy can no longer bypass auth, while CI still works.
- **What I saw:** `middleware.ts:44-46` lets any request through when `NODE_ENV !== "production" && E2E_TEST_MODE === "1"`. Playwright sets `E2E_TEST_MODE=1` on a real prod build in CI. **The mitigation that makes this safe:** `frontend/Dockerfile:35` hardcodes `ENV NODE_ENV=production`, and `E2E_TEST_MODE` is never set in any deploy config (confirmed across workflows + `.env` examples).
- **Why it matters:** the gate's *entire* security rests on `NODE_ENV` staying `"production"` at runtime. Railway service variables **take precedence over Dockerfile `ENV`** — if someone ever copies a CI/staging `.env` (with `E2E_TEST_MODE=1`) into an environment where `NODE_ENV` is also unset/misconfigured, **any client can set `job360_session=anything` and walk past the guard** on every protected route. Backend `Depends(require_user)` still protects the *data* (rule #12), but the shell renders.
- **Fix (P2, harden):** don't trust a boolean guarded only by `NODE_ENV`. Gate the bypass behind a random build-time `E2E_TEST_TOKEN` baked into the Playwright-only build and compared constant-time — a value that simply never exists in the production build.

---

## P2 — polish (real, but not urgent)
> **STATUS: FIXED** — dashboard error state (`903af8d`), a11y `role=alert` (`7c7e78b`) + `aria-invalid` (`31010ca`). The middleware fail-OPEN is now fail-CLOSED (`b939e29`): a backend outage redirects to `/login?error=service_unavailable` WITHOUT deleting the cookie (the outage is ours; the session may still be valid), and the login page explains rather than looking like a logout.
- **Dashboard jobs query has no error handling** (`dashboard/page.tsx:106-114`, render `334-343`): a failed fetch (500 / expired session / network blip) leaves `jobsData` undefined and renders **"No jobs found yet"** — identical to a genuinely empty result, with no retry. The user thinks it's empty when it actually broke. **Fix:** destructure `isError`/`error`, render an explicit error + retry (the search path already does this correctly at `267-279`).
- **Middleware fails open when the backend is unreachable** (`middleware.ts:51-62`): during a backend outage, any non-empty cookie renders the protected shell instead of bouncing to login. Deliberate availability tradeoff, and no *data* leaks (API still enforces auth), but combined with cached TanStack data it slightly widens the window. **Fix:** treat a signed/short-TTL cookie as sufficient during outages rather than mere presence, and log/alert on fail-open events so outages don't silently mask auth gaps.
- **Accessibility on auth forms:**
  - Inputs lack `aria-invalid` / `aria-describedby` linking them to their error text (login/register/reset/forgot). Screen readers don't announce "email, invalid." **Fix:** add both to every RHF+zod field.
  - `register`, `forgot-password`, `reset-password` server errors lack `role="alert"` (login already has it) → errors render visually but aren't announced. **Fix:** add `role="alert"` to every `serverError` paragraph.

---

## Fix order (frontend)
1. **P1 auth-page error leak** — swap two call sites to `friendlyAuthError`. Small, closes an enumeration channel.
2. **P2 E2E bypass hardening** — independent secret instead of `NODE_ENV`-only trust. Do before any infra/env reshuffle.
3. **P2 dashboard error state** — users currently can't tell "broken" from "empty."
4. **A11y batch** — `aria-invalid` + `role="alert"` across auth forms; one small PR.

**Verdict:** No open data-leak on the frontend — backend auth is the real gate and it holds. The two P1s are a small error-string swap and a defence-in-depth hardening of an already-safe bypass. The rest is the polish that separates "works" from "enterprise-grade": never show a user a raw status code, never render "empty" when you mean "broken."
