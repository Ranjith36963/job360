# Monetization Gaps — What's Missing to Make Money

> **What this is.** An honest gap analysis of everything between "Job360 is a working app" and "Job360 is a SaaS that charges money." Created 2026-06-22 from a code-verified audit.
>
> **What this is NOT.** A feature plan (see `LAUNCH_PLAN.md`) or a status doc (see `STATUS.md`). This answers one question: *what stops me taking real money today, and in what order do I fix it?*
>
> **The headline.** The **product is ~90% built**; the **business around it is ~30% built**. The remaining work is a different discipline — payments, hosting, metrics, legal — not features.

---

## Where we are (code-verified 2026-06-22)

| Dimension | % | Evidence |
|---|---|---|
| Product (features) | ~90% | engine + API + UI + auth + profiles + 4 scoring engines + control surface; 1,636/1,638 tests green |
| Launch readiness (ship it live) | **~90% — DONE** (2026-07-02) | CI gate live (`.github/workflows/ci.yml`, `ci-offline.yml`), Dockerfiles (`backend/Dockerfile`, `Dockerfile.worker`, `frontend/Dockerfile`) + `docker-compose.prod.yml`, Postgres (`database.py:5`), Resend email (`services/auth/email_sender.py`), prod Redis+ARQ on Railway, Sentry (`api/main.py`) + PostHog (`PostHogProviderWrapper.tsx`) all shipped — see `docs/maintenance/DOCSYNC.md` |
| **Money layer (charge for it)** | **~5% — still the gap** | **no Stripe, no billing, no plans, no pricing page, no LLM cost tracking** (verified: no `stripe` payment code, no `llm_usage`/cost-tracking table in `backend/src`) |

**What already exists (the hard part is done):** the working product (CV → scored jobs → pipeline → notifications), auth/sessions (+ magic-link, password reset, email verification, login lockout), profiles, `/privacy` + `/terms` pages, GDPR account-delete (password-gated), and — new since this doc was written — the whole launch-readiness tier (Postgres, Docker, CI, Resend, Sentry, PostHog, Railway hosting).

---

## 🔴 Tier 1 — Can't charge a single £ without these

| Missing | Reality (verified) |
|---|---|
| **Payments** | No Stripe, no billing route, no checkout. £0 is collectable. |
| **Plans + paywall** | No free/premium tiers, no quota enforcement, no pricing page. Design exists (`docs/plans/2026-06-21-free-premium-plans.md`) — not built. Without a gate, nobody has a reason to upgrade. |
| **A live website** ✅ DONE | Live on Railway (`frontend-production-c608f.up.railway.app`, per project memory) — no longer localhost-only. |
| **Legal-to-charge** | `/privacy` + `/terms` pages exist ✅; but **ICO registration** (UK — CVs are personal data) + lawyer review are still owner to-dos. |

## 🟠 Tier 2 — Keep customers & don't bleed money

| Missing | Why it matters |
|---|---|
| ~~**Notifications firing in prod** (Redis + ARQ deployed)~~ ✅ DONE | ARQ worker + Redis deployed on Railway (`Dockerfile.worker`, `workers/settings.py`). |
| ~~**Email backbone**~~ ✅ DONE (Resend, not SES) | Resend HTTP API wired in `services/auth/email_sender.py:60-85` (SES was the original plan; Railway blocks SMTP ports so Resend was used instead). |
| **LLM cost control + per-user quota** | Every search burns paid LLM calls. No caps = a few heavy/free users (or abuse) drain the wallet. No cost tracking exists — verified: no `llm_usage` table or cost-tracking code in `backend/src`. |
| **Scale + reliability** — partially DONE | Postgres now live (`database.py:5`) fixing the SQLite-lock concern (bug #11); Sentry now live (`api/main.py`) for error monitoring. DB backups still unverified. |

## 🟡 Tier 3 — Grow revenue (flying blind without these)

| Missing | Why |
|---|---|
| **Analytics + funnel** — PostHog wired ✅, funnel dashboards not built | PostHog is now live (`PostHogProviderWrapper.tsx`) capturing pageviews + identify/reset, but signup → activation → paid → churn *funnel tracking/dashboards* still need to be built on top of it. |
| **Onboarding** | Get users to the "wow" (CV → first great matches) fast = activation = conversion. |
| **Subscription management UI** | Upgrade / downgrade / cancel / invoices / billing history. |
| **Lifecycle emails + support** | Welcome, trial-ending, payment-failed, receipts; a contact/support channel. |

---

## Shortest path to first revenue

These four make it **charge** (everything in Tier 3 makes it **grow**):

1. ~~**Deploy it live** — hosting + domain (Step 4 ops).~~ ✅ DONE — live on Railway.
2. **Stripe + a free/Pro paywall with a usage cap** — the cap is the hard, valuable part, not the Stripe wiring. **Still not built** — this is now the single biggest blocker to first revenue.
3. ~~**Prod notifications + SES working**~~ ✅ DONE — Resend (not SES) + ARQ/Redis on Railway, emails send.
4. **ICO registration + a working contact email** — legal to operate + take money. Still owner to-do.

---

## Traps to avoid (the non-obvious bits)

- **The paywall matters more than the payment.** Stripe is a weekend; deciding *what's free vs paid* and *enforcing the quota* is what creates the reason to pay. Without a gate, even with Stripe wired, conversion ≈ 0.
- **The cost trap.** The product calls paid LLMs on every search. A free tier with no LLM-cost cap can lose money on every signup. Monetization here = making sure each user's revenue > their LLM cost. That gate must exist *before* opening the doors.
- **Built ≠ shippable.** The 10% that's left (payments, hosting, metrics, legal) is the entire gap between "cool tool" and "business." It feels small but it's a different kind of work.

---

*See also: `LAUNCH_PLAN.md` (phased roadmap), `docs/ExecutionOrder.md` (Step 4 ops hardening), `docs/plans/2026-06-21-free-premium-plans.md` (free/premium design), `STATUS.md` (what's shipped).*
