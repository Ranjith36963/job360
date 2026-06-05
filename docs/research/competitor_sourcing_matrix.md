# Competitor Sourcing Matrix — Where the Free-Forever Cohort Gets Its Jobs

> Job360 vs. the free-forever, UK-relevant competitor cohort: **what sourcing model each uses**
> and **which of Job360's 50 providers each one overlaps**. Job360 column = code-verified
> (`backend/src/main.py::SOURCE_REGISTRY`). Competitor columns = deduced from vendor marketing /
> integrations pages / founder posts (treat as directional). Scoutify's is most reliable — it
> publishes its source list at `scoutify.com/integrations`.
>
> **Created:** 2026-06-04 · **Related:** [`References.md` §1.17](../References.md) ·
> [`job_data_acquisition_methods.md`](./job_data_acquisition_methods.md)

---

## Headline finding

Job360 and the free-forever leaders **barely overlap on data sources.** Job360 aggregates from
**named job-board + ATS APIs**; competitors mostly **crawl company career pages directly at
scale.** The one shared layer is ATS (Greenhouse / Lever / Workday / Ashby) — but they tap
**thousands** of companies there while Job360 taps **~268 slugs**.

**Two opposite sourcing philosophies:**
- **Job360** = "pull from boards/APIs that already aggregate jobs" (Reed, Adzuna, RemoteOK, + 12
  ATS for named companies).
- **HiringCafe / Scoutify / FirstPost** = "discover 10K–150K company URLs, then crawl their
  career pages directly." This is why they can claim "we alert you *before* it hits Indeed" —
  they're **upstream** of the boards Job360 reads.
- **Welcome to the Jungle** = doesn't aggregate at all; employers post directly (and pay).
- **Jack & Jill** = the same free ATS APIs Job360 uses (deduced).

---

## Table A — Sourcing model (all free-forever, all UK-relevant)

| Player | Free-forever | Core sourcing model | Scale | Named job-boards? | ATS APIs? | Mass career-page crawl? | Employer-posted? |
|--------|:---:|---------------------|-------|:---:|:---:|:---:|:---:|
| **Job360 (you)** | ✅ self-host | Board + ATS API aggregation | 50 sources / ~268 ATS slugs | ✅ many | ✅ (12, limited slugs) | ❌ | ❌ |
| **HiringCafe** | ✅ | Career-page crawl | 30K companies | ❌ | ✅ (as crawl targets) | ✅ | ❌ |
| **Scoutify** | ✅ core | ATS integration + custom adapters | ~10K companies | ❌ | ✅ direct | ✅ | ❌ |
| **FirstPost** | ⚠️ freemium | Career-page + platforms + boards | 150K cos / 1M roles | ~ some | ✅ | ✅ | ❌ |
| **Welcome to the Jungle** | ✅ seekers | Employer-posted (closed) | ~7K vetted cos | ❌ | ❌ | ❌ | ✅ |
| **Jack & Jill** | ✅ | Free ATS APIs (deduced) | "14M jobs" | ❌ | ✅ | ~ | ❌ |

---

## Table B — Provider matrix: Job360's 50 vs. competitor sources

Legend: ✅ has it · ❌ doesn't · ~ partial/deduced · 🚩MINE = Job360 has, they don't ·
🚩THEIRS = they have, Job360 doesn't

| Data source / channel (Job360's providers) | Job360 | HiringCafe | Scoutify | FirstPost | WTTJ | J&J | Verdict |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| UK keyed boards — Reed, Adzuna, Careerjet, Findwork, Jooble, JSearch, Google Jobs | ✅ | ❌ | ❌ | ~ | ❌ | ❌ | 🚩MINE |
| Free remote JSON boards — RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas, DevITjobs, Landing.jobs, AIJobs.net | ✅ | ❌ | ❌ | ~ | ❌ | ❌ | 🚩MINE |
| Indeed + Glassdoor (via JobSpy) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 🚩MINE (they bypass these on purpose) |
| LinkedIn listings (scraper) | ✅ | ❌ | ❌ | ❌ | ❌ | ~ | 🚩MINE (fragile) |
| UK public-sector RSS — NHS Jobs, jobs.ac.uk, GOV.UK Apprenticeships, Teaching Vacancies, University Jobs | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 🚩MINE — strongest UK moat |
| Niche/sector boards — Climatebase, 80000Hours, BioSpace, BCS, NoFluffJobs, TheMuse, HN Jobs | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 🚩MINE (multi-domain) |
| Common ATS — Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Recruitee, Workday, Personio | ✅ ~268 slugs | ✅ 30K | ✅ 10K | ✅ 150K | ❌ | ✅ | ⚠️ SHARED — they cover 30×–500× more companies |
| Long-tail ATS — Rippling, Comeet, Pinpoint, SuccessFactors | ✅ | ~ | ~ | ~ | ❌ | ~ | ≈ slight MINE edge |
| Big-tech proprietary careers — Amazon, Apple, Microsoft, Google custom systems | ❌ | ✅ | ✅ adapters | ✅ | ❌ | ❌ | 🚩THEIRS |
| Mass career-page discovery/crawl (Apollo + Common Crawl + Google-dorking → tens of thousands of company URLs) | ❌ | ✅ | ✅ | ✅ | ❌ | ~ | 🚩THEIRS — their core moat |
| Schema.org JobPosting JSON-LD harvest (generic structured-data scrape) | ❌ | ✅ | ✅ | ✅ | ❌ | ~ | 🚩THEIRS |
| Employer-direct posts (paid posting product) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | 🚩THEIRS (different business) |

---

## Flag summary

**🚩 Job360 has, they don't (sourcing moat):**
- **UK breadth** — keyed UK boards (Reed/Adzuna/Careerjet) + UK public-sector RSS (NHS,
  jobs.ac.uk, gov apprenticeships). *No free-forever rival touches UK public sector* — the single
  most defensible source advantage.
- **Aggregator diversity** — Indeed/Glassdoor (JobSpy), free remote-job JSON boards, multi-domain
  niche boards. Competitors *skip* boards by design to stay upstream.
- **Long-tail ATS** — Rippling/Comeet/SuccessFactors that the big crawlers under-prioritise.

**🚩 They have, Job360 doesn't (sourcing gap):**
- **Mass career-page crawl at scale** — 30K (HiringCafe) → 150K (FirstPost) companies vs.
  Job360's **~268 ATS slugs**. *This is the gap.*
- **Big-tech proprietary career-system adapters** (Amazon/Apple/etc.) — Scoutify built these
  explicitly; Job360 has none.
- **Generic JSON-LD harvesting** — ingest any Schema.org-tagged career page with zero per-source
  code.
- **Employer-direct posts** (WTTJ/J&J) — different business model.

**⚠️ The shared layer is the real lever:** Greenhouse / Lever / Workday / Ashby / SmartRecruiters /
Recruitee. Job360 already calls the *same ATS APIs* — it just points them at ~268 hand-curated
slugs. **Closing the coverage gap = scaling the `core/companies.py` slug catalog + adding ATS
auto-discovery, not a new integration or architecture change.** Inventory-by-source-count is now a
losing axis (FirstPost = 150K companies); Job360's edge must be **match quality + UK depth +
multi-domain**, not raw job count.

---

## Sources

- Scoutify integrations — https://scoutify.com/integrations
- FirstPost — https://firstpost.io/
- WTTJ employers — https://employers.welcometothejungle.com/how-it-works
- Aggregator sourcing methods (ATS public APIs vs career-page crawl, Schema.org JSON-LD) —
  https://cavuno.com/blog/ats-platforms-public-job-posting-apis
- HiringCafe architecture (founder posts) — see `References.md` §1.1
