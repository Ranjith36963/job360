# Pricing — Final Decision (Job360)

**Decided 2026-06-25, research-verified.** This is the chosen model. Detail + sources
live in `PRICING_METHOD_3TIER.md` and `PRICING_METHOD_DRUG_DEALER.md`.

## The model: Hybrid (3 tiers + reverse-trial taste)
This is the 2026 SaaS default for a product with a clear free/premium split — not an
invention. Verified: ~65% of product-led SaaS use the hybrid; it's called the safest
default for a new product.

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
| Sources | 46 free boards | + Fantastic Jobs (LinkedIn/Indeed/ATS) | + TheirStack (344k, minute-fresh) |
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
