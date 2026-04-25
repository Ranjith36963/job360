# Job360 — References

> External repos, APIs, tools, patterns, and competitive intelligence for building and improving Job360.
> Every entry verified from research conducted 2026-04-11; competitive landscape extended 2026-04-25 (Dex, Sprout).
> All Job360 file paths cross-referenced against `CurrentStatus.md` to ensure accuracy.

---

## Table of Contents

1. [Competitive Landscape](#1-competitive-landscape)
2. [Free ATS APIs (The Foundation)](#2-free-ats-apis-the-foundation)
3. [Open Source Repos — Tier-Ranked](#3-open-source-repos--tier-ranked)
4. [What to Copy From Where (Mapped to Job360 Files)](#4-what-to-copy-from-where-mapped-to-job360-files)
5. [Company Discovery Sources (Free)](#5-company-discovery-sources-free)
6. [Free Job Aggregator APIs](#6-free-job-aggregator-apis)
7. [Paid Data Providers (Dismissed)](#7-paid-data-providers-dismissed)
8. [LLM Extraction Reference](#8-llm-extraction-reference)
9. [Infrastructure Patterns](#9-infrastructure-patterns)
10. [Key Technical Insights](#10-key-technical-insights)

---

## 1. Competitive Landscape

### 1.1 HiringCafe — https://hiring.cafe

**What it is:** Job search engine scraping 30K+ company career pages directly. 4.1M jobs, 1.3M monthly users, $0 marketing spend. Founded by Ali Mir (Stanford PhD, ex-Meta/DoorDash) and Hamed Nilforoshan (Stanford CS PhD).

**Architecture (confirmed from founder Reddit posts + GitHub gists):**
- **Company discovery:** Apollo.io free tier + Google dorking + Common Crawl → 30K company URLs
- **Crawling:** Node.js + Cheerio (static HTML) + Puppeteer (JS-rendered SPAs), 3x/day per company
- **LLM parsing:** GPT-4o-mini with strict JSON schema output, temperature 0 → 17+ structured fields
- **Search:** Elasticsearch for full-text + faceted filtering
- **Storage:** Firestore (Google NoSQL)
- **Frontend:** Next.js + React + TailwindCSS + Chakra UI
- **Proxy:** Oxylabs rotating proxy for rate-limited sites
- **Ghost detection:** Embedding similarity on same-company jobs, keep earliest posting date
- **Revenue:** Promoted listings + Talent Network (candidates opt-in). Free for job seekers.

**What they do that Job360 doesn't (yet):**

| Gap | HiringCafe | Job360 current state (from CurrentStatus.md) |
|---|---|---|
| Company coverage | 30K companies | 104 slugs in backend/src/core/companies.py (§11) |
| Scrape cadence | 3x/day (every 8 hrs) | 2x/day at 04:00+16:00 UK via cron_setup.sh — but script is stale (§13 Known Issue #6) |
| LLM enrichment | GPT-4o-mini for salary, visa, skills | No LLM enrichment. CV parsing uses Gemini/Groq/Cerebras (backend/src/services/profile/llm_provider.py:14-49) but only for profile, never for job descriptions |
| Ghost detection | Embedding similarity | No ghost detection. No ChromaDB/sentence-transformers in pyproject.toml (§14) |
| Boolean/AI search | Full-text + natural language | No search — push-based only |

**What Job360 does that HiringCafe doesn't:**

| Advantage | Job360 (from CurrentStatus.md) | HiringCafe |
|---|---|---|
| Personalised scoring | JobScorer with 4 dimensions: Title(40) + Skill(40) + Location(10) + Recency(10) at skill_matcher.py:17-27 | No scoring |
| CV-driven matching | Profile pipeline: CV PDF/DOCX → LLM → CVData → SearchConfig → JobScorer | No profiles |
| Push notifications | Email/Slack/Discord via backend/src/services/notifications/ | Pull-based search only |
| UK/remote filtering | _is_uk_or_remote() at backend/src/sources/base.py:31-45, shared with scorer | Global, no geographic filter at source level |
| Multi-source fan-out | 47 concurrent async sources via asyncio.gather at main.py:292-302 with 120s timeout | Single crawler architecture |
| Failure isolation | Each source wrapped in try/except + asyncio.wait_for; one bad source can't crash the run | Single-point failure |

**Key sources:**
- Architecture writeup: https://gist.github.com/thoroc/21601e286d9d4fec8505a88d71145ad9
- GPT prompt schema: https://gist.github.com/hamedn/b8bfc56afa91a3f397d8725e74596cf2
- Scaling blog post: https://blog.hiring.cafe/p/scaling-hiringcafe-from-0-to-1m-users
- Hacker News discussion: https://news.ycombinator.com/item?id=42806955

---

### 1.2 Jack & Jill — https://www.jackandjill.ai

**What it is:** Conversational AI career agent. $20M seed funding (Creandum, Oct 2025). 162K users. London-founded.

**Architecture (deduced — no public technical disclosure):**
- **Jack:** AI career agent. 10-min conversational onboarding, 14M jobs daily, yes/no feedback loop, mock interviews, salary benchmarking. Free.
- **Jill:** AI recruiter for employers. Direct intros to hiring managers.
- **Revenue:** 10% commission on successful hires.
- **Data sources:** Same free ATS APIs — no other way to reach 14M jobs.

**Relevance:** Validates personalised push-based model. Match percentages (91-98%) = same concept as Job360's 0-100 scorer.

**Key sources:**
- TechCrunch: https://techcrunch.com/2025/10/16/jack-jill-raises-20-million-to-bring-conversational-ai-to-job-hunting/

---

### 1.3 Simplify — https://simplify.jobs

NOT a competitor. Solves application speed, not discovery. Relevant only for GitHub Actions cron pattern (44K-star repo runs 22,958 workflows). Job360 has no CI (CurrentStatus.md §15).

---

### 1.6 Dex — https://app.meetdex.ai

**What it is:** Boutique recruiter-mediated marketplace for engineers. Curated, gated, intro-driven. Tagline: "Fewer roles, fewer candidates. Only the best opportunities." LinkedIn-OAuth onboarding → preference setting → hand-curated intros to hiring managers. Confidentiality is the headline promise (current employer not notified).

**Architecture (deduced — public marketing surface only):**
- **Onboarding:** LinkedIn OAuth (verify profile → set preferences → review opportunities)
- **Inventory:** Curated by recruiters; not aggregated. Gated behind auth.
- **Match model:** Two-way (candidate-of-interest + role-of-interest); manual matchmaker bottleneck
- **Revenue:** Placement fees from hiring companies (recruiter model). Free for seekers.
- **Tech:** Public-marketing-page only; production app gated behind auth — not inspected.

**Audience overlap with Job360:** Low. Dex targets the top 1% of senior engineers willing to wait for hand-curated intros. Job360 targets the broad professional (any domain) who wants 50-source coverage. Different value chains.

**Threat level:** Low. Complementary distribution channel rather than competition. A serious seeker would use Dex for the 3 hand-picked intros + Job360 for the 200 they should also be tracking.

**Borrow from them:**
- LinkedIn OAuth onboarding flow — faster than Job360's CV+LinkedIn-PDF+GitHub triple-upload
- Confidentiality positioning copy ("your current employer won't be notified") for any seekers worried about employer detection on the dashboard

**Don't copy:**
- "Fewer / better" exclusivity positioning — Job360's wedge is the opposite (broad coverage + intelligent ranking)

**Key sources:**
- Marketing landing: https://app.meetdex.ai/handoff?cid=… (per-user UUID handoff token; public marketing surface)

---

### 1.7 Sprout — https://app.usesprout.com

**What it is:** AI job-application automation tool for individual seekers. Tagline: "**Swipe right, it's handled.**" Tinder-style discovery feed → AI auto-tailors CV/cover letter per role → one-click apply → recruiter-response tracking. Direct overlap with Job360's seeker-side value chain, but operates one layer downstream (the *apply* step rather than the *discover* step).

**Architecture (deduced from public surface):**
- **Frontend:** Next.js + React (same as Job360), hosted on Vercel (`dpl_…` deployment ID is a Vercel signature)
- **Inventory:** Gated behind login (`/jobs` redirects to auth) — implies curated or scraped-but-monetised
- **Match model:** Swipe-feed UX, profile → AI infers fit
- **AI usage:** Per-application generation (CV + cover letter tailored to JD)
- **Revenue:** Likely freemium (no public pricing); paywall implied by gated `/jobs`
- **Tracking:** Recruiter-response tracking, in-app

**Audience overlap with Job360:** **High.** Same target (individual seekers), same channel (web), similar tech stack.

**Threat level:** Medium-direct. Their wedge is auto-tailored CV + one-click apply — the highest-leverage feature Job360 doesn't yet have on the roadmap.

**What they do that Job360 doesn't:**

| Gap | Sprout | Job360 current state |
|---|---|---|
| Auto-tailored CV per JD | ✅ AI-generated | ❌ Not on roadmap |
| Auto-tailored cover letter | ✅ AI-generated | ❌ Not on roadmap |
| One-click apply | ✅ Core flow | ❌ Manual via apply_url |
| Swipe-feed UX | ✅ Discovery interface | Browse-and-rank UX |

**What Job360 does that Sprout doesn't:**

| Advantage | Job360 | Sprout |
|---|---|---|
| Multi-source aggregation | 50 UK sources | Single curated inventory |
| Push notifications (Email/Slack/Discord) | ✅ Shipped | ❌ Pull-only |
| 7-dim scoring transparency | ✅ Pillar 2.9 | ❌ Black-box AI |
| Local-first / data-sovereign option | ✅ Self-host capable | ❌ Cloud-only |
| Multi-domain (any profession) | ✅ Vision | ❌ Tech-only |

**Strategic implication for Job360:** Auto-apply is a clean v2 feature. Consider adding to Step 5 (launch readiness) or splitting into Step 6. The CV-tailoring already has scaffolding (`backend/src/services/profile/llm_provider.py`) — same provider chain (Gemini→Groq→Cerebras) could generate per-JD CVs/cover letters.

**Borrow from them:**
- Tagline format: 4 words, agent-doing-work-for-you promise. Job360's current "we aggregate 50 UK sources" is verbose by comparison.
- Swipe UX as an *optional* feed mode alongside the radar/list view (frontend layer)

**Key sources:**
- App: https://app.usesprout.com/jobs (gated)
- Public marketing: https://usesprout.com (assumed root)

---

### 1.8 Strategic positioning summary (post-2026-04-25 audit)

| Player | Owns wedge | Threat to Job360 |
|---|---|---|
| HiringCafe (1.1) | Clean US aggregator, no spam, free forever | **Highest direct overlap** but US-focused + no alerts |
| Jack & Jill (1.2) | Conversational AI agent, $20M funded | Adjacent — different UX paradigm |
| Sprout (1.7) | Auto-tailored CV + one-click apply | Different layer (apply step) — adjacent |
| Dex (1.6) | Boutique recruiter-curated | Different audience — top-1% engineers only |
| Indeed/LinkedIn | Scale + brand | Not the same product class |

**Job360's open wedges (none of the above hold):**
1. **UK-first + multi-domain** — neither HiringCafe (US) nor Sprout/Dex (tech-only) ship this combo
2. **Score transparency** — every competitor is black-box; Job360 shows 7-dim breakdown
3. **Push-mode notifications** — HiringCafe explicitly admits no alerts; Job360 has Email/Slack/Discord shipped
4. **Self-host / data-sovereign** — no cloud-locked competitor offers this; Job360's architecture supports it
5. **Open inventory** — HiringCafe blocks scrapers (403 confirmed 2026-04-25); a Job360 public API would invert that posture

**Suggested elevator pitch (replaces current README opener):** "*HiringCafe scores you for UK roles and tells you when to apply — works while you sleep, across 50 sources, every domain.*"

### 1.4 career-ops — https://github.com/santifer/career-ops

NOT a competitor. CLI evaluation tool, 8.2K stars. Complementary — evaluates individual jobs deeply.

### 1.5 TheirStack — https://theirstack.com

Dismissed. Resells free ATS data. Free tier useless (200 jobs/mo). Starter $59/mo.

---

## 2. Free ATS APIs (The Foundation)

Cross-referenced against Job360's 10 ATS sources in backend/src/sources/ats/ (CurrentStatus.md §5).

### 2.1 Greenhouse (25,000+ companies)
```
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
```
- Auth: None | Rate limit: None (cached)
- Returns: id, title, updated_at, location, absolute_url
- Date: updated_at ONLY — NOT posting date
- Job360: backend/src/sources/ats/greenhouse.py — 25 slugs
- Bug: Uses updated_at as date_found (CurrentStatus.md §5 row 19)

### 2.2 Lever (5,000+ companies)
```
GET https://api.lever.co/v0/postings/{company}
```
- Auth: None | Rate limit: Generous | EU: api.eu.lever.co
- Returns: createdAt (ms epoch), workplaceType, salaryRange, categories, applyUrl
- Date: createdAt — real posting date, ms epoch
- Docs: https://github.com/lever/postings-api
- Job360: backend/src/sources/ats/lever.py — 12 slugs

### 2.3 Ashby (2,800+ companies)
```
GET https://api.ashbyhq.com/posting-api/job-board/{clientname}?includeCompensation=true
```
- Auth: None | Richest free endpoint
- Returns: publishedAt (ISO 8601), compensation (salary + equity), descriptionHtml
- Date: publishedAt — real posting date
- Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
- Job360: backend/src/sources/ats/ashby.py — 9 slugs

### 2.4 Workable
```
GET https://apply.workable.com/api/v1/widget/accounts/{clientname}
```
- Auth: None | Rate: 10 req/10 sec
- Date: May expose published_on — unverified
- Job360: backend/src/sources/ats/workable.py — 8 slugs
- Bug: Always now() (CurrentStatus.md §5 row 26)

### 2.5 Workday (Fortune 500)
```
POST https://{tenant}.wd{datacenter}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
```
- Auth: None (undocumented CXS) | Needs Referer header
- Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
- Date: postedOn — relative text, needs parsing
- Job360: backend/src/sources/ats/workday.py — 14 dict-format slugs, CXS at line 52

### 2.6 Other ATS in Job360
| ATS | File | Slugs | Date field | Accurate? |
|---|---|---|---|---|
| SmartRecruiters | ats/smartrecruiters.py | 6 | releasedDate | ✅ |
| Pinpoint | ats/pinpoint.py | 8 | None | ❌ always now() |
| Recruitee | ats/recruitee.py | 8 | published_at | ✅ |
| Personio | ats/personio.py | 10 | None | ❌ always now() |
| SuccessFactors | ats/successfactors.py | 3 | None | ❌ always now() |

### 2.7 Not in Job360 (candidates)
| ATS | Relevance | Phase |
|---|---|---|
| BambooHR | Non-tech SMEs | 6 |
| iCIMS | Large enterprises | Deferred |
| Taleo | Legacy enterprises | Deferred |
| Rippling | Mid-market tech | Deferred |

---

## 3. Open Source Repos — Tier-Ranked

### Tier 1 — Study line by line

**JobSpy** — https://github.com/speedyapply/JobSpy
- 3.1K stars | Python | MIT
- Date parsing reference. Returns None when missing.
- Job360 uses it internally (SOURCE_REGISTRY main.py:97-98)
- Phases: 1.1, 1.8, 1.10
- Warning: naive datetime (no tz=utc)

**Levergreen** — https://github.com/adgramigna/job-board-scraper
- Scrapy-based | Greenhouse, Lever, Ashby, Rippling
- run_hash tagging + SQL diff for disappearance
- Job360 gap: no disappearance tracking, INSERT OR IGNORE at database.py:114-132
- Phases: 3.1, 3.3
- Warning: their created_at = time.time() is same bug

**Feashliaa/job-board-aggregator** — https://github.com/Feashliaa/job-board-aggregator
- 4,000+ company slugs by ATS platform
- Job360 has ~104 (CurrentStatus.md §11)
- Phase: 8

### Tier 2 — Steal specific patterns

**HiringCafe GPT prompt** — https://gist.github.com/hamedn/b8bfc56afa91a3f397d8725e74596cf2
- 95 stars | Production schema | 17+ fields | 16-value category enum
- Phase: Future LLM enrichment

**HiringCafe architecture** — https://gist.github.com/thoroc/21601e286d9d4fec8505a88d71145ad9
- Full technical writeup from Ali Mir
- Phase: 8 (discovery methodology)

**Lever Postings API** — https://github.com/lever/postings-api
- Official docs, createdAt ms epoch format
- Phase: 1.3

**YC Companies API** — https://github.com/yc-oss/api
- 5,500 startups at https://yc-oss.github.io/api/companies/all.json
- Phase: 8

**HiringCafe scraper** — https://github.com/umur957/hiring-cafe-job-scraper
- Internal API reverse-engineered | dateFetchedPastNDays filter
- Phase: 7

### Tier 3 — Reference architecture

**JobFunnel** — https://github.com/PaulMcInnis/JobFunnel (1.6K stars)
- max_listing_days, company_block_list, YAML config

**SimplifyJobs** — https://github.com/SimplifyJobs/Summer2026-Internships (44K stars)
- GitHub Actions cron pattern | 22,958 workflow runs
- Job360 gap: no CI exists (CurrentStatus.md §15)

**Ashby API docs** — https://developers.ashbyhq.com/docs/public-job-posting-api

---

## 4. What to Copy From Where (Mapped to Job360 Files)

All paths verified against CurrentStatus.md Appendix A.

### Timestamp Fix (Phase 1)

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| None instead of now() | JobSpy | All 46 sources in backend/src/sources/ | §5 master table |
| LinkedIn <time datetime> | JobSpy linkedin/ | backend/src/sources/scrapers/linkedin.py:60 | §5 row 42 |
| Indeed datePublished epoch | JobSpy indeed/ | backend/src/sources/other/indeed.py:70 | §5 row 44 |
| Glassdoor ageInDays→None | JobSpy glassdoor/ | backend/src/sources/other/indeed.py | §5 row 44 |
| parse_relative_date | JobSpy utils | NEW: backend/src/utils/date_parsing.py | — |
| Lever createdAt ms epoch | Lever API repo | backend/src/sources/ats/lever.py:43 | §5 row 20 |
| Ashby publishedAt ISO | Ashby docs | backend/src/sources/ats/ashby.py:36 | §5 row 18 |
| Recency reads wrong field | Internal fix | backend/src/services/skill_matcher.py:165-184 + :299 | §2 Pillar 2 |
| Legacy score_job() too | Internal fix | backend/src/services/skill_matcher.py:231-240 | §13 Issue #4 |

### Disappearance Tracking (Phase 3)

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| run_hash tagging | Levergreen | backend/src/main.py:223-431 | §3 Step 9 |
| SQL snapshot diff | Levergreen | backend/src/repositories/database.py (new method) | §6 |
| Upsert on conflict | Levergreen | backend/src/repositories/database.py:114-132 | §6 |
| last_seen column | Levergreen | backend/src/repositories/database.py (new column) | §4 schema |

### Bucketing Fix (Phase 2)

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| Bucketing wrong timestamp | Internal fix | backend/src/utils/time_buckets.py:51-63 | §9 |
| Dashboard wrong ORDER BY | Internal fix | backend/src/dashboard.py | §9 |
| CLI view wrong fallback | Internal fix | backend/src/cli_view.py:121-124 | §13 Issue #12 |
| API duplicate bucket logic | Internal fix | backend/src/api/routes/jobs.py | §7 |

### Company Discovery (Phase 8)

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| 4,000+ ATS slugs | Feashliaa repo | backend/src/core/companies.py | §11 (~104 slugs) |
| 5,500 startup domains | YC API | NEW: scripts/discover_companies.py | — |
| Google dork method | Ali writeup | NEW: scripts/discover_companies.py | — |

### Infrastructure

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| GitHub Actions cron | SimplifyJobs | NEW: .github/workflows/scrape.yml | §15 (no CI) |
| Fix stale cron | Internal fix | cron_setup.sh:9-14 | §13 Issue #6 |

### Semantic Fixes (Phase 4)

| Pattern | From | Job360 target | CurrentStatus ref |
|---|---|---|---|
| Greenhouse updated_at ≠ posted | Lever API (contrast) | backend/src/sources/ats/greenhouse.py:41 | §5 row 19 |
| NHS closingDate = deadline | Internal fix | backend/src/sources/feeds/nhs_jobs.py:68 | §5 row 31 |

---

## 5. Company Discovery Sources (Free)

Job360 has ~104 slugs (CurrentStatus.md §11: Greenhouse=25, Lever=12, Workable=8, Ashby=9, SmartRecruiters=6, Pinpoint=8, Recruitee=8, Workday=14, Personio=10, SuccessFactors=3). Target: 500+.

### 5.1 Common Crawl
```sql
SELECT url_host_name, count(*) as n
FROM "ccindex"."ccindex"
WHERE crawl = 'CC-MAIN-2024-XX'
AND url_host_name LIKE '%greenhouse.io%'
GROUP BY 1 ORDER BY n DESC
```

### 5.2 Google Dorking
```
site:boards.greenhouse.io "London"
site:jobs.lever.co "United Kingdom"
site:jobs.ashbyhq.com "London"
site:apply.workable.com "United Kingdom"
```

### 5.3 Apollo.io Free Tier — 65+ filters, free seeding

### 5.4 YC API — https://yc-oss.github.io/api/companies/all.json — 5,500 startups

### 5.5 BuiltWith / Wappalyzer — ATS detection from DOM

---

## 6. Free Job Aggregator APIs

Complete mapping against CurrentStatus.md §5 (47-source master table):

| Source | Key? | Date field | Job360 file | Date accurate? |
|---|---|---|---|---|
| Reed | Yes (free) | date | apis_keyed/reed.py:51 | ✅ |
| Adzuna | Yes (free) | created | apis_keyed/adzuna.py:50 | ✅ |
| JSearch | Yes (free) | job_posted_at_datetime_utc | apis_keyed/jsearch.py:74 | ✅ |
| Jooble | Yes (free) | updated (not posted) | apis_keyed/jooble.py:60 | ⚠️ |
| Google Jobs | Yes (free) | Relative text | apis_keyed/google_jobs.py:101 | ✅ Inferred |
| Careerjet | Yes (free) | date | apis_keyed/careerjet.py:76 | ✅ |
| Findwork | Yes (free) | date_posted | apis_keyed/findwork.py:56 | ✅ |
| Arbeitnow | No | created_at | apis_free/arbeitnow.py:23 | ✅ |
| RemoteOK | No | date | apis_free/remoteok.py:28 | ✅ |
| Jobicy | No | pubDate | apis_free/jobicy.py:33 | ✅ |
| Himalayas | No | pubDate/createdAt | apis_free/himalayas.py:28 | ✅ |
| Remotive | No | publication_date | apis_free/remotive.py:39 | ✅ |
| DevITjobs | No | publishedAt | apis_free/devitjobs.py:45 | ✅ |
| LandingJobs | No | published_at | apis_free/landingjobs.py:65 | ✅ |
| AIJobs | No | date | apis_free/aijobs.py:34 | ✅ |
| HN Jobs | No | Unix epoch | apis_free/hn_jobs.py:66 | ✅ |
| YC Companies | No | N/A | apis_free/yc_companies.py:43 | ❌ REMOVE |
| FindAJob | No | Unknown | feeds/findajob.py:75 | ❌ always now() |
| NHS Jobs | No | closingDate (BUG) | feeds/nhs_jobs.py:68 | ❌ Future deadline |
| jobs.ac.uk | No | pubDate | feeds/jobs_ac_uk.py:65 | ✅ |
| WeWorkRemotely | No | pubDate | feeds/weworkremotely.py:59 | ✅ |
| LinkedIn | No | None | scrapers/linkedin.py:60 | ❌ always now() |
| Indeed+Glassdoor | Optional | date_posted | other/indeed.py:70 | ✅ |
| Nomis | No | N/A | other/nomis.py:52 | ❌ REMOVE |

Summary: 33/47 real dates. 14 always now(). 2 semantically wrong. Full table: CurrentStatus.md §5.

---

## 7. Paid Data Providers (Dismissed)

| Provider | Why dismissed | Free alternative |
|---|---|---|
| TheirStack | Resells free ATS data. $59/mo+ | Direct ATS APIs |
| Coresignal | LinkedIn. $500+/mo | JobSpy (free) |
| Bright Data | Enterprise. $250 min | Cheerio + aiohttp |
| Proxycurl | LinkedIn. Paid only | JobSpy |
| Techmap | Bulk datasets $2,400/country | ATS APIs + RSS |
| Jobo | Paid API | Already doing same free |

---

## 8. LLM Extraction Reference

### 8.1 HiringCafe Production Prompt
Source: https://gist.github.com/hamedn/b8bfc56afa91a3f397d8725e74596cf2

Settings: model=gpt-4o-mini, temperature=0, response_format=json_schema, strict=true

17+ fields including: title (canonical), category (16 enums), salary (min/max/currency/freq), skills (normalised array), visa_sponsorship, security_clearance.

Category enum: Software Development, Engineering, IT, Product Management, Project/Program Management, Design, Data & Analytics, Sales, Marketing, Customer Service, Business Operations, Finance & Accounting, HR, Legal & Compliance, Healthcare, Other

### 8.2 Job360 Current LLM State
No job description enrichment. llm_provider.py (Gemini→Groq→Cerebras) used only for CV parsing. No Ollama/Mistral (CurrentStatus.md §14).

### 8.3 Free LLM APIs (already configured in Job360)
| Provider | Model | File | Used for |
|---|---|---|---|
| Gemini | gemini-2.0-flash | llm_provider.py:89-103 | CV parsing (primary) |
| Groq | llama-3.3-70b-versatile | llm_provider.py:106-122 | CV parsing (fallback 1) |
| Cerebras | llama3.1-8b | llm_provider.py:125-144 | CV parsing (fallback 2) |

Could be repurposed for job enrichment at zero cost.

---

## 9. Infrastructure Patterns

### 9.1 GitHub Actions Free Cron
Job360 has no CI (CurrentStatus.md §15).

```yaml
name: Job360 Scrape
on:
  schedule:
    - cron: '0 */4 * * *'
  workflow_dispatch:
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: cd backend && pip install -e ".[indeed]"
      - uses: actions/download-artifact@v4
        with: { name: jobs-db, path: backend/data/ }
        continue-on-error: true
      - run: cd backend && python -m src.cli run
      - uses: actions/upload-artifact@v4
        with: { name: jobs-db, path: backend/data/jobs.db }
```
Budget: 2,000 min/mo free. 10min × 6/day × 30 = 1,800. Fits.

### 9.2 cron_setup.sh is stale
Paths broken after phase-1 directory move (CurrentStatus.md §13 Issue #6). Fix: cd backend && python -m src.cli run.

### 9.3 Freshness Targets
| Mode | Delay | Cost | Verdict |
|---|---|---|---|
| 4-hour cron | 4 hr | $0 | Target |
| 8-hour (HiringCafe) | 8 hr | $0 | Fallback |
| 12-hour (current) | 12 hr | $0 | Too stale |

---

## 10. Key Technical Insights

### 10.1 Industry Standard Architecture
```
Free ATS APIs (80%) → Optional LLM enrichment → DB → Frontend/Notifications
```
Job360 has left + right. Middle (LLM enrichment) is the gap.

### 10.2 Date Priority Chain (Industry Standard)
1. ATS API timestamp (Lever createdAt, Ashby publishedAt) — ground truth
2. LLM-extracted date from JD text — HiringCafe does this
3. First-seen timestamp — honest upper bound
4. Never datetime.now() — Job360's current bug in 14/47 sources

### 10.3 Job360's Unique Scoring Advantage
From CurrentStatus.md §2:
```
TITLE_WEIGHT=40  SKILL_WEIGHT=40  LOCATION_WEIGHT=10  RECENCY_WEIGHT=10
```
Plus penalties: negative keywords (−30), foreign location (−15). Clamped [0,100]. MIN_MATCH_SCORE=30.
No competitor has this granularity. HiringCafe: no scoring. Jack & Jill: proprietary.

### 10.4 Ghost Detection Hierarchy
1. Disappearance tracking (free, SQLite, Levergreen pattern) → Phase 3
2. Age-based flagging (45+ days live) → Phase 5A
3. Embedding similarity (requires chromadb, not installed) → Phase 5B deferred

### 10.5 Revenue Models
| Model | Used by |
|---|---|
| Placement fees (10%) | Jack & Jill |
| Promoted listings | HiringCafe |
| Talent Network | HiringCafe |
| Subscription ($39.99/mo) | Simplify |
| Free for candidates | HiringCafe, Jack & Jill, Job360 |

---

## Document History
- **2026-04-25 v3:** Extended §1 Competitive Landscape with Dex (1.6 — boutique recruiter-curated) and Sprout (1.7 — AI auto-apply). Added §1.8 strategic positioning summary mapping each competitor's wedge against Job360's open wedges (UK-first + multi-domain, score transparency, push-mode notifications, self-host, open inventory). Confirmed HiringCafe blocks server-side scrapers (403 against WebFetch).
- **2026-04-11 v2:** Cross-referenced against CurrentStatus.md. Corrected paths (repositories/database.py, services/skill_matcher.py, class JobDatabase). Added line numbers, slug counts, CurrentStatus section refs, complete source date accuracy table.
- **2026-04-11 v1:** Initial version from competitive research.
