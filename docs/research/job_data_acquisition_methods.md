# Job-Data Acquisition Methods — Full Taxonomy (Job360 vs Competitors)

> Every documented method for getting job data off the web, organised by family, with
> Job360's current usage vs. competitors'. The Job360 column is grounded against the
> actual `backend/src/main.py::SOURCE_REGISTRY` (50 entries) and
> `backend/src/services/scheduler.py` (`TIER_INTERVALS_SECONDS`), not assumptions.
> Competitor cells are deduced from vendor marketing / public posts (see `References.md` §1).
>
> **Created:** 2026-06-04 · **Related:** [`References.md` §1.17.2](../References.md) ·
> [`competitor_sourcing_matrix.md`](./competitor_sourcing_matrix.md)

---

## The two-step structure most people miss

Job-data acquisition is **discovery → extraction**:

1. **Discovery** — *which* companies/URLs even exist to look at?
2. **Extraction** — *pull* the jobs from each one.

Job360 has **fully solved extraction** but **barely touched discovery** (the company list is a
hand-maintained `core/companies.py`). Competitors' entire scale advantage is **automated
discovery (Family C)** — that's the real difference, more than the extraction tech.

Methods also sort on a **cost/cleanliness gradient**: structured APIs (cheap, clean, limited
coverage) → scraping (broad, fragile, legally grey) → buying data (instant, expensive). Job360
lives almost entirely in the clean-API zone; the crawlers live in the scraping zone.

---

## Family A — Structured API pulls (clean, no scraping)

| # | Method | How it works | Freshness | Job360? | Competitors? |
|---|--------|--------------|-----------|---------|--------------|
| A1 | Keyed job-board APIs | Authenticated REST (Reed, Adzuna, JSearch, Jooble, Careerjet, Findwork, Google Jobs/SerpApi) | mins–hours | ✅ 7 sources | ❌ (they skip boards) |
| A2 | Free/public board JSON APIs | No-key REST (RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas, DevITjobs…) | hours | ✅ 9 sources | ~ FirstPost partial |
| A3 | ATS public JSON APIs | No-auth endpoints (Greenhouse boards, Lever postings, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Comeet) | near-real-time | ✅ per ~268 slugs | ✅ at 10K–150K scale |
| A4 | RSS / XML / Atom feeds | Parse standardised feeds (jobs.ac.uk, NHS XML, WeWorkRemotely, GOV.UK) | 15 min–hours | ✅ 10 feeds | ❌ |
| A5 | Unified ATS API providers | One API → 60+ ATS (Merge.dev, Unified.to) — paid middleware | near-real-time | ❌ | ~ (build option) |

## Family B — Scraping / extraction (when there's no clean API)

| # | Method | How it works | Fragility | Job360? | Competitors? |
|---|--------|--------------|-----------|---------|--------------|
| B1 | Static HTML scraping | Fetch + regex/CSS parse (BeautifulSoup/Cheerio) | medium | ✅ 7 scrapers (LinkedIn, JobTensor, Climatebase, BCS…) | ✅ core |
| B2 | Headless-browser scraping | Puppeteer/Playwright renders JS SPAs | high | ❌ | ✅ HiringCafe (Puppeteer) |
| B3 | Schema.org JobPosting JSON-LD harvest | Extract embedded structured data from any career page's HTML | low–medium | ❌ | ✅ HiringCafe/FirstPost |
| B4 | Custom proprietary-system adapters | Bespoke parsers for Amazon/Apple/Google/Microsoft career systems | high (per-target) | ❌ | ✅ Scoutify |
| B5 | Meta-scraping the aggregators | Scrape Indeed/Glassdoor/LinkedIn (which themselves aggregate) | high | ✅ JobSpy (Indeed+Glassdoor) + LinkedIn | ❌ (avoid by design) |
| B6 | 3rd-party scraping platforms | Apify actors, ScraperAPI, Bright Data + rotating proxies (Oxylabs) | outsourced | ❌ (self-built) | ✅ HiringCafe (Oxylabs) |

## Family C — Company / URL discovery (the precursor step — Job360's biggest gap)

| # | Method | How it works | Job360? | Competitors? |
|---|--------|--------------|---------|--------------|
| C1 | Manual curated list | Hand-maintained company/slug catalog (`companies.py`) | ✅ ~268 slugs | — |
| C2 | Contact/company DBs | Apollo.io free tier → company URLs | ❌ | ✅ HiringCafe |
| C3 | Common Crawl mining | Filter the open web-crawl corpus for career pages | ❌ | ✅ HiringCafe |
| C4 | Google dorking | `site:lever.co`, `inurl:greenhouse.io` style queries to find ATS-hosted boards | ❌ | ✅ HiringCafe |
| C5 | ATS-tech detection | BuiltWith/Wappalyzer → "which companies use Greenhouse?" → auto-add | ❌ | ✅ (implied) |
| C6 | Sitemap parsing | Read `/sitemap.xml` to enumerate a site's job URLs | ❌ | ~ |

## Family D — Push / submitted / partnered (data comes *to* you)

| # | Method | How it works | Job360? | Competitors? |
|---|--------|--------------|---------|--------------|
| D1 | Employer-direct posting | Employers submit jobs to your platform (often paid) | ❌ | ✅ WTTJ, Jack & Jill |
| D2 | Email ingestion | Parse job-alert emails via inbox OAuth | ❌ | ✅ CareerSync (OSS) |
| D3 | Official data licensing / feed deals | Contract XML feeds from boards | ❌ | ~ (rare; boards resist — `pillar_3_batch_4.md`) |
| D4 | ATS webhooks | True real-time push on publish | ❌ (employer-only) | ❌ (nobody — not available to aggregators) |
| D5 | Community sources | HN "Who is hiring", Reddit, Slack/Discord | ✅ HN Jobs | ~ |

## Family E — Buy it

| # | Method | How it works | Job360? | Competitors? |
|---|--------|--------------|---------|--------------|
| E1 | Job-data resellers / datasets | TheirStack, Crustdata, JobsPikr, Bright Data datasets | ❌ | ❌ (TheirStack dismissed, `References.md` §1.5) |

---

## Summary

**Job360 currently uses ~8 of ~20 methods:** A1, A2, A3, A4 (structured APIs + feeds — the
strength), B1, B5 (static + meta-scraping), C1 (manual discovery), D5 (community). It is a
**structured-aggregation shop with a thin scraping layer.**

**What competitors do that Job360 doesn't:**
- **The entire discovery family (C2–C5)** — automated company-URL discovery. *The #1 gap* and
  the root of their scale.
- **B2 headless-browser + B3 JSON-LD harvest + B4 custom adapters** — extraction tech that lets
  them crawl *any* career page, not just ones with clean APIs.
- **D1 employer-direct** — a different (paid) business model.

**What Job360 does that they don't (method moat):**
- **A1 keyed UK boards + A4 UK public-sector RSS** — competitors have no UK board/feed coverage.
- **B5 meta-scraping Indeed/Glassdoor** — they deliberately avoid this to stay "upstream."

## Recommended moves

1. **Highest-leverage upgrade — C5 → A3.** Add ATS-tech detection (C5) feeding the ATS APIs
   (A3) Job360 already parses. You don't build new extraction; you grow `companies.py` from
   ~268 → thousands of slugs that flow into existing parsers. Converts the #1 gap into reuse of
   shipped code.
2. **Add B3 (JSON-LD harvest) for the long tail** — broad, low-maintenance coverage of career
   pages not on a supported ATS, accepting slightly lower freshness.
3. **Deliberately skip B2 + B4** — highest-maintenance methods (every site redesign breaks a
   custom adapter). Let competitors carry that burden while Job360 stays on clean APIs.
4. **Never drop A1 + A4** — the UK keyed boards + public-sector RSS are the one source class no
   ATS-crawler competitor can replicate (NHS/gov/university jobs don't live on Greenhouse).
