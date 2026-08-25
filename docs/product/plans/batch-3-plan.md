# Pillar 3 — Batch 3 Implementation Plan
<!-- doc: PLAN -->

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:subagent-driven-development` (for the 5 independent new sources), `superpowers:verification-before-completion`. Steps use checkbox syntax for tracking.

**Goal:** Replace broken twice-daily cron with tiered polling, add conditional-fetch, +5/−3 sources (net 48→50), expand ATS slug catalog, replace `newly_empty` with per-source circuit breakers.

**Architecture:** (1) New `services/scheduler.py` owns tier intervals and async dispatch. (2) `BaseJobSource._request` extends to surface `(body, headers)` and thread `If-None-Match`/`If-Modified-Since` from a per-source conditional-cache. (3) New `services/circuit_breaker.py` wraps fetch calls with open/half-open/closed state. (4) 5 new source files follow the existing `BaseJobSource` pattern with `posted_at` honesty (Batch 1 contract). (5) Slug catalog curated hand-picked from research-doc UK references + Feashliaa-style additions.

**Tech stack:** existing — `aiohttp`, `aiosqlite`, `asyncio`, `pytest-asyncio`, `aioresponses`, `freezegun` (new dev dep for scheduler time-mocking).

---

## POST-BATCH-2 BASELINE

Run 2026-04-18 on `pillar3/batch-3` HEAD (branched from `main` after Batch 2 merged).

```
Command: cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:  24 failed, 498 passed, 3 skipped in 184.91s
Log:     /tmp/batch3_baseline.log (1245 lines)
```

**This is the exact Batch 2 completion number (+1 vs the 497 tallied in the entry — one test flip between the review-response commits and today). Any Batch 3 regression claim compares against 498 passed.**

The 24 failures are the same four pre-existing buckets documented in Batch 1 (§ api sqlite init / cron+setup path drift / 7 source parsers / 3 matched_skills stale assertions). Untouched by Batch 3.

---

## Scope ceiling

Batch 3 stays out of these adjacent concerns:

- **Direct-URL 404→confirmed_expired verifier** for ghost detection — deferred Batch 1.5/3.5.
- **ARQ runtime wiring** (`src/workers/settings.py` with Redis pool) — deferred per Batch 2 §"What got deferred". Tiered scheduler ships pure-async; productionising is Batch 4 prerequisite work.
- **PostgreSQL migration** — deferred Batch 3 per Batch 2 D4 decision; the tiered scheduler is transport-layer and does not touch storage.
- **Slug catalog to 500+ via full Feashliaa clone** — see Phase G scope call.

---

## File-level plan

### New files (code)

| Path | Responsibility |
|---|---|
| `backend/src/services/scheduler.py` | Tier definitions, `TieredScheduler` async dispatcher, per-source tick tracker |
| `backend/src/services/circuit_breaker.py` | `CircuitBreaker` dataclass + per-source registry + open/half-open/closed transitions |
| `backend/src/services/conditional_cache.py` | Tiny in-memory (url → ETag/Last-Modified/body) store used by `BaseJobSource` |
| `backend/src/sources/apis_free/teaching_vacancies.py` | UK Teaching Vacancies gov.uk JSON API |
| `backend/src/sources/apis_free/gov_apprenticeships.py` | GOV.UK Apprenticeships API |
| `backend/src/sources/feeds/nhs_jobs_xml.py` | NHS Jobs XML feed (new entry alongside existing `nhs_jobs.py`) |
| `backend/src/sources/ats/rippling.py` | Rippling ATS public postings |
| `backend/src/sources/ats/comeet.py` | Comeet ATS public postings |

### New files (tests — one per unit)

| Path | Responsibility |
|---|---|
| `backend/tests/test_scheduler.py` | Tier intervals, fairness, freezegun time-advance |
| `backend/tests/test_circuit_breaker.py` | State transitions, half-open probe, fail-loop |
| `backend/tests/test_conditional_fetch.py` | ETag 304 roundtrip, Last-Modified, cache eviction |
| `backend/tests/test_teaching_vacancies.py` | Happy path, empty result, error |
| `backend/tests/test_gov_apprenticeships.py` | Happy path, empty result, error, rate-limit respect (cite published 150 req / 5 min) |
| `backend/tests/test_nhs_jobs_xml.py` | Happy path, empty result, XML parse error |
| `backend/tests/test_rippling.py` | Happy path, empty result, error |
| `backend/tests/test_comeet.py` | Happy path, empty result, error |

### Modified files

| Path | Change |
|---|---|
| `backend/src/sources/base.py` | `_request` + new helpers surface response headers; optional conditional-cache wiring |
| `backend/src/main.py` | Drop 3 source imports + registry entries + build list; add 5 new imports/registry/build entries; swap `newly_empty` logic for circuit breaker call |
| `backend/src/core/settings.py` | `RATE_LIMITS` — drop 3, add 5 (total still 50 with indeed+glassdoor twin) |
| `backend/src/core/companies.py` | Add Rippling + Comeet slug lists; expand existing Greenhouse/Lever/Ashby/Workable/Recruitee lists |
| `backend/tests/test_cli.py:44-60` | Update `test_source_registry_has_48_sources` → 50 with new expected set |
| `CLAUDE.md` | Append "Batch 3 additions" section |
| `docs/harness/IMPLEMENTATION_LOG.md` | Append Batch 3 completion entry |

### Deleted files

- `backend/src/sources/apis_free/yc_companies.py`
- `backend/src/sources/other/nomis.py`
- `backend/src/sources/feeds/findajob.py`
- Matching test blocks inside `backend/tests/test_sources.py` (inline, not separate files).

---

## Phase A — Plan committed (this doc)

**Commit:** `docs(pillar3): Batch 3 plan + POST-BATCH-2 baseline`

- [ ] Step A1: Write this file
- [ ] Step A2: Commit

---

## Phase B — Drop 3 low-value sources

**Reason per research:** FindAJob duplicates Adzuna (`pillar_3_report.md` §1), Nomis is ONS statistics not jobs (`pillar_3_batch_3.md` §1), YC Companies is covered by HN Jobs + Ashby (scope spec).

**Files:**
- Delete: `backend/src/sources/apis_free/yc_companies.py`, `backend/src/sources/other/nomis.py`, `backend/src/sources/feeds/findajob.py`
- Modify: `backend/src/main.py` (imports, `SOURCE_REGISTRY`, `_build_sources`), `backend/src/core/settings.py` (`RATE_LIMITS`)
- Modify: `backend/tests/test_sources.py` — remove 3 test classes

- [ ] Step B1: Delete the 3 source files + their inline tests
- [ ] Step B2: Remove imports + registry entries + build-list entries in `main.py`
- [ ] Step B3: Remove rate-limit entries in `settings.py`
- [ ] Step B4: Run `pytest tests/ --ignore=tests/test_main.py -q`, confirm no drop in non-drop tests (test_cli.py will fail — expected, fixed in Phase H)

**Commit:** `refactor(sources): drop YC Companies, Nomis, FindAJob (Batch 3 scope)`

---

## Phase C — Conditional-fetch layer

**Approach:** keep the existing `_get_json` / `_post_json` / `_get_text` signatures stable (no callsite churn). Add a private helper `_request_conditional()` that threads ETag / Last-Modified headers from a per-source `ConditionalCache` and recognises 304. On 304, return the cached body (same shape as a 200). The implementation is fully backwards-compatible: sources that don't opt in behave identically.

**Files:**
- Create: `backend/src/services/conditional_cache.py`
- Modify: `backend/src/sources/base.py`
- Create: `backend/tests/test_conditional_fetch.py`

- [ ] Step C1: Write `test_conditional_fetch.py` — 4 tests:
  - `test_first_fetch_stores_etag` — aioresponses returns `ETag: W/"abc"`; cache now holds abc
  - `test_second_fetch_sends_if_none_match_and_gets_304_returns_cached_body` — 304 → same body
  - `test_last_modified_roundtrip` — `Last-Modified` header path mirrors the ETag path
  - `test_no_cache_when_no_validator_header` — cache not written if server omits both headers
- [ ] Step C2: Run, confirm all 4 fail (helper does not exist)
- [ ] Step C3: Implement `ConditionalCache` (dict-backed with LRU eviction at 256 entries) + `BaseJobSource._request_conditional` which
  1. Looks up (method, url) in cache
  2. Adds `If-None-Match` / `If-Modified-Since` headers
  3. On 304 → returns cached body; on 200 → stores validator + body and returns body
  4. Otherwise identical to `_request`
- [ ] Step C4: Run → all 4 pass
- [ ] Step C5: No sources opt in yet (separate phase). Run full test suite → +4 passing, 0 regression

**Commit:** `feat(sources): ETag/Last-Modified conditional fetch in BaseJobSource`

---

## Phase D — Per-source circuit breakers

**State machine (per `pillar_3_batch_3.md` §"Circuit breakers"):**
- **CLOSED:** every call allowed
- Trip to **OPEN** after 5 consecutive failures (empty or exception)
- **OPEN → HALF_OPEN** after cooldown (5 min default)
- HALF_OPEN: single probe call allowed. Success → CLOSED, failure → OPEN (cooldown reset)

**Files:**
- Create: `backend/src/services/circuit_breaker.py`
- Create: `backend/tests/test_circuit_breaker.py`
- Modify: `backend/src/main.py` — replace the existing `newly_empty` loop (L370-383) with a circuit-breaker-aware version

- [ ] Step D1: Write `test_circuit_breaker.py` — 7 tests:
  - `test_starts_closed`
  - `test_5_failures_trip_to_open`
  - `test_open_rejects_call_without_hitting_source`
  - `test_open_transitions_to_half_open_after_cooldown` (uses `freezegun`)
  - `test_half_open_success_closes`
  - `test_half_open_failure_reopens_with_fresh_cooldown`
  - `test_registry_scopes_breakers_by_source_name`
- [ ] Step D2: Run, expect 7 fails
- [ ] Step D3: Implement `CircuitBreaker` + module-level `BreakerRegistry`
- [ ] Step D4: Run, 7 pass
- [ ] Step D5: Rewrite `main.py::run_search` source-health section to:
  1. Consult `BreakerRegistry` before dispatch (skip sources whose breaker is open)
  2. Record success/failure into the breaker post-fetch
  3. Log newly-opened breakers (replaces `newly_empty` warning)
- [ ] Step D6: Add 1 integration test in `tests/test_main.py` scoped specifically to breaker wiring (uses async fake sources)
- [ ] Step D7: Full suite, confirm +7 net passing

**Commit:** `feat(resilience): per-source circuit breakers replace newly_empty flag`

---

## Phase E — Tiered polling scheduler

**Tier map:**
```python
TIER_INTERVALS_SECONDS = {
    "ats":      60,    # Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Pinpoint, Personio, Rippling, Comeet
    "reed":     300,   # Reed API (5 min)
    "workday":  900,   # Workday (15 min — conservative anti-bot)
    "rss":      900,   # RSS feeds (15 min)
    "scrapers": 3600,  # 60 min
    "default":  3600,  # fallback
}
```

Category → tier resolver uses `BaseJobSource.category` + name overrides.

**Files:**
- Create: `backend/src/services/scheduler.py`
- Create: `backend/tests/test_scheduler.py`
- Modify: `backend/pyproject.toml` (add `freezegun>=1.4.0` to dev deps)

- [ ] Step E1: `pip install freezegun` + add to pyproject `[project.optional-dependencies].dev`
- [ ] Step E2: Write `test_scheduler.py` — 6 tests:
  - `test_resolve_tier_by_category_and_name`
  - `test_ats_source_polled_every_60s` (freezegun advances 59s → 0 calls; advances 1s → 1 call; advances 60s more → 2 calls)
  - `test_scrapers_polled_every_3600s`
  - `test_multiple_tiers_do_not_starve` (one tier slow does not delay another tier's tick)
  - `test_scheduler_respects_circuit_breaker_open` (skips the fetch while recording a noop tick)
  - `test_scheduler_honors_manual_source_filter` (single-source run bypasses tier system — keeps CLI `--source` working)
- [ ] Step E3: Run, expect 6 fails
- [ ] Step E4: Implement `TieredScheduler`:
  - `__init__(sources, breaker_registry, tier_overrides=None)`
  - `async def tick(now)`: dispatch sources whose `last_run + interval <= now`
  - `async def run_forever(interval=1.0)`: tick loop; breakable via `stop()`
  - `async def run_once(now=None)`: one-shot, the primary integration hook for pytest + CLI single-run
- [ ] Step E5: Run, 6 pass
- [ ] Step E6: CLI integration note — keep `python -m src.cli run` behaviour (single-pass run_search) working by calling `scheduler.run_once()`. The long-lived `run_forever` ships but is not yet wired to a system service; Phase F/G of Batch 4 will take that on.

**Commit:** `feat(scheduler): tiered polling replaces twice-daily cron`

---

## Phase F — Add 5 new sources (parallelisable via subagents)

Each source is an independent unit. Use `superpowers:subagent-driven-development` to dispatch 5 agents in parallel. Each subagent receives the same template: `BaseJobSource` subclass, `posted_at` honesty (no `datetime.now()` as `posted_at`), ≥3 tests (happy/empty/error) with `aioresponses` mocks.

### F1 — Teaching Vacancies (gov.uk)

**API:** `https://teaching-vacancies.service.gov.uk/api/v1/jobs.json` (public, no auth, OGL). Schema.org JobPosting format.

- [ ] Subagent task F1:
  - Create `backend/src/sources/apis_free/teaching_vacancies.py` (class `TeachingVacanciesSource`, `name = "teaching_vacancies"`, `category = "rss"` for tier purposes — 15min matches the schedule)
  - Extract `job.title`, `hiringOrganization.name`, `jobLocation.address.addressLocality`, `datePosted` → `posted_at` with `date_confidence="high"`, `url` → `apply_url`
  - Create `backend/tests/test_teaching_vacancies.py` — 3 tests (happy, empty, 500 error)
  - Rate-limit: `{"concurrent": 1, "delay": 2.0}` — no published cap, be polite
  - Cite in the test docstring: "No documented rate limit per teaching-vacancies.service.gov.uk/pages/api_specification"

### F2 — GOV.UK Apprenticeships

**API:** `https://findapprenticeship.service.gov.uk/api/v1/...` (rate: **150 req / 5 min**, cite this in test comment).

- [ ] Subagent task F2:
  - Create `backend/src/sources/apis_free/gov_apprenticeships.py` (class `GovApprenticeshipsSource`, `name = "gov_apprenticeships"`, `category = "rss"`)
  - Extract apprenticeship vacancies, include full UK location, `posted_at` from the feed's `postedDate`
  - Rate-limit: `{"concurrent": 1, "delay": 2.0}` — well under the 150/5-min budget
  - 3 tests

### F3 — NHS Jobs XML feed (additive, new entry)

The existing `nhs_jobs.py` uses the `search_xml` keyword endpoint and hits `closingDate`-as-date wrong-fielding (fixed in Batch 1). The new entry uses the **all-vacancies XML feed** (`/api/v1/feed/all_current_vacancies.xml`) which exposes `createdDate` directly, earning `date_confidence="high"`.

- [ ] Subagent task F3:
  - Create `backend/src/sources/feeds/nhs_jobs_xml.py` (class `NHSJobsXMLSource`, `name = "nhs_jobs_xml"`, `category = "rss"`)
  - Parse `<vacancy><createdDate>` directly
  - Do NOT replace `nhs_jobs.py` — coexist; the two entries have different names in the registry
  - Rate-limit: `{"concurrent": 1, "delay": 2.0}`
  - 3 tests: happy path, empty feed, XML parse error

### F4 — Rippling ATS

**API:** `https://ats.rippling.com/api/board/{slug}/jobs` (public, undocumented; be polite).

- [ ] Subagent task F4:
  - Create `backend/src/sources/ats/rippling.py` (class `RipplingSource`, `name = "rippling"`, `category = "ats"`)
  - Take `companies: list[str] | None` param matching the existing ATS pattern
  - Hit `createdAt` / `updatedAt` for `posted_at`
  - Rate-limit: `{"concurrent": 2, "delay": 1.5}` (matches other ATS)
  - 3 tests per ATS pattern
  - Add a `RIPPLING_COMPANIES` stub list in `companies.py` (start at ≥5 UK-facing — scope research needed in Phase G)

### F5 — Comeet ATS

**API:** `https://www.comeet.co/careers-api/2.0/company/{slug}/positions` (public).

- [ ] Subagent task F5:
  - Create `backend/src/sources/ats/comeet.py` (class `ComeetSource`, `name = "comeet"`, `category = "ats"`)
  - Take `companies: list[str] | None`
  - Rate-limit: `{"concurrent": 2, "delay": 1.5}`
  - 3 tests
  - Add a `COMEET_COMPANIES` stub in `companies.py`

**Commit per source:** `feat(source): add teaching_vacancies` / `gov_apprenticeships` / `nhs_jobs_xml` / `rippling` / `comeet` (5 commits — easier to revert/bisect).

---

## Phase G — ATS slug catalog expansion

**Scope call.** Fully importing Feashliaa's ~95,000 slugs (filtered to ~2–5K UK) requires a full GitHub clone + scraping + validation pipeline that belongs in its own batch. For Batch 3 we go **from 104 → 250+** via hand-curation of UK-confirmed slugs referenced in `pillar_3_report.md` + research cross-references, which delivers measurable coverage gain while keeping the diff reviewable.

**Deferral is documented in the completion entry** — full Feashliaa parse is Batch 3.5 or a Batch 4 follow-up.

**Files:**
- Modify: `backend/src/core/companies.py` — expand existing lists + add Rippling/Comeet stubs

Target distribution (additive, not replacing):
- Greenhouse: 25 → 80 (add 55 UK-tagged confirmed slugs)
- Lever: 12 → 35
- Ashby: 9 → 25
- Workable: 8 → 25
- Recruitee: 8 → 20
- SmartRecruiters: 6 → 15
- Pinpoint: 8 → 15
- Personio: 10 → 18
- Rippling: new, 5 stub
- Comeet: new, 5 stub
- Workday: 15 → 20 (add 5 more with tenant/wd/site triples)
- **Total ≈ 263 slugs — passes the "≥250, <500" Batch 3 target**

- [ ] Step G1: Expand `companies.py` with UK-tagged slugs
- [ ] Step G2: No test changes needed — ATS source tests use fixture slug lists, not the real list
- [ ] Step G3: Add an `test_companies_slugs.py` sanity test: total slug count across all 10 platforms ≥ 250

**Commit:** `feat(companies): expand ATS slug catalog 104 → 263`

---

## Phase H — Registry + rate-limit + assertion rotation

**Files:**
- Modify: `backend/src/main.py` (add 5 new imports + registry entries + build-list entries)
- Modify: `backend/src/core/settings.py` (add 5 new `RATE_LIMITS` entries)
- Modify: `backend/tests/test_cli.py:44-60` → 50 count + new expected set

Final set: `{all 45 survivors}` ∪ `{teaching_vacancies, gov_apprenticeships, nhs_jobs_xml, rippling, comeet}` = **50 entries**.

(45 survivors = 48 existing − 3 dropped.)

- [ ] Step H1: Wire the 5 new sources into `main.py`
- [ ] Step H2: Add 5 `RATE_LIMITS` entries
- [ ] Step H3: Update `test_source_registry_has_48_sources`:
  - Rename to `test_source_registry_has_50_sources`
  - Update count to 50
  - Drop `yc_companies`, `nomis`, `findajob` from expected set
  - Add 5 new names
- [ ] Step H4: Full suite; confirm `test_cli.py` passes

**Commit:** `chore(registry): rotate source count 48 → 50 (CLAUDE.md #8)`

---

## Phase I — Docs + verification + handoff

- [ ] Step I1: Full pytest run from `backend/`:
  ```
  python -m pytest tests/ --ignore=tests/test_main.py -q > /tmp/batch3_after.log 2>&1
  tail -5 /tmp/batch3_after.log
  ```
  Expected delta: baseline 498 passed + conditional (4) + circuit breaker (7) + scheduler (6) + 5×3 source tests (15) + slug sanity (1) = **+33 → 531 passed**, 24 failed unchanged, 3 skipped unchanged.
- [ ] Step I2: Run `python backend/scripts/measure_date_reliability.py` — confirm bucket_accuracy_24h is computable and the new sources with real `posted_at` are contributing high-confidence rows.
- [ ] Step I3: Append Batch 3 completion entry to `docs/harness/IMPLEMENTATION_LOG.md` using the template at the bottom of that file. Include: test deltas, KPI deltas (Phase 0→1 latency expectations: ATS sub-minute, RSS 15-min, scrapers 60-min), what shipped, what deferred (Feashliaa full clone, ARQ worker runtime, direct-URL 404 verifier), surprises.
- [ ] Step I4: Append `## Batch 3 additions` section to `CLAUDE.md` covering:
  - New modules (`services/scheduler.py`, `services/circuit_breaker.py`, `services/conditional_cache.py`)
  - New source count 50 / 49 unique
  - New rule #13: "when adding a source, also set its tier via `category` or a name-override in `TIER_INTERVALS` — an un-tiered source falls to the 60-min default"
- [ ] Step I5: Commit docs: `docs(pillar3): Batch 3 completion entry + CLAUDE.md appendix`
- [ ] Step I6: Push the branch: `git push -u origin pillar3/batch-3`
- [ ] Step I7: Print `READY_FOR_REVIEW pillar3/batch-3 @ <hash>` and STOP

---

## Self-review

**Spec coverage.** Every scope bullet has a phase:
- Tiered polling scheduler → E
- Conditional-fetch → C
- 5 new sources → F
- 3 drops → B
- Slug expansion → G (with explicit deferral to 250-ish vs 500+)
- Circuit breakers → D
- Rule #8 assertion rotation → H

**Placeholder scan.** The only deliberate under-specification is Phase F subagent bodies — each task is a stable template delegated to subagents, not a TBD. Everything else has files, code shape, test names, and commit messages.

**Type consistency.** `CircuitBreaker`, `BreakerRegistry`, `TieredScheduler`, `ConditionalCache`, and the 5 source class names are used consistently across phases D/E/F/C/H.

**Scope honesty.** The 500+ slug target is capped at 250+ with explicit reviewer-visible deferral, because a full Feashliaa parse belongs in its own batch. This is called out in the completion entry plan (Step I3).

---

_Last updated: 2026-04-18 — ready to execute_
