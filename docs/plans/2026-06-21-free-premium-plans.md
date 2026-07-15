# Plan — Free / Premium subscription tiers (DEFERRED)

**Status:** 📌 DEFERRED — captured 2026-06-21, **not started**. Build later.
**Replaces:** the old "admin RBAC (#13)" idea, which is **scrapped**. Job360 has
no admin role model. Access is split by **subscription plan**, like a normal SaaS.

## The idea (plain words)

Every user has a **plan**: `free` or `premium`.
- **Free** = a taste of the product (limited).
- **Premium** = everything unlocked.

Same mechanism that "admin RBAC" would have used (a field on the user + a check on
routes), but the *meaning* is subscription tier, not admin.

## Why it's cheap to build

The codebase already has the on/off levers. Premium = flip them on; Free = keep
them limited. We are **gating features that already exist**, not building new ones:
- `ENGINE1..4_ENABLED` — keyword / dimensions / semantic / LLM-judge engines.
- `MAX_CONCURRENT_SEARCHES_PER_USER` + (future) a daily search cap.
- Notification rules + dispatcher (email / Slack / Discord).
- CSV export (`/api/jobs/export`).

## Proposed Free vs Premium split (decide before building)

| Feature | Free | Premium |
|---|---|---|
| Searches | few per day (cap) | unlimited |
| Job sources | a subset | all 47 |
| Scoring | keyword only (Engine 1) | all 4 engines + AI judge verdicts |
| Notifications (email/Slack/Discord) | ❌ | ✅ |
| CSV export | ❌ | ✅ |
| Jobs shown | top N | all |

> ⚠️ Open product question: confirm these lines. (e.g. AI verdicts free but
> notifications premium? a 7-day premium trial?)

## Build pieces

### Phase 1 — plan system (no payment, works end-to-end)
1. **DB** — migration: add `plan TEXT NOT NULL DEFAULT 'free'` to `users`
   (optionally `plan_expires_at`). Backfill existing user to `free` (or `premium`
   for yourself, by hand, to test).
2. **Backend** — a `require_premium` FastAPI dependency (mirrors `require_user` /
   `require_verified_user` in `auth_deps.py`): free user hitting a premium route
   → `402 Payment Required` (or a limited result). Per-plan limits enforced in the
   search path (daily cap, source subset, engine gating).
3. **Frontend** — show the user's plan; lock premium UI behind an "Upgrade"
   button; show what premium unlocks. New `plan` field on the `/me` response.
4. **Tests** — free vs premium on each gated route (402 vs 200), like the #15
   email-enforcement tests (`test_email_enforcement.py`).

### Phase 2 — real payment (Stripe)
- Stripe Checkout session + a webhook that flips `free → premium` on successful
  payment and back on cancel/expiry. Store the Stripe customer/subscription id on
  the user. Reconcile on `plan_expires_at`.

## Notes
- Keep `require_verified_user` (#15) — verification and plan are independent gates.
- The `/admin/sources` ops/health page stays as-is for now (read-only, low risk);
  decide later whether it's premium-only or stays open.
