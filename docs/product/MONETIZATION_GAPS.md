# Monetization Gaps — What's Missing to Make Money
<!-- doc: LIVING -->

> **What this is.** An honest gap analysis of everything between "Job360 is a working app" and "Job360 is a SaaS that charges money." Created 2026-06-22 from a code-verified audit.
>
> **What this is NOT.** A feature plan (see `LAUNCH_PLAN.md`) or a status doc (see `STATUS.md`). This answers one question: *what stops me taking real money today, and in what order do I fix it?*
>
> **The headline.** The **product is ~90% built**; the **business around it is ~30% built**. The remaining work is a different discipline — payments, metrics, legal — not features.
>
> **Correction 2026-08-24 (doc truth check).** This doc listed hosting, a
> domain, Postgres and Sentry as gaps. All four shipped on 2026-07-02. They are
> struck through below. Listing shipped infrastructure as a gap is the
> expensive kind of stale: a reader goes and builds it twice. The payments /
> Stripe gap is still real and still unbuilt — verified, no Stripe anywhere in
> `backend/src/`.

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
| ~~A live website~~ **SHIPPED** | Live on Railway at **job360.uk** since 2026-07-02 — five services (backend, frontend, worker, Postgres, Redis). No longer a gap. |
| **Legal-to-charge** | `/privacy` + `/terms` pages exist ✅; but **ICO registration** (UK — CVs are personal data) + lawyer review are still owner to-dos. |

## 🟠 Tier 2 — Keep customers & don't bleed money

| Missing | Why it matters |
|---|---|
| **Notifications firing in prod** (Redis + ARQ deployed) | The core pitch is "works while you sleep" — dead in prod until the worker runs. |
| **Email backbone (SES)** | Gmail SMTP won't send receipts / resets / alerts at scale. |
| **LLM cost control + per-user quota** | Every search burns paid LLM calls. No caps = a few heavy/free users (or abuse) drain the wallet. No cost tracking exists. |
| **Scale + reliability** — *partly shipped* | Postgres via psycopg3 since 2026-07-02 (the SQLite lock problem is gone), Sentry wired at `backend/src/api/main.py:82-98`, nightly `db-backup` workflow. What REMAINS: load testing, and alerting anyone actually reads. |

## 🟡 Tier 3 — Grow revenue (flying blind without these)

| Missing | Why |
|---|---|
| ~~**Analytics + funnel**~~ **PARTLY SHIPPED** | PostHog **is** wired and consent-gated (`posthog-js` in `frontend/package.json:38`, mounted at `frontend/src/app/layout.tsx:66`, init at `PostHogProviderWrapper.tsx:61-80`), and the signup→activation funnel is instrumented: `signup_completed`, `cv_uploaded`, `extraction_completed`, `search_run`, `job_viewed`, `application_created`, `$pageview`. What REMAINS is the **paid → churn** half, which needs the Stripe work in Tier 1 — there is no revenue event to track yet. |
| **Onboarding** | Get users to the "wow" (CV → first great matches) fast = activation = conversion. |
| **Subscription management UI** | Upgrade / downgrade / cancel / invoices / billing history. |
| **Lifecycle emails + support** | Welcome, trial-ending, payment-failed, receipts; a contact/support channel. |

---

## Shortest path to first revenue

These four make it **charge** (everything in Tier 3 makes it **grow**):

1. ~~**Deploy it live**~~ — **DONE 2026-07-02**: Railway + job360.uk. Left here so the ordering below still reads.
2. **Stripe + a free/Pro paywall with a usage cap** — the cap is the hard, valuable part, not the Stripe wiring.
3. **Prod notifications + SES working** — so the product's promise is real and emails send.
4. **ICO registration + a working contact email** — legal to operate + take money.

---

## Traps to avoid (the non-obvious bits)

- **The paywall matters more than the payment.** Stripe is a weekend; deciding *what's free vs paid* and *enforcing the quota* is what creates the reason to pay. Without a gate, even with Stripe wired, conversion ≈ 0.
- **The cost trap.** The product calls paid LLMs on every search. A free tier with no LLM-cost cap can lose money on every signup. Monetization here = making sure each user's revenue > their LLM cost. That gate must exist *before* opening the doors.
- **Built ≠ shippable.** The 10% that's left (payments, metrics, legal) is the entire gap between "cool tool" and "business." It feels small but it's a different kind of work.

---

*See also: `LAUNCH_PLAN.md` (phased roadmap), `docs/harness/ExecutionOrder.md` (Step 4 ops hardening), `docs/product/plans/2026-06-21-free-premium-plans.md` (free/premium design), `STATUS.md` (what's shipped).*
