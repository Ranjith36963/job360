<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Pillar 3 — Job Providers

> **Audience.** Read this if you want to understand where Job360's raw job postings actually come from — the 40 source classes, the one base class they all inherit, the shared retry/rate-limit/conditional-fetch machinery, the ATS company-slug catalog, and how a posting becomes a normalised `Job` row in the shared catalog.
>
> **Scope.** Covers code on `main`, post-Batch-3 source rotation and the **M6 rotation (2026-06)** that dropped 4 upstream-dead sources (jobtensor, comeet, gov_apprenticeships, aijobs_global) — gov_apprenticeships was later restored 2026-06-16 on the DfE Display Advert API v2. Current canonical counts: **40 classes / 41 registry keys / 40 instances**. Counts in this doc were verified against the files on disk and the test assertions.

---

## 1. TL;DR — what the providers pillar does

> *Job360 talks to 40 distinct job-source classes — keyed aggregators, free JSON APIs, company ATS boards, RSS/XML feeds, HTML scrapers, and a few one-offs. Every one inherits a single `BaseJobSource` that gives it retry-with-backoff, per-source rate limiting, optional conditional (ETag/304) fetching, and a UK/remote location filter for free. Each source's only real job is to implement one async method — `fetch_jobs() -> list[Job]` — turning whatever shape the upstream returns into the canonical `Job` dataclass. The orchestrator (Pillar 2) then scores, dedups, and stores them.*

### The count reconciliation (read this once, never wonder again)

Three numbers float around the codebase and they're all correct because they count different things:

| Number | What it counts | Where |
| --- | --- | --- |
| **40** | Source *class files* on disk | `find backend/src/sources -name '*.py'` minus `__init__`/`base` |
| **41** | *Registry keys* in `SOURCE_REGISTRY` | `main.py` — `"glassdoor"` is a second key that aliases `JobSpySource` |
| **40** | Live *instances* built per run | `SOURCE_INSTANCE_COUNT = 40` (`main.py:168`) — `indeed`+`glassdoor` collapse to one `JobSpySource` |

So: **40 classes → 41 registry keys → 40 instances.** The single fork is Indeed/Glassdoor, both handled by `JobSpySource` in `other/indeed.py`. The test suite pins all of this — `test_cli.py:55` asserts `len(SOURCE_REGISTRY) == 41`, `test_api.py` asserts `sources_total == 41` at `:43` and `:160` (CLAUDE.md rule #13). Measure these, never quote them: six sources were pruned on 2026-08-10 (`main.py:161`) and this table said 46/47/46 for a week afterwards.

---

## Walkthrough — One source's fetch cycle (worked example)

> Trace exactly what happens from the scheduler deciding it's time to poll `greenhouse` to a `Job` row landing back in the orchestrator. Uses Greenhouse because it's the cleanest of the ATS sources, but the same shape applies to all 40.

### T+0 — Scheduler decides

Inside `TieredScheduler.tick()`:

1. `now = 2026-05-28T14:30:00Z`. The scheduler iterates its sources.
2. For `greenhouse`: `category="ats"` → `TIER_INTERVALS_SECONDS["ats"] = 60` seconds.
3. `last_tick_at = 14:29:00` (one minute ago). `now - last_tick_at = 60 s` ≥ 60 s → **due**.
4. `default_registry().get("greenhouse").can_proceed()` — breaker state is `CLOSED` → ✅ proceed.
5. The source is added to the `asyncio.gather()` batch.

### T+0 — Source `__init__`

The instance was built earlier in `_build_sources()` with:

```python
GreenhouseSource(session=shared_aiohttp_session, search_config=alice_search_config)
```

In `BaseJobSource.__init__`:
- `self._session = shared_aiohttp_session`
- `self._search_config = alice_search_config` (so `self.relevance_keywords` returns Alice's dynamic keywords)
- `RATE_LIMITS["greenhouse"] = {"concurrent": 2, "delay": 1.5}` → `RateLimiter(concurrent=2, delay=1.5)`
- `ConditionalCache()` instantiated (unused — see §2.4: no source opts in today)

### T+0 — `fetch_jobs()` runs

```python
async def fetch_jobs(self) -> list[Job]:
    jobs = []
    for slug in self._companies:             # GREENHOUSE_COMPANIES by default — 82 slugs
        # `?content=true` is LOAD-BEARING, not decoration: without it the board
        # list endpoint returns no `content` field at all, which is why 996 prod
        # rows carried an empty description until 2026-08-05 (greenhouse.py:31-36).
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        data = await self._get_json(url)     # all retry/rate-limit machinery
        if not data:
            continue
        for posting in data.get("jobs", []):
            ...                              # build Job(...)
            jobs.append(job)
    return jobs
```

Each `_get_json(url)` call goes through `_request()`:

1. `await self._rate_limiter.acquire()` — at most 2 concurrent requests across all 82 companies; 1.5 s minimum delay between acquisitions.
2. `aiohttp.GET(url, timeout=30)`.
3. Response handling:
   - `200` → `response.json()`, return.
   - `429` → read `Retry-After` (capped 60 s), sleep, retry. Up to 3 attempts.
   - `401/403/404/422` → `return None` immediately (no retries — auth/not-found won't fix on retry).
   - Other 4xx/5xx → sleep `RETRY_BACKOFF[attempt]` (1/2/4 s), retry up to 3×.
   - `aiohttp.ClientError` / `TimeoutError` / `JSONDecodeError` → same backoff retry.
4. `self._rate_limiter.release()` in `finally`.

For one healthy company (say `acme-corp`), this returns ~6 postings in JSON like:

```json
{
  "jobs": [
    {"id": 12345, "title": "Senior Python Engineer",
     "location": {"name": "London, UK"},
     "absolute_url": "https://boards.greenhouse.io/acme-corp/jobs/12345",
     "content": "&lt;p&gt;We're hiring...&lt;/p&gt;",
     "first_published": "2026-05-20T11:30:00Z",
     "updated_at": "2026-05-28T09:00:00Z"},
    ...
  ]
}
```

### T+0 — Per-posting transformation

For each upstream posting, the source builds a canonical `Job`:

```python
# The date contract, established BEFORE the Job is built (greenhouse.py:64-68).
# posted_at comes from `first_published`, NOT `updated_at` — the latter tracks
# edits (a salary tweak) and would bump a stale posting back into the "just
# posted" bucket. normalize_posted_at() returns the confidence alongside it, so
# an unparseable value is reported low rather than fabricated as "high".
raw_updated_at = posting.get("updated_at")          # audit only, never recency
raw_published = posting.get("first_published")
posted_at, confidence = normalize_posted_at(raw_published)

job = Job(
    title=posting["title"],
    company="acme-corp",  # or via COMPANY_NAME_OVERRIDES → "Acme Corp"
    apply_url=posting["absolute_url"],
    source="greenhouse",
    location=posting.get("location", {}).get("name", ""),
    description=_strip_html(posting["content"]),  # raw HTML → text
    date_found=now_iso(),
    posted_at=posted_at,
    date_confidence=confidence,                    # derived, never hardcoded
    date_posted_raw=raw_updated_at,
)
```

Two things the `Job.__post_init__` does automatically:
- HTML-unescapes title + company (`&amp;` → `&`).
- Sanitises salary fields (none here, but if present: `<10k → None`, `>500k → None`).

### T+0 — Return + scheduler post-processing

`await GreenhouseSource.fetch_jobs()` returns a `list[Job]` gathered across 82 companies.
Volume is an upstream fact, not a constant, so it is quoted only as a dated measurement:
**996 greenhouse rows in prod** as of 2026-08-05 (`backend/src/sources/ats/greenhouse.py:34`),
and `first_published` verified across **928 live jobs** (`greenhouse.py:57-58`).

Back in `TieredScheduler.tick()`:

```python
results = await asyncio.gather(*coros, return_exceptions=True)
for source, result in zip(sources, results):
    if isinstance(result, Exception):
        registry.get(source.name).record_failure()
        log.warning(f"source={source.name} failed: {result}")
    else:
        registry.get(source.name).record_success()
```

`record_success()` resets `consecutive_failures = 0`, state stays `CLOSED`. If five `record_failure()`s in a row had happened: `state = OPEN`, `opened_at = now()`. After 300 s `can_proceed()` would promote to `HALF_OPEN` for a probe call.

### T+0 — Hand-off to orchestrator

`run_search()` collects all sources' returns into `all_jobs: list[Job]` and proceeds to Pillar 2 stages 2–6 (prefilter → score → dedup → enrich → store). See Pillar 2 §2 for that side.

### What ran in parallel

For one tick of the scheduler:

- ~10 ATS sources (each with 5–80 slugs) firing in parallel batches limited by their per-source `concurrent` rate-limiter.
- A mix of keyed/free APIs/RSS feeds also dispatched if their tier intervals elapsed.
- Each source's HTTP calls are serialised at the source level by the semaphore but parallel across sources.
- Total wall-clock: typically 30–120 s depending on the slowest source.

### A more interesting variant — a misbehaving source

If LinkedIn's HTML regex breaks because they changed markup:

1. `linkedin.fetch_jobs()` raises an exception inside one of its regex parses.
2. The exception propagates out (not caught by `_request()` — that handles HTTP layer only).
3. `asyncio.gather(return_exceptions=True)` captures it.
4. `breaker.record_failure()` → `consecutive_failures = 1`. After 5 ticks of this it'd flip to OPEN.
5. The orchestrator's run log shows `per_source_errors["linkedin"] = 1`, `per_source_duration["linkedin"] = 0.2`.
6. Engineer's first stop: `grep "source=linkedin" data/logs/job360.log | tail -50` and the linkedin.py regex.

---

## 2. The base class — `backend/src/sources/base.py`

Every source extends `BaseJobSource`. **Never change this class without checking all 40 subclasses** (CLAUDE.md rule #2) — every change propagates to every source.

### 2.1 Constructor (`base.py:98-105`)

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
- Rate limiter is pulled per-source from `RATE_LIMITS` (41 entries) with a safe `{concurrent:2, delay:1.0}` default.

### 2.2 The four dynamic properties (`base.py:107-137`)

This is how a single source body serves both "profile loaded" and "no profile" cases without branching:

| Property | Returns when `search_config` set | Returns when `None` |
| --- | --- | --- |
| `relevance_keywords` | `search_config.relevance_keywords` | `keywords.RELEVANCE_KEYWORDS` (empty post-3ba1342) |
| `job_titles` | `search_config.job_titles` | `keywords.JOB_TITLES` (empty) |
| `search_titles` | `search_config.search_titles` if non-empty | falls back to `job_titles` |
| `search_queries` | `search_config.search_queries` if non-empty | `[]` |

> `search_titles` is what a source may put in a *search request*; `job_titles` is the scorer's *evidence* list and holds raw CV strings no job board indexes. Don't swap them.

> Sources MUST access keywords through these properties — never `from src.core.keywords import ...` directly. This is what makes the system domain-agnostic (CLAUDE.md "Dynamic keywords" pattern).

### 2.3 HTTP helpers + retry machinery (`base.py:150-266`)

The core is `_request()` (`base.py:150-216`). Everything else is a thin wrapper:

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

### 2.4 Conditional fetch (`base.py:244-331`)

`_conditional_fetch()` stores `ETag` and `Last-Modified` per `(url, params)` in the `ConditionalCache` (256-entry FIFO). On a repeat call it sends `If-None-Match` / `If-Modified-Since`; a `304 Not Modified` replays the cached body at zero parse cost. If the upstream provides no validators, it transparently degrades to a normal GET.

**Today NO source opts in.** The only callers of `_get_json_conditional` / `_get_text_conditional` anywhere are `backend/tests/test_conditional_fetch.py` — the Batch-3.5.3 `nhs_jobs` pilot was reverted to a plain `_get_text` (`feeds/nhs_jobs.py:34`), and there is no `nhs_jobs_xml.py`. Per CLAUDE.md rule #14, sources should only opt in when their upstream honours validators (CDN-fronted ATS boards, honest RSS feeds); polling a validator-less endpoint every 60 s just thrashes the cache.

### 2.5 Location filter (`base.py:39-81`)

`_is_uk_or_remote(location)` is the free UK-relevance gate every source can call:

- Empty location → `True` (unknown; don't pre-filter, let the door decide)
- Markup, a newline, or anything longer than 120 chars → `True` (that is prose, not a location)
- `uk_gate.names_foreign_place` says the whole trimmed value NAMES a foreign country/admin division → `False`
- Anything else (UK, remote, unknown) → `True`; the door (`uk_gate.check_uk`) decides at ingestion

`base.py` imports `names_foreign_place` straight from `src.services.uk_gate` (`base.py:16`) — it holds **no term list of its own**, and it no longer imports anything from `skill_matcher.py`. There is also no scorer penalty to fall back on: the −15 foreign penalty was deleted 2026-08-12 (CLAUDE.md rule #30). One gate, one data set.

### 2.6 The class attributes the rest of the engine reads

| Attribute | Default | Read by | Purpose |
| --- | --- | --- | --- |
| `name` | `"base"` | `SOURCE_REGISTRY`, `RATE_LIMITS` | Unique source id |
| `category` | `"unknown"` | `scheduler.py` | Tier key (`ats`/`rss`/`keyed_api`/`free_json`/`scrapers`/`other`) → polling cadence |
| `DOMAINS` | `{"general"}` | `domain_classifier.py` via `_build_sources()` | Which user domains this source serves; `{"general"}` = everyone |

### 2.7 The abstract contract (`base.py:146-148`)

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

### 3.4 `normalized_key()` — the dedup key (`models.py:106-127`)

```python
def normalized_key(self) -> tuple[str, str]:
    company = _COMPANY_SUFFIXES.sub("", self.company).strip()
    company = _COMPANY_REGION_SUFFIXES.sub("", company).strip().lower()
    # Collapse internal whitespace runs (rule #1) — "Software  Engineer"
    # and "Software Engineer" must produce the SAME key.
    company = re.sub(r"\s+", " ", company)
    title = re.sub(r"\s+", " ", self.title.strip().lower())
    # Cap each component at _KEY_COMPONENT_MAX (300). Found live 2026-07-30:
    # one scraped title blew Postgres's btree index-row limit and that ONE
    # poison row aborted the whole catalog insert.
    return (company[:_KEY_COMPONENT_MAX], title[:_KEY_COMPONENT_MAX])
```

- Strips legal suffixes: `Ltd|Limited|Inc|PLC|Corp|Group|LLC|GmbH|AG|SA|Co|Holdings|Solutions|Technologies|Services|Systems|Pty`
- Strips region suffixes: `UK|US|USA|DE|SG|EU|EMEA|APAC|Global|International`
- Lowercases + trims both fields

This tuple is the DB's UNIQUE constraint and the deduplicator's Layer-1 key. **CLAUDE.md rule #1: never touch this without verifying the deduplicator and DB UNIQUE still align** — a change can cause duplicate rows or missed dedup. (`__post_init__` also HTML-unescapes title + company, so `&amp;` → `&`.)

---

## 4. The six source categories

40 classes across 6 folders. The pattern each follows is the differentiator.

### 4.1 Keyed APIs — `apis_keyed/` (8)

Pattern: accept `api_key` in `__init__`, return `[]` early if the key is empty (so the source skips gracefully on free installs). The log line is `WARNING` in seven of the eight — `gov_apprenticeships.py:62` is the only `INFO`.

| Source | Upstream | Env var |
| --- | --- | --- |
| Reed | reed.co.uk API | `REED_API_KEY` |
| Adzuna | Adzuna aggregator | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` |
| JSearch | RapidAPI JSearch | `JSEARCH_API_KEY` |
| Jooble | Jooble EU board | `JOOBLE_API_KEY` |
| Google Jobs | SerpApi → Google Jobs SERP | `SERPAPI_KEY` |
| Careerjet | multi-country search | `CAREERJET_AFFID` |
| Findwork | remote/freelance (Token auth) | `FINDWORK_API_KEY` |
| Gov Apprenticeships | DfE Display Advert API v2 (restored 2026-06-16) | `DFE_APPRENTICESHIPS_API_KEY` |

### 4.2 Free JSON APIs — `apis_free/` (9 files; 8 declare `category="free_json"`, `teaching_vacancies` declares `rss`)

Pattern: no auth, filter results with `self.relevance_keywords` on title+description, `_is_uk_or_remote()` on location.

Arbeitnow (DE/EU tech), RemoteOK (skips metadata element 0), Jobicy (remote data/AI), Himalayas (paginated remote), Remotive (remote software-dev), DevITJobs `{tech}`, Landing.jobs `{tech}`, AIJobs.net `{tech}`, HN Jobs (Firebase "Who is Hiring") `{tech}`, **Teaching Vacancies** `{education}` (Batch 3). _(Gov Apprenticeships was dropped in M6 rotation then restored 2026-06-16 as a keyed API — see §4.1.)_

### 4.3 ATS boards — `ats/` (10)

Pattern: accept a `companies` slug list (default from `companies.py`), iterate each company's board API. See §5 for the catalog.

Greenhouse, Lever (`createdAt` ms epoch), Workable (POST `/v2/accounts/{slug}/jobs`), Ashby (uses `publishedAt`), SmartRecruiters, Pinpoint (`{slug}.pinpointhq.com`), Recruitee (`{slug}.recruitee.com`), Workday (XML/HTML scrape, dict-config slugs), Personio (XML feed, 3 s inter-company delay), SuccessFactors (sitemap XML). _(Comeet dropped in M6 rotation; **Rippling** dropped in the 2026-08-10 rotation — `RIPPLING_COMPANIES` survives in `companies.py` but no source class reads it.)_

### 4.4 RSS/XML feeds — `feeds/` (4)

Pattern: `_get_text()` + `xml.etree.ElementTree`, extract `<item>` from `<channel>`.

NHS Jobs (keyword-search XML) `{healthcare}`, WeWorkRemotely, RealWorkFromAnywhere, University Jobs (Cambridge + others) `{academia}`. _(Dropped 2026-08-10 as dead upstreams: `jobs_ac_uk`, `nhs_jobs_xml`, `workanywhere`, `biospace`.)_ Note `teaching_vacancies` is `category = "rss"` but lives in `apis_free/` — folder ≠ tier (rule #15).

### 4.5 HTML scrapers — `scrapers/` (5)

Pattern: `_get_text()` + regex/embedded-JSON parsing. No auth.

LinkedIn (guest API, regex HTML fragments), Climatebase `{climate}` (Next.js embedded JSON), 80,000 Hours (Algolia API, public keys), BCS Jobs `{tech}`, AIJobs.ai `{tech}`. _(JobTensor and AIJobs Global dropped in M6 rotation — upstream-dead.)_

### 4.6 Other — `other/` (4)

Indeed/Glassdoor (`JobSpySource` wrapping `python-jobspy`, optional dep — skips with a warning if not installed; this is the one class behind two registry keys), HackerNews (Algolia "Who is Hiring"), TheMuse (paginated public API), NoFluffJobs `{tech}`.

### 4.7 Domain routing summary

`_build_sources()` calls `classify_user_domain(profile)` and keeps only sources whose `DOMAINS` overlap (or are `{"general"}`):

- **tech**: devitjobs, landingjobs, hn_jobs, climatebase, bcs_jobs, aijobs_ai, nofluffjobs
- **healthcare**: nhs_jobs
- **academia**: uni_jobs
- **education**: teaching_vacancies
- **climate**: climatebase
- **general** (every user): all keyed aggregators + RemoteOK, Jobicy, Himalayas, Remotive, Arbeitnow, LinkedIn, 80000hours, the remote feeds, Indeed, HackerNews, TheMuse

---

## 5. The ATS company-slug catalog — `backend/src/core/companies.py`

ATS sources don't search — they poll a *known list of companies'* boards. The catalog holds 302 slugs across 11 platform lists (one of them, `RIPPLING_COMPANIES`, has no source class since the 2026-08-10 rotation):

| Platform | Companies | Shape |
| --- | --- | --- |
| Greenhouse | 82 | list of slug strings |
| Pinpoint | 39 | slug strings |
| Lever | 35 | slug strings |
| Recruitee | 31 | slug strings |
| Personio | 26 | slug strings |
| Ashby | 25 | slug strings |
| Workable | 21 | slug strings |
| Workday | 20 | **dicts** `{tenant, wd, site, name}` (multi-tenant URL construction) |
| SmartRecruiters | 15 | slug strings |
| ~~Rippling~~ | 5 | slug strings — **no source class since 2026-08-10**; the list is not polled |
| SuccessFactors | 3 | **dicts** `{name, sitemap_url}` (sitemap crawl) |

Total **302**. Counts are exact AST counts of the list literals, not estimates — every `~N` in this table was wrong by up to 24 (Pinpoint read `~15` against a real 39).

A `COMPANY_NAME_OVERRIDES` dict (55 entries) maps ugly slugs (`darktracelimited`) to display names (`Darktrace`) for the UI. Most platforms take simple slug lists; Workday and SuccessFactors need structured dicts because their URLs aren't derivable from a slug alone.

---

## 6. Cross-cutting: rate limits & the async limiter

### 6.1 `RATE_LIMITS` — `backend/src/core/settings.py:279-336`

41 entries (one per registry key), each `{source: {concurrent: int, delay: float}}`. Representative tuning:

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

## 7. Source rotations (Batch 3, then M6)

Batch 3 rotated the roster: **−3 dropped, +5 added**, net 48 → 50 registry keys. The later **M6 rotation (2026-06)** then dropped 4 upstream-dead sources (jobtensor, comeet, gov_apprenticeships, aijobs_global), taking the registry **50 → 46** — gov_apprenticeships was later restored 2026-06-16 on the DfE Display Advert API v2, bringing it back to **47**. A further prune on **2026-08-10** dropped six upstream-dead sources — aijobs, jobs_ac_uk, biospace, rippling, nhs_jobs_xml, workanywhere — taking the registry **47 → 41** (`main.py:158-165` records each reason). Note `nhs_jobs_xml` was retired but the separate **`nhs_jobs` source is still alive** and registered at `main.py:142`. The Batch-3 tables below are kept as history; the M6 drops are flagged inline.

### Dropped (verified absent from disk)

| Source | Was in | Why dropped |
| --- | --- | --- |
| `findajob` | feeds/ | Duplicate of Adzuna's coverage |
| `nomis` | other/ | ONS *statistics* endpoint — vacancy counts, not individual listings |
| `yc_companies` | apis_free/ | Already covered by HN Jobs + Ashby ATS |

### Added in Batch 3 (struck-through rows have since been removed from disk)

| Source | Folder | Upstream | Tier |
| --- | --- | --- | --- |
| `teaching_vacancies` | apis_free/ | gov.uk Teaching Vacancies API (schema.org JobPosting, `datePosted` → high confidence) | rss (15 min) |
| `gov_apprenticeships` | apis_free/ | GOV.UK Find an Apprenticeship API (150 req/5 min) | rss (15 min) — **later dropped in M6** |
| ~~`nhs_jobs_xml`~~ | feeds/ | NHS full `all_current_vacancies.xml` | rss (15 min) — **dropped 2026-08-10**, upstream serves HTML not XML |
| ~~`rippling`~~ | ats/ | `ats.rippling.com/api/board/{slug}/jobs` | ats (60 s) — **dropped 2026-08-10**, upstream dead |
| `comeet` | ats/ | `comeet.co/careers-api/2.0/company/{slug}/positions` | ats (60 s) — **later dropped in M6** |

### The five load-bearing surfaces (CLAUDE.md rule #13)

Adding/removing a source means moving **all five** together, or tests break:

1. `src/main.py` — `SOURCE_REGISTRY` dict + `_build_sources()` list
2. `src/core/settings.py` — `RATE_LIMITS` dict
3. `tests/test_cli.py` — `len(SOURCE_REGISTRY) == 41` + the expected set
4. `tests/test_api.py` — two `== 41` checks (`:43`, `:160`)
5. `CLAUDE.md` — the documented count

All five are currently aligned at **41**.

---

## 8. Testing — `backend/tests/test_sources.py` + friends

- **`test_sources.py`** — 110 test functions covering all 41 keys. All HTTP mocked with `aioresponses` (rule #4 — the suite must run offline). A typical source test asserts: returns `list[Job]`, parses fields into the `Job` model, filters non-UK locations, handles an empty response (`jobs == []`), and (keyed sources) returns `[]` when the API key is `""`. Newer sources follow a 3-test shape: parse / empty / http-error. (The old `test_sources.py:1561-1688` citation is dropped — that range is Greenhouse's tests today; the file has grown to 3,196 lines and any fixed range here rots within weeks.)
- **`test_conditional_fetch.py`** — 13 tests for the shared ETag/Last-Modified/304 machinery and FIFO eviction at 256 entries. They drive a throwaway `BaseJobSource` subclass, not a real source: these tests are the **only** callers of the conditional helpers in the repo.
- **`test_cli.py`** — `len(SOURCE_REGISTRY) == 41` + exact expected set.
- **`test_api.py`** — the two hardcoded `== 41` assertions.

There are **no** separate `test_ats*.py` / `test_feed*.py` files — all source tests live inline in `test_sources.py`.

---

## Environment variables — every var the Providers pillar reads

Almost all are the keyed-source API credentials. The other **33** registry keys need no env at all — 41 keys minus the 8 keyed sources (README's "API Key Setup" states the same split).

| Var | Required by | Default | What changes when you flip it |
| --- | --- | --- | --- |
| `REED_API_KEY` | `ReedSource` | (unset) | Reed `return []` silently when unset; logged at INFO |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | `AdzunaSource` | (unset) | Both must be set; either unset → return [] |
| `JSEARCH_API_KEY` | `JSearchSource` | (unset) | RapidAPI key |
| `JOOBLE_API_KEY` | `JoobleSource` | (unset) | |
| `SERPAPI_KEY` | `GoogleJobsSource` | (unset) | SerpApi → Google Jobs SERP. **Only this name works** — `settings.py:45` reads `SERPAPI_KEY` alone and `main.py:279` passes it straight in; there is no `GOOGLE_JOBS_API_KEY` alias anywhere. Set the wrong name and the source skips, logging `GoogleJobs: no SERPAPI_KEY, skipping` at WARNING (`google_jobs.py:47`) — visible in the run log, but the run still reports success |
| `CAREERJET_AFFID` | `CareerjetSource` | (unset) | Affiliate ID |
| `FINDWORK_API_KEY` | `FindworkSource` | (unset) | Token auth |
| `GITHUB_TOKEN` | (none directly, but used by `github_enricher` in Pillar 1) | (unset) | Anonymous GitHub API has 60 req/hr; token raises to 5000 |
| `EIGHTYKHOURS_ALGOLIA_APP_ID` / `EIGHTYKHOURS_ALGOLIA_API_KEY` | `EightyKHoursSource` | hard-coded public keys | Allow override of the (public) Algolia search keys 80,000 Hours embeds in their site |
| (per-source rate-limit knobs) | All sources | from `RATE_LIMITS` dict in `settings.py` | Not env-configurable; edit code |

> **All sources skip gracefully without their key**: the keyed-source pattern is `if not api_key: return []` with a **`WARNING`** log line — Adzuna, Careerjet, Findwork, GoogleJobs, Jooble, JSearch and Reed all use `logger.warning`; `gov_apprenticeships.py:62` is the lone `INFO`. The pipeline never errors — sources just don't contribute, and **the run still reports success**, so a missing key is only visible in the log.

---

## Failure modes — when things go wrong

| Symptom | Most likely cause | Where it surfaces | Fix |
| --- | --- | --- | --- |
| One source returns 0 jobs every run | (1) Env var unset for keyed source; (2) HTML markup change for scraper; (3) breaker stuck OPEN within the process | Source's `INFO` log + missing entries in `run_log.per_source` | Decision tree: check env, then `grep "source=X" data/logs/job360.log` for exceptions, then restart process to reset breaker |
| LinkedIn returns 0 jobs / 403 | Anti-scrape throttle hit, or markup changed | `linkedin.py` regex returns no matches | Inspect a fresh response by hand; adjust regex; the source pattern is brittle by design (rule #8 acknowledges this) |
| Workday job count drops by 80%+ | One tenant's URL config (in `WORKDAY_COMPANIES` dict) became wrong | Per-tenant fetch fails | `WORKDAY_COMPANIES` slugs are dicts (`{tenant, wd, site, name}`) — the dict shape needs all four right; companies sometimes change tenant subdomains silently |
| `jobspy` import errors at startup | `python-jobspy` not installed (it's an *optional* dep) | Indeed/Glassdoor source skipped at warning level | Either `pip install python-jobspy` to enable, or leave it — source skips cleanly |
| ATS source skipping companies | One company slug deleted their board upstream → 404 → `_request()` returns None → that slug is silently skipped | Run log shows lower count than expected | Audit `companies.py` slugs against the live ATS — periodically expected, no fix needed unless it's a key company |
| Source returns mostly non-UK jobs that get filtered | `_is_uk_or_remote()` doing its job — source is intrinsically global (Arbeitnow, RemoteOK) | Most fetched jobs dropped between `fetch_jobs()` return and dedup | Working as intended; this is the cost of including global remote boards |
| Rate-limit 429 spirals | Source's `delay` too aggressive vs upstream quota | Repeated `429` retries with `Retry-After` headers | Increase the source's `RATE_LIMITS[name]["delay"]` and reduce `concurrent` |
| Source returns duplicate jobs across runs | Upstream pagination returning the same page; or the source isn't honouring date cursor | DB UNIQUE on `(normalized_company, normalized_title)` quietly dedups | Working as intended at the storage layer, but it wastes fetch budget — fix the source's pagination |
| Conditional-fetch cache never hits for a source | The upstream doesn't return `ETag` or `Last-Modified` headers (most don't) | `cache.get_metrics()` shows misses but no hits for this source | This is expected — rule #14 says conditional fetch only helps for upstreams that honour validators. Don't opt in unless they do |
| New source added but pipeline doesn't pick it up | Forgot one of the FIVE load-bearing surfaces (rule #13) | `test_cli.py` or `test_api.py` assertion failure | Update: `SOURCE_REGISTRY` + `_build_sources()` + `RATE_LIMITS` + `test_cli` + `test_api` — all five |
| Domain-filtered source still appearing | Source's `.DOMAINS = {"general"}` (default) — it's included for every user | Pillar 2 domain filter only excludes sources whose DOMAINS are strictly outside the user's | Set `.DOMAINS = {"healthcare"}` etc. on the source class if it shouldn't be general |
| Posting has `date_confidence="low"` | Source doesn't expose a real `posted_at`, only `date_found` (when *we* saw it) | Recency score capped at 60% of band | Working as intended (anti-fabrication signal); upgrade by parsing the upstream's actual post-date field if it exists |
| Source fetch hangs for >30 s | One upstream is dragging; `REQUEST_TIMEOUT=30` should kick in | Eventually `TimeoutError` → retry → after 3 attempts source returns partial result | Working as intended; if persistent, lower `REQUEST_TIMEOUT` for that source via per-source override or accept the latency |
| `JobSpy` (Indeed/Glassdoor) returns weird results | Upstream Indeed/Glassdoor changed; `python-jobspy` library lagging | Both `indeed` and `glassdoor` registry keys affected (they share the class) | Upgrade `python-jobspy`; this is a third-party dependency we don't control |

For operational queries (test one source in isolation, inspect breaker state, reset rate limits), see [`runbook.md`](./runbook.md). For unfamiliar terminology (ATS, RSS, Algolia, JobSpy, normalized_key), see [`glossary.md`](./glossary.md).

---

## 9. Current status — what works, what's incomplete

Legend: ✅ done & wired · 🟡 partial · ❌ planned but not built · ⚠️ subtle gap

### 9.1 Base machinery

| Surface | Status | Notes |
| --- | --- | --- |
| `BaseJobSource` retry (3×, backoff 1/2/4) | ✅ | `base.py:150-216` |
| Per-source rate limiting via `RATE_LIMITS` | ✅ | 41 entries, all sources covered |
| 429 `Retry-After` honouring (cap 60 s) | ✅ | |
| No-retry on 401/403/404/422 | ✅ | |
| Conditional fetch (ETag/304) infrastructure | ✅ | `_get_json_conditional` / `_get_text_conditional` |
| Conditional-fetch **adoption** | 🔴 | **no source opts in** — tests are the only callers (rule #14 keeps it opt-in) |
| Dynamic-keyword properties (config/fallback) | ✅ | but fallback defaults are now empty (`keywords.py`) |
| `_is_uk_or_remote()` location gate | ✅ | delegates to `uk_gate.names_foreign_place`; holds no term list, and there is no scorer penalty behind it (rule #30) |
| `Job.normalized_key()` dedup key | ✅ | rule #1 protected |
| Salary sanitisation (<10k / >500k → None) | ✅ | `models.py:91-95` |
| HTML entity unescape in title/company | ✅ | `__post_init__` |

### 9.2 Source roster

| Surface | Status | Notes |
| --- | --- | --- |
| 40 source classes / 41 registry keys / 40 instances | ✅ | reconciled in §1 |
| 8 keyed APIs (skip gracefully without key) | ✅ | `category = "keyed_api"` |
| 8 free JSON APIs | ✅ | `category = "free_json"` |
| 10 ATS boards over 297 polled company slugs | ✅ | `core/companies.py` holds 302 across 11 platform lists; `RIPPLING_COMPANIES` (5) has no source class, so 297 are actually polled |
| 5 RSS/XML feeds | ✅ | `category = "rss"` — includes `apis_free/teaching_vacancies.py` (folder ≠ tier, rule #15) |
| 5 scrapers + 4 other | ✅ | `category = "scrapers"` / `"other"` |
| 5 HTML scrapers | ✅ | regex/embedded-JSON — brittle by nature ⚠️ |
| 4 other (incl. optional jobspy) | ✅ | jobspy skips with warning if uninstalled |
| Batch-3 rotation (−3, +5) | ✅ | verified on disk |
| Domain tagging via `.DOMAINS` | ✅ | 5 domains + general |
| Per-source tier categorisation (`.category`) | ✅ | drives scheduler cadence |

### 9.3 Known fragilities & gaps

| Item | Status | Notes |
| --- | --- | --- |
| HTML scrapers break when sites change markup | ⚠️ | inherent to LinkedIn/Workday/BCS/AIJobs regex parsing — no schema contract upstream |
| `RIPPLING_COMPANIES` slugs remain with no source class | 🟡 | `rippling` dropped 2026-08-10; the list is dead weight until a source is re-added |
| ATS catalog is hand-curated | 🟡 | no auto-discovery of new company boards |
| Conditional fetch used by **no** source | 🔴 | tests are the only callers; rule #14 — opportunity to reduce upstream load |
| University Jobs — only Cambridge feed confirmed valid | ⚠️ | other uni feeds may silently return nothing |
| `python-jobspy` not in `requirements.txt` | ✅-by-design | optional; Indeed/Glassdoor skip if absent |
| Per-source health/uptime dashboard | ❌ | breaker state is logged per-run but not surfaced in UI |
| Source-level dedup of overlapping aggregators | ✅ | handled downstream by the 4-layer deduplicator (Pillar 2 §4) |

---

## 10. Quick reference — every file in the Providers pillar

```
backend/src/sources/
├── base.py                         — BaseJobSource: retry, rate-limit, conditional fetch, _is_uk_or_remote
├── apis_keyed/   (8)               — reed, adzuna, jsearch, jooble, google_jobs, careerjet, findwork, gov_apprenticeships
├── apis_free/    (9)               — arbeitnow, remoteok, jobicy, himalayas, remotive, devitjobs,
│                                     landingjobs, hn_jobs, teaching_vacancies*
├── ats/          (10)              — greenhouse, lever, workable, ashby, smartrecruiters, pinpoint,
│                                     recruitee, workday, personio, successfactors
├── feeds/        (4)               — nhs_jobs, weworkremotely, realworkfromanywhere, uni_jobs
├── scrapers/     (5)               — linkedin, climatebase, eightykhours, bcs_jobs, aijobs_ai
└── other/        (4)               — indeed (JobSpySource → indeed+glassdoor), hackernews, themuse, nofluffjobs
                                       (* = added in Batch 3)

backend/src/
├── models.py                       — Job dataclass + normalized_key() (rule #1)
├── main.py                         — SOURCE_REGISTRY (41 keys) + _build_sources() + domain filter
├── core/
│   ├── companies.py                — 302 ATS slugs across 11 platform lists + name overrides
│   ├── settings.py:279-336         — RATE_LIMITS (41 entries)
│   └── keywords.py                 — LOCATIONS + VISA_KEYWORDS (the rest emptied 2026-04-09)
└── utils/rate_limiter.py           — async semaphore + delay

backend/tests/
├── test_sources.py                 — 110 tests, all sources, aioresponses-mocked
├── test_conditional_fetch.py       — 13 tests, ETag/304/FIFO (the only callers of the
│                                     conditional helpers — no source opts in)
├── test_cli.py                     — len(SOURCE_REGISTRY) == 41
└── test_api.py                     — two == 41 assertions
```

---

## 11. What this pillar does *not* cover

- **What happens to a job after `fetch_jobs()` returns it** — scoring, dedup, enrichment, storage → Pillar 2 (Search & Match Engine).
- **The scheduler/circuit-breaker that decide *when* to call each source** — documented in Pillar 2 §4 (they wrap the providers but are engine infrastructure).
- **How a stored job reaches a user** — feed, notifications, dashboard → Pillar 1 (User Side).

---

## 12. Architectural rules touched by this pillar

- **#1** — never touch `normalized_key()` without verifying deduplicator + DB UNIQUE.
- **#2** — never change `BaseJobSource` (constructor, properties, retry, HTTP helpers) without checking all 40 subclasses.
- **#4** — always mock HTTP in tests (`aioresponses`); the suite runs offline.
- **#8 / #13** — adding/removing a source touches FIVE surfaces (registry, build list, rate limits, `test_cli.py`, `test_api.py`) plus CLAUDE.md.
- **#14** — conditional fetch (`_get_json_conditional`) only for upstreams that honour ETag/Last-Modified.
- **#15** — new sources MUST set `.category` to a scheduler tier key (or add a `NAME_TIER` override).

---

*Source roster (post-2026-08-10 rotation): 40 classes / 41 registry keys / 40 instances. Backend tests: 218 `test_*.py` files; measure the collected count, never quote it (2 `live` deselected offline).*
