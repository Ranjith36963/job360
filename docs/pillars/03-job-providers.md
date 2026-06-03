# Pillar 3 — Job Providers

> **Audience.** Read this if you want to understand where Job360's raw job postings actually come from — the 49 source classes, the one base class they all inherit, the shared retry/rate-limit/conditional-fetch machinery, the ATS company-slug catalog, and how a posting becomes a normalised `Job` row in the shared catalog.
>
> **Scope.** Covers code on `main` as of 2026-05-28 (HEAD `a7a2268`), post-Batch-3 source rotation. Counts in this doc were verified directly against the files on disk and the test assertions — not taken from prose.

---

## 1. TL;DR — what the providers pillar does

> *Job360 talks to 49 distinct job-source classes — keyed aggregators, free JSON APIs, company ATS boards, RSS/XML feeds, HTML scrapers, and a few one-offs. Every one inherits a single `BaseJobSource` that gives it retry-with-backoff, per-source rate limiting, optional conditional (ETag/304) fetching, and a UK/remote location filter for free. Each source's only real job is to implement one async method — `fetch_jobs() -> list[Job]` — turning whatever shape the upstream returns into the canonical `Job` dataclass. The orchestrator (Pillar 2) then scores, dedups, and stores them.*

### The count reconciliation (read this once, never wonder again)

Three numbers float around the codebase and they're all correct because they count different things:

| Number | What it counts | Where |
| --- | --- | --- |
| **49** | Source *class files* on disk | `find src/sources -name '*.py'` minus `__init__`/`base` = 7+11+12+8+7+4 |
| **50** | *Registry keys* in `SOURCE_REGISTRY` | `main.py:106-159` — `"glassdoor"` is a second key that aliases `JobSpySource` |
| **49** | Live *instances* built per run | `SOURCE_INSTANCE_COUNT = 49` (`main.py:165`) — `indeed`+`glassdoor` collapse to one `JobSpySource` |

So: **49 classes → 50 registry keys → 49 instances.** The single fork is Indeed/Glassdoor, both handled by `JobSpySource` in `other/indeed.py`. The test suite pins all of this — `test_cli.py` asserts `len(SOURCE_REGISTRY) == 50`, `test_api.py` asserts `sources_total == 50` in three places (CLAUDE.md rule #13).

---

## 2. The base class — `backend/src/sources/base.py`

Every source extends `BaseJobSource`. **Never change this class without checking all 49 subclasses** (CLAUDE.md rule #2) — every change propagates to every source.

### 2.1 Constructor (`base.py:64-69`)

```python
def __init__(self, session: aiohttp.ClientSession, search_config=None):
    self._session = session
    self._search_config = search_config
    cfg = RATE_LIMITS.get(self.name, {"concurrent": 2, "delay": 1.0})
    self._rate_limiter = RateLimiter(concurrent=cfg["concurrent"], delay=cfg["delay"])
    self._conditional_cache = ConditionalCache()
```

- `session` — one shared `aiohttp.ClientSession` across all sources (connection pooling).
- `search_config=None` — when present, the source uses the user's dynamic keywords; when `None`, it falls back to the (now-empty) hard-coded defaults from `keywords.py`.
- Rate limiter is pulled per-source from `RATE_LIMITS` (50 entries) with a safe `{concurrent:2, delay:1.0}` default.

### 2.2 The three dynamic properties (`base.py:71-87`)

This is how a single source body serves both "profile loaded" and "no profile" cases without branching:

| Property | Returns when `search_config` set | Returns when `None` |
| --- | --- | --- |
| `relevance_keywords` | `search_config.relevance_keywords` | `keywords.RELEVANCE_KEYWORDS` (empty post-3ba1342) |
| `job_titles` | `search_config.job_titles` | `keywords.JOB_TITLES` (empty) |
| `search_queries` | `search_config.search_queries` if non-empty | `[]` |

> Sources MUST access keywords through these properties — never `from src.core.keywords import ...` directly. This is what makes the system domain-agnostic (CLAUDE.md "Dynamic keywords" pattern).

### 2.3 HTTP helpers + retry machinery (`base.py:100-245`)

The core is `_request()` (`base.py:100-155`). Everything else is a thin wrapper:

- `_get_json(url, params, headers)` → JSON dict/list
- `_post_json(url, body, headers)` → JSON dict/list
- `_get_text(url, params, headers)` → raw text (for XML/HTML)
- `_get_json_conditional()` / `_get_text_conditional()` → the cached/304 path

**Retry contract** (shared by every source):

| Behaviour | Detail |
| --- | --- |
| Max attempts | `MAX_RETRIES = 3` |
| Backoff | `RETRY_BACKOFF = [1, 2, 4]` seconds |
| Rate limit | `await self._rate_limiter.acquire()` before each attempt, `release()` in `finally` |
| **No-retry statuses** | 401, 403, 404, 422 → return `None` immediately (auth/not-found won't fix on retry) |
| **429 handling** | reads `Retry-After` (capped at 60 s), else `RETRY_BACKOFF[attempt] * 3`; retries if attempts remain |
| Other 4xx/5xx | sleep + retry up to 3× |
| Exceptions caught | `aiohttp.ClientError`, `asyncio.TimeoutError`, and (JSON mode) `json.JSONDecodeError` |

This is why individual source files are so short — all the resilience lives here.

### 2.4 Conditional fetch (`base.py:185-245`)

`_conditional_fetch()` stores `ETag` and `Last-Modified` per `(url, params)` in the `ConditionalCache` (256-entry FIFO). On a repeat call it sends `If-None-Match` / `If-Modified-Since`; a `304 Not Modified` replays the cached body at zero parse cost. If the upstream provides no validators, it transparently degrades to a normal GET.

**Today only one source opts in: `nhs_jobs_xml`** (`feeds/nhs_jobs_xml.py:33`) — the Batch-3.5.3 pilot. Per CLAUDE.md rule #14, sources should only opt in when their upstream honours validators (CDN-fronted ATS boards, honest RSS feeds); polling a validator-less endpoint every 60 s just thrashes the cache.

### 2.5 Location filter (`base.py:32-46`)

`_is_uk_or_remote(location)` is the free UK-relevance gate every source can call:

- Empty location → `True` (unknown; don't pre-filter, let the scorer decide)
- Matches `FOREIGN_INDICATORS` (e.g. "usa", "new york", ", CA") → `False`
- Matches `UK_TERMS` or `REMOTE_TERMS` → `True`
- Default → `True` (conservative: include unknowns, let scoring penalise)

The term lists are imported from `skill_matcher.py` so the filter and the scorer agree.

### 2.6 The class attributes the rest of the engine reads

| Attribute | Default | Read by | Purpose |
| --- | --- | --- | --- |
| `name` | `"base"` | `SOURCE_REGISTRY`, `RATE_LIMITS` | Unique source id |
| `category` | `"unknown"` | `scheduler.py` | Tier key (`ats`/`rss`/`keyed_api`/`free_json`/`scrapers`/`other`) → polling cadence |
| `DOMAINS` | `{"general"}` | `domain_classifier.py` via `_build_sources()` | Which user domains this source serves; `{"general"}` = everyone |

### 2.7 The abstract contract (`base.py:96-98`)

```python
@abstractmethod
async def fetch_jobs(self) -> list[Job]:
    ...
```

That's the entire surface a new source must implement. Return a `list[Job]` or raise (the scheduler catches it and the circuit breaker records a failure).

---

## 3. The `Job` dataclass — `backend/src/models.py`

The canonical shape every source produces. ~27 fields:

### 3.1 Core fields

| Field | Type | Notes |
| --- | --- | --- |
| `title`, `company`, `apply_url`, `source`, `date_found` | str | Required |
| `location` | str | `""` default |
| `salary_min`, `salary_max` | Optional[float] | Annual GBP; **sanitised**: `<10k → None` (likely hourly), `>500k → None` (likely non-GBP/error) |
| `description` | str | Full text |
| `match_score`, `visa_flag`, `experience_level` | int/bool/str | Filled by the scorer |
| `is_new` | bool | Internal, not persisted |

### 3.2 Pillar-3 date-confidence columns (Batch 3.1)

`posted_at`, `date_confidence` (`"low"` default), `date_posted_raw` (audit), `first_seen_at`, `last_seen_at`, `staleness_state`. These power the anti-staleness recency scoring (Pillar 2 §3.1) and the ghost-detection sweep.

### 3.3 Score-breakdown columns (Step-1 B4)

`role`, `skill`, `seniority_score`, `experience`, `credentials`, `location_score`, `recency`, `semantic`, `penalty` — the per-dimension scores written by `JobScorer` (Pillar 2 §3).

### 3.4 `normalized_key()` — the dedup key (`models.py:83-87`)

```python
def normalized_key(self) -> tuple[str, str]:
    company = _COMPANY_SUFFIXES.sub("", self.company).strip()
    company = _COMPANY_REGION_SUFFIXES.sub("", company).strip().lower()
    title = self.title.strip().lower()
    return (company, title)
```

- Strips legal suffixes: `Ltd|Limited|Inc|PLC|Corp|Group|LLC|GmbH|AG|SA|Co|Holdings|Solutions|Technologies|Services|Systems|Pty`
- Strips region suffixes: `UK|US|USA|DE|SG|EU|EMEA|APAC|Global|International`
- Lowercases + trims both fields

This tuple is the DB's UNIQUE constraint and the deduplicator's Layer-1 key. **CLAUDE.md rule #1: never touch this without verifying the deduplicator and DB UNIQUE still align** — a change can cause duplicate rows or missed dedup. (`__post_init__` also HTML-unescapes title + company, so `&amp;` → `&`.)

---

## 4. The six source categories

49 classes across 6 folders. The pattern each follows is the differentiator.

### 4.1 Keyed APIs — `apis_keyed/` (7)

Pattern: accept `api_key` in `__init__`, return `[]` early with an info log if the key is empty (so the source skips gracefully on free installs).

| Source | Upstream | Env var |
| --- | --- | --- |
| Reed | reed.co.uk API | `REED_API_KEY` |
| Adzuna | Adzuna aggregator | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` |
| JSearch | RapidAPI JSearch | `JSEARCH_API_KEY` |
| Jooble | Jooble EU board | `JOOBLE_API_KEY` |
| Google Jobs | SerpApi → Google Jobs SERP | `SERPAPI_KEY` / `GOOGLE_JOBS_API_KEY` |
| Careerjet | multi-country search | `CAREERJET_AFFID` |
| Findwork | remote/freelance (Token auth) | `FINDWORK_API_KEY` |

### 4.2 Free JSON APIs — `apis_free/` (11)

Pattern: no auth, filter results with `self.relevance_keywords` on title+description, `_is_uk_or_remote()` on location.

Arbeitnow (DE/EU tech), RemoteOK (skips metadata element 0), Jobicy (remote data/AI), Himalayas (paginated remote), Remotive (remote software-dev), DevITJobs `{tech}`, Landing.jobs `{tech}`, AIJobs.net `{tech}`, HN Jobs (Firebase "Who is Hiring") `{tech}`, **Teaching Vacancies** `{education}` (Batch 3), **Gov Apprenticeships** `{education,general}` (Batch 3, 150 req/5 min).

### 4.3 ATS boards — `ats/` (12)

Pattern: accept a `companies` slug list (default from `companies.py`), iterate each company's board API. See §5 for the catalog.

Greenhouse, Lever (`createdAt` ms epoch), Workable (POST `/v2/accounts/{slug}/jobs`), Ashby (uses `publishedAt`), SmartRecruiters, Pinpoint (`{slug}.pinpointhq.com`), Recruitee (`{slug}.recruitee.com`), Workday (XML/HTML scrape, dict-config slugs), Personio (XML feed, 3 s inter-company delay), SuccessFactors (sitemap XML), **Rippling** (Batch 3, `ats.rippling.com/api/board/{slug}/jobs`), **Comeet** (Batch 3, `comeet.co/careers-api/2.0/...`).

### 4.4 RSS/XML feeds — `feeds/` (8)

Pattern: `_get_text()` + `xml.etree.ElementTree`, extract `<item>` from `<channel>`.

jobs.ac.uk `{academia}`, NHS Jobs (keyword-search XML) `{healthcare}`, **nhs_jobs_xml** (full vacancy feed, **conditional fetch pilot**) `{healthcare}` (Batch 3), WorkAnywhere (remote data/AI), WeWorkRemotely, RealWorkFromAnywhere, BioSpace `{healthcare}`, University Jobs (Cambridge + others) `{academia}`.

### 4.5 HTML scrapers — `scrapers/` (7)

Pattern: `_get_text()` + regex/embedded-JSON parsing. No auth.

LinkedIn (guest API, regex HTML fragments), JobTensor `{tech}` (AJAX), Climatebase `{climate}` (Next.js embedded JSON), 80,000 Hours (Algolia API, public keys), BCS Jobs `{tech}`, AIJobs.ai `{tech}`, AIJobs Global `{tech}` (WP Job Manager AJAX).

### 4.6 Other — `other/` (4)

Indeed/Glassdoor (`JobSpySource` wrapping `python-jobspy`, optional dep — skips with a warning if not installed; this is the one class behind two registry keys), HackerNews (Algolia "Who is Hiring"), TheMuse (paginated public API), NoFluffJobs `{tech}`.

### 4.7 Domain routing summary

`_build_sources()` calls `classify_user_domain(profile)` and keeps only sources whose `DOMAINS` overlap (or are `{"general"}`):

- **tech**: devitjobs, landingjobs, aijobs, hn_jobs, jobtensor, climatebase, bcs_jobs, aijobs_ai, aijobs_global, nofluffjobs
- **healthcare**: nhs_jobs, nhs_jobs_xml, biospace
- **academia**: jobs_ac_uk, uni_jobs
- **education**: teaching_vacancies, gov_apprenticeships
- **climate**: climatebase
- **general** (every user): all keyed aggregators + RemoteOK, Jobicy, Himalayas, Remotive, Arbeitnow, LinkedIn, 80000hours, the remote feeds, Indeed, HackerNews, TheMuse

---

## 5. The ATS company-slug catalog — `backend/src/core/companies.py`

ATS sources don't search — they poll a *known list of companies'* boards. The catalog holds ~266 companies across 12 platforms:

| Platform | Companies | Shape |
| --- | --- | --- |
| Greenhouse | ~80 | list of slug strings |
| Lever | ~35 | slug strings |
| Workable | ~25 | slug strings |
| Ashby | ~25 | slug strings |
| Recruitee | ~20 | slug strings |
| Workday | ~20 | **dicts** `{tenant, wd, site, name}` (multi-tenant URL construction) |
| Personio | ~18 | slug strings |
| Pinpoint | ~15 | slug strings |
| SmartRecruiters | ~15 | slug strings |
| Rippling | 5 | slug strings (Batch 3 starter) |
| Comeet | 5 | slug strings (Batch 3 starter) |
| SuccessFactors | 3 | **dicts** `{name, sitemap_url}` (sitemap crawl) |

A `COMPANY_NAME_OVERRIDES` dict (~77 entries) maps ugly slugs (`darktracelimited`) to display names (`Darktrace`) for the UI. Most platforms take simple slug lists; Workday and SuccessFactors need structured dicts because their URLs aren't derivable from a slug alone.

---

## 6. Cross-cutting: rate limits & the async limiter

### 6.1 `RATE_LIMITS` — `backend/src/core/settings.py:93-146`

50 entries (one per registry key), each `{source: {concurrent: int, delay: float}}`. Representative tuning:

| Source | concurrent | delay (s) | Why |
| --- | --- | --- | --- |
| reed / adzuna | 1 | 2.0 | keyed quota |
| jsearch | 1 | 3.0 | RapidAPI quota |
| linkedin / indeed / glassdoor | 1 | 3.0 | aggressive anti-scrape throttle |
| greenhouse / lever / ashby | 2 | 1.5 | public board APIs tolerate parallelism |
| personio | 1 | 3.0 | XML feed, 429-prone |
| himalayas | 2 | 1.0 | generous free API |

> This is the *in-request* concurrency/backoff surface. The *between-runs* polling cadence is separate — that's `scheduler.TIER_INTERVALS_SECONDS` (Pillar 2 §4.1). Don't conflate them.

### 6.2 `RateLimiter` — `backend/src/utils/rate_limiter.py`

```python
class RateLimiter:
    def __init__(self, concurrent=2, delay=1.0):
        self._semaphore = asyncio.Semaphore(concurrent)
        self._delay = delay
```

- `acquire()` — await the semaphore, then sleep `delay` (releases on `CancelledError` before re-raising)
- `release()` — release the semaphore (no delay)
- Usable as `async with limiter:`

Effect: at most `concurrent` parallel requests, with a minimum `delay` between acquisitions on that source.

---

## 7. The Batch-3 source rotation (the most recent change)

Batch 3 rotated the roster: **−3 dropped, +5 added**, net 48 → 50 registry keys.

### Dropped (verified absent from disk)

| Source | Was in | Why dropped |
| --- | --- | --- |
| `findajob` | feeds/ | Duplicate of Adzuna's coverage |
| `nomis` | other/ | ONS *statistics* endpoint — vacancy counts, not individual listings |
| `yc_companies` | apis_free/ | Already covered by HN Jobs + Ashby ATS |

### Added (verified present on disk)

| Source | Folder | Upstream | Tier |
| --- | --- | --- | --- |
| `teaching_vacancies` | apis_free/ | gov.uk Teaching Vacancies API (schema.org JobPosting, `datePosted` → high confidence) | rss (15 min) |
| `gov_apprenticeships` | apis_free/ | GOV.UK Find an Apprenticeship API (150 req/5 min) | rss (15 min) |
| `nhs_jobs_xml` | feeds/ | NHS full `all_current_vacancies.xml` (**additive** — coexists with keyword-search `nhs_jobs`) | rss (15 min) |
| `rippling` | ats/ | `ats.rippling.com/api/board/{slug}/jobs` | ats (60 s) |
| `comeet` | ats/ | `comeet.co/careers-api/2.0/company/{slug}/positions` | ats (60 s) |

### The five load-bearing surfaces (CLAUDE.md rule #13)

Adding/removing a source means moving **all five** together, or tests break:

1. `src/main.py` — `SOURCE_REGISTRY` dict + `_build_sources()` list
2. `src/core/settings.py` — `RATE_LIMITS` dict
3. `tests/test_cli.py` — `len(SOURCE_REGISTRY) == 50` + the expected set
4. `tests/test_api.py` — three `== 50` checks (`test_sources_returns_*`, `test_status_returns_counts`, `test_full_api_workflow`)
5. `CLAUDE.md` — the documented count

All five are currently aligned at **50**.

---

## 8. Testing — `backend/tests/test_sources.py` + friends

- **`test_sources.py`** — 81 test functions covering all 50 keys. All HTTP mocked with `aioresponses` (rule #4 — the suite must run offline). A typical source test asserts: returns `list[Job]`, parses fields into the `Job` model, filters non-UK locations, handles an empty response (`jobs == []`), and (keyed sources) returns `[]` when the API key is `""`. Batch-3 sources have 3 tests each (`test_sources.py:1561-1688`): parse / empty / http-error.
- **`test_conditional_fetch.py`** — 11 tests for the shared ETag/Last-Modified/304 machinery, FIFO eviction at 256 entries, and the `nhs_jobs_xml` pilot proving `If-None-Match` is sent on the second call.
- **`test_cli.py`** — `len(SOURCE_REGISTRY) == 50` + exact expected set.
- **`test_api.py`** — the three hardcoded `== 50` assertions.

There are **no** separate `test_ats*.py` / `test_feed*.py` files — all source tests live inline in `test_sources.py`.

---

## 9. Current status — what works, what's incomplete

Legend: ✅ done & wired · 🟡 partial · ❌ planned but not built · ⚠️ subtle gap

### 9.1 Base machinery

| Surface | Status | Notes |
| --- | --- | --- |
| `BaseJobSource` retry (3×, backoff 1/2/4) | ✅ | `base.py:100-155` |
| Per-source rate limiting via `RATE_LIMITS` | ✅ | 50 entries, all sources covered |
| 429 `Retry-After` honouring (cap 60 s) | ✅ | |
| No-retry on 401/403/404/422 | ✅ | |
| Conditional fetch (ETag/304) infrastructure | ✅ | `_get_json_conditional` / `_get_text_conditional` |
| Conditional-fetch **adoption** | 🟡 | only `nhs_jobs_xml` opts in today — rule #14 pilot |
| Dynamic-keyword properties (config/fallback) | ✅ | but fallback defaults are now empty (`keywords.py`) |
| `_is_uk_or_remote()` location gate | ✅ | shares term lists with the scorer |
| `Job.normalized_key()` dedup key | ✅ | rule #1 protected |
| Salary sanitisation (<10k / >500k → None) | ✅ | `models.py:69-72` |
| HTML entity unescape in title/company | ✅ | `__post_init__` |

### 9.2 Source roster

| Surface | Status | Notes |
| --- | --- | --- |
| 49 source classes / 50 registry keys / 49 instances | ✅ | reconciled in §1 |
| 7 keyed APIs (skip gracefully without key) | ✅ | |
| 11 free JSON APIs | ✅ | |
| 12 ATS boards over ~266 company slugs | ✅ | `companies.py` |
| 8 RSS/XML feeds | ✅ | |
| 7 HTML scrapers | ✅ | regex/embedded-JSON — brittle by nature ⚠️ |
| 4 other (incl. optional jobspy) | ✅ | jobspy skips with warning if uninstalled |
| Batch-3 rotation (−3, +5) | ✅ | verified on disk |
| Domain tagging via `.DOMAINS` | ✅ | 5 domains + general |
| Per-source tier categorisation (`.category`) | ✅ | drives scheduler cadence |

### 9.3 Known fragilities & gaps

| Item | Status | Notes |
| --- | --- | --- |
| HTML scrapers break when sites change markup | ⚠️ | inherent to LinkedIn/Workday/BCS/AIJobs regex parsing — no schema contract upstream |
| Rippling/Comeet slug lists are 5-company starters | 🟡 | need expansion to be impactful |
| ATS catalog is hand-curated | 🟡 | no auto-discovery of new company boards |
| Conditional fetch used by only 1 of ~16 eligible feeds/ATS | 🟡 | rule #14 — opportunity to reduce upstream load |
| University Jobs — only Cambridge feed confirmed valid | ⚠️ | other uni feeds may silently return nothing |
| `python-jobspy` not in `requirements.txt` | ✅-by-design | optional; Indeed/Glassdoor skip if absent |
| Per-source health/uptime dashboard | ❌ | breaker state is logged per-run but not surfaced in UI |
| Source-level dedup of overlapping aggregators | ✅ | handled downstream by the 4-layer deduplicator (Pillar 2 §4) |

---

## 10. Quick reference — every file in the Providers pillar

```
backend/src/sources/
├── base.py                         — BaseJobSource: retry, rate-limit, conditional fetch, _is_uk_or_remote
├── apis_keyed/   (7)               — reed, adzuna, jsearch, jooble, google_jobs, careerjet, findwork
├── apis_free/    (11)              — arbeitnow, remoteok, jobicy, himalayas, remotive, devitjobs,
│                                     landingjobs, aijobs, hn_jobs, teaching_vacancies*, gov_apprenticeships*
├── ats/          (12)              — greenhouse, lever, workable, ashby, smartrecruiters, pinpoint,
│                                     recruitee, workday, personio, successfactors, rippling*, comeet*
├── feeds/        (8)               — jobs_ac_uk, nhs_jobs, nhs_jobs_xml*, workanywhere, weworkremotely,
│                                     realworkfromanywhere, biospace, uni_jobs
├── scrapers/     (7)               — linkedin, jobtensor, climatebase, eightykhours, bcs_jobs,
│                                     aijobs_ai, aijobs_global
└── other/        (4)               — indeed (JobSpySource → indeed+glassdoor), hackernews, themuse, nofluffjobs
                                       (* = added in Batch 3)

backend/src/
├── models.py                       — Job dataclass + normalized_key() (rule #1)
├── main.py:106-318                 — SOURCE_REGISTRY (50 keys) + _build_sources() + domain filter
├── core/
│   ├── companies.py                — ~266 ATS slugs across 12 platforms + name overrides
│   ├── settings.py:93-146          — RATE_LIMITS (50 entries)
│   └── keywords.py                 — LOCATIONS + VISA_KEYWORDS (the rest emptied 2026-04-09)
└── utils/rate_limiter.py           — async semaphore + delay

backend/tests/
├── test_sources.py                 — 81 tests, all sources, aioresponses-mocked
├── test_conditional_fetch.py       — 11 tests, ETag/304/FIFO + nhs_jobs_xml pilot
├── test_cli.py                     — len(SOURCE_REGISTRY) == 50
└── test_api.py                     — three == 50 assertions
```

---

## 11. What this pillar does *not* cover

- **What happens to a job after `fetch_jobs()` returns it** — scoring, dedup, enrichment, storage → Pillar 2 (Search & Match Engine).
- **The scheduler/circuit-breaker that decide *when* to call each source** — documented in Pillar 2 §4 (they wrap the providers but are engine infrastructure).
- **How a stored job reaches a user** — feed, notifications, dashboard → Pillar 1 (User Side).

---

## 12. Architectural rules touched by this pillar

- **#1** — never touch `normalized_key()` without verifying deduplicator + DB UNIQUE.
- **#2** — never change `BaseJobSource` (constructor, properties, retry, HTTP helpers) without checking all 49 subclasses.
- **#4** — always mock HTTP in tests (`aioresponses`); the suite runs offline.
- **#8 / #13** — adding/removing a source touches FIVE surfaces (registry, build list, rate limits, `test_cli.py`, `test_api.py`) plus CLAUDE.md.
- **#14** — conditional fetch (`_get_json_conditional`) only for upstreams that honour ETag/Last-Modified.
- **#15** — new sources MUST set `.category` to a scheduler tier key (or add a `NAME_TIER` override).

---

*Last updated 2026-05-28. HEAD `a7a2268`. Source roster verified on disk: 49 classes / 50 registry keys / 49 instances. Backend test baseline 600p/0f/3s.*
