# Job360 — Product Requirements Document (PRD)

> **Version:** 1.0  
> **Date:** 15 April 2026  
> **Author:** Ranjith (Founder & Engineer)  
> **Status:** Draft — awaiting internal review  
> **Companion documents:** `CurrentStatus.md` (technical audit, 2026-04-11), `References.md` (competitive intelligence, 2026-04-11)

---

## 1. Product Vision

Job360 is a hosted SaaS platform that automates personalised job discovery for white-collar professional job seekers across all domains in the United Kingdom. The platform searches the web on behalf of each user, scores every listing against their unique profile, and delivers trusted, time-bucketed results through a dashboard and push notifications — so the job seeker never has to manually trawl job boards again.

**Core thesis:** The value of a job search tool is determined entirely by the quality of its results. Features are worthless if the engine delivers irrelevant, stale, or untrustworthy listings. Quality gates everything.

**Differentiator:** Personalisation and push-based delivery. Unlike generic search engines (HiringCafe, LinkedIn, Indeed) where users pull results from a firehose, Job360 pushes the right jobs to each user based on their CV, skills, preferences, and career trajectory — across every professional domain, not just tech.

---

## 2. Target User

**Primary persona:** A white-collar professional job seeker in the United Kingdom, across any domain — technology, finance, legal, marketing, healthcare, engineering, operations, HR, consulting, academia, or any other skilled professional field. The user holds a proper CV, possesses demonstrable skills, and is actively or passively searching for their next role.

**User characteristics:**

The target user is not necessarily technical. They may be a marketing director, a financial analyst, a clinical researcher, or a software engineer. What unites them is that they are professionals with a structured career history, they are seeking roles that match their skills and experience, and they do not have the time or inclination to check dozens of job boards daily.

**Exclusions:** Job360 does not target entry-level unskilled roles, gig work, freelance marketplaces, or trades/manual labour positions. The platform is designed for roles where a CV and professional skills are the primary hiring currency.

**Geographic scope (v1):** United Kingdom only. This includes UK-based roles, remote roles available to UK residents, and hybrid arrangements within commuting distance of UK cities.

---

## 3. Product Architecture — Three Layers

Job360 is architecturally divided into three interdependent layers. The quality of the overall system is bounded by the weakest layer.

### Layer 1 — Job Seeker Profile

This layer captures, parses, and structures everything the platform knows about a user. It is the input to the matching engine.

**What enters this layer:** The user's CV (PDF or DOCX), optional LinkedIn data export (ZIP), optional GitHub profile, and manually entered preferences (target titles, skills, excluded skills, preferred locations, salary range, work arrangement, experience level, and negative keywords).

**What exits this layer:** A structured `SearchConfig` that the engine uses for scoring — containing tiered skills (primary, secondary, tertiary), target job titles, relevance keywords, negative keywords, locations, core domain words, supporting role words, and pre-computed search queries.

**Design constraint:** Zero hardcoded keywords anywhere in the system. The entire scoring vocabulary is derived from the user's profile. This is what makes the platform domain-agnostic — a financial analyst and a machine learning engineer produce completely different SearchConfigs from the same pipeline.

### Layer 2 — Search & Match Engine

This is the core of Job360. It fetches job listings from all sources, normalises them into a common format, scores each listing against the user's SearchConfig, deduplicates across sources, filters by quality threshold, and persists the results.

**Scoring dimensions (current, 100-point scale):**

| Dimension | Weight | What it measures |
|---|---|---|
| Title match | 40 points | How closely the job title matches the user's target titles |
| Skill match | 40 points | How many of the user's skills appear in the job listing |
| Location match | 10 points | Whether the job is in a UK city, remote, or foreign |
| Recency | 10 points | How recently the job was posted (decays over 7 days) |
| Negative penalty | −30 points | Presence of explicitly excluded keywords in the title |
| Foreign penalty | −15 points | Job is located outside the UK |

**Quality threshold:** Jobs scoring below 30/100 are silently dropped. Only jobs meeting this bar reach the user.

**Design constraint:** The engine's quality is the single most important factor in the product. If the engine delivers irrelevant jobs, no amount of features (pipeline tracker, push notifications, beautiful dashboard) will retain users. The engine must be the primary focus of engineering effort until it demonstrably delivers trusted results across multiple professional domains.

### Layer 3 — Job Provider Data

This layer is responsible for sourcing raw job listings from the web. It operates 47 concurrent async sources spanning keyed APIs, free JSON APIs, ATS board APIs, RSS feeds, HTML scrapers, and specialty sources.

**Coverage categories:**

| Category | Count | Examples |
|---|---|---|
| Keyed APIs (free keys) | 7 | Reed, Adzuna, JSearch, Jooble, Google Jobs, Careerjet, Findwork |
| Free JSON APIs | 10 | Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs |
| ATS direct boards | 10 | Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Workday |
| RSS/XML feeds | 8 | FindAJob (UK DWP), NHS Jobs, jobs.ac.uk, WeWorkRemotely |
| HTML scrapers | 7 | LinkedIn, Climatebase, 80000Hours, BCS Jobs |
| Other | 5 | HackerNews, Indeed/Glassdoor, TheMuse, NoFluffJobs |

**Design constraint:** Job provider data must be free. The platform does not pay for job data. All sources use public, unauthenticated, or free-tier APIs. Paid aggregators (TheirStack, Coresignal, Bright Data) are permanently dismissed — they resell the same data that is available directly.

---

## 4. User Journey — End to End

### 4.1 Sign Up

The user arrives at the Job360 website and creates an account. Authentication method is to be determined through research — candidates include Google OAuth, GitHub OAuth, and magic link (email-based passwordless login). The chosen method must optimise for ease of sign-up (minimal friction for non-technical users), compatibility with future payment integration, and data retention/security requirements.

### 4.2 Onboarding — Build Profile

After sign-up, the user enters the profile builder. This is a guided flow, not a blank form.

**Step 1 — Upload CV.** The user uploads their CV as a PDF or DOCX. The system extracts text and sends it through an LLM parsing pipeline (Gemini → Groq → Cerebras fallback chain) to extract skills, job titles, companies, education, certifications, summary, and experience text. The parsed result is displayed to the user for review.

**Step 2 — Optional enrichment.** The user may optionally upload a LinkedIn data export (ZIP) or connect their GitHub username. LinkedIn enrichment adds positions, skills, and industry context. GitHub enrichment infers technical skills from repository languages and topics.

**Step 3 — Manual preferences.** The user reviews the auto-extracted profile and edits as needed. They can add or remove target job titles, skills, excluded skills, preferred locations, salary range, work arrangement (remote/hybrid/onsite), experience level, and negative keywords. The system auto-parses according to any changes.

**Step 4 — Save preferences (click 1).** The user clicks "Save Preferences" to confirm their profile. This is a deliberate action — the system does not auto-save on every keystroke, because saving triggers profile recomputation.

**Step 5 — Trigger first search (click 2).** After saving, the user clicks "Search" to trigger their first pipeline run. This is a separate, deliberate action. The two-click pattern (save, then search) exists because each search consumes compute resources (47 source fetches, LLM calls, scoring), and auto-triggering on every profile edit would waste resources on incomplete configurations.

**Why two clicks, not one:** Every time a user changes a single word in their preferences, if search ran automatically, the platform would burn compute on an unfinished configuration. The user must explicitly signal "I am satisfied with my profile" (save) and "run the search now" (search) as two separate intents.

### 4.3 Results — Dashboard

After the search completes, results appear in the user's dashboard. The dashboard displays scored job listings organised by time buckets (last 24 hours, 24–48 hours, 48–72 hours, 3–7 days). Each listing shows the match score, title, company, location, salary (if available), source, posting date, visa sponsorship flag, and experience level.

The user can filter by minimum score, source, time bucket, visa sponsorship, and action status. They can view detailed score breakdowns for any listing, seeing how points were allocated across title match, skill match, location, and recency.

### 4.4 Actions — Like, Skip, Apply

For each job listing, the user can take one of three actions: "Liked" (bookmark for later), "Not Interested" (hide from future views), or "Applied" (mark as applied, which moves it to the pipeline tracker).

### 4.5 Pipeline Tracker

The pipeline tracker is a Kanban-style board with stages: Applied → Outreach → Interview → Offer / Rejected. The user can advance applications through stages, add notes, and receive reminders for stale applications (no update in 7+ days).

### 4.6 Push Notifications

The user configures their preferred delivery endpoints: email, Slack, Telegram, Discord, or any combination. On each scheduled search run, the platform pushes new results to the user's chosen endpoints. The push contains the same information as the dashboard — scored listings, time-bucketed, with match scores and key details.

The push notification is the primary differentiator. The user does not need to open the dashboard to discover new matches. Job360 finds jobs for the user and delivers them proactively.

### 4.7 Scheduled Re-search

After the initial manual search, the platform runs automated searches on a configurable schedule (default: twice daily). Each scheduled run fetches fresh listings from all sources, scores them against the user's current profile, deduplicates against previously seen listings, and delivers only genuinely new matches.

The user can trigger a manual search at any time in addition to the scheduled runs.

---

## 5. Functional Requirements

### FR-1: Multi-User Architecture

**FR-1.1:** Each user has an isolated profile, search configuration, job results, actions, and pipeline state. One user's data is never visible to or affected by another user's activity.

**FR-1.2:** The platform supports N users across N professional domains simultaneously. A financial analyst and a software engineer can both be active users with completely independent experiences.

**FR-1.3:** User profiles are persisted in a durable, multi-tenant data store. The current single-file JSON storage (`backend/data/user_profile.json`) and single SQLite database must be replaced with a multi-tenant architecture.

**FR-1.4:** Each user's search runs are isolated — one user's scheduled search does not block, delay, or interfere with another user's search.

### FR-2: Profile System

**FR-2.1:** The system accepts CV uploads in PDF and DOCX formats. CV text extraction must handle standard document layouts. Scanned/image-only PDFs are out of scope for v1 but should fail gracefully with a clear error message.

**FR-2.2:** CV parsing uses LLM-based extraction with a fallback chain (currently Gemini → Groq → Cerebras). The system must never crash if all LLM providers are unavailable — it must fall back to manual-only preferences entry.

**FR-2.3:** LinkedIn data export parsing accepts the standard LinkedIn ZIP format and extracts positions, skills, education, certifications, and industry.

**FR-2.4:** GitHub enrichment fetches public repository data and infers skills from languages and topics.

**FR-2.5:** The preference editor allows the user to override, add, or remove any auto-extracted field. User-entered preferences take priority over auto-extracted values.

**FR-2.6:** Skill auto-tiering (primary, secondary, tertiary) must be improved beyond the current naive position-based thirds split. Tiering should account for recency of skill usage, frequency of mention across CV/LinkedIn/GitHub, and explicit user prioritisation.

**FR-2.7:** Zero hardcoded domain-specific keywords. The system derives all scoring vocabulary from the user's profile. The only hardcoded lists are geographic (UK locations) and structural (visa keywords).

### FR-3: Search Engine

**FR-3.1:** The engine fetches job listings from all configured sources in parallel using async fan-out. Each source failure is isolated — a single broken source must not prevent other sources from returning results.

**FR-3.2:** Every listing is normalised into a common data model (title, company, location, salary, description, URL, source, posting date, and derived fields).

**FR-3.3:** Every listing is scored from 0 to 100 against the user's SearchConfig. The scoring formula combines title match, skill match, location match, and recency, with penalties for negative keywords and foreign locations.

**FR-3.4:** Posting date accuracy must be improved. Sources that cannot provide a real posting date must return `None` (not `datetime.now()`). The recency scoring dimension must handle `None` dates gracefully — either awarding zero recency points or using `first_seen` as a conservative fallback.

**FR-3.5:** Deduplication must merge identical listings that appear across multiple sources, keeping the highest-scoring variant. The deduplication key must be robust to minor title variations (seniority prefixes, job codes, parenthetical annotations).

**FR-3.6:** The minimum score threshold (currently 30) filters out low-relevance listings before they reach the user. This threshold should be configurable per user in future iterations.

**FR-3.7:** Source routing must be profile-aware. A non-tech user should not have their results dominated by tech-only job boards. The engine must weight source selection based on the user's professional domain.

### FR-4: Job Provider Data

**FR-4.1:** All job data sources must be free (public APIs, free-tier keys, or unauthenticated endpoints). No paid data providers.

**FR-4.2:** ATS direct board coverage must expand beyond the current approximately 104 company slugs. Target: 500+ companies across diverse industries.

**FR-4.3:** UK general aggregator coverage must be strengthened. Reed, Adzuna, and FindAJob are insufficient for non-tech domains. Additional UK-focused aggregators should be researched and integrated.

**FR-4.4:** Sources that do not provide real job listings (YC Companies, Nomis vacancy statistics) must be removed or reclassified as supplementary discovery tools rather than job sources.

**FR-4.5:** Each source must accurately report whether it can provide a real posting date. Sources that cannot must explicitly signal this rather than fabricating timestamps.

### FR-5: Dashboard

**FR-5.1:** The dashboard displays the user's scored job listings, organised by time buckets (24h, 48h, 72h, 7d).

**FR-5.2:** Filter controls allow narrowing by minimum score, source, time bucket, visa sponsorship, action status, and keyword search.

**FR-5.3:** Each listing is expandable to show a detailed score breakdown (title match points, skill match points, location points, recency points, penalties applied).

**FR-5.4:** The dashboard must be responsive and functional on both desktop and mobile browsers.

**FR-5.5:** The Streamlit dashboard is deprecated and must be removed. The Next.js frontend is the sole dashboard.

### FR-6: Push Notifications

**FR-6.1:** The platform supports push delivery to email (SMTP), Slack (webhook), Discord (webhook), and Telegram (bot API). Additional channels may be added in future.

**FR-6.2:** Each user configures their own delivery endpoints. A user may have multiple endpoints active simultaneously (for example, email and Slack).

**FR-6.3:** Push notifications are triggered by scheduled search runs and contain only genuinely new matches (listings not previously seen by the user).

**FR-6.4:** Notification content mirrors the dashboard — scored listings with match scores, time buckets, and key details.

**FR-6.5:** The `--no-email` CLI flag must be renamed to `--no-notify` to accurately reflect that it disables all notification channels, not just email.

### FR-7: Pipeline Tracker

**FR-7.1:** Each user has an independent application pipeline with stages: Applied, Outreach, Interview, Offer, Rejected.

**FR-7.2:** Users can advance applications between stages, add notes, and view history.

**FR-7.3:** Stale application detection alerts the user when an application has had no stage change for 7+ days.

### FR-8: Scheduling

**FR-8.1:** Each user has an independent search schedule. The default schedule is twice daily (to be configured).

**FR-8.2:** Scheduled searches run automatically without user intervention and deliver results to the user's configured push endpoints.

**FR-8.3:** Users can trigger manual searches at any time, independent of their schedule.

**FR-8.4:** The scheduling system must support N concurrent users without search runs interfering with each other.

### FR-9: Authentication and User Management

**FR-9.1:** Users sign up and log in via a secure authentication method. The specific method (Google OAuth, GitHub OAuth, magic link, or email+password) will be determined through research, optimising for ease of use, payment integration compatibility, and security.

**FR-9.2:** Each user's data is isolated and accessible only to that user.

**FR-9.3:** Users can delete their account and all associated data.

### FR-10: Freemium Model

**FR-10.1:** New users receive a free tier with a limited number of searches. The exact limit is deferred pending quality validation — higher engine quality allows a tighter free tier.

**FR-10.2:** The paid tier unlocks unlimited (or higher-volume) searches, higher scheduling frequency, and additional delivery channels.

**FR-10.3:** The payment integration must be seamless and compatible with the chosen authentication provider.

**FR-10.4:** Pricing is deferred until the engine quality is validated in production. The initial launch may be fully free while quality is being iterated.

---

## 6. Non-Functional Requirements

### NFR-1: Performance

**NFR-1.1:** A single user's search pipeline must complete within 5 minutes (47 sources, 120s per-source timeout, parallel execution).

**NFR-1.2:** Dashboard page load must be under 3 seconds for up to 500 job listings.

**NFR-1.3:** The system must support concurrent search runs for multiple users without degradation.

### NFR-2: Reliability

**NFR-2.1:** Individual source failures must not crash the pipeline. The system must degrade gracefully, returning results from healthy sources.

**NFR-2.2:** Scheduled search runs must execute reliably. Failures must be logged, and the next scheduled run must proceed normally.

**NFR-2.3:** Database operations must be ACID-compliant. No partial writes or data corruption under concurrent access.

### NFR-3: Security

**NFR-3.1:** User CVs, profile data, and search results must be stored securely. CVs may contain sensitive personal information (address, phone, email).

**NFR-3.2:** API keys for job sources must not be exposed in logs, error messages, or client-facing responses.

**NFR-3.3:** CORS must be configured for the production domain, not hardcoded to localhost.

**NFR-3.4:** The API must implement authentication — no unauthenticated access to user data.

### NFR-4: Scalability

**NFR-4.1:** The data model must support multi-tenancy from day one. Single-user assumptions (single profile JSON, single SQLite file) must be eliminated.

**NFR-4.2:** The architecture must support migration from SQLite to a managed database (PostgreSQL) when user volume requires it.

### NFR-5: Observability

**NFR-5.1:** Every search run must be logged with per-source success/failure counts, total jobs found, new jobs inserted, and execution time.

**NFR-5.2:** Source health must be monitored. Sources that consistently return zero results must trigger alerts.

**NFR-5.3:** Scoring quality metrics must be tracked — for example, what percentage of delivered jobs receive a "Liked" action versus "Not Interested."

---

## 7. Current State vs. Target State — Gap Analysis

This section maps the current codebase state (from `CurrentStatus.md`) against the requirements above. Each row identifies a gap that must be closed for v1 launch.

### Layer 1 — Job Seeker Profile

| Requirement | Current State | Gap | Severity |
|---|---|---|---|
| FR-1.1: Multi-user isolation | Single `user_profile.json` file, single SQLite DB | Must redesign for multi-tenant storage | **Critical** |
| FR-2.1: CV ingestion | PDF/DOCX extraction works (pdfplumber + python-docx) | No OCR for scanned PDFs (acceptable for v1) | Low |
| FR-2.2: LLM parsing | Gemini → Groq → Cerebras fallback chain functional | Hard crash if all 3 keys missing; needs graceful fallback to manual-only | Medium |
| FR-2.3: LinkedIn enrichment | ZIP parsing works for standard format | No handling of LinkedIn format changes | Medium |
| FR-2.4: GitHub enrichment | 32 language + 50 topic mappings | Limited coverage; public repos only | Low |
| FR-2.5: Preference override | Manual preferences override auto-extracted values | No conflict resolution (CV says "junior", prefs say "senior") | Medium |
| FR-2.6: Skill tiering | Naive position-based thirds split | Must weight by recency, frequency, and explicit priority | **High** |
| FR-2.7: No hardcoded keywords | All domain lists emptied on 2026-04-09 | **Done.** Only LOCATIONS and VISA_KEYWORDS remain (structural, not domain). | None |

### Layer 2 — Search & Match Engine

| Requirement | Current State | Gap | Severity |
|---|---|---|---|
| FR-3.1: Parallel fan-out | asyncio.gather with 47 sources, failure isolation | Working. No changes needed for v1 architecture. | None |
| FR-3.2: Normalisation | Job dataclass with HTML unescape, company cleaning, salary bounds | No currency detection, binary visa flag loses nuance | Medium |
| FR-3.3: Scoring accuracy | 4-dimension scoring (title 40, skill 40, location 10, recency 10) | Title matching is substring-based (crude). Skill matching is regex-only (no synonyms, no semantic similarity). | **High** |
| FR-3.4: Date accuracy | 14/47 sources hardcode `now()`. 3 more use wrong date field. | Recency scoring is broken for 36% of sources. Must fix to `None` + fallback logic. | **Critical** |
| FR-3.5: Deduplication | Works within a run. DB unique key is narrower than dedup key (documented). | Cross-run reappearance of seniority variants is a known tradeoff. Acceptable for v1. | Low |
| FR-3.7: Source routing | All 47 sources run for every user regardless of domain | Non-tech users drowned in tech-only results. Must implement domain-aware routing. | **Critical** |
| Not in FRs: LLM enrichment | Not implemented. LLM used only for CV parsing. | HiringCafe extracts 17+ fields per listing via GPT-4o-mini. Job360 has zero JD enrichment. | **High** |
| Not in FRs: Semantic matching | Not implemented. No embeddings, no ChromaDB, no cross-encoder. | Skill matching misses synonyms ("ML" vs "Machine Learning"). | **High** |
| Not in FRs: Ghost detection | Not implemented. No disappearance tracking. | Filled/expired jobs stay in DB until 30-day purge. | Medium |
| Salary in scoring | Used only as sort tiebreaker, not part of 0–100 score | Salary is a major decision factor but contributes zero to match score. | Medium |

### Layer 3 — Job Provider Data

| Requirement | Current State | Gap | Severity |
|---|---|---|---|
| FR-4.1: Free sources only | All 47 sources are free | **Done.** | None |
| FR-4.2: ATS company coverage | Approximately 104 company slugs across 10 ATS platforms | Feashliaa repo has 4,000+. Target: 500+. | **High** |
| FR-4.3: UK general aggregators | Reed, Adzuna, FindAJob (3 sources) | Critically insufficient for non-tech domains. Need more UK-wide boards. | **Critical** |
| FR-4.4: Remove non-job sources | YC Companies (career links), Nomis (statistics) still active | Should be removed or reclassified | Low |
| FR-4.5: Date field honesty | 14 sources fabricate dates with `now()` | Must return `None` instead | **Critical** (tied to FR-3.4) |

### Cross-Cutting

| Requirement | Current State | Gap | Severity |
|---|---|---|---|
| FR-9.1: Authentication | No auth anywhere. API is fully open. | Must implement auth for multi-user SaaS. | **Critical** |
| FR-1.3: Multi-tenant data | Single SQLite file, single profile JSON | Must redesign for N users. | **Critical** |
| FR-8.1: Per-user scheduling | `cron_setup.sh` is broken. Single-user cron assumed. | Must build per-user scheduling system. | **Critical** |
| FR-6.2: Per-user notifications | Single SMTP/Slack/Discord config in env vars | Must store per-user delivery endpoints. | **High** |
| FR-5.5: Remove Streamlit | Streamlit dashboard still functional | Must remove and migrate any reusable logic. | Medium |
| NFR-3.3: CORS | Hardcoded to `http://localhost:3000` | Must configure for production domain. | **High** |
| NFR-5: Observability | Rotating log file + run_log table. No alerting. | Need structured monitoring and source health alerts. | Medium |
| CI/CD | No GitHub Actions, no Dockerfile, no docker-compose | Must implement for hosted SaaS deployment. | **High** |
| Deployment config | No railway.json, no vercel.json, no fly.toml | Must choose and configure hosting platform. | **High** |

---

## 8. Quality-First Prioritisation

Per the founding principle that **quality gates everything**, work must be sequenced so that the core engine delivers trusted results before features are layered on top.

### Phase 0 — Foundation (Multi-User + Auth)

The single-user architecture is incompatible with every other requirement. This must be resolved first, even before engine quality work, because every subsequent change touches the data model.

**Scope:** Authentication system, multi-tenant database schema, per-user profile storage, per-user job results isolation, API authentication middleware, CORS configuration for production.

### Phase 1 — Engine Quality (The Three Pillars)

This is the highest-value work in the entire product. Nothing else matters if this does not work well.

**Pillar 1 fixes (Job Seeker Profile):** Improve skill auto-tiering beyond position-based thirds. Add LLM graceful fallback. Validate parsed profile quality.

**Pillar 2 fixes (Search & Match):** Fix `date_found` to return `None` instead of `now()` in all 14 affected sources. Fix 3 semantically wrong date fields (jooble, greenhouse, nhs_jobs). Update recency scoring to handle `None` dates. Implement source routing by professional domain. Evaluate and begin semantic skill matching.

**Pillar 3 fixes (Job Provider Data):** Expand ATS company slugs from approximately 104 to 500+. Research and integrate additional UK general aggregators for non-tech domains. Remove or reclassify non-job sources.

### Phase 2 — Delivery

**Scope:** Per-user push notification configuration. Per-user scheduled search runs. Dashboard with scored results, time buckets, filters, and score breakdowns. Pipeline tracker (Kanban board). CSV export.

### Phase 3 — Polish and Launch

**Scope:** Freemium metering (search count limits). Payment integration. Onboarding flow refinement. Mobile responsiveness. Remove Streamlit. CI/CD pipeline. Production deployment. Monitoring and alerting.

---

## 9. Success Metrics

### Engine Quality Metrics (Primary — these gate everything)

| Metric | Definition | Target |
|---|---|---|
| Relevance rate | % of delivered jobs that receive "Liked" or "Applied" action (vs "Not Interested") | > 40% |
| Date accuracy | % of delivered jobs with a real posting date (not fabricated) | > 90% |
| Cross-domain relevance | Relevance rate for non-tech profiles specifically | > 30% |
| Source health | % of sources returning non-zero results per run | > 85% |

### Product Metrics (Secondary — measured after engine quality is validated)

| Metric | Definition | Target |
|---|---|---|
| Onboarding completion | % of sign-ups that complete profile + first search | > 60% |
| Retention (7-day) | % of users who return to dashboard or receive a push notification within 7 days of sign-up | > 40% |
| Push engagement | % of push notification recipients who click through to at least one listing | > 20% |
| Pipeline usage | % of active users who advance at least one application through the pipeline tracker | > 15% |

### Business Metrics (Tertiary — measured after product-market fit)

| Metric | Definition | Target |
|---|---|---|
| Free-to-paid conversion | % of free-tier users who convert to paid | TBD (pending pricing) |
| Word-of-mouth referrals | % of new users who were referred by an existing user | > 20% |
| Monthly active users | Users who perform at least one search or receive one push notification per month | Growth target TBD |

---

## 10. Open Questions

These items require research or decisions before implementation can begin on the relevant features.

| # | Question | Depends on | Owner |
|---|---|---|---|
| 1 | Authentication provider: Google OAuth, GitHub OAuth, magic link, or combination? Must consider payment integration, data retention, and non-tech user friction. | Phase 0 | Ranjith |
| 2 | Database: PostgreSQL (managed), Supabase, PlanetScale, or multi-file SQLite with per-user sharding? | Phase 0 | Ranjith |
| 3 | Hosting platform: Railway, Fly.io, Render, Vercel (frontend) + Railway (backend), or AWS? | Phase 0 | Ranjith |
| 4 | Free tier limits: Number of searches? Number of results shown? Time-limited trial? Determined by engine quality. | Phase 3 | Deferred |
| 5 | Telegram bot API integration: Build in-house or use a notification service? | Phase 2 | Ranjith |
| 6 | Semantic matching stack: sentence-transformers + ChromaDB, or lighter-weight approach? | Phase 1 | Ranjith |
| 7 | LLM job description enrichment: Repurpose existing Gemini/Groq/Cerebras chain, or add Ollama/local model? | Phase 1 | Ranjith |
| 8 | Source routing heuristic: How to classify a user's professional domain from their profile to route to appropriate sources? | Phase 1 | Ranjith |

---

## 11. Constraints

**Budget:** Zero-cost or near-zero-cost infrastructure. Free-tier APIs, free LLM providers, free hosting tiers where possible. No paid job data providers.

**Solo engineer:** Ranjith is the sole developer. Architecture and sequencing must account for one person's bandwidth.

**UK only (v1):** Geographic scope is limited to the United Kingdom. International expansion is a future consideration, not a v1 requirement.

**No hardcoded keywords:** The system must be fully profile-driven. Any hardcoded domain-specific keyword list is a scalability limitation and a violation of the product's core design principle.

**Quality over features:** If engine quality is insufficient, feature development pauses until quality improves. Features built on top of a bad engine are worse than no features at all — they create a polished experience that delivers wrong results, which destroys user trust faster than a rough experience that delivers right results.

---

*End of PRD v1.0. This document should be reviewed alongside `CurrentStatus.md` (what the code does today) and `References.md` (competitive intelligence and technical patterns) to form a complete picture of where Job360 is, where it needs to go, and what exists in the ecosystem to learn from.*
