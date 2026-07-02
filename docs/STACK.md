# Job360 — Tech Stack: What We Have vs What to Add

> **What this is.** The current technology stack, layer by layer, with what's already built and what's still missing to ship + monetize. Code-verified 2026-06-30.
>
> **How to read it.** The "Add" column splits into two jobs: **build/ops** (makes it shippable) and **business** (makes it a paying product).
>
> **⚠️ Re-synced 2026-07-02.** This file had drifted out of sync with its own companion `STACK_checklist.md` — several rows below marked shipped infra (Postgres, Sentry, PostHog, Docker, Resend, Railway) as "to add" when the checklist already had them as ✅ done. Fixed below; see `docs/maintenance/DOCSYNC.md` for the audit.

---

## Stack table

| Section | ✅ Have now | ➕ To add | Cost |
|---|---|---|---|
| **Backend** | Python 3.9+, FastAPI, uvicorn, httpx, aiohttp | — (solid) | free |
| **Background jobs** | ARQ (async task queue, Redis-backed) | Deploy the ARQ worker live (built, not running in prod) | free |
| **Database** | **PostgreSQL** (psycopg3) — `backend/src/repositories/pg.py`, `database.py` imports it as the SQLite shim replacement | — (done) | free tier |
| **Vector store** | ChromaDB | Optional: **pgvector** (fold vectors into Postgres) | free |
| **Frontend** | Next.js 16, React 19, Tailwind 4, shadcn/ui | — (ahead of typical stacks) | free |
| **AI / LLM** | Claude, OpenAI, Gemini, Groq, Cerebras (direct, multi-provider fallback) | **LLM cost tracking**; optional gateway (LiteLLM/OpenRouter) | ⚠️ pay-per-use |
| **Auth** | Custom: argon2 passwords + signed cookies + sessions | — (fine; swap to Supabase/Clerk later if desired) | free |
| **Payments** | ❌ none | **Stripe** — the money switch | ~2–3% of sales |
| **Plans / paywall** | ❌ none (design only) | **Free/Premium tiers + usage quota + pricing page** | free |
| **Email** | Resend (HTTP API) + apprise — `backend/src/services/auth/email_sender.py` posts to `api.resend.com` | — (done) | ~free at low volume |
| **Notifications** | apprise multi-channel (email/Slack/Discord) | Deploy Redis + worker so they fire in prod | free |
| **Encryption / secrets** | Fernet (cryptography), env vars | Secrets manager + boot-time env validation | free |
| **Logging** | File logs + correlation IDs + `audit.log` | Fill dark zones (workers, DB, auth, notifications) | free |
| **Error monitoring** | **Sentry** — `backend/src/api/main.py` `sentry_sdk.init` + frontend `@sentry/nextjs` | — (done) | free tier |
| **Product analytics** | **PostHog** — `frontend/src/components/providers/PostHogProviderWrapper.tsx` | — (done) | free tier |
| **Containers** | `docker-compose.dev.yml` + **Dockerfile (backend/frontend) + `docker-compose.prod.yml`** | — (done) | free |
| **Deployment / hosting** | **Railway** — see `docs/DEPLOY.md` (then Hetzner VPS later if needed) | — (done) | ~$5–20/mo |
| **Domain** | ❌ none | Buy a domain | ~$12/yr |
| **CI/CD** | GitHub Actions (`ci-offline`, `live-e2e`) | Full gate (pytest + ruff + mypy + build) + enable on GitHub | free |
| **Scaling / reliability** | Postgres + DB backup script (`backend/scripts/backup_db.py`) | uptime monitor | free tier |
| **Legal / compliance** | `/privacy` + `/terms` pages, GDPR delete | **ICO registration** + lawyer review of privacy/LIA | £40 once |
| **Job data sources** | 47 free/keyed sources | Optional: paid aggregator (Fantastic Jobs) for LinkedIn/Indeed | ~$1/1k |
| **Onboarding** | basic register → CV → search | Guided first-run flow (drives activation → paid) | free |
| **Support** | ❌ none | Contact/support email + help/FAQ | free |

---

## Additional layers (added 2026-06-30 after deeper code audit)

| Section | ✅ Have now | ➕ To add | Cost |
|---|---|---|---|
| **Testing** | pytest (111 files / 1,571 tests) + vitest + Playwright e2e | wire full suite into the CI gate | free |
| **Redis (broker/cache)** | used by ARQ (`REDIS_URL`) | deploy a managed Redis alongside the app | free tier |
| **Feature flags** | env toggles (`ENRICHMENT_/SEMANTIC_/MATCHER_ENABLED`) | optional: a flags dashboard later | free |
| **Security — CORS** | ✅ CORSMiddleware (`FRONTEND_ORIGIN` allow-list) | — | free |
| **Security — headers** | ❌ none | add CSP / HSTS / X-Frame-Options middleware | free |
| **File storage (CV uploads)** | temp file (`save_upload_to_temp`), parsed, then discarded — **not** persisted | add object storage (S3/R2) only if you keep raw CVs | ~free |
| **API rate limiting** | auth only (sliding-window) | add a global limit for abuse protection | free |

## Priority order to launch + charge

1. **PostgreSQL** — replace SQLite so many users can write at once.
2. **Dockerfile + Railway deploy** — get it off the laptop onto a live URL.
3. **Stripe + Free/Premium paywall + usage quota** — collect money + cap AI cost.
4. **Resend/SES email** — receipts, resets, notifications.
5. **Sentry + PostHog** — catch errors, measure growth.
6. **LLM cost tracking** — keep each user's revenue > their AI cost.
7. **ICO registration + domain** — legal + reachable.

## Cost reality (solo founder, small scale)
- **Must pay:** hosting ~$5–20/mo · domain ~$12/yr · LLM calls (variable — watch this).
- **Free at your size:** Postgres, Docker, Sentry, PostHog, email (low volume), ChromaDB.
- **Pay only when you earn:** Stripe (a cut per sale).
- **One-time:** ICO £40.
- **Rough monthly to be live: ~$10–25 + your AI usage.**

`★ The two traps`
- **Paywall > payments.** Stripe is a weekend; deciding free-vs-paid and *enforcing a usage cap* is what creates the reason to pay AND stops AI cost from outrunning revenue.
- **Built ≠ shippable.** The product is ~90% built; the remaining gap is ops + business (Postgres, deploy, Stripe, legal) — a different discipline, not features.

---

*See also: `MONETIZATION_GAPS.md` (what's missing to charge), `LAUNCH_PLAN.md` (phased roadmap), `docs/ExecutionOrder.md` (Step 4 ops hardening), `STATUS.md` (what's shipped).*
