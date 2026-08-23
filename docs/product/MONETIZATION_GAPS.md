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
| Product (features) | ~90% | engine + API + UI + auth + profiles + 4 scoring engines + control surface; 1,571 tests green |
| Launch readiness (ship it live) | ~30% | no CI gate, no Dockerfile/deploy, no prod Redis+ARQ, no SES |
| **Money layer (charge for it)** | **~5%** | **no Stripe, no billing, no plans, no pricing page, no analytics** |

**What already exists (the hard part is done):** the working product (CV → scored jobs → pipeline → notifications), auth/sessions, profiles, `/privacy` + `/terms` pages, GDPR account-delete (password-gated).

---

## 🔴 Tier 1 — Can't charge a single £ without these

| Missing | Reality (verified) |
|---|---|
| **Payments** | No Stripe, no billing route, no checkout. £0 is collectable. |
| **Plans + paywall** | No free/premium tiers, no quota enforcement, no pricing page. Design exists (`docs/product/plans/2026-06-21-free-premium-plans.md`) — not built. Without a gate, nobody has a reason to upgrade. |
| **A live website** | Runs only on localhost. No hosting, no domain — customers can't reach it. |
| **Legal-to-charge** | `/privacy` + `/terms` pages exist ✅; but **ICO registration** (UK — CVs are personal data) + lawyer review are still owner to-dos. |

## 🟠 Tier 2 — Keep customers & don't bleed money

| Missing | Why it matters |
|---|---|
| **Notifications firing in prod** (Redis + ARQ deployed) | The core pitch is "works while you sleep" — dead in prod until the worker runs. |
| **Email backbone (SES)** | Gmail SMTP won't send receipts / resets / alerts at scale. |
| **LLM cost control + per-user quota** | Every search burns paid LLM calls. No caps = a few heavy/free users (or abuse) drain the wallet. No cost tracking exists. |
| **Scale + reliability** | SQLite locks under concurrent load (bug #11) → Postgres for paying users; plus DB backups + error monitoring (no Sentry). |

## 🟡 Tier 3 — Grow revenue (flying blind without these)

| Missing | Why |
|---|---|
| **Analytics + funnel** | No signup → activation → paid → churn tracking (no PostHog/GA). Can't optimize money you can't measure. |
| **Onboarding** | Get users to the "wow" (CV → first great matches) fast = activation = conversion. |
| **Subscription management UI** | Upgrade / downgrade / cancel / invoices / billing history. |
| **Lifecycle emails + support** | Welcome, trial-ending, payment-failed, receipts; a contact/support channel. |

---

## Shortest path to first revenue

These four make it **charge** (everything in Tier 3 makes it **grow**):

1. **Deploy it live** — hosting + domain (Step 4 ops).
2. **Stripe + a free/Pro paywall with a usage cap** — the cap is the hard, valuable part, not the Stripe wiring.
3. **Prod notifications + SES working** — so the product's promise is real and emails send.
4. **ICO registration + a working contact email** — legal to operate + take money.

---

## Traps to avoid (the non-obvious bits)

- **The paywall matters more than the payment.** Stripe is a weekend; deciding *what's free vs paid* and *enforcing the quota* is what creates the reason to pay. Without a gate, even with Stripe wired, conversion ≈ 0.
- **The cost trap.** The product calls paid LLMs on every search. A free tier with no LLM-cost cap can lose money on every signup. Monetization here = making sure each user's revenue > their LLM cost. That gate must exist *before* opening the doors.
- **Built ≠ shippable.** The 10% that's left (payments, hosting, metrics, legal) is the entire gap between "cool tool" and "business." It feels small but it's a different kind of work.

---

*See also: `LAUNCH_PLAN.md` (phased roadmap), `docs/harness/ExecutionOrder.md` (Step 4 ops hardening), `docs/product/plans/2026-06-21-free-premium-plans.md` (free/premium design), `STATUS.md` (what's shipped).*
