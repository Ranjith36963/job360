# Deep analysis of Job360 Pillar 3 improvements

**Job360 can reach 500+ company slugs within days, add 4 high-value free APIs immediately, and cut fetching bandwidth by 60–90% — but most UK niche job boards have no free APIs.** The most impactful discovery is that two GOV.UK services (Teaching Vacancies and Apprenticeships) offer fully free, open-licensed REST APIs returning schema.org-compliant JSON with real posting dates. Meanwhile, the Feashliaa/job-board-aggregator GitHub repo contains ~95,000 ATS company slugs harvested via Common Crawl, making the 500+ slug target trivially achievable. Non-tech domain coverage will rely primarily on better use of generalist aggregators (Reed, Adzuna) with sector filtering rather than dozens of niche integrations, since the research found that **zero** free APIs exist across finance, legal, marketing, consulting, supply chain, and media job boards in the UK.

---

## UK aggregator APIs: three to keep, one to add, one to drop

Job360's existing UK aggregator stack (Reed, Adzuna, Careerjet, Indeed/JobSpy, FindAJob) covers the generalist base well but contains redundancy. **Reed** returns ~200,000+ live vacancies across all white-collar domains with real posting dates via a simple REST API (`GET https://www.reed.co.uk/api/1.0/search`), Basic Auth, and pagination up to 100 results per call. **Adzuna** is even larger — it powers the DWP's FindAJob service, partners with the ONS, and covers every sector with ISO 8601 `created` timestamps. **Careerjet** functions as a meta-search engine aggregating from Reed, Indeed, Totaljobs, and thousands more, with real datetime posting dates. **Indeed via JobSpy** provides the broadest scraping coverage with minimal rate limiting and fields including `date_posted`, `company_industry`, and `salary`.

The FindAJob (DWP) scraper should be deprioritized or dropped. Since Adzuna powers FindAJob behind the scenes, Job360 is likely double-counting many listings. The HTML scraping yields no reliable dates and fights Akamai anti-bot protections — effort better spent elsewhere.

**Jooble is already integrated** in Job360 (listed in the keyed APIs). No genuinely new free UK aggregator API was found. **CV-Library** has an API at `cv-library.co.uk/developers` but requires partner approval via `partners@cv-library.co.uk` — worth pursuing as it is one of the UK's top three job boards with unique listings. The entire StepStone group (Totaljobs, CWJobs, Jobsite, CityJobs) has **no free API access whatsoever**. Monster UK is effectively defunct. Glassdoor's API was deprecated in 2021. Regional boards (s1jobs, nijobs) have no APIs and their coverage is already captured by Reed and Adzuna with location filtering.

For date accuracy improvements across existing sources, Reed provides `date` and `expirationDate`, Adzuna provides `created` as ISO 8601, Careerjet provides full datetime strings, and JobSpy provides `date_posted`. All four major aggregators return real posting dates, so the 14/47 sources hardcoding `now()` are likely ATS scrapers and HTML scrapers that need per-source date extraction fixes rather than new source additions.

---

## Two new ATS platforms worth integrating, and where the 95,000 slugs live

Beyond Job360's existing 10 ATS platforms, the research confirmed that only **6 ATS platforms have truly public, unauthenticated job board APIs**: Greenhouse, Lever, Ashby, Workable, Recruitee, and Personio — all already integrated. However, two additional platforms emerged as strong candidates.

**Rippling** has an undocumented but functional public endpoint at `GET https://ats.rippling.com/api/v2/board/{board_slug}/jobs?page=0&pageSize=50` returning JSON with `uuid`, `name`, `department`, `workLocation`, and `url` fields. Full descriptions require a per-job detail call. No authentication is needed. Rippling has **15,000+ customers** with growing UK presence across tech and non-tech sectors. Integration complexity is medium — the two-call pattern (listing + detail) mirrors Greenhouse's approach.

**Comeet** (now Spark Hire Recruit) has a Careers API using public, embeddable tokens: `GET https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true`. Fields include `uid`, `name`, `department`, `location` (with country, city, postal code), `employment_type`, `experience_level`, `time_updated`, and full `description`. The token is designed for public embedding — not a secret key. Market presence is primarily US/Israel with ~2,000+ companies.

**BambooHR, JazzHR, Breezy HR, Jobvite, Taleo, iCIMS, Applied, Factorial, and Fountain all require authenticated access** (per-company API keys or partner agreements) and cannot be integrated in Job360's unauthenticated model. **Teamtailor** has a partner program for job boards that could provide webhook-based integration or XML feeds, but requires formal registration.

UK ATS market share data shows **Workday dominates enterprise** (37.1% of Fortune 500), followed by **SuccessFactors** (13.4%). Greenhouse has ~7,500–25,000 companies globally; Lever is popular with 100–1,000 employee companies; Ashby is newest and growing fastest in startups. No precise UK-specific market share data is publicly available.

For slug discovery, the **Feashliaa/job-board-aggregator** GitHub repo is the single most valuable resource. It contains **~95,000 unique company identifiers** across Greenhouse, Lever, Ashby, BambooHR, and Workday, stored as per-ATS JSON files (`data/greenhouse_companies.json`, etc.). These were harvested from **Common Crawl CDX index data** using regex extraction on 20+ ATS domain patterns across multiple crawl snapshots. The repo is actively maintained (196 commits, daily GitHub Actions) with MIT-licensed code and CC BY-NC 4.0 data. The path to 500+ UK-relevant slugs:

- **Feashliaa repo** (Day 1): Clone, parse JSON files, filter by UK job locations → estimated **2,000–5,000 UK slugs**
- **Google dorking** (Day 1–2): `site:boards.greenhouse.io UK`, `site:jobs.lever.co UK`, etc. → **100–300 confirmed UK slugs**
- **Common Crawl Athena** (Day 2–3): SQL queries against the columnar index for each ATS domain, costing **under $1 per query** → **tens of thousands of slugs**
- **YC Companies API** (Day 2): Fetch `all.json`, filter by `regions` containing "United Kingdom" and `isHiring: true`, crawl websites for ATS detection → **50–200 slugs** (but heavily tech-biased)

---

## The non-tech domain gap is real, but four free APIs exist

The research systematically investigated 70+ niche UK job boards across 12 professional domains. The finding is stark: **the vast majority have no free API, no RSS feed, and no programmatic access**. The four exceptions are genuinely valuable.

**Teaching Vacancies** (`teaching-vacancies.service.gov.uk/api/v1/jobs.json`) is a GOV.UK service with a fully free REST API under Open Government Licence. It returns schema.org `JobPosting` JSON with `datePosted`, `description`, `educationRequirements`, salary details, `jobLocation`, and `hiringOrganization`. Pagination is 50 results per page. **~6,485 live teaching and education support jobs** across England. Integration complexity is trivially low — this is the single highest-value new source discovered in the entire research.

**GOV.UK Apprenticeships** (`api.apprenticeships.education.gov.uk`) requires a free API key from the developer portal, with a rate limit of 150 requests per 5-minute period. Returns apprenticeship vacancies in structured JSON.

**NHS Jobs** has a confirmed XML Search API at `https://www.jobs.nhs.uk/api/v1/search_xml` returning structured data with job ID, reference, title, employer, salary, posting/closing dates, URL, and location with postcode. Additionally, an External Job Board API exists for approved third-party platforms — eligibility requires being UK-based, health-related, and not charging job seekers. Apply via `nhsbsa-nhsjobs-support@nhsbsa.nhs.uk`. **20,000–30,000+ active vacancies** from the UK's largest employer.

**EURAXESS** (European research mobility portal) has an XML import/export system with a documented XSD schema. Registration requires contacting `support@euraxess.org` for an organisation ID. Data is processed nightly. Covers thousands of EU/UK research positions with **2+ million annual visitors**.

Beyond these, the **Madgex platform** represents a strategic opportunity. GAAPweb (accountancy/finance), TotallyLegal, and several other niche UK boards run on Madgex (now Wiley). Madgex has SOAP webservices at `jobboard.webservice.madgex.co.uk` and a Node.js client library at `github.com/guidesmiths/node-madgex`. A single Madgex partnership could unlock access to multiple boards across finance, legal, and professional verticals. Similarly, several **Haymarket Media Group** properties (Campaign Jobs, CIPD/People Management Jobs) may share a common backend.

For all other non-tech domains — finance (eFinancialCareers), legal (TotallyLegal), marketing (Campaign Jobs, The Drum), consulting (Top-Consultant, Consultancy.uk), supply chain (CIPS), and media (Press Gazette) — the most practical strategy is **sector-filtered queries against Reed and Adzuna APIs** rather than scraping dozens of tiny niche boards. Both Reed and Adzuna support keyword and category filtering that covers all these verticals.

---

## Open-source patterns worth adopting for source management

The **Feashliaa/job-board-aggregator** architecture offers the most directly transferable patterns. Its pipeline runs as: Common Crawl slug harvesting → per-ATS JSON company lists → 30-concurrent-worker scraping (10 for rate-limited platforms like BambooHR) → skill-level classification via weighted keyword scoring → URL-based deduplication → 30-day stale-job pruning → gzipped chunk output → GitHub Actions deployment. The variable concurrency per platform and automatic skill-level tagging are particularly relevant to Job360.

The **Levergreen/job-board-scraper** uses a more production-grade architecture: Scrapy spiders → S3 raw HTML caching → Postgres → dbt transformation → Airtable/Softr frontend. Key innovations include **S3 HTML caching** (skip re-scraping if already fetched today), **dbt Core for data normalization** (industry-standard transformation layer), and a `compare_workflow_success.py` validation step comparing expected vs actual scrape counts per company — directly addressing Job360's source health monitoring gap.

**JobSpy's unified `JobPost` schema** is the gold standard for data normalization across heterogeneous sources. Core fields: `title`, `company`, `company_url`, `job_url`, `location` (country/city/state), `is_remote`, `description`, `job_type`, `compensation` (interval/min/max/currency), `date_posted`, with source-specific extensions for LinkedIn (`job_level`), Indeed (`company_industry`, `company_employees_label`), and Naukri (`skills`, `experience_range`). The `enforce_annual_salary` flag automatically normalizes hourly/daily/weekly wages to annual figures.

**HiringCafe's internal API** (`hiring.cafe/api/search-jobs`) provides access to **2.8 million listings from 46 ATS platforms** via an Elasticsearch-backed search. The scraper at `umur957/hiring-cafe-job-scraper` reveals the API returns 1,000 jobs per page with fields including `board_token`, `apply_url`, `description_clean`, and unique engagement metrics (`viewed_count`, `applied_count`). HiringCafe itself scrapes 30,000+ company career pages 3x daily using Oxylabs rotating proxies and GPT-4o-mini for NLP extraction. While using HiringCafe as a meta-source carries dependency risk, it could provide immediate coverage of 46 ATS platforms.

---

## Conditional fetching can cut bandwidth 60–90% with three specific changes

Most ATS APIs do not support HTTP conditional requests (ETag/If-Modified-Since), but three practical patterns can dramatically reduce unnecessary data transfer.

**Date-based incremental fetching** works for APIs that support temporal filtering. Greenhouse's Harvest API accepts an `updated_after` ISO-8601 parameter on `/v1/job_posts`. Adzuna supports `max_days_old` to limit results by listing age. Reed supports sorting by date. For these sources, storing a `last_fetch_at` timestamp per source and filtering accordingly can reduce data transfer by **80–95%** on subsequent runs.

**RSS conditional GET** is highly effective. Research confirms **89% of RSS feeds support ETags** and **73% honor Last-Modified headers**. Sending `If-None-Match` and `If-Modified-Since` headers yields 304 responses that eliminate all parsing overhead. The `aiohttp-client-cache` library adds this automatically to aiohttp sessions with SQLite or Redis backends. For Job360's 8 RSS/XML feed sources, this is a drop-in improvement.

**Content hashing** handles sources with no server-side support. For HTML scrapers and APIs without conditional headers, SHA-256 hashing of normalized job content (title + company + location + description snippet) detects changes without server cooperation. Hash individual job entries rather than entire pages to avoid false positives from layout changes.

A complete implementation would layer these: `aiohttp-client-cache` as the session layer for automatic conditional requests, `IncrementalFetchState` storing per-source `last_fetch_at` timestamps in JSON, and `ContentHashStore` for scraped sources. The fetch flow becomes: check circuit breaker state → send conditional headers → if 304, skip → if 200, check content hash → if unchanged, skip processing → if changed, process and update state.

---

## Circuit breakers and health scoring replace "newly empty" detection

Job360's current source health monitoring (comparing current run vs last 5 runs, logging "newly_empty") is detection-only with no automated response. Three patterns provide a complete solution.

**Circuit breakers** using `pybreaker` create per-source breakers with configurable thresholds. The standard Closed → Open → Half-Open state machine prevents wasted requests against failing sources. Recommended defaults: API sources get `fail_max=3, reset_timeout=300s`; RSS feeds get `fail_max=5, reset_timeout=600s`; HTML scrapers get `fail_max=5, reset_timeout=900s`. The critical pattern is placing the circuit breaker **outside** the retry logic — retries happen within a closed breaker, but once the breaker opens, no retries are attempted. The `tenacity` library provides exponential backoff retries (1s → 2s → 4s, matching Job360's existing [1,2,4]s pattern) that compose cleanly with pybreaker.

**Source quality scoring** replaces binary empty/not-empty with a **composite 0–100 health score** computed from six metrics: success rate (30% weight), data completeness (20% weight), response time (15%), freshness (15%), consistency (10%), and duplicate rate (10%). These metrics are tracked in a rolling window of the last 100 fetches or 7 days per source. An exponential moving average (EMA) with α=0.1 detects degradation trends before complete failure.

**Auto-disable with exponential backoff probing** defines four source states: Active (normal), Degraded (health score <70%, still fetching but alerting), Disabled (health score <30% or 5+ consecutive failures, periodic probing only), and Quarantined (manual review required). Disabled sources are probed at exponentially increasing intervals: 5 minutes → 10 minutes → 20 minutes → 1 hour. On successful probe, the source transitions to Half-Open (requiring 2-3 consecutive successes before returning to Active). This eliminates manual investigation of broken sources while ensuring recovery is automatic.

---

## Complete inventory of recommended changes

**Immediate integrations (free APIs, 1–3 days effort each):**

| Source | Domain | Jobs | Effort | Impact |
|--------|--------|------|--------|--------|
| Teaching Vacancies API | Education/Public Sector | ~6,485 | Very Low | High |
| GOV.UK Apprenticeships API | Cross-domain | Thousands | Low | Medium |
| NHS Jobs XML API | Healthcare | 20,000–30,000+ | Low-Medium | Very High |
| Rippling ATS | Cross-domain | Per-company | Medium | Medium |
| Comeet/Spark Hire ATS | Cross-domain | Per-company | Low-Medium | Low |

**Partnership inquiries (free access possible, 1–4 weeks):**

| Source | Domain | Contact | Expected Value |
|--------|--------|---------|----------------|
| CV-Library API | Cross-domain | partners@cv-library.co.uk | Very High |
| CharityJob API | Charity/Non-profit | developers.charityjob.co.uk | Medium |
| EURAXESS XML | Academic/Research | support@euraxess.org | Medium |
| Madgex Partnership | Finance/Legal/Professional | madgex.com | High (multi-board) |
| NHS Jobs External API | Healthcare | nhsbsa-nhsjobs-support@nhsbsa.nhs.uk | Very High |

**Sources to remove or reclassify:** YC Companies (not job listings — use only for slug discovery), Nomis (vacancy statistics, not jobs). FindAJob scraping should be deprioritized given Adzuna redundancy.

**Source routing configuration** should map domains to sources:

- **Tech**: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Pinpoint, Recruitee, HN Jobs, DevITjobs, Findwork, RemoteOK, WeWorkRemotely, Landing.jobs, Remotive, Himalayas, Jobicy, Arbeitnow + all generalists
- **Healthcare**: NHS Jobs API, BMJ Careers (scrape), BioSpace + generalists (Reed, Adzuna filtered)
- **Education**: Teaching Vacancies API, jobs.ac.uk RSS, EURAXESS + generalists
- **Finance**: eFinancialCareers (scrape/Madgex), GAAPweb (Madgex) + generalists
- **Legal**: TotallyLegal (scrape/Madgex) + generalists
- **Public Sector**: Civil Service Jobs (scrape), Teaching Vacancies, GOV.UK Apprenticeships, LocalGov.co.uk RSS + generalists
- **Charity**: CharityJob API + generalists
- **All other domains**: Reed API + Adzuna API with sector-specific keyword/category filtering

**Slug expansion strategy targets 500+ in 3 days:** Parse Feashliaa's ~95,000 slugs (filter to UK = ~2,000–5,000), validate with Google dorking (~200–500 confirmed UK), optionally run Common Crawl Athena queries for independent discovery. Industry diversification comes from targeting non-tech UK companies specifically: search `site:boards.greenhouse.io "London" OR "Manchester" OR "Birmingham"` filtered to finance, legal, healthcare, and professional services companies.

**Estimated total UK job coverage after improvements:** Reed (~200K) + Adzuna (~hundreds of thousands, deduplicated) + NHS Jobs (~25K) + Teaching Vacancies (~6.5K) + ATS boards (500+ companies × ~5–20 jobs each = 2,500–10,000) + Indeed/JobSpy (~unlimited with targeted queries) + remaining niche sources (~5K). Conservative estimate: **300,000–400,000 unique UK job listings** across all white-collar domains, up from the current unquantified but likely tech-heavy subset. The bigger improvement is domain breadth — healthcare alone adds 25,000+ listings that Job360 currently misses entirely.