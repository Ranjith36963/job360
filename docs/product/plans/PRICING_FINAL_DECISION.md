# Pricing — Final Decision (Job360)
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

**Decided 2026-06-25, research-verified. UPDATED 2026-07-05 (two advisers, incl. Fable):
LAUNCH WITH 2 TIERS, not 3.** Detail + sources live in `PRICING_METHOD_3TIER.md` and
`PRICING_METHOD_DRUG_DEALER.md`.

## LAUNCH DECISION: 2 tiers (Free + Pro). Max is DEFERRED.
Both advisers (Opus + Fable) independently chose 2 tiers for this stage. Why:
- **Direct competitors (Teal, Huntr, Jobscan) are all 2-tier.** AI giants' 3-tier was
  earned *after millions of users* revealed a power segment — we have zero users.
- **Max is the most expensive tier to build AND run** (TheirStack 17-40x pricier + a
  whole new company-intel UI) aimed at the *least-proven* demand, while Free still has
  known issues. Wrong place to spend hours pre-launch.
- **One paid tier = one clean signal:** "is live LinkedIn worth £X/mo?" 3 tiers = muddy
  signals + choice paralysis on an unproven product.

### Build so Max drops in later with near-zero rework
- `users.plan` = **string** (`'free'|'pro'|'max'`), NOT a boolean `is_paid`.
- Single **entitlements map** `PLAN_ENTITLEMENTS = {plan: {sources, company_intel, ...}}` —
  never `if plan == 'pro'` scattered in routes. Adding Max = one dict entry.
- Add **`min_plan`** attribute per source (alongside existing `.category`); registry/
  scheduler filters per user. TheirStack later = one source file + one flag.
- Pricing page: leave room for a 3rd column (optionally "Max — coming soon" to test demand).

### Price risk (no anchor)
Without a Max anchor Pro can look expensive standalone → **price Pro at the TOP of the
competitor band (~£20-25/mo), not the bottom.** Discounting later is easy; raising is hard.

---
## (DEFERRED) The full 3-tier model — build toward this, sell 2 for now
This is the 2026 SaaS default for a product with a clear free/premium split — not an
invention. Verified: ~65% of product-led SaaS use the hybrid; it's called the safest
default for a new product. **Keep as the eventual target once Free is solid + Pro has
paying users.**

| Piece | Decision |
|---|---|
| Model | 3 tiers + reverse-trial taste on top |
| Taste (new user) | First **7 days OR 5 searches** = full **Max**, **no card** |
| After taste | Drop to **Free** — data kept, premium greyed + visible |
| Card | Asked **only at upgrade**, never at the taste |
| Annual | 2 months free (~17% off) |

## The three tiers

| | Free | Pro (push, "Most Popular") | Max (anchor) |
|---|---|---|---|
| Price | £0 | **~£14.99/mo** | ~£29-39/mo (decide) |
| Sources | 46 free boards | + Fantastic Jobs (LinkedIn + career sites; **no Indeed**) | + TheirStack (Indeed/Glassdoor/344k, minute-fresh) |
| Scoring | keyword only | + AI judge verdicts | all engines + priority rank |
| Freshness | free-board cadence | hourly | minute-level |
| Notifications | ❌ | ✅ | ✅ instant |
| Searches/day | few (cap) | higher | unlimited |

## Rules that make it work (verified levers)
- **Taste stays short (5 searches)** — it hits paid APIs = real money.
- **No card at taste** — protects the cheap free-funnel edge (biggest advantage).
- **Card at upgrade** — ~2.7-5x conversion lift without killing signups.
- **One aha-moment = "first strong match"** — onboarding beats method choice.
- **Meter paid-data per user** — or heavy users bleed margin (plan-aware `_build_sources()`).
- **At drop: grey-out, don't delete** — seeing the loss is the conversion trigger.

## Verified honesty flags (don't repeat as fact)
- Reverse-trial's edge over freemium is **small/contested**, not a proven 2x.
- Card lift is **~2.7-5x**, and it **reduces signup volume** — hence card-at-upgrade only.
- Free-to-paid realistic target: **3-6%**.

## Still your call (research settled everything else)
1. Taste size: 5 searches vs 7 days (or whichever comes first).
2. Max tier price point.
