# Pricing Method — The "Drug Dealer" Method (Reverse Trial)
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

**Captured 2026-06-25. Research-backed. Status: design captured, not built.**

The industry name for the "drug dealer method" is the **Reverse Trial**:
*give every new user the full premium experience first, then drop them to a
limited-but-working free tier* — with the premium features left visible and locked.

This is the sibling doc to `PRICING_METHOD_3TIER.md` (the pricing *structure*).
This doc is the *conversion mechanic* that runs on top of it. Related paid-data
research: memory `reference_paid_job_aggregator_apis`.

---

## 1. What it is (plain words)

Give the best for free for a short while, then take it away.

- **Day 1:** new user gets **full Max** — live LinkedIn/Indeed jobs, all engines,
  AI judge verdicts, instant notifications. No card needed (decide later, see §6).
- **After N searches / N days:** they **drop to Free** — keyword scoring, 46 free
  boards, daily digest. The account and their saved data stay intact.
- **The locked premium features stay visible**, greyed out: "🔒 47 live LinkedIn
  roles matched you — upgrade to view."

It's **freemium in reverse**: paid-first, free-forever-second.

**How it differs from its two neighbours:**

| Model | Day 1 | At the drop |
|---|---|---|
| Classic free trial | Full access, card often required | **Access ENDS** — hard paywall |
| Plain freemium | Free tier only, never tastes premium | Nothing changes — pay to ever taste |
| **Reverse trial (this)** | **Full premium, no card** | **Drops to a free tier that still works** + upgrade nudge |

Exactly the **ChatGPT/Claude pattern**: best model for a few messages → "you've hit
your limit, wait or upgrade." We meter *searches* instead of *messages*, and
*live-data quality* instead of *model quality*.

---

## 2. Why it works — the psychology (sourced)

- **Loss aversion is the engine.** Kahneman & Tversky, *Prospect Theory* (1979):
  losses feel ~**2x** as intense as equal gains. Once a user has *had* the live
  LinkedIn feed, losing it hurts more than never having had it. That ache converts.
- **Endowment effect.** Thaler: people value things more once they *own* them. A
  user who built saved searches + saw AI verdicts on their real jobs is attached.
- **Faster "aha" moment.** Zero feature-gating on day 1 = they hit the magic
  (live matches + "why this fits you") immediately, before they'd churn.
- **Honest caveat:** these are behavioural-economics + practitioner framings. There
  is **no peer-reviewed proof** a reverse trial *causally* beats plain freemium.

---

## 3. How real companies do "taste then drop"

**Usage-capped (the model that fits us best):**
- **Slack** — free hides message history past 90 days; data isn't deleted, it
  *reappears on upgrade*. Cleanest "cap forces upgrade" example.
- **Zoom** — 40-minute cap on group calls.
- **Notion AI** — **20 lifetime AI responses**, no reset, then locked.
- **ChatGPT** — when free users exhaust the frontier model, it **silently falls back
  to a cheaper model (GPT-4o-mini)** instead of blocking. *Confirmed by OpenAI's
  help center.* This is the closest real analog to what we should do.
- **Claude.ai** — rolling 5-hour usage window; on cap → upgrade prompt / wait. (No
  numeric caps published by design.)
- **Cursor** — throttles **speed** not quality ("never downgraded in quality"). A
  useful contrast: you can meter availability instead of quality.

**Time-capped:**
- **Canva Pro** 30 days · **LinkedIn Premium** 1 month · **Duolingo Super** ~14 days /
  **Max** 7 days · **Grammarly** 7 days.
- **Superhuman** — the textbook "reverse trial" name, but has **no free tier**; it
  starts self-serve users on the *higher* Business tier.

**Anti-farming lever worth copying:** LinkedIn — **no repeat premium trial for 12
months** per account.

---

## 4. The AI rate-limit variant — why it's the strongest fit

A **usage cap creates a recurring upgrade moment** — every time the user hits the
wall on a high-intent day, not a single day-14 decision. They keep walking back to
the paywall themselves.

For Job360 that means: **meter the expensive thing** (LLM judge calls + paid data
sources like Fantastic Jobs/TheirStack). When a free user runs out, **fall back to
the cheap engine** (keyword scoring on the 46 free boards) — like ChatGPT dropping
to GPT-4o-mini — so Free stays useful *and* cheap to serve. Never a hard block.

---

## 5. Does it "double conversion"? — the honest answer

**No verified data supports "reverse trials double conversion." Do not claim 2x.**

- Best controlled data (**ChartMogul + ProductLed, Jan 2026, n=200 B2B SaaS**):
  reverse trial ≈ **4–6% good / 8–12% great** — essentially **the same as
  freemium** (3–5% / 8–12%). The gap is **not statistically significant**.
- The "15–30%" figures floating around blogs have **no cited primary study** —
  treat as folklore.
- **The evidenced levers are elsewhere:** credit-card-on-file trials convert
  **~5x higher** than no-card; onboarding/activation quality beats trial length.

**Defensible internal framing:** "Reverse trial *can* modestly beat freemium
because everyone tastes premium, but the proven gap is small. The real levers are
card-on-file and a great first-run experience."

---

## 6. Risks & how not to lose money

- **The free taste has real cost.** Those first Max searches hit the paid providers
  + LLM judge — the free sample is a **marketing spend**. Meter it by
  tokens/searches per user; keep N small (**3–5 searches or 7–14 days**).
- **Anti-farming (layer them — none holds alone):** block disposable emails +
  require verification (baseline); signup-velocity / IP checks; LinkedIn's
  "no repeat trial per 12 months" rule; consider **card-on-file** (raises abuse
  cost *and* lifts conversion ~5x).
- **At the drop:** keep account + data intact, disable premium **gracefully**
  (grey out, don't delete), leave locked features **visible**. Seeing what you lost
  is the whole point. Avoid a silent feature cliff — tell them what changed.
- **Trial length:** 14 days is the common B2B sweet spot, but **most conversions
  happen in week 1** — for us, "first N searches" works as well as a day count.

---

## 7. Applying it to Job360 (the concrete shape)

1. New user → **first 5 searches (or 7 days) run on full Max**: live LinkedIn/Indeed,
   all 4 engines, AI verdicts, instant notifications.
2. At the drop → **Free tier that still works**: keyword scoring on 46 free boards,
   daily digest, top-N jobs. Premium features **visible but locked**.
3. Meter the expensive calls; **fall back to the cheap engine** (ChatGPT-style), never
   hard-block.
4. Gate farming: email verification + "no repeat premium taste per account/12mo";
   consider card-on-file for the taste (the ~5x lever).
5. Surface the upgrade at the **drop moment** and on every later wall-hit.

### Open questions
- Taste size: 5 searches? 7 days? Both (whichever first)?
- Give **Max** quality or only **Pro** quality in the taste? (Max converts harder,
  costs more per free user.)
- Card-on-file for the taste — worth the signup friction for the ~5x lift?

---

## 8. Corrections the research surfaced (so we cite honestly)

- The term is best attributed to **Elena Verna** (secondary sources); the clearest
  primary write-up is **OpenView, "Your Guide to Reverse Trials."** The
  "Bhavik Patel" origin could **not** be verified — don't cite it.
- Lenny's Newsletter's freemium post does **not** use the term "reverse trial."
- "Doubles conversion" = **folklore**, not data (see §5).

---

## Sources

- OpenView — Guide to Reverse Trials: https://openviewpartners.com/blog/your-guide-to-reverse-trials/
- GTM Strategist — reverse trial best practices: https://knowledge.gtmstrategist.com/p/reverse-trials-best-practices-for-saas-companies
- Prospect Theory (Kahneman & Tversky 1979): https://en.wikipedia.org/wiki/Prospect_theory · https://www.nngroup.com/articles/prospect-theory/
- Endowment effect: https://www.getmonetizely.com/articles/how-does-the-endowment-effect-make-free-trials-so-powerful
- ChatGPT free-tier fallback (OpenAI): https://help.openai.com/en/articles/9275245-chatgpt-free-tier-faq
- Claude usage limits: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work
- Cursor pricing (throttles speed not quality): https://cursor.com/docs/models-and-pricing
- Slack free usage limits: https://slack.com/help/articles/115002422943-Usage-limits-for-free-workspaces
- Canva pricing: https://www.canva.com/en/pricing/ · Notion pricing: https://www.notion.com/pricing
- LinkedIn Premium trial rules: https://www.linkedin.com/help/linkedin/answer/a1355837
- Superhuman pricing: https://newsletter.pricingsaas.com/p/inside-superhumans-pricing-evolution
- Free-to-paid conversion report (ChartMogul + ProductLed, n=200): https://www.growthunhinged.com/p/free-to-paid-conversion-report
- Free-trial abuse prevention: https://fingerprint.com/blog/free-trial-abuse/ · https://clearout.io/blog/saas-free-trial-abuse-prevention/
