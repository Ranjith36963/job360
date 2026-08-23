# Webhooks don't exist for free job data

**The brutal truth: of Job360's 47 current sources, exactly zero offer free webhook-based push delivery of new job postings to external consumers.** The only source with true real-time push capability is the Hacker News Firebase API (WebSocket/SSE streaming), which covers a single niche. Every other source — including all 10 ATS platforms, all 7 keyed APIs, and all RSS/XML feeds — requires polling. This is not a gap in Job360's implementation; it reflects the structural economics of the job data ecosystem, where sources have no incentive to push data to downstream aggregators. The realistic path to Job360's freshness SLA is not webhooks but **optimised tiered polling**, which can achieve sub-5-minute latency for the highest-value sources at zero cost.

---

## 1. Webhook availability matrix for all 47+ sources

The table below classifies every current source by its real-time capability, realistic latency floor, and the binding constraint that limits freshness.

### Keyed APIs (7 sources)

| Source | Webhook | Best alternative | Rate limit | Latency floor | Binding constraint | Upgrade effort |
|--------|---------|-----------------|------------|---------------|-------------------|---------------|
| Reed API | ❌ No | `postedWithin` days filter + offset pagination | 2,000/hr (recruiter) | **~5 min** | Rate budget | Low |
| Adzuna API | ❌ No | `max_days_old` + `sort_by=date` | Undocumented (per-key, ~250–1K/mo free) | **15–60 min** | Opaque rate limits | Low |
| JSearch (RapidAPI) | ❌ No | `date_posted=today` bucket | Free: 200/mo; Pro ~$30/mo: 10–20K/mo | **Tier-dependent** (free: ~3h; pro: ~10 min) | RapidAPI credit budget | Low |
| Jooble API | ❌ No | `datecreatedfrom` (undocumented) | Undocumented, per-key | **5–15 min** | Opaque limits | Low |
| SerpApi (Google Jobs) | ⚠️ Async only (no event webhook; explicitly "wontfix") | `chips` date filter + `no_cache=true` | Free: 100/mo; Dev $75/mo: 5K/mo | **~60 min** (1h cache TTL) | SerpApi cache + Google indexing lag | Low |
| Careerjet API | ❌ No | `sort=date` only — **no date filter** | ~1,000/hr (unofficial) | **~60 min** | No delta-fetch capability | Medium |
| Findwork API | ❌ No | `order_by=-date_posted` + page pagination | **60/min** (documented) | **~1 min** (small volume) | Source volume is low | Low |

**No keyed API supports webhooks, ETag, or Last-Modified conditional fetching.** Delta fetching depends entirely on date-range parameters (where they exist) and client-side deduplication by job ID. Reed and Findwork have the most generous rate budgets. Careerjet is the weakest — no date filter forces full-feed pagination on every poll.

### Free JSON APIs (10 sources)

| Source | Webhook | Rate limit | Date filter | Latency floor | Class |
|--------|---------|------------|-------------|---------------|-------|
| Arbeitnow | ❌ | Unofficial; IP ban risk | ❌ None | ~60 min | Standard 1–6h |
| RemoteOK | ❌ | Strict IP blocking; needs valid UA | ❌ None (returns full feed) | ≥60 min | Standard 1–6h |
| Jobicy | ❌ | ≤1 req/hr enforced | ❌ None | **≥6h** (intentional 6h publication delay) | Slow >6h |
| Himalayas | ❌ | 429 on abuse; undocumented | ❌ None | **≥24h** (explicit 24h cache) | Slow >6h |
| Remotive | ❌ | **>2 req/min blocked; max 4/day recommended** | ❌ None | **≥24h** (intentional delay) | Slow >6h |
| DevITjobs | ❌ | Undocumented | ❌ (RSS/XML, no filter) | ~60 min | Standard 1–6h |
| Landing.jobs | ❌ | Undocumented (stale since 2018) | offset/limit only | ~60 min | Standard 1–6h ⚠️ |
| AIJobs.net | ❌ | Undocumented | ❌ (200 most recent; 2h refresh) | **~120 min** | Standard 1–6h |
| HN Jobs (Algolia) | ❌ | **~10,000/hr** (very generous) | ✅ `created_at_i` Unix timestamp filter | **~1 min** | **Near-real-time** |
| HN Jobs (Firebase) | ✅ **WebSocket/SSE streaming** | **No rate limit** | ❌ (ID diff only) | **~seconds** | **Real-time** |
| YC Companies (yc-oss) | ⚠️ GitHub repo webhook on push | Unlimited (GitHub Pages) | ❌ (full JSON dump) | **~24h** (daily sync) | Slow >6h |

**HN Firebase is the only source in Job360's entire stack with true real-time push capability.** Three free APIs — Jobicy, Himalayas, and Remotive — **intentionally delay publication by 6–24 hours** for SEO attribution purposes, making them structurally incapable of freshness faster than their artificial delays regardless of polling frequency.

### ATS boards (10 platforms, ~104 slugs)

| Platform | Slugs | Webhooks exist? | Available to external consumers? | Job-publish event? | Public API rate limit | Latency floor |
|----------|-------|----------------|--------------------------------|-------------------|----------------------|---------------|
| Greenhouse | 25 | Yes | **No** (employer-scoped) | `job_post_updated` (internal) | None (heavily cached) | **~60 sec** |
| Lever | 12 | Yes | **No** | **No posting event exists** | Unquoted for GET | **~60 sec** |
| Ashby | 9 | Yes | **No** | Vague "posting updates" (internal) | Not documented | **~60 sec** |
| Workable | 8 | Yes | **No** | **No `job_published` event** | 10 req/10 sec | **~60–120 sec** |
| SmartRecruiters | 6 | Yes | **No** | ✅ `onJobCreated` (but employer-only) | Not published | **~60 sec** |
| Pinpoint | 8 | Yes | **No** | Not prominent | Requires auth (no public endpoint) | **~60 sec** |
| Recruitee | 8 | Yes | **No** | ✅ `offer_updated` status→published | Not published | **~60 sec** |
| Workday | 14 | No | **No** | No | Aggressive anti-scraper | **5–15 min** |
| Personio | 10 | Yes (person events only) | **No** | **No job event** | Not published | **~60–120 sec** |
| SuccessFactors | 3 | Partial (Intelligent Services) | **No** | No public event | ≤10 concurrent | **5–15 min** |

**This is the most important finding for the ATS category: no ATS platform runs an "aggregator partner" program.** All 10 platforms scope webhooks to the employer's own tenant. To receive push notifications, Job360 would need to become a listed marketplace integration on each ATS and then convince every individual employer to install it — a sales/BD problem, not an engineering one. No amount of technical effort changes this.

The good news: **Greenhouse, Lever, Ashby, and SmartRecruiters have generous or unmetered public job board APIs** that can be polled every 30–60 seconds without hitting rate limits. For these 52 slugs, sub-minute polling is free and sustainable. **Workday (14 slugs) and SuccessFactors (3 slugs) are the long-pole problems** — undocumented internal endpoints with aggressive anti-scraping, forcing 5–15 minute latency floors.

### RSS/XML feeds (8 sources)

| Source | Webhook | ETag/Last-Modified | Update freq | Latency floor | Class |
|--------|---------|-------------------|-------------|---------------|-------|
| BioSpace | ❌ | Likely yes (CDN) | Daily (news only — **jobs NOT in RSS**) | ~6h | Standard ⚠️ |
| FindAJob (DWP) | ❌ | N/A (no API, HTML scrape only) | Continuous | 1–6h | Standard (scrape) |
| jobs.ac.uk | ❌ | Likely yes | Hourly | 1–2h | Standard |
| NHS Jobs | ❌ | Not documented | Continuous | ~1h | Standard (near-RT if partner) |
| RealWorkFromAnywhere | ❌ | Likely yes | Low (derivative — aggregates from WWR/Remotive) | 1–6h | Standard |
| WorkAnywhere | ❌ | N/A (no RSS/API) | Low | >6h | Slow |
| WeWorkRemotely | ❌ | Likely yes (Rails) | Many/day | **15–60 min** | Near-real-time |
| University Jobs | ❌ | Per-institution | Low per feed | 6–24h | Slow |

### HTML scrapers (7 sources) and Other (5 sources)

| Source | Webhook | Rate limit | Latency floor | Class |
|--------|---------|------------|---------------|-------|
| LinkedIn (guest API) | ❌ (zero public webhook) | Aggressive 429 + CAPTCHA | 1–2h (with proxy rotation) | Standard |
| JobTensor | ❌ | Undocumented, tolerant | 2–6h | Standard |
| Climatebase | ❌ | Undocumented | 6–12h | Standard/Slow |
| 80000Hours (Algolia) | ❌ | Per-IP (configurable by owner) | 2–6h | Standard |
| BCS Jobs | ❌ | Undocumented | Daily | Slow |
| AIJobs Global | ❌ | Tolerant | 2–6h | Standard |
| AIJobs AI | ❌ | Tolerant | 2–6h | Standard |
| Indeed+Glassdoor (JobSpy) | ❌ (Publisher Program **discontinued**) | Cloudflare-protected | 2–6h (with proxies, fragile) | Standard (degraded) |
| TheMuse | ❌ | **3,600/hr with API key** | **15–60 min** | Near-real-time |
| NoFluffJobs | ❌ | Moderate anti-scraping | 1–4h | Standard |
| Nomis | ❌ | Documented | **Monthly/quarterly** | **Miscategorised** — macro-statistics, not job postings |

**Nomis (nomisweb.co.uk) is the ONS labour market statistics portal, not a job board.** It should be removed from Job360's source list or reclassified as a market-analytics input.

### Proposed new sources (8 sources)

| Source | API type | Webhook | Rate limit | UK coverage | Integration effort |
|--------|----------|---------|------------|-------------|-------------------|
| Teaching Vacancies | REST/JSON (schema.org) | ❌ | None stated | ★★★★★ England schools | **Low** (no auth, OGL) |
| GOV.UK Apprenticeships | REST/JSON | ❌ | 150 req/5 min | ★★★★ England | Low-Medium |
| NHS Jobs XML (public) | XML | ❌ | Not published | ★★★★★ UK-wide NHS | **Low** |
| EURAXESS | XML batch | ❌ | N/A (nightly 02:00–06:00 CET) | ★★ (post-Brexit, research only) | Medium |
| Civil Service Jobs | ❌ No API (deprecated 2012) | ❌ | N/A | ★★★★★ potential | **High** (scrape/partner) |
| Rippling ATS | REST/JSON public | ❌ | Unofficial; be polite | ★★ (US-skewed) | Low |
| CV-Library | REST/JSON partner APIs | ✅ **DirectApply webhook** | Partner terms | ★★★★★ | Medium (partner signup) |
| Madgex | REST + SOAP per-client | ⚠️ Via Zapier triggers | Client SLA | ★★★★ (Guardian, Times, Telegraph Jobs) | **High** (per-board contracts) |

**CV-Library is the only proposed source with a genuine webhook** — its DirectApply API posts candidate application data to partner webhook URLs. Teaching Vacancies is the fastest, cheapest new integration: public JSON, no auth, Open Government Licence, schema.org JobPosting format.

---

## 2. ATS platform deep dive

### The fundamental structural barrier

ATS webhooks exist to serve **employers** (their paying customers), not downstream job aggregators. Every ATS studied follows the same pattern: webhooks are configured inside the employer's admin panel, authenticated with the employer's API key, and scoped to that employer's tenant. **No ATS operates a programme where a third-party job board can subscribe to aggregate webhook notifications across all their customers.**

The closest approximations are marketplace partner programs (Teamtailor, SmartRecruiters, Greenhouse), but these still require **per-employer opt-in installation** — each of Job360's 104 company slugs would need to individually approve the integration. This is fundamentally a business development problem, not an engineering one, and scales poorly.

### Platform-by-platform engineering assessment

**Greenhouse (25 slugs — best story):** The public Job Board API at `boards-api.greenhouse.io/v1/boards/{slug}/jobs` has **no documented rate limits** and is heavily cached. Each job object includes an `updated_at` timestamp enabling client-side diffing. Job360 can poll all 25 slugs every 30–60 seconds (25 requests/minute) without issue. The Harvest API webhook catalogue includes `job_created`, `job_post_updated`, and `delete_job` events — relevant but inaccessible without the employer's API key. The Ingestion API exists for "sourcing partners" but pushes candidates INTO Greenhouse, not job data out.

**Lever (12 slugs — good but no posting webhook):** The public Postings API at `api.lever.co/v0/postings/{company}` is similarly unmetered for GET requests. Each posting includes `createdAt` and `updatedAt` fields (millisecond epoch). Critically, **Lever has no `posting.created` or `posting.published` webhook event at all** — a GitHub issue (#26) explicitly requesting this remains unimplemented. Even if Job360 had employer-level access, it couldn't receive posting webhooks.

**Workday (14 slugs — worst story):** No public job board API exists. Career sites use an undocumented internal endpoint (`/wday/cxs/{tenant}/{site}/jobs`) consumed by the site's SPA. Workday is **aggressively anti-scraper** with no published rate limits. Commercial job aggregators (Fantastic.jobs, Apify) build dedicated crawling infrastructure for Workday. Job360 should expect a **5–15 minute latency floor** and intermittent failures. Consider offloading Workday scraping to Apify actors ($1.50–5/1K results) rather than scaling internal infrastructure.

**SuccessFactors (3 slugs — similarly hostile):** No official public API. The hidden `sitemap.xml` on Career Site Builder instances exposes job metadata, but the OData `JobRequisition` endpoint requires tenant credentials. SAP recommends ≤10 concurrent threads. Same 5–15 minute latency floor as Workday.

**SmartRecruiters (6 slugs — tantalising but locked):** The only ATS with explicit job-creation webhook events (`onJobCreatedCallback`, `onJobUpdatedCallback`, `onJobStatusUpdatedCallback`, `onJobAdCreatedCallback`). But these are Customer API-scoped, requiring OAuth 2.0 with employer admin access. The public postings API at `api.smartrecruiters.com/v1/companies/{companyId}/postings` is rate-limit-undocumented but widely used by aggregators.

**Practical recommendation:** Job360 should poll Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, and Personio slugs every **60 seconds** using simple HTTP GET with client-side `updated_at`/`createdAt` diffing. No ETag or conditional fetch is supported anywhere — **hash comparison or timestamp tracking is the only delta mechanism**. Workday and SuccessFactors require separate, more conservative polling (every 10–15 minutes) with error handling for anti-bot responses. Pinpoint requires authenticated API access, so Job360 likely scrapes career-site HTML instead.

---

## 3. Meta-source and partnership opportunities

### TheirStack is the standout option at $59/month

**TheirStack** (theirstack.com) is the only service found that offers exactly what Job360 wants: **"subscribe to new jobs matching criteria X" as a webhook.** Users create saved searches (by role, location, keywords, salary, tech stack) and attach webhook URLs. Events `job.new` and `company.new` fire when matching records are discovered. TheirStack aggregates **327,000+ career pages** across 195 countries, including UK, using ATS scraping (Greenhouse, Lever, Workday, Ashby, etc.) — essentially doing at massive scale what Job360 does across its 47 sources. Pricing starts at **$59/month** (200 API credits + 50 company credits). Each `job.new` webhook event costs 1 credit. This is the most practical single upgrade for freshness, but the credit economy needs careful analysis: at $59/month for 200 credits, Job360 would receive notifications for ~200 new matched jobs/month — likely insufficient for comprehensive UK coverage. Higher tiers exist but pricing escalates.

### Other aggregator options ranked by practicality

**Apify** ($1.50–5/1K results) offers robust platform-level webhooks (`ACTOR.RUN.SUCCEEDED`) that fire when a scraping run completes. Job360 can schedule Apify actors for difficult sources (Workday, Indeed, LinkedIn) on hourly cadences and receive webhook pushes of the resulting datasets. This offloads the most hostile scraping to Apify's proxy infrastructure while keeping costs controlled. The Apify approach is **complementary to direct polling** — use it specifically for sources where Job360's own polling is unreliable.

**Superfeedr** (still active at websub.superfeedr.com) can bridge any RSS feed to a webhook push. Subscribe to WeWorkRemotely, jobs.ac.uk, NHS Jobs XML, DevITjobs XML, and other feeds; Superfeedr polls upstream and POSTs to Job360 on changes. This offloads RSS polling infrastructure but does not improve upstream freshness — the latency floor remains whatever the RSS feed's refresh interval is. Free for up to 10 feeds; paid plans for more. **Practical and cheap** for consolidating RSS polling.

**Coresignal** ($49/mo+) has 437M+ job records but its webhook capability is limited to Employee Data API — the Jobs API is pull-only with ~6-hour refresh cycles. Not suitable for real-time.

**Bright Data** ($250/100K records minimum) offers webhook delivery for completed dataset snapshots, not per-job events. Over-engineered and expensive for Job360's scale.

**Indeed Publisher Program** is **effectively dead** — Indeed deprecated single-source XML feeds (March 31, 2026 for organic jobs; end of 2026 for sponsored). The legacy Publisher API at `api.indeed.com/ads/apisearch` is largely non-functional for new applicants. The replacement Job Sync API is for ATS vendors posting TO Indeed, not aggregators pulling out.

**HiringCafe** scrapes ~30K career pages 3×/day using GPT-4o-mini for structuring, aggregating from 46 ATS platforms (**2.1–2.8M listings**). But it has **no public API, no partnership programme, and no webhook offering.** The GitHub scraper (`umur957/hiring-cafe-job-scraper`) reverse-engineers internal endpoints behind Cloudflare. Relying on this is fragile and ToS-violating. A formal partnership inquiry to the founder (Ali, Stanford PhD) is worth attempting but speculative.

**Google Cloud Talent Solution** is a **search infrastructure service, not a data source** — you upload your own job data and it provides ML-powered matching. It does not supply job listings. Irrelevant for data sourcing.

**LinkedIn Talent Hub** and LinkedIn's Job Posting API are enterprise-only, partner-gated, and do not offer consumer-side webhooks for job feed events. LinkedIn provides **zero public webhook access** for job postings.

### Partnership opportunities with genuine potential

**CV-Library** is the highest-value UK partnership. Their DirectApply API includes webhook-based delivery of candidate applications, and their Job Search/Job View APIs cover one of the UK's largest job boards (4.3M unique seekers/month, 17M CVs). Partnership requires emailing `partnerships@cv-library.co.uk` — not self-serve but well-documented. Already integrates with Greenhouse, Workable, SmartRecruiters, Recruitee, and others.

**Madgex** powers **Guardian Jobs, The Times Jobs, Telegraph Jobs, New Scientist Jobs, Physics World Jobs** and ~250+ publisher career sites globally. However, Madgex is not a single API — each publisher board is a separate deployment with its own webservice URL, API key, and commercial agreement. Job360 would need individual data-sharing deals per publisher. The Zapier integration offers pseudo-webhook triggers (new job live, recruiter published) but still requires per-board contracts. **High effort, high value.**

**NHS Jobs** formal partnership (via `nhsbsa.nhs.uk`) unlocks the Self-Serve API beyond the free public `search_xml` endpoint. Organisations must be UK-based and public-facing. Worth pursuing for the UK's single largest healthcare employer.

---

## 4. The realistic real-time roadmap

### Phase 0: Current state and true latency

Job360 polls all 47 sources on a single cron schedule. Assuming a conservative 4–12 hour polling interval, the **true end-to-end latency from job posting to user notification is 2–12 hours** for most sources, with worst-case latency of 24+ hours for sources with artificial delays (Jobicy, Himalayas, Remotive). The time-bucket promise from Batch 1 (≥90% accuracy in the "last 24h" bucket) is achievable with current polling — but the "last 24h" bucket contains stale jobs that were posted 12–23 hours ago, undermining the freshness perception.

### Phase 1: Quick wins with zero cost (1–2 weeks)

Implement **tiered polling schedules** based on source capability:

- **Every 60 seconds** (52 slugs): Greenhouse (25), Lever (12), Ashby (9), SmartRecruiters (6) — all have unmetered or generous public APIs. Total: ~52 requests/minute, trivial server load.
- **Every 5 minutes**: Reed API (within 2K/hr budget), Findwork API (within 60/min budget), Rippling ATS (polite), Jooble API.
- **Every 15 minutes**: Workday (14 slugs, conservative to avoid blocks), TheMuse (well within 3,600/hr budget), WeWorkRemotely RSS, NHS Jobs XML, Teaching Vacancies JSON.
- **Every 60 minutes**: Adzuna, JSearch (pro tier), SerpApi (with `no_cache`), jobs.ac.uk RSS, HN Algolia, Workable, Recruitee, Personio XML.
- **Every 2–6 hours**: RemoteOK, Arbeitnow, DevITjobs, AIJobs.net, 80000Hours, LinkedIn (with proxy rotation), Indeed/Glassdoor (via JobSpy), NoFluffJobs, BioSpace, FindAJob, Climatebase, JobTensor, AIJobs Global/AI.
- **Daily**: Himalayas (24h cache), Remotive (24h delay), YC Companies (daily sync), EURAXESS (nightly batch), BCS Jobs, University Jobs, WorkAnywhere, SuccessFactors (3 slugs, conservative).

This alone moves **~52 ATS slugs from hours-stale to ~1-minute freshness** and upgrades Reed, Findwork, NHS Jobs, and WeWorkRemotely to sub-15-minute freshness — at zero incremental cost.

**Also in Phase 1:** Enable HN Firebase SSE streaming for the single true real-time source. Implement client-side `updated_at` / `createdAt` diffing across all ATS APIs to avoid processing unchanged listings. Add `If-Modified-Since` headers to all RSS feed requests (most servers will honour them even without documentation).

### Phase 2: Infrastructure optimisation (1–2 months)

- **Implement conditional fetching everywhere**: Even without documented ETag/Last-Modified support, attempt conditional headers on every request. Many web servers return 304 Not Modified from CDN layers even when the API documentation doesn't mention it. This reduces bandwidth and processing cost for no-change polls.
- **Build a unified diff engine**: Hash each job listing (title + company + URL) and store hashes. On each poll, compare hashes to detect new, updated, and deleted listings without parsing full payloads. This is the core infrastructure that makes high-frequency polling sustainable.
- **Deploy Superfeedr** for RSS feeds: Subscribe WeWorkRemotely, jobs.ac.uk, NHS Jobs XML, DevITjobs XML, and BioSpace (if a jobs RSS exists) to Superfeedr's WebSub hub. Receive HTTP POST webhooks on feed changes instead of polling. Cost: free for ≤10 feeds.
- **Add Teaching Vacancies and GOV.UK Apprenticeships APIs**: Both are free, no-auth, well-documented — immediate additions to the source list.
- **Upgrade Jobicy, Himalayas, and Remotive to their actual refresh cadence**: Stop polling these more frequently than their artificial delays allow (6h, 24h, 24h respectively). Redirect the saved polling budget to higher-value sources.

### Phase 3: Partnerships (3–6 months)

- **CV-Library partnership**: Contact `partnerships@cv-library.co.uk` for Job Search/Job View API access and DirectApply webhook integration. This is the highest-ROI partnership available — one of the UK's largest job boards with genuine webhook capability.
- **NHS Jobs formal partnership**: Contact the NHS Jobs integration team for Self-Serve API access beyond the public XML endpoint. Unlocks higher-frequency, structured access to UK's largest healthcare employer.
- **TheirStack trial** ($59/mo): Test the saved-search webhook feature for specific high-value UK job categories. Evaluate whether the credit economy (200 credits/month at base tier) provides sufficient coverage or requires scaling to a higher tier.
- **Madgex/publisher outreach**: Approach Guardian Jobs and Times Jobs about data-sharing agreements. High effort but unlocks premium UK job inventory.

### Phase 4: Paid infrastructure scaling (future)

- **Apify for hostile sources**: Deploy Apify actors for Workday (14 slugs), SuccessFactors (3 slugs), and Indeed/Glassdoor scraping. Webhook on run completion pushes datasets to Job360. Cost: ~$5–20/month depending on volume and frequency.
- **TheirStack scale-up**: If the $59/mo trial proves valuable, scale to higher tiers for broader UK webhook coverage across thousands of ATS career pages Job360 doesn't directly poll.
- **Proxy infrastructure for LinkedIn/Indeed**: If LinkedIn or Indeed volume justifies it, deploy residential proxy rotation (Bright Data, IPRoyal) at ~$10–50/month to sustain higher-frequency polling of these hostile sources.

### Expected latency outcomes by phase

| Phase | Sources at <5min | Sources at <1h | Sources at 1–6h | Sources at >6h |
|-------|-----------------|----------------|-----------------|----------------|
| Phase 0 (current) | 0 | 0 | ~10 | ~37 |
| Phase 1 (tiered polling) | **~55** (ATS + Reed + Findwork + HN) | **~10** (Adzuna, SerpApi, TheMuse, WWR, NHS, etc.) | **~20** | **~7** (Jobicy, Himalayas, Remotive, YC, etc.) |
| Phase 2 (optimisation) | ~55 | ~15 | ~18 | ~5 |
| Phase 3 (partnerships) | ~55 | ~20 (+ CV-Library, NHS partner) | ~15 | ~5 |

---

## 5. The honest verdict

### How many sources support real-time or near-real-time?

Of 47 current sources, **exactly one** (HN Firebase) supports true real-time push. **Zero** offer free webhooks for new job posting events. After implementing Phase 1 tiered polling, approximately **55 source endpoints** (primarily ATS slugs) can achieve **sub-5-minute** freshness through simple high-frequency polling against unmetered APIs — this is 60% of Job360's source endpoints by count and likely a higher percentage by UK job volume, since the ATS boards represent ~104 company career pages.

Approximately **10 additional sources** can achieve sub-1-hour freshness. The remaining ~25 sources are limited to 1–24 hour freshness due to rate limits (LinkedIn, Indeed, Adzuna), artificial delays (Jobicy, Remotive, Himalayas), or structural limitations (EURAXESS nightly batch, Nomis quarterly statistics).

### Can the 24h bucket KPI be met through polling alone?

**Yes.** Batch 1's KPI 1 (≥90% of jobs correctly classified in the 24h bucket) is achievable with 4-hourly polling of all sources. **The binding constraint is not real-time ingestion but accurate timestamp extraction** — many sources provide only day-level `date` granularity (Reed uses DD/MM/YYYY) rather than precise timestamps. A job posted at 23:00 and polled at 03:00 the next day has a 4-hour true lag but might be classified into the wrong day-bucket if the date field lacks hour precision. Solving this is a data-parsing problem, not a webhook problem.

With Phase 1 tiered polling, the 24h bucket accuracy should exceed **95%** because the highest-volume sources (ATS boards) will be polled every 60 seconds, and delta detection via `updated_at` timestamps will catch new postings within 1–2 minutes.

### Does webhook-based ingestion matter for the MVP?

**No.** Webhooks are a solution to a problem Job360 does not have at its current scale. The only sources where webhooks would provide meaningful improvement over optimised polling are the ones that don't offer webhooks. The ATS platforms — which represent the majority of Job360's unique, high-value job listings — can be polled every 60 seconds at zero cost, achieving the same effective latency as a webhook.

**The effort should go into polling optimisation, not webhook integration.** Specifically:

- **Tiered polling schedules** (Phase 1) deliver 90% of the freshness improvement for 10% of the effort
- **Client-side diffing** (hash comparison, `updated_at` tracking) makes high-frequency polling sustainable
- **Conditional HTTP headers** reduce bandwidth and processing for unchanged feeds
- **Superfeedr for RSS feeds** is the only webhook-adjacent investment worth making immediately (free for ≤10 feeds)

### Minimum viable real-time architecture

To meet a freshness SLA of **"95% of new high-score jobs reach user within 2 hours of posting"**, Job360 needs:

- **60-second polling** of Greenhouse, Lever, Ashby, SmartRecruiters (52 slugs) — these are likely the highest source of high-score jobs
- **5-minute polling** of Reed, Findwork, Teaching Vacancies, NHS Jobs
- **15-minute polling** of Workday, TheMuse, WeWorkRemotely
- **A diff engine** that detects new listings by comparing against a local hash/ID store
- **Push notification dispatch** within 60 seconds of detecting a new high-score job (per Batch 2 architecture)
- Total infrastructure: a single lightweight worker process making ~60–80 HTTP requests per minute, plus a Redis/SQLite store for job ID hashes. **No message queue, no webhook receiver, no additional infrastructure required.**

The estimated infrastructure cost for this architecture is effectively **zero incremental cost** beyond existing server capacity — 80 HTTP requests/minute is trivial load.

### The one webhook investment worth making

If Job360 allocates any budget to webhook-based data sourcing, **TheirStack at $59/month** is the single best investment. It provides the exact "subscribe to criteria → receive webhook on new match" capability across 327K+ career pages, including UK sources that Job360 doesn't directly poll. The credit economy limits volume (200 events/month at base tier), but even as a supplementary signal for high-priority job categories, it fills gaps in Job360's ATS coverage without requiring new scraping infrastructure. The alternative — building equivalent coverage through direct polling — would require discovering and maintaining hundreds of additional company career page URLs.

**Everything else should be polling.** The job data ecosystem has no economic incentive to offer free push notifications to downstream aggregators. Accepting this reality and building excellent polling infrastructure is the pragmatic path to Job360's freshness promise.