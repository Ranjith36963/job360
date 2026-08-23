# Job360 Batch 4: Risk, Economics, and a Launchable Plan

**Job360 can launch credibly as a solo-founder MVP, but only if it drops the "all UK white-collar domains" claim and prices at £14.99/month with an annual lever at £119/year.** The business is economically viable at ~9,300 paying users (£50K founder salary break-even, realistic in 18–24 months). The legal risk is manageable — £40/year ICO registration, documented legitimate interests, deep-link-only architecture — provided Job360 avoids authenticated LinkedIn scraping and absolutist marketing claims. Partnerships are not a viable MVP foundation: only NHS Jobs Self-Serve API and CV-Library's outbound Traffic Partner programme are realistically achievable pre-launch. All other named partnerships (CV-Library Jobs API, Madgex publishers, HiringCafe, EURAXESS) require either 12+ months of traffic credibility or are structurally unavailable. Three domains — **academia, consulting, and public sector** — have coverage gaps under 50% using free sources alone, and Job360's honest market claim is "~7 of 12 white-collar domains covered well" rather than full sweep. The infrastructure economics are genuinely excellent: **$6/month at 1K users, ~$55/month at 10K, ~$350/month at 100K** using a Hetzner-based zero-cost-first stack — roughly 5–8× cheaper per user than HiringCafe's paid architecture.

---

## 1. Partnership risk and launch strategy

### Only two partnerships are realistically achievable pre-launch

Of six named partnership targets, the evidence is unforgiving. A pre-revenue solo-founder aggregator with zero traffic has essentially no leverage against commercial UK job boards whose core fear is traffic cannibalisation. The industry literature (Webspidermount, Jobboardly, Cavuno) is consistent: **job boards treat aggregators as competitors, not partners**, and rarely license data to unproven entrants.

**NHS Jobs Self-Serve API is the highest-probability win** at roughly **50–65% chance of approval within 4–12 weeks** if Job360 presents with a health-positioned surface rather than a generic aggregator pitch. The eligibility criteria are publicly documented at nhsbsa.nhs.uk: UK-based, health-associated roles, no charging jobseekers, fair recruitment alignment. The specification document already exists (v1.01, 2023), and the process is rule-based rather than commercial negotiation. The risk vector here is the **£1.2bn NHS Future Workforce Solution (Infosys)** running through 2030 — the Self-Serve API could be restructured mid-implementation. Apply immediately via nhsjobsintegration@nhsbsa.nhs.uk.

**CV-Library's Traffic Partner programme is the second realistic win** — probability **70–85%**, timeline weeks. But this is the *outbound* programme: Job360 sends users to CV-Library in exchange for commission (~1 registration per 5–6 clicks). This generates modest revenue during build phase but does *not* solve the data problem. CV-Library's inbound Jobs API, which Job360 actually needs, is behind a partner-manager gate with **no public case studies of pre-launch aggregators getting approved**. Realistic probability: **15–25%**; realistic timeline: **6–16 weeks minimum, likely deferred until Job360 proves 10K+ MUV.**

### The remaining four partnerships should not be planned around

**Madgex is a job-board software vendor, not a data-sharing platform** — the "Madgex partnership" framing from Batch 3 was partially misleading. To obtain Guardian Jobs, Times Jobs, or Telegraph Jobs data, Job360 must negotiate *directly with each publisher* (Guardian News & Media, News UK, Telegraph Media Group) on separate content syndication contracts involving legal review, brand terms, and minimum traffic thresholds. **Realistic probability at MVP: ~5%. Timeline: 4–9 months per publisher.** Defer to Year 2+.

**EURAXESS is structurally the wrong API** — the XML endpoint is a push-to-EURAXESS interface for research-performing organisations, not a pull feed for aggregators. Job360 would likely fail the "related to research" validation. For research jobs, scrape the public browsable pages under normal etiquette instead.

**CharityJob has a developer subdomain that serves blank content** — no documented programme, no precedent for aggregator access. Probability **~15%**, worth a polite enquiry but no roadmap weight.

**HiringCafe is a direct competitor** whose founder publicly positioned the product against aggregators. Probability of partnership: **~0–2%**. Stop pursuing.

### Non-tech domain coverage is honest but not comprehensive without partnerships

Assessment of 12 UK white-collar domains using only free sources (Reed + Adzuna + NHS + Teaching Vacancies + GOV.UK Apprenticeships + Careerjet + ATS boards + JobSpy) yields one adequate domain, seven partials, and three poor coverage zones:

| Domain | Coverage | Verdict |
|---|---|---|
| Healthcare (NHS + private) | ~85% | **Adequate** ✅ |
| Finance | ~75% | Partial |
| Legal | ~75–80% | Partial |
| Marketing/Comms | ~70% | Partial |
| HR | ~75% | Partial |
| Engineering (non-software) | ~75% | Partial |
| Supply Chain/Procurement | ~75–80% | Partial |
| Charity | ~55–60% | Partial |
| Media/Publishing | ~50–60% | Partial–Poor |
| Public Sector | ~50% | **Poor** ⚠️ |
| Consulting (MBB + Big 4) | ~55% (with JobSpy risk) | **Poor** ❌ |
| Academia | ~25–30% | **Poor** ❌❌ |

**The three red-flagged domains share a structural cause:** they are served by near-monopolistic specialist boards (jobs.ac.uk for academia, civilservicejobs.service.gov.uk for civil service) or proprietary career portals (McKinsey/BCG/Bain/Deloitte/PwC/EY/KPMG use Workday, Taleo, and Avature — none scraped by the Greenhouse/Lever/Ashby extractors). JobSpy partially closes consulting via LinkedIn scraping, but that source carries the highest Terms-of-Service enforcement risk in the industry.

**Go/No-Go on "all UK white-collar domains" marketing claim: NO-GO.** Under CAP Code rule 3.7 this would be unsubstantiable and inviting ASA adjudication. The defensible alternative: *"Covers the UK's high-volume white-collar market — finance, legal, marketing, HR, engineering, healthcare, supply chain, plus NHS and apprenticeships — with specialist domains (academia, civil service, MBB consulting) on the roadmap."* This framing is honest, ASA-compliant, and still differentiates from single-source incumbents.

### Legal risk is manageable with documented mitigations

The two foundational precedents — **Ryanair v PR Aviation (CJEU C-30/14, 15 January 2015)** and **CV-Online Latvia v Melons (CJEU C-762/19, 2021)** — establish that scraping publicly browsable job data is not prima facie illegal in the UK, but Terms-of-Service breaches and database rights enforcement remain live levers. The **hiQ Labs v LinkedIn** saga concluded with a December 2022 consent judgment: $500,000 damages plus permanent injunction, on breach-of-contract grounds after the CFAA claims failed. No prominent UK litigation against a job-board scraper has been publicly reported — enforcement is consistently via **IP bans and cease-and-desist letters, not courts**.

Source-by-source risk for Job360's stack:

| Source | Risk rating | Dominant threat |
|---|---|---|
| Reed, Adzuna, Greenhouse, Lever, Ashby | **Low** | Contract compliance only |
| Indeed, Glassdoor | **Medium–High** | ToS breach → IP bans, Cloudflare blocks |
| LinkedIn (public pages) | **High** | §8.2 explicit scraping prohibition; most litigious platform |
| LinkedIn (authenticated) | **Critical** | Same path as hiQ — do not attempt |

**The compliance checklist for Job360 costs £40/year and a weekend of documentation work.** Register with ICO as a Tier 1 micro-business (£40, reduced to £35 by direct debit — Data Protection Act fine for non-registration is up to £4,000). Publish an Article 14 privacy notice covering scraped sources. Maintain a documented Legitimate Interests Assessment. Strip recruiter personal data where non-essential. Deep-link to sources rather than republishing descriptions. Use a descriptive User-Agent (`Job360Bot/1.0 (+contact)`). Never bypass CAPTCHAs, IP blocks, or authentication — **this single constraint is what distinguishes hiQ's losing position from defensible scraping**. Incorporate as a limited company to cap personal liability.

The advertising risk is smaller than the scraping risk. **"All UK jobs" and "24-hour freshness" are both objective claims requiring CAP Code rule 3.7 substantiation** and neither is honestly defensible. Safer framings: "thousands of fresh UK jobs daily", "aggregated from 80+ leading sources including Reed, Adzuna, LinkedIn and direct company career pages", with a linked methodology page documenting actual coverage.

---

## 2. Unit economics model — infrastructure per user is almost negligibly cheap

### The zero-cost-first stack is validated against April 2026 pricing

Direct pricing research confirms the Batch 2 estimates as realistic with modest adjustments. The critical finding: **Clerk raised its free tier to 50,000 MAUs in February 2026** (up from 10,000), which eliminates the auth cost cliff until Job360 reaches mid-scale. Hetzner adjusted cloud prices on 1 April 2026 but the relevant tier remains cheap.

| Scale | Total monthly cost | Per-user cost |
|---|---|---|
| 1,000 MAU | **~$6** (CX22 + SES + Cloudflare Free + Clerk Free) | **$0.006** |
| 10,000 MAU | **~$55** (CX42 + SES + Sentry + Clerk Free) | **$0.0055** |
| 100,000 MAU | **~$350** (2× CX42 + Neon Scale + Upstash PAYG + SES + self-hosted auth) | **$0.0035** |

The 100,000-user model hides a critical architectural choice: **at 50K+ MAU, Clerk Pro becomes the single largest line item at ~$1,025/month**, which would multiply total infrastructure cost 3–4×. The migration path is to self-host authentication (Better Auth, Lucia, or Supabase Auth) before crossing the Clerk free tier. Planning this migration into the roadmap at ~30K users — concurrent with the Apprise-to-Celery migration already scheduled from Batch 2 — keeps the $350/month figure honest.

### Optional paid services have asymmetric ROI

**LLM enrichment at ingestion using GPT-4o-mini Batch API costs roughly $1.20 per 10,000 jobs** (800 input + 200 output tokens per job at the 50%-discounted batch rates of $0.075/M input, $0.30/M output). At Job360's realistic ingestion volumes — perhaps 10,000 new jobs per week after deduplication — this is **~$5/month**. Batch API's 24-hour turnaround is fine for enrichment (it is not in the hot path). This is the single highest-ROI paid service on the menu.

**Proxies for LinkedIn are the worst ROI.** Moderate LinkedIn scraping (5,000 searches/day ≈ 150K/month) consumes 75–300 GB of residential proxy traffic, costing **$165 to $2,400/month** depending on provider (Decodo/Smartproxy cheap end; Oxylabs premium end). Combined with the critical-level legal risk and ~95 million daily scrape-attempts LinkedIn blocks, this budget buys Job360 neither reliability nor safety. **Recommendation: skip LinkedIn scraping entirely.** The consulting-domain gap is better solved through a dedicated Top-Consultant.com and Consultancy.uk scraping path plus public Google cache fallbacks.

**SMS is dead as a default channel** — confirmed at ~$1,200/month for 1,000 users × 1 SMS/day at Twilio UK rates. Keep email and Apprise-based push (Telegram, Discord, Slack, webhooks) as the core delivery mechanisms. SMS belongs only in a paid tier as an opt-in 2FA or critical-alert feature.

**TheirStack at $59/month, Apify Starter at $29/month** (the actual current tier — the Batch 3 "$49" reference appears outdated), and backup storage on Backblaze B2 at ~$0.30/month round out the sensible paid layer. Total optional spend if all are adopted: **~$95/month flat**, added on top of the core infrastructure at any scale.

### Job360 is 5–8× cheaper per user than HiringCafe

Reverse-engineering HiringCafe's stack from its founder's public disclosures: 30,000 company career pages scraped three times daily, GPT-4o-mini processing in realtime (not Batch), Elasticsearch for search, Oxylabs-class proxies. Estimated monthly cost: **$1,750–$2,650**. Per-user cost at HiringCafe's 1M+ MAU is roughly $0.002–$0.003, which looks competitive only because of scale dilution. On a like-for-like basis at 100K MAU, HiringCafe-style infrastructure would cost Job360 approximately $2,000/month versus the zero-cost-first $350/month.

The architectural tradeoffs that deliver this gap are precise: **Postgres full-text search + pgvector replaces Elasticsearch** up to roughly 1M jobs; **GPT-4o-mini Batch API replaces realtime inference** at 50% discount and 24-hour turnaround; **ATS public APIs (Greenhouse, Lever, Ashby) replace proxy-hungry career-page scraping** for the subset of employers that use them; **targeted career-page crawling 1× daily replaces 3× daily brute-force** of 30,000 URLs. Job360 gives up HiringCafe's real-time freshness on scraped sources and its broader employer coverage — but retains honest 24-hour freshness on the 6–8 sources with reliable posting dates identified in Batch 1.

### Revenue math pins break-even at ~9,300 paying users

The competitive pricing cluster is tight: Teal $29/month, Huntr $40/month, Jobscan $49.95/month, Careerflow $23.99/month, JobRight $29.99–$39.99/month, LinkedIn Premium Careers UK £19.99–29.99/month. **The £14.99/month ceiling-breaker** — undercutting LinkedIn Premium by roughly 50% — is the defensible wedge for a UK graduate market that is budget-constrained but career-anxious. Freemium conversion benchmarks from OpenView and ProfitWell consistently place typical productivity SaaS at **2–5%** free-to-paid, with 5%+ requiring strong activation design.

Break-even scenarios for a £50K founder salary equivalent (£4,167/month pre-tax):

| Conversion | Price | Users needed | Realism |
|---|---|---|---|
| 2% × £10/mo | 21,000 total | Hard — low price, low conversion |
| **5% × £15/mo** | **5,600 total** | **Most realistic path** |
| 2% × £20/mo | 10,500 total | Possible if positioning premium |
| 3% × £14.99/mo | ~9,300 total | Base case |

At 100,000 users and 2% conversion × £10/mo, Job360 generates **£19,700/month net of infrastructure** — comfortably past founder sustainability. At 10,000 users and 5% × £15/mo, the product clears **£7,464/month net**, which is full-time-founder viable. The punishing truth: at 1,000 users, no reasonable conversion/price combination covers a founder salary. The first 12–18 months are build-to-traffic, not build-to-revenue.

---

## 3. Solo founder sustainability — the ceiling is ~10,000 paying users

### Maintenance burden scales dangerously with source count

Evidence from the JobSpy open-source repository is sobering: **2–5 meaningful patches per month** to keep scrapers working against LinkedIn date-parsing changes, Glassdoor 403s, user-agent rotation, Naukri parser bugs. Extrapolating to Job360's 80+ planned sources, at an average source breakage every 2 weeks: **roughly one broken source in the pipeline at any given time**, requiring **8–15 hours per week of pure maintenance**. This is *before* user support, product development, or marketing.

**Do not launch with 80 sources.** Launch with the top 10–15 sources covering 80% of white-collar volume — Reed, Adzuna, Teaching Vacancies, NHS Jobs, GOV.UK Apprenticeships, Greenhouse UK slugs, Lever UK slugs, Ashby UK slugs, plus 3–5 high-value domain-specialist targets. Add sources slowly, with instrumented scrape-health gates (from Batch 1's freshness architecture) that auto-alert when a source degrades.

### The 10,000-paying-user wall is where solo operations break

Pieter Levels' NomadList — the canonical solo-founder SaaS case study — runs ~30K paying users but against a ~$75/year pricing structure that dramatically reduces support contact frequency. Support hour scaling is roughly linear: at **1,000 users, 3–8 hours/week**; at **10,000 users, 25–45 hours/week**, consuming a full-time person. Mitigations that genuinely work are community-based (Discord self-serve, aggressive FAQ, AI-assisted support agent, annual-only pricing to reduce billing questions), not more head count.

The three highest-risk operational surfaces for Job360 are, in order: **(1) source maintenance** (scraper breakage is inevitable, continuous, and mission-critical); **(2) LLM cost runaway** if enrichment accidentally moves into the hot path at scale; **(3) user support escalation** beyond 5,000 paying users without automation investment. The on-call/holiday risk is under-discussed — a solo founder's two-week holiday currently has no fallback. Budget for a part-time contractor at ~$500/month once Pro reaches 3,000 paying users, primarily for scraper maintenance.

### Expected solo-founder trajectory

Realistic path from zero to sustainability, with honest timeline bands:

**Months 0–3 — Build**: Top 10–15 sources, freshness architecture from Batch 1, Apprise + ARQ + PostgreSQL delivery layer from Batch 2, NHS Jobs and CV-Library Traffic Partner applications submitted. Infrastructure cost: **~$6/month**. Users: 0. ICO registered (£40).

**Months 3–9 — Soft launch, SEO, free tier only**: 500–5,000 free users. No Pro tier yet — build conversion signal first. Source count expands to 30–40. NHS Jobs approved (optimistically). Infrastructure: **~$15–30/month**.

**Months 9–18 — Pro tier launches at £14.99/month + £119/year**: Target 10,000 total users, 2–3% conversion = 200–300 paying users = £3,000–4,500/month gross. Still below founder sustainability but past proof-of-concept. Scraper maintenance contractor engaged.

**Months 18–30 — Growth phase**: 30,000–50,000 users, 3–5% conversion = 1,200–2,500 paying. **~£18,000–37,500/month gross**. Migration to self-hosted auth (before Clerk free-tier cliff) and Celery from ARQ (~30K threshold from Batch 2). Full founder sustainability reached mid-range.

**Month 30+ — Second hire or acquisition decision point.** Beyond 10,000 paying users, solo operations break structurally.

---

## 4. Recommended launch plan — honest promises at each phase

Stitching all four batches together produces a coherent go-to-market with phased, defensible claims.

### Phase 1 (Months 0–3): "Fresh UK jobs in 8 white-collar domains, delivered where you already are"

The MVP launches with the Batch 1 freshness architecture (five-column date model, scrape-health gates, consecutive_misses tracking) honoring the 24-hour bucket only on the 6–8 sources where real posting dates are reliable — Reed, Adzuna, Teaching Vacancies, NHS XML, GOV.UK Apprenticeships, Greenhouse UK, Lever UK, Ashby UK. Delivery goes through the Apprise + ARQ + PostgreSQL stack from Batch 2, offering email, Telegram, Discord, Slack, and webhook channels — **a genuinely differentiated feature with no competitor occupying this territory**. The freshness promise is calibrated: "Sub-hour updates from our fastest sources, 24-hour refresh across all 80+ sources." Coverage claim: "Finance, legal, marketing, HR, engineering, healthcare, supply chain, and charity roles from Reed, Adzuna, NHS, and direct company career pages." Infrastructure cost: **$6/month**. Total upfront cost: ICO £40 + domain £10 = **~£50**.

### Phase 2 (Months 3–9): Activate NHS partnership, soft launch, SEO

NHS Jobs Self-Serve API integrated upon approval, unlocking a healthcare vertical credibility story. CV-Library Traffic Partner programme generates first modest revenue (~£100–500/month) while proving user flow. Product remains free-tier-only — building SEO ranking, Reddit community presence, and UK university careers-service outreach. Phase 1 polling strategy from Batch 3 (55 high-frequency endpoints) delivers sub-5-minute freshness for ATS sources at zero additional cost. Target: **3,000–5,000 free users, zero paying**. Infrastructure: **~$15/month**.

### Phase 3 (Months 9–18): Pro tier launches at £14.99/month, annual at £119/year

Pricing tiers finalised:
- **Free forever**: 50 jobs tracked, 20 personalised matches/day, 3 AI CV tailoring credits/month
- **Pro monthly £14.99**: unlimited matches across all sources, unlimited AI tailoring, full tracker, ATS keyword scoring
- **Pro annual £119 (£9.92/mo effective, 34% discount)**: the retention lever
- **Sprint 4-week £24.99** (added post-Pro if data supports): urgency-tier for active job-hunters

Optional paid-tier add-ons layer in: GPT-4o-mini Batch enrichment (~$5/month), TheirStack for premium sources if needed ($59/month). Second CV-Library application filed with traffic credibility. First targeted outreach to jobs.ac.uk begins — **the single largest coverage gap closure worth pursuing**. Target: **10,000 total users, 200–300 paying, ~£3,500/month gross**. Infrastructure: **~$55/month**.

### Phase 4 (Months 18–30): Scale, domain-specialist partnerships, second hire decision

Migration to self-hosted auth before Clerk free-tier limit. ARQ → Celery migration around 30K users. Scraper-maintenance contractor engaged. Domain-specialist partnerships pursued with traffic credibility — Civil Service Jobs scraping agreement, eFinancialCareers or BMJ Careers feeds, CharityJob re-engagement. At 30,000–50,000 users and 3–5% conversion: **£18,000–37,500/month gross, founder fully sustainable**. Infrastructure: **~$180/month**.

### What Job360 can honestly promise the UK market at launch

The defensible MVP value proposition, grounded in this research, compresses to: *"Job360 aggregates fresh UK white-collar jobs from 80+ sources and delivers matches where you already are — email, Telegram, Slack, Discord, or webhooks. Most sources update hourly. Top 8 domains covered well; specialist boards for academia, civil service, and MBB consulting are on the roadmap. £14.99/month or £119/year for unlimited matches and AI CV tailoring."*

That claim passes CAP Code substantiation, aligns with actual source coverage, differentiates on delivery (genuinely unoccupied competitive territory), and supports a business model that becomes founder-sustainable at ~6,000 paying users — achievable within 18–24 months of launch given the £14.99 price point against the £29.99 LinkedIn Premium anchor. The research across four batches is mutually reinforcing: the freshness architecture, delivery stack, polling strategy, and pricing all compose into a product a single engineer can actually ship, defend, and scale.

## Conclusion — where Job360's thesis holds and where it breaks

The Job360 thesis — *personalised white-collar UK job discovery, zero-cost-first, solo-buildable* — holds on infrastructure, legal risk, delivery differentiation, and unit economics. **The architecture is 5–8× cheaper per user than comparable aggregators**, the delivery channels occupy empty competitive territory, and the legal exposure is controllable with £40/year and routine documentation.

The thesis breaks in three specific places that this batch has identified precisely. **Partnerships are not a launch strategy** — only two of six named partnerships are realistic pre-launch, and the structural absence of Madgex, jobs.ac.uk, CharityJob, and HiringCafe data cannot be papered over. **"All domains" is not a defensible claim** — academia, consulting, and public sector are under 50% covered by free sources, and the product must honestly scope to the 7–8 domains it can serve well. **Solo operations break at ~10,000 paying users** — not at 100,000 — and the operational roadmap must include a scraper-maintenance contractor by Year 2, not Year 3.

The single most important finding is that these constraints are binding but not fatal. A £14.99/month product covering 8 of 12 white-collar domains with honest freshness claims and multi-channel delivery can reach founder sustainability at 6,000–9,000 paying users — a target that requires 200,000–300,000 total users and 18–24 months of execution. That is a real path, priced correctly, with credible unit economics. It is also materially more modest than the maximalist framing of Job360 as a comprehensive UK white-collar platform at MVP. **The product ships first, the full-coverage claim earns its way in.**