# Plan — Free / Premium subscription tiers (DEFERRED)
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

**Status:** 📌 DEFERRED — captured 2026-06-21, **not started**. Build later.
**Replaces:** the old "admin RBAC (#13)" idea, which is **scrapped**. Job360 has
no admin role model. Access is split by **subscription plan**, like a normal SaaS.

## The idea (plain words)

Every user has a **plan**. Moved to a **3-tier model** (like ChatGPT Free/Pro/Max),
updated 2026-06-25:
- **Free** = the 46 free job boards only. Still real jobs, just limited.
- **Pro** = free boards + **paid real-time data** (Fantastic Jobs: LinkedIn/Indeed/ATS,
  hourly) + AI judge + notifications.
- **Max** = everything + **minute-fresh premium data** (TheirStack, 344k sources) +
  unlimited + priority.

Same mechanism that "admin RBAC" would have used (a field on the user + a check on
routes), but the *meaning* is subscription tier, not admin.

### Why the paid data is the wedge (the key economic point)
The paid providers (TheirStack / Fantastic Jobs — researched in memory
`reference_paid_job_aggregator_apis`) charge **per job fetched**. So live
LinkedIn/Indeed CANNOT be in Free — every free signup would cost money at zero
revenue. Free = the 46 free sources = **$0 marginal cost**. Pro/Max = paid data,
capped per plan to protect margin. That gating IS the business model.

## Why it's cheap to build

The codebase already has the on/off levers. Higher tier = flip them on; Free = keep
them limited. We are **gating features that already exist**, not building new ones:
- `ENGINE1..4_ENABLED` — keyword / dimensions / semantic / LLM-judge engines.
- `MAX_CONCURRENT_SEARCHES_PER_USER` + (future) a daily search cap.
- Notification rules + dispatcher (email / Slack / Discord).
- CSV export (`/api/jobs/export`).

**The one genuinely new lever:** `_build_sources()` in `src/main.py` must become
**plan-aware** — today it filters sources by *domain*; it also needs to filter by
*plan* so Free users skip the paid `ActiveJobsDBSource` / `TheirStackSource`. Small,
clean addition to the existing domain filter. This is what keeps Free at $0 cost.

## Proposed Free / Pro / Max split (decide before building)

| Feature | 🆓 Free | 💷 Pro | 👑 Max |
|---|---|---|---|
| Job sources | 46 free boards only | Free + Fantastic Jobs (LinkedIn/Indeed/ATS) | Free + TheirStack (344k sources) |
| Freshness | free-board cadence (60s ATS, daily rest) | hourly LinkedIn/Indeed | minute-level real-time |
| Scoring | keyword only (Engine 1) | + enrichment + semantic (E2/E3) | all 4 + AI judge verdicts (E4) |
| Searches/day | few (cap) | higher cap | unlimited |
| Jobs shown | top N | all | all + priority ranking |
| Notifications (email/Slack/Discord) | ❌ | ✅ | ✅ + instant |
| CSV export | ❌ | ✅ | ✅ |
| AI CV tailoring | 3/month | unlimited | unlimited |

**What Free deliberately loses (so upgrade feels worth it):** no live LinkedIn/Indeed,
no AI judge "why it fits you", no push notifications, capped searches + top-N only.
Free still delivers real jobs from 46 sources — a taste, not useless.

> ⚠️ Open product questions: exact daily caps per tier; per-tier paid-data job
> budget (protects margin); Pro vs Max price points; 7-day Pro trial?

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
