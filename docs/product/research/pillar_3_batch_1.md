# Job360's freshness promise requires a new data model, honest source tiers, and hard trade-offs

**Job360's core product promise — trustworthy time-bucketed delivery of live UK jobs — is architecturally achievable, but only with significant compromises the current system does not make.** Of 47 current sources, only 6–8 can provide real posting dates with sub-24-hour reliability. Fourteen sources fabricate dates outright, three use semantically wrong date fields, and most ATS platforms expose no creation date at all. The HiringCafe model — the most frequently referenced benchmark — depends on paid proxies, GPT-4o-mini extraction, and Elasticsearch, making it unreplicable under zero-cost constraints. The minimum viable freshness architecture requires a multi-column date model with confidence tracking, per-source freshness tiers visible to users, a scrape-and-diff ghost detection pipeline with completeness guards, and an honest reclassification of what "last 24 hours" means per source.

This report provides concrete specifications for all four architectural components plus a brutally honest assessment of what is and is not achievable.

---

## 1. A five-column date model replaces the broken single-column approach

### Why the current schema fails

Job360's single `date_found` column conflates three fundamentally different concepts: when the source claims a job was posted, when Job360 first discovered it, and when Job360 last confirmed it still exists. Worse, **14 of 47 sources hardcode `date_found = datetime.now()`**, fabricating a "posted today" timestamp for jobs that may be weeks old. Three additional sources use semantically wrong fields — Jooble's `updated` timestamp, Greenhouse's `updated_at`, and NHS Jobs' `closingDate`. This means **roughly 36% of all sources produce dates that are actively misleading** for time-bucket assignment.

### What open-source tools actually do

No existing open-source job scraper implements a production-grade multi-date model. **JobSpy** stores a single nullable `date_posted` field (type `date`, not `datetime`) with no first-seen or last-confirmed tracking. **JobFunnel** tracks `POST_DATE` but defaults it to today when missing — silently fabricating freshness. **Feashliaa's job-board-aggregator** performs 30-day pruning based on implicit age but stores no explicit date columns. **HiringCafe** comes closest with a two-layer architecture: the LLM extraction layer produces a nullable `posted_at` (ISO 8601, extracted by GPT-4o-mini from job description text), while the infrastructure layer tracks `dateFetchedPastNDays` as the primary freshness signal. Critically, HiringCafe treats its own discovery date — not the employer's claimed date — as the authoritative freshness metric.

None of these tools track date confidence, and none distinguish between "source posted date" and "first seen by us" as separate database columns.

### Proposed schema for Job360

```sql
ALTER TABLE jobs ADD COLUMN posted_at         TIMESTAMPTZ     NULL;
ALTER TABLE jobs ADD COLUMN first_seen_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW();
ALTER TABLE jobs ADD COLUMN last_seen_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW();
ALTER TABLE jobs ADD COLUMN last_updated_at    TIMESTAMPTZ     NULL;
ALTER TABLE jobs ADD COLUMN date_confidence    TEXT            NOT NULL DEFAULT 'low';
ALTER TABLE jobs ADD COLUMN date_posted_raw    TEXT            NULL;
ALTER TABLE jobs ADD COLUMN consecutive_misses INTEGER         NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN staleness_state    TEXT            NOT NULL DEFAULT 'active';
```

**Field definitions and update rules:**

**`posted_at`** (nullable) — The date the source claims the job was posted. Set only when the source provides a genuine posting date: an API field explicitly named `datePosted`, `date_posted`, or `publishedAt`; a schema.org JSON-LD `datePosted` value from structured HTML; or a parsed relative date string ("3 days ago" → `now() - 3 days`). **Never set this from `updated_at` fields, closing dates, or `datetime.now()` fallbacks.** When null, it signals that no trustworthy source date exists.

**`first_seen_at`** (required) — The UTC timestamp when Job360's scraper first inserted this job. Set exactly once at INSERT time. Never updated afterward. This is the fallback freshness signal when `posted_at` is null.

**`last_seen_at`** (required) — The UTC timestamp when Job360's scraper most recently confirmed this job is still present on the source. Updated on every scrape cycle where the job appears. Drives the ghost detection pipeline.

**`last_updated_at`** (nullable) — Set when the source's content hash (title + description + salary) changes between scrapes, indicating the employer modified the listing. Distinct from `last_seen_at`, which updates even when content is unchanged.

**`date_confidence`** — An enum with four values reflecting the trustworthiness of `posted_at`:

- **`high`** — `posted_at` comes from a structured API field (`datePosted`, `publishedAt`, `created`) or schema.org JSON-LD. The source is a primary job board (Reed, Ashby) or government API (Teaching Vacancies).
- **`medium`** — `posted_at` was parsed from a relative date string ("3 days ago", "Posted yesterday"). Accuracy degrades with time since scrape.
- **`low`** — `posted_at` is null; `first_seen_at` is used as a proxy. The source provides no posting date or only an `updated_at` field.
- **`fabricated`** — Legacy flag for sources known to hardcode `datetime.now()`. These jobs must be excluded from the 24h bucket or shown with a warning.

**`date_posted_raw`** — The raw string from the source before parsing (e.g., "3 days ago", "2025-04-15T10:00:00Z", or null). Stored for audit, debugging, and future reprocessing.

### Bucket assignment logic

The effective posting date for bucket assignment should follow this precedence:

1. If a repost is detected (same company, cosine similarity ≥ 0.85), use the **original posting's** `posted_at` or `first_seen_at` — whichever is earlier
2. Else if `posted_at` is not null and `date_confidence` is `high`, use `posted_at`
3. Else if `posted_at` is not null and `date_confidence` is `medium`, use `posted_at` but flag the job in the UI as "approximate date"
4. Else use `first_seen_at` and flag as "date unavailable — showing discovery date"

Jobs with `date_confidence = 'low'` or `'fabricated'` should **never** enter the "last 24h" bucket without an explicit UI disclaimer.

---

## 2. Only 6–8 of 47+ sources can honestly support the 24-hour bucket

### The tiered freshness SLA

After auditing all 47 current sources plus the 6 proposed in Pillar 3 research, each source falls into one of four freshness tiers based on three factors: whether it provides a real posting date, how frequently the upstream data refreshes, and what rate limits constrain scrape frequency.

**Tier A — Real-time capable (<1h data lag, real posting dates):**

| Source | Date Field | Rate Limit | Recency Filter | Scrape Interval |
|--------|-----------|------------|----------------|-----------------|
| Reed API | `datePosted` (real) | ~2,000 req/hr | Likely `postedWithin` (undocumented) | Every 2h |
| Adzuna API | `created` (ISO 8601) | Generous (unspecified) | `max_days_old=1` ✓ | Every 4h |
| Ashby ATS API | `publishedAt` (real) | No documented limit | None (full list) | Every 4h per company |
| Teaching Vacancies | `datePosted` (schema.org) | Reasonable use | None documented | Every 6h |
| NHS Jobs XML | Posted date + closing date | Requires approved access | None documented | Every 6h |
| GOV.UK Apprenticeships | Standard posting dates | 150 req / 5 min | None documented | Every 6h |

**These 6 sources are the only ones that can honestly populate the "last 24 hours" bucket.** Reed and Adzuna provide the highest volume of UK white-collar jobs in this tier. Ashby provides real `publishedAt` dates but requires per-company polling. The three government sources cover niche sectors (education, health, apprenticeships) with excellent date quality.

**JSearch (via RapidAPI)** would be Tier A — it provides real-time Google for Jobs data with `job_posted_at_datetime_utc` and a `date_posted=today` filter — but the free tier allows only **500 requests per month (~17/day)**, making it structurally insufficient for production. The Pro tier costs $25/month.

**Tier B — Hourly freshness (1–6h lag, usable posting dates):**

| Source | Date Field | Key Limitation | Scrape Interval |
|--------|-----------|----------------|-----------------|
| FindWork API | `date_posted` | Aggregator lag, tech-focused | Every 6h |
| UK HTML boards (Totaljobs, CV-Library, CWJobs) | `datePosted` via JSON-LD | Requires HTML parsing, anti-bot risk | Every 6h |
| Workable ATS | Likely `published_on` | Public API underdocumented | Every 6h per company |

These sources provide real posting dates but with some lag or parsing complexity. HTML-scraped UK boards typically embed schema.org `datePosted` in JSON-LD structured data, which is reliable when extractable.

**Tier C — Daily freshness (6–24h lag, date ambiguity):**

| Source | Date Issue | Scrape Interval |
|--------|-----------|-----------------|
| Greenhouse ATS | Only `updated_at` — no `created_at` in public API | Every 4h (use first-seen as proxy) |
| Lever ATS | `createdAt` undocumented and reportedly unreliable (GitHub Issue #35) | Every 4h (use first-seen) |
| Careerjet | Aggregation date, not original posting | Every 12h |
| Jooble | Returns `updated` not `posted` | Every 12h |
| Himalayas | 24h data cache | Every 24h |
| Jobicy | 6h intentional publication delay | Every 12h |
| TheMuse | US-focused, moderate freshness | Every 24h |
| Arbeitnow | ATS aggregator, unclear date semantics | Every 12h |
| Comeet ATS | Only `time_updated`, no creation date | Every 6h (use first-seen) |
| EURAXESS | No read API, HTML scraping required | Every 24h |
| Rippling ATS | **No date fields at all** in public API | Every 6h (first-seen only) |

**Greenhouse is the critical gap.** It is the most widely used ATS among tech companies, yet its public API exposes only `updated_at`. Every content edit — a salary tweak, a department change — resets this timestamp. Job360 must use `first_seen_at` as the primary freshness signal for all Greenhouse-sourced jobs, with `date_confidence = 'low'`.

**Tier D — Weekly or no freshness guarantee (>24h lag or date fabrication):**

| Source | Issue |
|--------|-------|
| Remotive API | **24h intentional delay** in free tier |
| RemoteOK API | **24h delay** on free tier, no filtering |
| Workday | No public API; requires per-company HTML scraping |
| Civil Service Jobs | No API; scraping required |
| **14 current sources hardcoding datetime.now()** | **Date fabrication — structurally unfixable without rewriting scrapers** |

### The 14 fabricating sources

These sources call `date_found = datetime.now()` because they scrape HTML without access to real posting dates. For many, **this is not a bug that can be fixed** — the upstream HTML genuinely does not contain a posting date in any extractable form. Options for each are:

1. **Check for schema.org JSON-LD** — many UK job boards now embed `datePosted` in structured data even when it's not visible in the page layout. A targeted extraction pass could recover real dates for some of these sources.
2. **Accept first-seen as the date** — rename `datetime.now()` to `first_seen_at` and set `posted_at = NULL` with `date_confidence = 'low'`. This is honest rather than fabricated.
3. **Exclude from the 24h bucket** — jobs from these sources enter the "48h–7d" or "7–21d" bucket only, with a UI note: "Discovery date shown — source posting date unavailable."

### Per-source scrape schedule recommendation

Given 47+ sources and the current twice-daily cron (04:00, 16:00 UK time — currently broken), the schedule should shift to tiered polling:

- **Tier A sources**: Every 2–4 hours (6–12 scrapes/day) — these drive the 24h bucket
- **Tier B sources**: Every 6 hours (4 scrapes/day)
- **Tier C sources**: Every 12 hours (2 scrapes/day) — current frequency is adequate
- **Tier D sources**: Every 24 hours (1 scrape/day) — no benefit to more frequent polling

With 47 sources at a 120-second timeout per source via `asyncio.gather`, a full scrape cycle takes ~10–15 minutes if parallelised well. Running Tier A sources on a 2-hour cron and the remainder on 12-hour crons is feasible even on modest infrastructure.

---

## 3. Ghost detection requires scrape-health gates before absence signals

### The scale of the ghost problem

Research consistently shows **18–22% of job postings are ghost jobs** — listings that appear active but are no longer being recruited for. The Greenhouse 2024 State of Job Hunting Report found this rate across 2,500 workers in the US, UK, and Germany. Resume Builder's May 2024 survey found **39% of hiring managers admitted posting fake listings**. Revelio Labs found hires per job posting halved from 8-in-10 in 2019 to 4-in-10 in 2024. The problem is structural and worsening.

Job360 currently has **zero ghost detection**. The `INSERT OR IGNORE` pattern means once a job enters the database, it remains "active" for 30 days regardless of whether it still exists on the source. This contaminates every time bucket.

### HiringCafe's repost detection via embedding similarity

HiringCafe's core innovation for ghost filtering is not detection by absence — it's **repost detection via same-company embedding similarity**. The approach works as follows: when a new job is indexed, compute the text embedding of its title and description, then search for high-similarity matches among existing jobs **from the same company**. If two listings from the same company exceed a cosine similarity threshold, they are treated as the same underlying vacancy. The posting date is **pinned to the earliest occurrence**, preventing the repost from appearing in fresh buckets.

This is effective because ghost jobs frequently manifest as reposts — employers repost the same role monthly to signal "active hiring" without genuine intent. By backdating reposts to their true first appearance, freshness filters naturally exclude them.

Academic research supports this approach. Engelbach et al. (2024) achieved an **F1 score of 0.94** for job posting duplicate detection using a hybrid of string similarity, text embeddings, and weighted skill-keyword matching. Textkernel's production system found that true duplicates can have text similarity as low as 37% due to different boilerplate, meaning a simple threshold alone is insufficient for general deduplication — but within the same company, a threshold of **cosine similarity ≥ 0.85** on title + description is a strong signal.

### Recommended ghost detection algorithm for Job360

**Step 1: Scrape completeness gate (prevent false positives from scraper failures)**

Before interpreting any job's absence from a scrape as a ghost signal, validate that the scrape itself succeeded:

- Compare the result count against a **7-day rolling average** for that source. If the current scrape returned fewer than **70% of the rolling average**, mark the entire scrape as `unhealthy` and skip absence processing for all jobs from that source.
- Track HTTP status code distribution per source. A spike in 403/429/5xx responses indicates rate limiting or blocking, not job disappearance.
- Maintain 3–5 **canary jobs** per major source — known long-running listings (e.g., evergreen NHS roles, perpetual Amazon warehouse postings) that should always appear. If canaries disappear, the scrape is suspect.

This follows the pattern from Levergreen's `compare_workflow_success.py`, which validates expected vs. actual career page scrape counts before inferring job status changes.

**Step 2: Presence/absence tracking**

On every healthy scrape cycle:
- For each job found: set `last_seen_at = NOW()`, reset `consecutive_misses = 0`
- For each previously-active job NOT found: increment `consecutive_misses` by 1

**Step 3: State transitions with confidence tiers**

| Consecutive Misses | Time Window | State | Action |
|---|---|---|---|
| 0 | — | `active` | Show in appropriate time bucket |
| 1 | <12h | `active` | No change (single miss is noise) |
| 2 | ≥12h | `possibly_stale` | Trigger direct URL verification; show with amber indicator |
| 3+ | ≥24h | `likely_stale` | Exclude from 24h and 24–48h buckets; show with warning in older buckets |
| N/A | 404/410 on direct URL | `confirmed_expired` | Remove from all active buckets; retain in archive 30 days |
| N/A | "Position filled" text on URL | `confirmed_expired` | Same as above |

**Step 4: Direct URL verification (for `possibly_stale` jobs)**

When a job hits 2 consecutive misses, perform a direct HTTP HEAD/GET on the original job URL:
- **404 or 410** → `confirmed_expired` immediately
- **301/302 redirect to company careers homepage** → `likely_stale`
- **200 with job content present** → restore to `active` (was a pagination/scrape issue)
- **200 with "this position has been filled" or "no longer available"** → `confirmed_expired`
- **403/429/5xx** → inconclusive; keep in quarantine, retry next cycle

**Step 5: Repost detection (on each new job insert)**

For each newly discovered job:
1. Compute a text embedding of `title + description` using **all-MiniLM-L6-v2** (384-dimension, free, runs locally, ~10ms per embedding on CPU)
2. Search for matches within the same company from the past 90 days where cosine similarity ≥ **0.85**
3. If match found: flag as repost, set `posted_at` to the **original listing's earliest date**, set `date_confidence` to `'repost_backdated'`
4. The repost enters the database as a new record but inherits the original's temporal position for bucket assignment

This is achievable at zero cost. The all-MiniLM-L6-v2 model is 80MB, runs on CPU, and can process Job360's entire database in minutes. No GPT-4o-mini or paid embedding API needed.

**Step 6: High-turnover role exceptions**

Known high-volume employers (Amazon, NHS, Tesco, large retailers) and perpetually-open role categories (warehouse operative, delivery driver, care assistant) should have reduced ghost scoring. These roles genuinely stay open for months. Apply a 50% reduction to age-based ghost signals for roles matching known evergreen patterns.

### Minimum infrastructure requirements for ghost detection

- **Scrape frequency**: Tier A sources must be scraped ≥ 3x/day for the state machine to have enough data points. At 2x/day (current broken cron), a single missed scrape means 12+ hours without signal.
- **URL verification**: Budget ~100–500 direct URL checks per day for quarantined jobs. At 1 request/second this takes 2–8 minutes.
- **Embedding computation**: One-time index build for existing jobs (~5 minutes for 10,000 jobs on CPU), then incremental per new job.
- **Storage**: Add ~50 bytes per job for the new columns, plus a `scrape_events` table for audit trail (~100 bytes per job-scrape pair).

---

## 4. Ten KPIs make the freshness promise measurable and accountable

### The observability gap

Job360 currently has **no way to verify its own product promise**. There is no metric for bucket accuracy, no measurement of notification latency, no tracking of how many jobs have real dates versus fabricated ones. Without observability, the time-bucket feature is a UI label, not a guarantee.

### Core KPI definitions

**KPI 1: `bucket_accuracy_24h`** — The percentage of jobs displayed in the "last 24h" bucket whose effective posting date (per the precedence rules in Section 1) actually falls within the last 24 hours. Formula: `count(24h_bucket jobs WHERE effective_posted_at >= now() - 24h) / count(24h_bucket jobs)`. **Target: ≥ 90%.** Below 85% triggers a critical alert. This is the single most important metric for the product.

**KPI 2: `bucket_accuracy_48h`** — Same formula for the 24–48h bucket. **Target: ≥ 85%.**

**KPI 3: `bucket_accuracy_7d`** and **KPI 4: `bucket_accuracy_21d`** — Same pattern. **Targets: ≥ 80%** for both.

**KPI 5: `date_reliability_ratio`** — The percentage of active jobs with `date_confidence` of `high` or `medium` (real source dates) versus `low` or `fabricated` (fallback dates). Formula: `count(jobs WHERE date_confidence IN ('high','medium')) / count(active_jobs)`. **Target: ≥ 70%.** This is the meta-metric — if most dates are fabricated, all bucket accuracy numbers are meaningless. Currently, with 14/47 sources fabricating dates plus 3 using wrong fields, this ratio is likely around **60–65%**, already below target.

**KPI 6: `notification_latency_p50` and `notification_latency_p95`** — End-to-end time from `posted_at` (or `first_seen_at` fallback) to notification delivery. **Targets: p50 ≤ 4 hours, p95 ≤ 12 hours** for 24h-bucket jobs. Measured only for jobs with `date_confidence` of `high` to avoid polluting the metric with fallback dates.

**KPI 7: `stale_listing_rate`** — The percentage of active jobs in the database that are stale (older than 21 days or in `confirmed_expired` state). **Target: ≤ 5%.** Currently, with no ghost detection and a 30-day purge, this is likely **15–25%**.

**KPI 8: `crawl_freshness_lag`** — Per source, the time since the last successful scrape. Alert if any Tier A source exceeds **2× its configured interval** (e.g., 8 hours for a 4-hour source). Alert if >50% of all sources are overdue.

**KPI 9: `pipeline_end_to_end_latency`** — Time from job ingestion to availability in the dashboard and notification queue. **Targets: p50 ≤ 15 minutes, p95 ≤ 60 minutes.** This isolates internal processing speed from source crawl lag.

**KPI 10: `notification_delivery_success_rate`** — Per channel (Slack, Gmail, Telegram), the fraction of notifications that succeed. **Target: ≥ 99%.** Freshness is irrelevant if notifications fail silently.

### Alert rules and escalation

| Condition | Severity | Action |
|-----------|----------|--------|
| `bucket_accuracy_24h` < 90% for 30 min | ⚠️ Warning | Slack alert, investigate date extraction |
| `bucket_accuracy_24h` < 80% for 30 min | 🔴 Critical | Pause 24h notifications, investigate immediately |
| `date_reliability_ratio` < 60% for 1h | 🔴 Critical | Bucket integrity compromised; review source parsers |
| Any Tier A source not crawled in 2× interval | ⚠️ Warning | Check crawler health for that source |
| >50% of sources overdue | 🔴 Critical | Crawler infrastructure failure |
| `notification_latency_p95` > 14h for 1h | ⚠️ Warning | Check notification queue depth |
| `delivery_success_rate` < 95% on any channel | 🔴 Critical | Channel integration broken |

### Implementation with zero cost

Deploy **Prometheus** (metric collection) + **Grafana OSS** (dashboards and alerting) via a single `docker-compose.yml`. Write a **~200-line Python exporter** using `prometheus_client` that runs SQL queries against the PostgreSQL database every 5 minutes and exposes metrics. Connect Grafana alerts to a Slack webhook. Total additional infrastructure: **~512MB RAM, $0/month**.

The Grafana dashboard should have four rows: (1) bucket accuracy gauges at the top — visible without scrolling, (2) notification latency heatmap and per-channel delivery rates, (3) per-source crawl health table with red/green status, (4) volume trends showing jobs ingested per hour by bucket.

---

## 5. The honest verdict: achievable with hard trade-offs, not achievable as currently promised

### What is structurally broken

**The "last 24 hours" bucket cannot be honest across all 47 sources under current constraints.** Here is the arithmetic:

- **6 sources** (Reed, Adzuna, Ashby, Teaching Vacancies, NHS Jobs, GOV.UK Apprenticeships) provide real posting dates and refresh fast enough for a 24h claim. These are the only sources that belong in the 24h bucket with `date_confidence = 'high'`.
- **~5–8 sources** (UK HTML boards with schema.org `datePosted`, FindWork) provide real dates with 1–6h lag. These can enter the 24h bucket with `date_confidence = 'medium'`.
- **~15–20 sources** (Greenhouse, Lever, Careerjet, Jooble, Arbeitnow, Himalayas, Jobicy, etc.) have date ambiguity — they provide `updated_at` fields, aggregation timestamps, or intentionally delayed data. Their jobs should enter the 24–48h or 48h–7d buckets at best.
- **14 sources** fabricate dates. Their jobs cannot enter the 24h bucket at all without lying.
- **~5 sources** (Remotive, RemoteOK, Workday, Civil Service Jobs) have >24h lag by design.

**This means roughly 25–35% of Job360's job volume can honestly be placed in the "last 24 hours" bucket.** The remaining 65–75% must be placed in slower buckets or shown with a confidence disclaimer.

### HiringCafe's approach is not replicable at zero cost

HiringCafe's architecture involves:

- **Oxylabs rotating proxies** — typically $300–1,000+/month at scale, necessary for scraping 30,000+ company career pages 3x/day without IP blocks
- **GPT-4o-mini** for structured extraction from every job description — estimated $50–200/month at 2.1M jobs
- **Elasticsearch** for search and filtering — hosting costs for 2.1M documents are substantial
- **Apollo.io** for company discovery — subscription-based

Job360's zero-cost constraint means none of these are available. However, several components **can** be approximated:

- **Repost detection** via all-MiniLM-L6-v2 embeddings is free and runs on CPU. This is HiringCafe's most valuable innovation and is fully replicable.
- **Date extraction from HTML** via schema.org JSON-LD parsing replaces GPT-4o-mini for the specific task of date recovery. It won't extract salary, skills, or other fields as robustly, but for dates it's sufficient.
- **PostgreSQL full-text search** replaces Elasticsearch for Job360's scale (~10,000–50,000 active jobs vs. HiringCafe's 2.1M).
- **Free-tier API access** (Reed, Adzuna) replaces Oxylabs for API-based sources, though HTML scraping at scale without proxies will hit rate limits.

### The unavoidable compromises

**Compromise 1: The 24h bucket must be source-qualified.** Show a confidence indicator next to each job in the 24h bucket. Jobs from Tier A sources get a green "verified date" badge. Jobs from Tier B sources get an amber "approximate date" indicator. Jobs from Tier C/D sources should not enter the 24h bucket at all — they go to 48h–7d with a note: "Discovery date shown."

**Compromise 2: The bucket definition should be relaxed for low-confidence sources.** Instead of "last 24 hours," the honest framing for sources without real dates is "discovered in last 24 hours." The UI can show both: "Posted within 24h (verified)" and "Discovered within 24h (unverified)" as separate sub-sections or toggles. This preserves the value proposition while being honest.

**Compromise 3: Some sources must be deprioritised or dropped.** The 14 date-fabricating sources should be audited one by one. For each, determine whether schema.org JSON-LD extraction can recover real dates. Sources where no date is recoverable should be moved to a "browse by discovery date" section, clearly separated from the time-bucketed results. If a source provides so few jobs that fixing its date handling isn't worth the effort, consider dropping it.

**Compromise 4: Scrape frequency must increase for Tier A sources.** The current twice-daily cron (broken) is insufficient for a 24h bucket. Tier A sources need 4–6h polling at minimum, which means fixing the cron and implementing tiered scheduling. This is achievable on current infrastructure.

**Compromise 5: Ghost detection requires embedding infrastructure.** The all-MiniLM-L6-v2 model is free but requires ~80MB of model storage and CPU time for inference. For Job360's scale this is trivial, but it is a new dependency that must be integrated into the scrape pipeline.

### The minimum viable freshness architecture

The smallest set of changes that makes the time-bucket promise defensible:

1. **Database migration** (1–2 days): Add the five new columns from Section 1. Migrate all existing `date_found` values to `first_seen_at`. Set `posted_at = NULL` and `date_confidence = 'low'` for the 14 fabricating sources. Set `date_confidence = 'high'` for sources with known good date fields.

2. **Fix the cron** (hours): The directory restructure broke scheduled execution. Fix this immediately and implement tiered scheduling — Tier A sources every 4 hours, others every 12 hours.

3. **Date extraction pass** (3–5 days): For each of the 47 sources, audit the date extraction code. Replace `datetime.now()` with proper parsing where possible. For HTML sources, add schema.org JSON-LD extraction. For API sources, map the correct date field (not `updated_at`, not `closingDate`). This single change could move 5–10 sources from `date_confidence = 'low'` to `'medium'` or `'high'`.

4. **Scrape completeness gates** (2–3 days): Implement rolling-average result count checks and canary job monitoring. Without this, any absence-based ghost detection will produce false positives.

5. **Basic ghost detection** (3–5 days): Implement `consecutive_misses` tracking and direct URL verification. No embedding similarity needed in v1 — just track whether jobs are still present on sources and verify via direct URL when they disappear.

6. **Observability foundation** (2–3 days): Deploy Prometheus + Grafana, implement the Python metrics exporter, create the bucket accuracy dashboard. Until you can measure `bucket_accuracy_24h`, you cannot prove the product promise.

7. **Repost detection** (5–7 days, Phase 2): Integrate all-MiniLM-L6-v2 embeddings for same-company similarity matching. This is the highest-impact ghost-filtering mechanism but requires more engineering effort.

**Total estimated timeline for minimum viable freshness: 2–3 weeks of focused engineering.**

### The bottom line

Job360's time-bucket promise is **not a lie, but it is currently unsubstantiated** — and for roughly a third of sources, it is actively misleading. The good news is that the architecture to make it honest is well-understood, implementable at zero marginal cost (free tools, free models, existing infrastructure), and achievable in weeks rather than months. The hard truth is that "last 24 hours with verified posting dates" will initially cover only **25–35% of job volume** (from Tier A and B sources). The remaining volume must be placed in slower buckets or shown with explicit confidence disclaimers. 

The product can still be excellent — in fact, being transparent about date confidence would differentiate Job360 from every competitor that silently presents `updated_at` as "posted today." But it requires abandoning the fiction that all 47 sources deliver equal freshness, and building the UI to communicate that honestly.