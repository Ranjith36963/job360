# Pricing Method — 3-Tier Freemium (Free / Pro / Max)
<!-- doc: PLAN -->

**Captured 2026-06-25. Research-backed. Status: design captured, not built.**

The pricing *structure*: three permanent tiers you subscribe to, like Anthropic
(Free / Pro / Max) or ChatGPT (Free / Plus / Pro). Sibling doc:
`PRICING_METHOD_DRUG_DEALER.md` (the conversion *mechanic* that runs on top).
Paid-data research: memory `reference_paid_job_aggregator_apis`.

> Stats that could not be traced to a primary source are flagged **[unverified]** /
> **[approximate]** / **[industry-reported]**. Full sources at the end.

---

## TL;DR (the playbook)

- **Use 3 tiers, not 2 or 4+.** Enough to segment casual vs power vs heavy users,
  few enough to avoid choice paralysis. 3 is the industry sweet spot.
- **Design the middle (Pro) tier to win.** ~60-70% of buyers pick the middle in a
  3-tier layout **[industry-reported]**. Badge it "Most Popular."
- **Free must be genuinely useful but capped where a *daily power user* feels pain** —
  cap by usage volume + time-decay, never by removing the core action.
- **Anchor with an expensive Max tier** so Pro looks reasonable (decoy/anchoring).
- **Realistic free-to-paid target: 3-6%** (median freemium ~2.6-8%).
- **Charm pricing (£14.99) has real field-experiment backing.** Annual discount
  ≈ 16-20% ("2 months free").

---

## 1. Tier structure ("Good / Better / Best")

**How to split features:**
- **Free** = enough to *prove value* (the hook). Exposes ~20-40% of core capability.
- **Pro (middle)** = the plan built for the **majority** — full core product,
  moderate caps. This is the designed winner.
- **Max (top)** = power/heavy users — higher limits, priority, exclusive top-end
  features. Doubles as the price **anchor**.

**Middle-tier-as-target:** the "center-stage effect" / Goldilocks option — with 3
options, people disproportionately pick the middle. Price it to look 50-70% cheaper
than the top anchor. Nudge with a "Most Popular" badge, contrasting colour, larger
card, default-selected CTA.

**Decoy/anchoring — the classic Economist experiment** (Ariely, *Predictably
Irrational*): Web-only $59 / Print-only $125 / Print+Web $125. With all three shown,
**84% chose the $125 bundle**; remove the "useless" print-only decoy and **68% flip
to Web-only**. An intentionally-inferior option reframes the expensive one as value.

**Why 3, not 2 or 4+:** 2 leaves segmentation on the table; 4+ risks choice overload
(Hick's Law; the Iyengar & Lepper 2000 "jam study" — 6 jams outsold 24 by 10x, but
this is **contested**, a 2010 meta-analysis found no reliable overall effect — cite
as influential, not gospel). A 4th tier should only exist as an anchor/decoy.

---

## 2. What Free should contain — "useful but capped where it hurts"

| Product | Free cap | Paid unlocks |
|---|---|---|
| **Spotify** | lower bitrate, ads, skip limits | ad-free, 320 kbps, offline |
| **Notion** | **1,000-block cap once 2+ members**, 7-day history | unlimited, 30-90d history |
| **Slack** | **90-day** message history, 10 apps | unlimited history + apps |
| **Canva** | 5 GB, ~2% of library [approx] | full library, AI, bg-remover |
| **Dropbox** | **2 GB**, 3 devices | 2 TB, unlimited devices |
| **ChatGPT** | flagship for ~10-15 msgs then mini model [approx] | more flagship, unlimited images |
| **Claude** | ~15-40 msgs/5h [approx], **no Opus** | ~5x usage, Opus |
| **LinkedIn** | 0 InMail, last 5 viewers, capped search | InMail, full history |

**The shared principles (the important part):**
1. **Cap by usage volume, not core-feature removal.** You can always do the core
   thing; the wall is *quantity/frequency*.
2. **Free reaches the "aha" repeatedly, then bites at habit formation.** Caps sit
   where a *daily-active* user lives.
3. **Time-decay + collaboration are the sharpest knives** (Slack 90-day history;
   Notion's cap triggers only when you add teammates).
4. **Quality/convenience downgrade, not denial** (Spotify bitrate; ChatGPT smaller
   model) — never blocked, just nudged.
5. **Caps are often deliberately fuzzy/dynamic** (ChatGPT/Claude/LinkedIn don't
   publish exact numbers — flexibility + anti-gaming).

---

## 3. How AI companies tier (real prices, ~mid-2026 — verify at checkout)

| Company | Free | Pro (~$20) | Max/top |
|---|---|---|---|
| **Claude** | Sonnet+Haiku, no Opus | **$20**, adds Claude Code | **Max 5x $100** (unlocks Opus) / **20x $200** |
| **ChatGPT** | GPT-5 few msgs then downgrade | **Plus $20** (UK ~£20) | **Pro $100** (5x, added Apr 2026) / **$200** (20x) |
| **Perplexity** | ~3 Pro Searches/day | **Pro $20**, model choice | **Max $200** |
| **Cursor** | limited completions | **Pro $20** credit pool | **Pro+ $60** (3x) / **Ultra $200** (20x) |

**The universal AI shape:** Free (capped, weaker/no top model) → Pro ~$20/mo (full
model, moderate cap) → Max/Ultra $100-200/mo (**5x-20x usage multiplier** + a few
exclusives like top model, bigger context, priority). Price = **usage multiplier +
model gate**.

---

## 4. Conversion benchmarks

- **Freemium → paid: median ~2.6% (OpenView); 3-5% "good", 8-12% "great".** First
  Page Sage avg **3.7%** (category matters). Tunguz: **2-4%**.
- **Free trials convert ~2x higher** (~14-25%) but freemium wins absolute volume.
- **Named numbers (flagged):** Dropbox ~4% (credible); Slack "30%" **disputed**;
  LinkedIn "39%" **likely wrong**; Spotify ~35-40% **not analogous** (ad→paid).
- **What drives it — the activation "aha moment":** Facebook "7 friends in 10 days",
  Slack "2,000 messages", Twitter "~30 follows". Paywalls at **natural usage limits**
  convert ~25% better [Chargebee]; **engagement-triggered paywalls** 2-3x better than
  time-based [Elena Verna]; users who activate before any sales touch convert ~5x.

---

## 5. Pricing psychology

- **Anchoring:** a high Max tier resets the reference point so Pro *feels* reasonable
  (its price didn't change).
- **Charm pricing (£14.99) — real research:** Anderson & Simester (2003) catalog
  field experiments — a jacket sold **better at $39 than at $34**. (Soft "24% lift"
  pop claims **[unverified]**.)
- **Annual discount — real numbers:** Notion ~20% off, Slack ~17% off; standard "2
  months free" = **16.7%** off.
- **"Most Popular" badge:** social proof + decision simplification, paired with the
  center-stage/anchoring effect on the middle tier.

---

## 6. Common mistakes (with real examples)

**A) Free too generous → no reason to pay:** Baremetrics killed its free plan after
11 weeks (1,000 signups, 53 conversions, **lost ~60 paying customers**); Docebo
7,000 free signups, **zero** converted; Evernote's 2016 2-device clampdown. *Lesson
(a16z): gate the feature that delivers meaningful value, not just volume.*

**B) Free too stingy → no hook, kills virality:** Trello's "1 Power-Up per board"
forced tool-switching; PLG rule (Lenny) — paywalling collaboration/seats kills the
viral loop. *Tell: low adoption at the free tier itself = too restrictive.*

**C) Too many tiers → choice paralysis.** Good/Better/Best 3-tier is the default;
add a 4th only as a deliberate anchor.

**D) Framing:** freemium is an **acquisition model, not a revenue model** (Paddle) —
sub-5% free→paid means the tier design needs fixing (a16z).

---

## 7. Applying this to Job360

- **Free** = 46 free boards, keyword scoring, capped daily matches + limited saved
  alerts. Must reach the "this found me a good job" moment for a *casual* seeker,
  then bite at *daily power use* (match volume, alert count/frequency, tracker
  history) — **not** at first use.
- **Pro (the designed winner, badge it)** = + paid real-time data (Fantastic Jobs:
  LinkedIn/Indeed/ATS), AI judge verdicts, notifications, higher caps. Target
  ~**£14.99/mo**, annual ~£119 (2 months free).
- **Max (the anchor)** = + TheirStack (minute-fresh, 344k sources), unlimited,
  priority ranking. Its main job on the pricing page is to make Pro look cheap.
- **One activation event to design onboarding around** — e.g. "user gets their first
  strong matched job." Put the paywall *after* activation, at habitual daily use.
- **The economic guardrail:** paid data + LLM judge cost per use, so those are the
  Pro/Max gates; Free stays on $0-cost free boards. (See the drug-dealer doc for the
  taste-then-drop mechanic that feeds this ladder.)

---

## Sources

**Structure & psychology:** getmonetizely.com (good-better-best; anchoring) ·
thestrategystory.com (Economist decoy) · lawsofux.com/choice-overload ·
kellogg.northwestern.edu (Anderson & Simester 9-price-endings PDF) · notion.com/pricing ·
slack.com/pricing
**Free-tier caps:** spotify support · notion.com/help/understanding-block-usage ·
slack.com/help/articles/115002422943 · help.dropbox.com/plans/dropbox-basic-faq ·
help.openai.com/.../chatgpt-free-tier-faq · support.claude.com/.../choose-a-claude-plan
**AI pricing:** claude.com/pricing · chatgpt.com/pricing · techcrunch.com (ChatGPT Pro $100, Apr 2026) ·
perplexity.ai/pro · cursor.com/blog/june-2025-pricing
**Conversion:** openviewpartners.com/blog/freemium-pricing-guide · chartmogul.com/reports/saas-conversion-report ·
userpilot.com/blog/freemium-conversion-rate · mode.com/blog/facebook-aha-moment · productled.com/blog/aha-moment
**Mistakes:** a16z.com/how-to-optimize-your-free-tier-freemium · lennysnewsletter.com/p/why-saas-freemium-playbooks-dont ·
getmonetizely.com free-tier-trap · paddle.com/blog/state-of-freemium
