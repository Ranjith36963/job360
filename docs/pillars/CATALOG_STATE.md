# CATALOG STATE — what is actually true today

**Measured: 2026-08-16.** Every number below carries its date and how it was got.
A number without provenance is a lie waiting to happen — this repo has been bitten
by stale doc figures before. If you are reading this more than ~2 weeks after the
date above, **re-measure first** (see [HOW TO RE-MEASURE THIS](#how-to-re-measure-this)).

**This document describes the CATALOG side only.**

| Side | What it means | In this doc? |
|---|---|---|
| **CATALOG** | Everything TRUE ABOUT THE JOB. Fetch → UK gate → universal shelves → JOB SOURCE ENRICHMENT → embeddings. No user involved. | **YES** |
| **SEARCH** | Everything about FIT between a job and a person (keyword score, semantic rank, LLM judge). | **NO — deliberately out of scope.** See `docs/pillars/02-search-and-match-engine.md`. |

Some columns physically live in the catalog tables but are SEARCH-side (the 9 score
dimension columns on `jobs`). They are marked SEARCH in the tables below and are not
analysed here.

### Vocabulary (owner-set — use these words)

| Word | Means |
|---|---|
| **CATALOG** | The shared pool of jobs (the `jobs` table). Same for everyone. |
| **SHELF** | One field on a job that we can fill and later match on. |
| **UNIVERSAL SHELF** | The fixed set of shelves EVERY job must carry, whatever source it came from. |
| **JOB SOURCE ENRICHMENT** | Using an LLM to READ a job ad and extract its facts (salary, seniority, workplace, visa, deadline). Facts about the JOB, identical for every user → CATALOG work, never "search". Never call it just "enrichment". |

---

## 1. THE HEADLINE — five things that matter

*(measured 2026-08-16 against prod Postgres, read-only `SELECT`s)*

| # | Fact | Number |
|---|---|---|
| 1 | The catalog is **10,579 jobs**, ~44 days old (oldest `first_seen` 2026-07-02), growing ~280–450/day. | 10,579 rows |
| 2 | **JOB SOURCE ENRICHMENT is STALLED.** Newest `enriched_at` anywhere in the table is **2026-08-08T14:38:24Z** — zero new rows in 8 days, though the cron fires every 30 min. | 6,560 / 10,579 = 62% |
| 3 | **Embeddings are structurally dead on the worker.** `Dockerfile.worker` installs plain `.`, not `.[semantic]` — the process that runs the nightly backfill has no sentence-transformers. Coverage has not moved while the catalog grew ~600 rows. | 454 vectors / 10,579 = **4.3%** |
| 4 | **Nothing ever removes a stale job.** `purge_old_jobs(30)` would delete **0** rows today; 47.8% of the catalog is flagged `likely_stale` and that flag deletes nothing and hides nothing except a 24h-bucket view. | 0 purgeable / 5,060 flagged |
| 5 | **Money is not the constraint.** Fetch is free, storage is ~$0.0075/mo, and full JOB SOURCE ENRICHMENT of a month's intake would cost **~$3.80/mo** even at paid OpenAI prices. Today actual spend is ~$0.01/mo because two stages are dead. | <$5/mo at 1x, <$40/mo at 10x |

**Single biggest hole: `job_embeddings`.** 95.7% of the catalog has no vector at all.
Semantic retrieval structurally cannot see those jobs — they are invisible to that path
no matter how good the ranking is. Cause is not budget or model quality, it is a missing
pip extra in one Dockerfile.

---

## 2. WHAT IS THE CATALOG — tables and how they join

Four tables. All keyed off `jobs.id`. **None of them carry `user_id`** (hard rules #10 / #17).

```
jobs (10,579)  ──1:0..1──  job_enrichment (6,560)   PK job_id  ON DELETE CASCADE
     │                     JOB SOURCE ENRICHMENT: LLM-read facts about the ad
     └──1:0..1──           job_embeddings (687 rows / 454 vectors)  PK job_id  ON DELETE CASCADE
                           pgvector(384), all-MiniLM-L6-v2

run_log (31)   — per-RUN aggregate stats. NOT joined to jobs. No FK, no job_id.
```

| Table | Cols | Rows (prod 2026-08-16) | Total size on disk | Holds |
|---|---:|---:|---:|---|
| `jobs` | **36** | 10,579 | 29,310,976 B (~28.0 MB) | The catalog itself |
| `job_enrichment` | 20 | 6,560 | 2,727,936 B (~2.6 MB) | JOB SOURCE ENRICHMENT output |
| `job_embeddings` | 4 | 687 | 1,941,504 B (~1.9 MB) | Vector + audit row |
| `run_log` | 12 | 31 | 131,072 B (~128 KB) | Per-run stats |
| **Whole DB** (incl. SEARCH-side tables like `user_feed`) | — | — | ~51 MB | — |

> *Measured:* `information_schema.columns`, `count(*)`, `pg_total_relation_size()` run
> against prod via `railway run -s Postgres`, 2026-08-16.
> **Column count correction:** an earlier survey said `jobs` has 34 columns. Live
> `information_schema` says **36**. Use 36.

`jobs` heap alone is 9,887,744 B (~9.4 MB) — so **~65% of the on-disk footprint is
indexes + TOAST**, not row data. (Why, was not investigated.)

### 2.1 `jobs` — the universal shelf, all 36 columns

Source of truth for the DDL: `backend/src/repositories/database.py:99-127` (`init_db`),
mirrored at `:171-199` (`_migrate`), plus migrations `0011` / `0021` / `0029`.
Note the actual `CREATE TABLE jobs` lives in the **legacy init path**, not in a numbered
migration — `0000_baseline.up.sql` is a documented no-op, and `0011`'s
`CREATE TABLE IF NOT EXISTS jobs` is a hand-synced mirror for test fixtures.

| Column | Type | Default | Null? | Filled by | Side |
|---|---|---|---|---|---|
| `id` | bigint | — | NO | pipeline (PK) | CATALOG |
| `title` | text | — | NO | source | CATALOG |
| `company` | text | — | NO | source | CATALOG |
| `location` | text | `''` | YES | source | CATALOG |
| `salary_min` | real | — | YES | source (nulled if <10000, `models.py:92-93`) | CATALOG |
| `salary_max` | real | — | YES | source (nulled if >500000, `models.py:94-95`) | CATALOG |
| `description` | text | `''` | YES | source; backfill-UPDATE on re-fetch | CATALOG |
| `apply_url` | text | — | NO | source | CATALOG |
| `source` | text | — | NO | source name | CATALOG |
| `date_found` | text | — | NO | source — **SCRAPE timestamp, NOT the posting date** (e.g. `sources/scrapers/linkedin.py:167`) | CATALOG |
| `match_score` | integer | 0 | YES | `scorer.score()`, `main.py:699` | **SEARCH** |
| `visa_flag` | integer | 0 | YES | `scorer.check_visa_flag`, `main.py:705` | CATALOG |
| `experience_level` | text | `''` | YES | `detect_experience_level`, `main.py:706` | CATALOG |
| `normalized_company` | text | — | NO | `Job.normalized_key()` | CATALOG (dedup key) |
| `normalized_title` | text | — | NO | `Job.normalized_key()` | CATALOG (dedup key) |
| `first_seen` | text | — | NO | pipeline, always `now()` at insert (`database.py:413`) | CATALOG |
| `posted_at` | text | — | YES | source (claimed posting date) | CATALOG |
| `first_seen_at` | text | — | YES | pipeline | CATALOG |
| `last_seen_at` | text | — | YES | pipeline — **this is what purge reads** | CATALOG |
| `last_updated_at` | text | — | YES | **DEAD — declared, never written anywhere in `backend/src/`** | — |
| `date_confidence` | text | `'low'` | YES | date-parsing logic | CATALOG |
| `date_posted_raw` | text | — | YES | source (audit-only raw value) | CATALOG |
| `consecutive_misses` | integer | 0 | YES | ghost detector | CATALOG |
| `staleness_state` | text | `'active'` | YES | ghost detector | CATALOG |
| `deadline` | text | — | YES | `extract_deadline`, `main.py:710-716` | CATALOG |
| `deadline_source` | text | — | YES | `'listing'` or `'description'` | CATALOG |
| `description_backfill_attempts` | integer | 0 | YES | `workers/tasks.py` backfill sweep | CATALOG |
| `role` | integer | 0 | YES | `ScoreBreakdown.title_score` | **SEARCH** |
| `skill` | integer | 0 | YES | `ScoreBreakdown.skill_score` | **SEARCH** |
| `seniority_score` | integer | 0 | YES | `ScoreBreakdown.seniority_score` | **SEARCH** |
| `experience` | integer | 0 | YES | multi-dim scoring (enrichment-gated) | **SEARCH** |
| `credentials` | integer | 0 | YES | multi-dim scoring (enrichment-gated) | **SEARCH** |
| `location_score` | integer | 0 | YES | `ScoreBreakdown.location_score` | **SEARCH** |
| `recency` | integer | 0 | YES | `ScoreBreakdown.recency_score` | **SEARCH** |
| `semantic` | integer | 0 | YES | multi-dim scoring (enrichment-gated) | **SEARCH** |
| `penalty` | integer | 0 | YES | `ScoreBreakdown` penalty component | **SEARCH** |

**Constraints** (read live from `pg_constraint`, 2026-08-16):

| Name | Kind | Columns |
|---|---|---|
| `jobs_pkey` | PRIMARY KEY | `id` |
| `jobs_normalized_company_normalized_title_key` | **UNIQUE** | `(normalized_company, normalized_title)` |
| (9 unnamed) | NOT NULL | the 9 "NO" rows above |

That UNIQUE constraint is the **entire** cross-run dedup story. There is no other.

### 2.2 `job_enrichment` — JOB SOURCE ENRICHMENT output (20 columns)

Migration `0008_job_enrichment.up.sql`. `job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE`.
List/dict fields are stored as JSON-in-TEXT.

Live column list (from `information_schema`, 2026-08-16):
`job_id, title_canonical, category, employment_type, workplace_type, locations, salary,
required_skills, preferred_skills, experience_min_years, experience_level,
requirements_summary, language, employer_type, visa_sponsorship, seniority,
remote_region, apply_instructions, red_flags, enriched_at`

### 2.3 `job_embeddings` — vectors (4 columns)

Migration `0009`, extended by `0027_job_embedding_vectors.up.sql`.
`job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE`.

Live columns: `job_id, model_version, embedding_updated_at, embedding` (the last is
`USER-DEFINED` = **pgvector `vector(384)`**, confirmed live).

`0027` moved vectors out of ChromaDB-on-container-disk and into this Postgres column.
Its own comment records why: the worker container had no `[semantic]` extra and a separate
disk from backend, so the nightly refresh could never embed anything.
**That Dockerfile gap is still open today** — see §7.

What text gets embedded (`backend/src/services/embeddings.py:303-361`, `encode_job`):

| Job has a `job_enrichment` row? | Text embedded |
|---|---|
| YES | `title + " | " + requirements_summary + " | " + " ".join(required_skills)` |
| NO (degraded mode) | `title + " | " + description` |

Long descriptions are separately chunked and pooled.

### 2.4 `run_log` — per-run stats (12 columns)

Migration `0010` + a `_migrate()` mirror. Aggregate only, **not** FK-joined to `jobs`.
Live columns: `id, timestamp, total_found, new_jobs, sources_queried, per_source,
run_uuid, per_source_errors, per_source_duration, total_duration, user_id, matcher_stats`.

> **Schema drift:** `matcher_stats` exists in prod but appears in **no numbered migration
> file** — it was only ever added through `database.py::_migrate()`. Whether that is
> intentional or an oversight was not determined.

---

## 3. HOW A JOB GETS IN — the real path, function by function

**One full sweep per day.** ARQ cron `refresh_catalog` at **04:00 UTC**
(`backend/src/workers/settings.py:222`) → `workers/tasks.py:1416` → `src.main.run_search()`.
A user clicking Search also triggers `run_search()`.

`TieredScheduler.tick(force=True)` (`main.py:944`) dispatches **all 40 source instances at
once and ignores the tier intervals**. The tiers in `scheduler.py` (60s ATS / 5min Reed /
15min Workday / 60min default) are defined but never exercised — `run_forever()` is not
wired to any process (`scheduler.py:194-199` says so explicitly).

| # | Step | Where | What happens |
|---|---|---|---|
| 1 | `source.fetch_jobs()` | each source file | Builds raw `Job()` objects. **No shared normalization** — `BaseJobSource.fetch_jobs` is abstract. Nearly every source truncates `description[:5000]` at this point. |
| 2 | collect | `main.py:970` | `all_jobs.extend(...)` |
| 3 | id backfill | `main.py:1021-1028` | Builds a `normalized_key() → id` map over the whole catalog and stamps `job.id` on already-known jobs. (Rule-relevant: dim scoring silently zeroes if `id` is unset.) |
| 4 | ghost pass | `main.py:1005` | `_ghost_detection_pass` per source, gated behind a 70%-of-7-day-rolling-average completeness check (`main.py:171-216`) so a rate-limited scrape isn't read as "jobs vanished". |
| 5 | score + enrich fields | `asyncio.to_thread(_score_dedup_and_filter)` | `scorer.score()` (SEARCH), then CATALOG-side: `visa_flag` (`:705`), `experience_level` (`:706`), `extract_deadline` (`:710-716`, only `if job.deadline is None and job.description`). |
| 6 | dedup **within the run** | `services/deduplicator.py` | 4 layers: exact key → RapidFuzz → TF-IDF → optional embedding-repost. |
| 7 | score floor | same thread | `MIN_STORE_SCORE` drop. |
| 8 | **UK gate** | `services/uk_gate.check_uk`, `main.py:1040` | THE single chokepoint that refuses foreign jobs (hard rule #30). Not per-source, not a penalty. |
| 9 | insert | `database.py:372-448` `insert_job()` | `INSERT OR IGNORE` on the UNIQUE key. Duplicate silently no-ops (`rowcount=0`). **One exception:** if duplicate AND incoming description is non-empty while stored one is empty, a separate UPDATE backfills the description. Empty→non-empty is the only allowed transition. Each insert is in its own try/except so one poison row never aborts the run. |

### 3.1 The only normalization that exists

`Job.normalized_key()` — `backend/src/models.py:106-127`. Called in exactly three places:

| Call site | Why |
|---|---|
| `database.py:382` | at insert time, to fill the dedup columns |
| `deduplicator.py:117` | in-run dedup grouping |
| `main.py:1028` | the id-backfill lookup |

It strips a company legal-suffix regex, a region-suffix regex, lowercases, collapses
whitespace runs, then **truncates each of (company, title) to 300 chars** — added
2026-07-30 after a poison scraped title blew Postgres's 2,704-byte btree index-row
limit and aborted an insert batch.

Nothing else cleans title/company/location text anywhere in the pipeline.

### 3.2 A deliberate mismatch worth knowing

`deduplicator._normalize_title()` (`deduplicator.py:34-51`) is **wider** than the DB key —
it also strips seniority prefixes, job-req codes, parentheticals, marketing suffixes.
Result: "Senior ML Engineer" and "ML Engineer" collapse **within one run** but stay as
two separate rows **across runs**. Documented as by-design; unifying them needs a migration.

### 3.3 Fetch volume per nightly run

| Category | Sources | Requests/night | Basis |
|---|---:|---:|---|
| ATS company-slug sweeps | 10 | **297** | **Measured** — imported `core/companies.py`, summed the 10 lists used by ATS sources: Greenhouse 82, Pinpoint 39, Lever 35, Recruitee 31, Personio 26, Ashby 25, Workable 21, Workday 20, SmartRecruiters 15, SuccessFactors 3 |
| SerpApi (`google_jobs`) | 1 | ≤8 | **Measured in code** — `search_titles[:8]` hard cap, `sources/apis_keyed/google_jobs.py:53` |
| DfE apprenticeships | 1 | 1–few | Budget 150 / 5-min window, not binding |
| Other keyed APIs (Reed, Adzuna, JSearch, Jooble, Careerjet, Findwork) | 6 | ~1–8 each | **ESTIMATE** — not individually measured |
| Free JSON / RSS / scrapers | ~22 | ~1 each | **ESTIMATE** |
| **Total dispatched** | **40 instances** | **~330–400** | 297 exact + remainder estimated |

> **Registry count, corrected 2026-08-16:** `SOURCE_REGISTRY` has **41 keys**;
> `SOURCE_INSTANCE_COUNT = 40` (`main.py:168`) because `indeed` + `glassdoor` share one
> `JobSpySource`. Measured by parsing `backend/src/main.py:110-154`.
> The root `CLAUDE.md` still says "47 entries / 46 unique classes" — **that is stale**;
> `main.py:161-165` documents the 47→41 removal on 2026-08-10.

---

## 4. HOW A JOB DIES — purge, staleness, deadline

Short answer: **it mostly doesn't.**

### 4.1 Removal — the only real DELETE

`purge_old_jobs()`, `backend/src/repositories/database.py:674-730`.
Deletes where `COALESCE(last_seen_at, first_seen) < now - 30 days`.
Keyed on **LIVENESS**, not age — a job re-scraped daily never ages out, ever.

It is **not its own cron**. It runs inline inside `run_search()` (`main.py:839-842`),
so it fires on the 04:00 UTC `refresh_catalog` cron and on any user search.
`run_log` confirms a run every day 2026-08-08 → 2026-08-16.

**`last_seen_at` age buckets — what purge actually sees (measured 2026-08-16):**

| Bucket | Jobs |
|---|---:|
| < 24h | 4,051 |
| 1–7 days | 2,242 |
| 7–30 days | 4,286 |
| **> 30 days (purge-eligible)** | **0** |

`purge_old_jobs(30)` would delete **0 rows today**. Nothing has ever survived to the
threshold — the catalog is only 44 days old and everything keeps being re-scraped.

### 4.2 Staleness (ghost detection) — a flag that removes nothing

`backend/src/services/ghost_detection.py`. State machine:

`ACTIVE` → `POSSIBLY_STALE` (≥2 misses + ≥12h absent) → `LIKELY_STALE` (≥3 misses + ≥24h absent) → `CONFIRMED_EXPIRED` (sticky).

Runs on two paths: the `nightly_ghost_sweep` ARQ cron at **02:00 UTC**
(`workers/settings.py:211`), and `_ghost_detection_pass` inside every `run_search`
(`main.py:1005`).

**Live state counts (measured 2026-08-16, 10,579 jobs):**

| `staleness_state` | Count | % | min misses | max misses | Does the flag remove/hide it? |
|---|---:|---:|---:|---:|---|
| `active` | 4,708 | 44.5% | 0 | 1 | — |
| `possibly_stale` | 569 | 5.4% | 2 | 2 | No |
| `likely_stale` | **5,060** | **47.8%** | 3 | 32 | **No** — only excluded from a "24h bucket" read (`ghost_detection.py:48-50`) |
| `confirmed_expired` | 242 | 2.3% | **0** | 12 | Sticky hide — **but not set by ghost logic, see below** |

Nearly half the live catalog is flagged probably-dead and stays fully present and fully
searchable. The flag's only consumer is `should_exclude_from_24h()`.

`consecutive_misses` maxes at 32, consistent with one miss-check per calendar day since
the 2026-07-02 launch — i.e. the daily cron is the only thing incrementing it.

### 4.3 `CONFIRMED_EXPIRED` is not what its name says

The `ghost_detection.py` docstring and `mark_missed_for_source`'s comment both describe
`CONFIRMED_EXPIRED` as "set by a later direct-URL verification step".
**That step was never built.** Grep across `backend/src/` finds zero URL-liveness checks.

The 242 rows in that state trace to `backend/scripts/uk_sweep.py` — a one-time, explicitly
reversible script that reuses the sticky state to retro-hide jobs failing the UK gate
(hard rule #30), an unrelated concern. **Proof:** those rows have `consecutive_misses`
ranging **0**–12; a minimum of 0 is impossible if absence had set the state.

`apply_url` is populated on **10,579 / 10,579 (100%)** of jobs — the link is always there,
it is simply never re-queried after ingest.

### 4.4 Deadline — the shelf that is basically empty

Only **139 / 10,579 = 1.31%** of jobs have a deadline. `deadline_source` breakdown:
**139 `description`, 0 `listing`, 10,440 NULL.**

Structured (`'listing'`) extraction code exists in **8** source files and fires **zero**
times in prod: `teaching_vacancies.py:91`, `landingjobs.py:91`, `himalayas.py:48`,
`weworkremotely.py:76`, `nhs_jobs.py:105`, `workday.py:146`, `recruitee.py:84`,
`pinpoint.py:61`. Every filled deadline (139/139) came from the regex fallback instead.

The fallback (`services/deadline.py`, invoked `main.py:708-716`) is conservative by design:
needs an explicit keyword (deadline / closing date / apply by / …) followed by a parseable
date within a 60-char lookahead, rejects past dates and dates >2 years out.

**Deadline fill by source (measured 2026-08-16):**

| source | jobs | with deadline | % | from `listing` | from `description` |
|---|---:|---:|---:|---:|---:|
| devitjobs | 4,534 | 0 | 0.0% | 0 | 0 |
| greenhouse | 1,079 | 0 | 0.0% | 0 | 0 |
| teaching_vacancies | 1,066 | 71 | 6.7% | 0 | 71 |
| arbeitnow | 844 | 0 | 0.0% | 0 | 0 |
| remoteok | 654 | 2 | 0.3% | 0 | 2 |
| workday | 542 | 0 | 0.0% | 0 | 0 |
| smartrecruiters | 220 | 0 | 0.0% | 0 | 0 |
| uni_jobs | 204 | 63 | **30.9%** | 0 | 63 |
| adzuna | 190 | 2 | 1.1% | 0 | 2 |
| ashby | 163 | 0 | 0.0% | 0 | 0 |
| remaining 16 sources | 632 | 1 | ~0.2% | 0 | 1 |
| **TOTAL** | **10,579** | **139** | **1.31%** | **0** | **139** |

**24 jobs are past their own deadline and still fully present** (overdue 2–16 days;
17 from uni_jobs, 2 teaching_vacancies, 1 remoteok, 1 adzuna). Nothing reads the
`deadline` column at removal or hide time — purge checks `last_seen_at`, ghost detection
ignores deadlines entirely.

### 4.5 Age of the catalog

| `date_found` bucket | Jobs |
|---|---:|
| 0–1 day | 602 |
| 1–7 days | 1,416 |
| 7–14 days | 2,823 |
| 14–30 days | 5,701 |
| 30+ days | 37 |

`date_found` has **0 NULLs** (range 2026-07-02 → 2026-08-16), so "no date at all" never
happens. The 37 rows older than 30 days are **not a purge bug** — purge keys on
`last_seen_at` and they keep being re-seen. That is the documented design.

**Growth, jobs added per day by `first_seen` (measured 2026-08-16):**

| Date | New |
|---|---:|
| 08-03 | 434 |
| 08-04 | 275 |
| 08-05 | 441 |
| 08-06 | 452 |
| 08-07 | 423 |
| 08-08 | 391 |
| 08-09 | **73** |
| 08-10 | **0** |
| 08-11 | 278 |
| 08-12 | 284 |
| 08-13 | 414 |
| 08-14 | 367 |
| 08-15 | 280 |
| 08-16 | 322 (partial day) |

The 08-09 dip and 08-10 zero-day are real (not a query artifact). **Cause not investigated.**

---

## 5. HOW FULL IS EVERY SHELF — today, per source

All percentages measured live 2026-08-16 against 10,579 rows.

### 5.1 Top 10 sources — they are 89.8% of the catalog

| source | rows |
|---|---:|
| devitjobs | 4,534 |
| greenhouse | 1,079 |
| teaching_vacancies | 1,066 |
| arbeitnow | 844 |
| remoteok | 654 |
| workday | 542 |
| smartrecruiters | 220 |
| uni_jobs | 204 |
| adzuna | 190 |
| ashby | 163 |
| **top-10 total** | **9,496 of 10,579 (89.8%)** |

### 5.2 `jobs` shelf fill % — overall and per top-10 source

| source | n | title | company | location | salary_min | salary_max | description | apply_url | visa_flag=1 | experience_level | posted_at | date_posted_raw | deadline |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **OVERALL** | 10,579 | 100.0 | 100.0 | 98.4 | **39.7** | **46.2** | **75.9** | 97.9 | 2.2 | **31.6** | 44.7 | 54.9 | **1.3** |
| devitjobs | 4,534 | 100.0 | 100.0 | 100.0 | 85.5 | 85.7 | 74.2 | 100.0 | 0.0 | 31.1 | 0.0 | 0.0 | 0.0 |
| greenhouse | 1,079 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 42.5 | 100.0 | 0.5 | 42.4 | 0.0 | 100.0 | 0.0 |
| teaching_vacancies | 1,066 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 100.0 | 100.0 | 4.5 | 8.8 | 100.0 | 100.0 | 6.7 |
| arbeitnow | 844 | 100.0 | 100.0 | 94.8 | 0.0 | 0.0 | 100.0 | 100.0 | 12.4 | 44.7 | 100.0 | 100.0 | 0.0 |
| remoteok | 654 | 100.0 | 100.0 | 100.0 | 1.2 | 99.8 | 100.0 | 100.0 | 2.6 | 8.1 | 100.0 | 100.0 | 0.3 |
| workday | 542 | 99.8 | 100.0 | 99.1 | 0.0 | 0.0 | **13.8** | 99.8 | 0.0 | 50.0 | 99.8 | 99.8 | 0.0 |
| smartrecruiters | 220 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 39.5 | 100.0 | 3.2 | 62.3 | 100.0 | 100.0 | 0.0 |
| uni_jobs | 204 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 100.0 | 100.0 | 4.9 | 16.7 | 100.0 | 100.0 | 30.9 |
| adzuna | 190 | 100.0 | 100.0 | 100.0 | 98.9 | 100.0 | 100.0 | 100.0 | 2.1 | 25.8 | 100.0 | 100.0 | 1.1 |
| ashby | 163 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 100.0 | 100.0 | 0.6 | 30.1 | 100.0 | 100.0 | 0.0 |

Also measured: `normalized_title` empty on exactly **1** row, `normalized_company` on **0**
(these are DB-derived, effectively 100%). `first_seen_at` / `last_seen_at` 100%.
`last_updated_at` **0%** — never written.

**Salary reality check:** only 3 of the top 10 sources fill salary at all — devitjobs
(~85%), remoteok (max only, 99.8%; min only 1.2%), adzuna (~99%). The other 7 —
**68% of catalog rows** — contribute exactly 0%.

### 5.3 Description length — the "stub row" problem

| source | n | p10 | median | p90 | % under 300 chars | fully empty |
|---|---:|---:|---:|---:|---:|---:|
| **OVERALL** | 10,579 | 0 | 330 | 5000 (cap) | **46.7%** | **2,553 (24.1%)** |
| devitjobs | 4,534 | 0 | 224 | 381 | **74.2%** | 1,171 |
| greenhouse | 1,079 | 0 | **0** | 5000 (cap) | 57.5% | 620 |
| workday | 542 | 0 | **0** | 4,499 | **86.2%** | 467 |
| smartrecruiters | 220 | 0 | **0** | 4,999 | 60.5% | 133 |
| teaching_vacancies | 1,066 | 872 | 2,694 | 5000 (cap) | 1.3% | 0 |
| arbeitnow | 844 | 3,068 | 6,444 * | 11,280 * | 1.4% | 0 |
| remoteok | 654 | 428 | 1,256 | 6,163 * | 0.0% | 0 |
| uni_jobs | 204 | 2,487 | 3,726 | 4,941 | 0.0% | 0 |
| adzuna | 190 | 500 | 500 | 500 | 0.0% | 0 |
| ashby | 163 | 3,087 | 5000 (cap) | 5000 (cap) | 0.0% | 0 |

\* Values above the 5,000-char cap that ~20 source files apply at fetch
(`sources/ats/greenhouse.py:73`, `workday.py:198`, `scrapers/linkedin.py:219`, …).
**Unexplained** — either those rows predate the truncation or those sources skip it.

A p90 of exactly 5000.0 means ≥10% of that source's rows were **cut**, not naturally that length.

**devitjobs, greenhouse, workday, smartrecruiters together are 6,375 rows (60% of the
catalog) and are majority-stub.** Since JOB SOURCE ENRICHMENT reads `description[:4000]`,
a stub row cannot be enriched into anything useful — this is upstream of every LLM cost.

### 5.4 JOB SOURCE ENRICHMENT coverage — 62%, unevenly

| source | jobs | enriched | % |
|---|---:|---:|---:|
| greenhouse | 1,079 | 956 | 88.6 |
| workday | 542 | 446 | 82.3 |
| ashby | 163 | 121 | 74.2 |
| uni_jobs | 204 | 134 | 65.7 |
| devitjobs | 4,534 | 2,831 | 62.4 |
| adzuna | 190 | 113 | 59.5 |
| smartrecruiters | 220 | 125 | 56.8 |
| arbeitnow | 844 | 466 | 55.2 |
| remoteok | 654 | 330 | 50.5 |
| teaching_vacancies | 1,066 | 257 | **24.1** |
| **ALL** | **10,579** | **6,560** | **62.0** |

### 5.5 What is actually IN those 6,560 enriched rows

Percent **empty / unknown**, of the 6,560 enriched rows (not of the catalog):

| Field | % empty/unknown | Note |
|---|---:|---|
| `locations` (`'[]'`) | **100.0** | Known dead — already dropped from the extraction schema (`services/job_enrichment_schema.py:12-19`) |
| `employer_type` (`'unknown'`) | **100.0** | Known dead — same comment |
| `remote_region` (NULL) | **100.0** | Never wired to produce a value |
| `apply_instructions` (NULL) | **100.0** | Never wired |
| `red_flags` (`'[]'`) | **100.0** | Never wired |
| `salary->>'max'` | 96.6 | |
| `salary->>'min'` | 95.7 | |
| `visa_sponsorship` (`'unknown'`) | **93.6** | |
| `salary->>'currency'` | 92.4 | |
| `experience_min_years` (NULL) | **91.0** | |
| `experience_level` (`'unknown'`) | 77.2 | |
| `employment_type` (`'unknown'`) | 63.6 | |
| `seniority` (`'unknown'`) | 61.4 | |
| `workplace_type` (`'unknown'`) | 60.8 | |
| `category` = `'other'` | 42.2 | |
| `preferred_skills` (`'[]'`) | 4.7 | |
| `required_skills` (`'[]'`) | 0.2 | **the one that works** |
| `requirements_summary` (`''`) | 0.0 | always filled |

> **Trap:** the `salary` JSON column is never the literal string `'{}'`, so a naive
> `salary = '{}'` check reports 0% empty. The sub-fields are null ~92–97% of the time.
> The salary shelf inside JOB SOURCE ENRICHMENT is effectively **4–8% filled**.

Compounding: a field 93.6% unknown inside a table covering 62% of jobs is unknown for
**~96% of the whole catalog**.

### 5.6 Embeddings coverage — the biggest hole

| Metric | Value |
|---|---|
| Jobs with an audit row | 687 / 10,579 = **6.5%** |
| Audit rows carrying an actual vector | 454 / 687 = 66.1% |
| **Jobs with a usable vector** | **454 / 10,579 = 4.3%** |
| Model versions in use | 1 — `sentence-transformers/all-MiniLM-L6-v2` (no drift) |

| source | jobs | audit rows | with vector |
|---|---:|---:|---:|
| devitjobs | 4,534 | 121 | 63 |
| greenhouse | 1,079 | 54 | 42 |
| teaching_vacancies | 1,066 | **0** | **0** |
| arbeitnow | 844 | 194 | 91 |
| remoteok | 654 | 125 | 96 |
| workday | 542 | 7 | 6 |
| smartrecruiters | 220 | 7 | 3 |
| uni_jobs | 204 | 7 | 4 |
| adzuna | 190 | 1 | 1 |
| ashby | 163 | 33 | 31 |

`teaching_vacancies` — the 3rd-biggest source — has **zero** embeddings.

### 5.7 Gap ranking (matching value × emptiness)

| Rank | Shelf | Why here |
|---|---|---|
| 1 | `job_embeddings.embedding` | 95.7% of jobs have no vector. Semantic retrieval cannot see them at all. |
| 2 | `job_enrichment` structured fields (`experience_min_years`, `visa_sponsorship`, `employment_type`, `seniority`, `workplace_type`) | Core matchable facts, unknown for 61–93% of an already-62%-covered table → unknown for ~75–97% of the catalog. |
| 3 | `jobs.salary_min` / `salary_max` | 40–46% catalog-wide; 7 of top-10 sources contribute 0%. |
| 4 | `jobs.description` (stub rows) | 24.1% fully empty; 60% of the catalog comes from majority-stub sources. Poisons everything downstream. |
| 5 | `jobs.experience_level` | 31.6%. |
| 6 | `jobs.deadline` | 1.3% — near-total gap, but lower matching value. |
| 7 | `job_enrichment.locations` / `employer_type` / `remote_region` / `apply_instructions` / `red_flags` | 100% empty, but these are **known-dead** fields: 2 already removed from the extraction schema, 3 never wired. Not really gaps — dead weight. |

---

## 6. WHAT IT COSTS TO RUN

### 6.1 Per stage, per 1,000 jobs

| Stage | Cost / 1,000 jobs | Note |
|---|---:|---|
| Fetch | **~$0** | All 40 source instances are free-tier APIs or unmetered scrapers. |
| Store (Postgres) | **~$0.0004** | 2.65 MB per 1,000 jobs × $0.15/GB/mo |
| JOB SOURCE ENRICHMENT | **~$0.39** if OpenAI serves it / **$0** if a free-tier fallback does / **$0 today** (stalled) | see 6.2 |
| Embedding | **$0** in dollars (local CPU) | but 0% of new jobs get one — see §7 |

### 6.2 JOB SOURCE ENRICHMENT — token cost

Provider chain: **OpenAI `gpt-4o-mini` PRIMARY**
(`services/profile/llm_provider.py:329-333`, `core/settings.py:61`) → Gemini
(`gemini-3.7-flash`) → Groq (`llama-3.3-70b-versatile`) → Cerebras (`gpt-oss-120b`),
the last three all free-tier.

| Item | Value | How got |
|---|---:|---|
| System prompt | 43 tokens | **Measured** — tiktoken `o200k_base` on the real `_build_prompt()` |
| User prompt, average | 753 tokens (range 384–1,082) | **Measured** — same, over 8 real job rows sampled from prod |
| **Total input / call** | **~796 tokens** | Measured |
| Output tokens | **~450 — ESTIMATE, NOT MEASURED** | Structural guess from the 16-field schema. No live LLM call was made (hard rule #4). |
| `gpt-4o-mini` price | $0.15 / 1M in, $0.60 / 1M out | Web-verified 2026 |
| **Cost per job (OpenAI)** | **~$0.00039** (~0.04¢) | Computed |
| Cost per job (free fallback) | $0 | — |
| **Actual spend today** | **~$0** | 0 new rows in 8 days |

Avg live description length is **1,722.8 chars** (real Postgres `AVG`), under the
4,000-char truncation in `_build_prompt()` — so most descriptions reach the LLM whole.

Hypothetical, if unstalled and fully OpenAI-paid:

| Scope | Jobs | Cost |
|---|---:|---:|
| Whole current catalog, once | 10,579 | ~$4.13 |
| One day of new jobs | 322 | ~$0.13 |
| One month at current intake | ~9,660 | **~$3.77** |

### 6.3 Storage (Railway published pricing, web-verified 2026)

Volume $0.15/GB/mo · RAM $10/GB/mo · vCPU $20/vCPU/mo · egress $0.05/GB.

| Table | Size | Rows | Bytes/row |
|---|---:|---:|---:|
| `jobs` | 28 MB | 10,579 | ~2.65 KB |
| `job_enrichment` | 2,664 KB | 6,560 | ~416 B |
| `job_embeddings` | 1,896 KB | 687 | ~2.8 KB (a 384-float vector) |
| **CATALOG-side total** | **~32.5 MB** | | |
| Whole DB (incl. SEARCH-side `user_feed` 7,880 KB) | 51 MB | | |

Storage-only cost at 51 MB: **~$0.0075/month**. At 10× (510 MB): **~$0.075/month**.
**The RAM/vCPU share of the real Postgres bill is UNKNOWN** — not measurable this pass,
and on Railway compute normally dominates storage.

### 6.4 Monthly totals

| Scale | Theoretical (fully caught up, OpenAI-paid) | Actual today |
|---|---:|---:|
| Current (~9,660 new jobs/mo) | ~$3.78 enrichment + ~$0.01 storage ≈ **$3.79/mo** | **~$0.01/mo** |
| 10× (~96,600 new jobs/mo) | ~$37.70 + ~$0.08 ≈ **$37.78/mo** | same near-$0 unless §7 is fixed |

### 6.5 The one real external quota

| Quota | Limit | We use | Margin |
|---|---|---|---|
| **SerpApi free tier** | 250 searches/month (50/hr), web-verified 2026 | ≤8/day × 30 = **≤240/month** | **10 searches** |

**Any change that raises the `[:8]` cap in `google_jobs.py:53`, or adds a second daily
run, blows the free tier.** SerpApi does not scale with catalog size — it stays ~240/mo
at 1× or 10×.

Other limits, none binding today: DfE apprenticeships 150 req / 5-min rolling window
(`core/settings.py:318`); Reed client-throttled to concurrent=1 / 2.0s delay
(~1,800 req/hr ceiling vs its 2,000 req/hr budget, `scheduler.py:8`).
No other source carries a documented hard external quota.

---

## 7. WHAT IS BROKEN OR MISSING

*Stated, not fixed. Ordered by impact.*

| # | Problem | Evidence | Impact |
|---|---|---|---|
| **B1** | **Embedding backfill can never run.** `backend/Dockerfile` installs `.[semantic]` + torch (line 20); `backend/Dockerfile.worker` installs plain `.` (line 17) — no sentence-transformers. The ARQ worker is exactly where `refresh_catalog` + the embedding backfill run (`workers/tasks.py:1293-1330`). | `job_embeddings` = 687 rows on both 2026-08-15 and 2026-08-16 while `jobs` grew 9,977 → 10,579. `EMBED_BACKFILL_PER_RUN=300` (`settings.py:227`) means it *should* be attempting 300/run. | 95.7% of the catalog is invisible to semantic retrieval. Structural, not budget. |
| **B2** | **JOB SOURCE ENRICHMENT has been stalled since 2026-08-08.** Cron `enrichment_sweep` fires every 30 min (`workers/settings.py:229`, budget `ENRICHMENT_MAX_JOBS=20`) and lands nothing. | `max(enriched_at)` = `2026-08-08T14:38:24Z`, measured live 2026-08-16. 0 rows in 24h, 0 in 7d. | Coverage frozen at 62% while the catalog grows. Root cause NOT diagnosed. |
| **B3** | **No job-liveness check exists.** The `apply_url` is never re-fetched. `CONFIRMED_EXPIRED`'s docstring promises "a later direct-URL verification step" that was never built. | Zero grep hits in `backend/src/`. The 242 `confirmed_expired` rows have `min(consecutive_misses)=0` — set by `scripts/uk_sweep.py`, not by absence. | Dead links stay in the catalog indefinitely. |
| **B4** | **`likely_stale` does nothing.** 5,060 jobs (47.8%) flagged probably-dead remain fully present and searchable; the flag's only consumer is `should_exclude_from_24h()` (`ghost_detection.py:48-50`). | State counts, §4.2 | Half the catalog may be dead and users still see it. |
| **B5** | **Deadline is 1.3% filled and the structured path is 0%.** 8 source files can set `deadline_source='listing'`; that value appears 0 times in prod. 24 jobs are past their own deadline and still shown. | §4.4 | Expired postings shown as live. |
| **B6** | **Stub descriptions poison everything downstream.** 24.1% of jobs have an empty description; devitjobs/greenhouse/workday/smartrecruiters (60% of the catalog) are majority-under-300-chars. | §5.3 | JOB SOURCE ENRICHMENT and embeddings both read the description — a stub cannot yield facts. |
| **B7** | **`jobs.last_updated_at` is a dead column** — declared at `database.py:119` and `:176`, zero write sites in `backend/src/`, 0% filled in prod. | grep + live count | Schema noise; anything reading it silently gets NULL. |
| **B8** | **5 `job_enrichment` fields are 100% empty.** `locations`, `employer_type` (both already dropped from the extraction schema per `job_enrichment_schema.py:12-19`), plus `remote_region`, `apply_instructions`, `red_flags` (never wired). | §5.5 | Dead columns that look like gaps in every audit. |
| **B9** | **Schema drift:** `run_log.matcher_stats` exists in prod but in no numbered migration — only added via `database.py::_migrate()`. Likewise the real `CREATE TABLE jobs` lives in the legacy `init_db()` path, not a migration; `0011`'s copy is a hand-synced mirror. | §2.4, §2.1 | A fresh migrate-only environment will not match prod. |
| **B10** | **The tiered scheduler is dead code in prod.** `TieredScheduler.tick(force=True)` ignores all tier intervals; `run_forever()` is wired to no process (`scheduler.py:194-199`). Hard rule #15 (new sources must set `.category`) therefore guards a tier that never fires today. | `main.py:944`, `scheduler.py` | One sweep/day, not the tiered polling the code implies. |
| **B11** | **Root `CLAUDE.md` source count is stale** — says 47 entries / 46 classes; live is **41 keys / 40 instances** (`main.py:110-168`, removal documented at `:161-165` on 2026-08-10). | measured 2026-08-16 | Every session that trusts it starts wrong. |
| **B12** | **SerpApi free-tier margin is 10 searches/month.** ≤240 used of 250. | §6.5 | One config change away from a hard failure. |

### Known UNKNOWNS — say "not measured", never guess

| Unknown | Why it matters |
|---|---|
| **Why JOB SOURCE ENRICHMENT stalled on 2026-08-08** (B2 root cause). Candidates not tested: expired/rotated LLM key, all providers stuck in `_DEAD_PROVIDERS`, cron exception. | Deserves a dedicated pass. |
| **Whether `OPENAI_API_KEY` / `ENGINE2_ENABLED` / `ENRICHMENT_ENABLED` are actually set in prod.** `railway variables` is blocked by the permission system here; no provider-tagged `llm_call` log line was observed. | Decides whether enrichment costs real money or $0 free-tier. |
| **Why 08-09 dipped to 73 and 08-10 was 0.** Only the counts were measured. | Could be an outage worth alerting on. |
| **Why some sources show p90 description length ABOVE the coded 5,000-char cap** (arbeitnow p90 = 11,280). | Either legacy rows or a source skipping truncation. |
| **Whether the 233 audit-rows-without-a-vector are historical Chroma-era debris or an ongoing failure.** | Changes whether a backfill is enough. |
| **Whether the 5,060 `likely_stale` jobs are genuinely delisted upstream** — the 70%-completeness gate exists, but no individual job was checked against its real source. | Decides if B4 is a real user-facing problem or false alarm. |
| **Railway-provisioned RAM/vCPU of prod Postgres** — compute dominates Railway billing and was not measurable. | The storage figure alone understates the DB bill. |
| **Enrichment OUTPUT token count (~450)** is an estimate from the schema shape, not a measurement. | Could shift the cost figure meaningfully either way. |
| **Exact request count for the ~30 non-ATS sources per run.** Only the 297 ATS requests were counted exactly. | The ~330–400 total is part-estimate. |
| **`jobs` on-disk ~28 MB vs ~9.4 MB heap** — index bloat vs TOAST'd description text, not investigated. | Only matters at 10×. |

---

## HOW TO RE-MEASURE THIS

Do not trust the numbers above after ~2 weeks. Re-run these.

**Rules:** prod Postgres is **READ-ONLY, `SELECT` only**. Never print a secret or a DSN.
Multi-line `python -c` gets mangled on this shell — write a script under
`backend/scripts/`, run it, then **delete it**.

### A. Live prod queries

```bash
# From: backend/ .  DATABASE_PUBLIC_URL is injected by railway run.
# Write the script first, then:
cd backend && railway run -s Postgres python -X utf8 scripts/_tmp_measure.py
# ...and delete scripts/_tmp_measure.py when done.
```

Script body (`psycopg.connect(os.environ["DATABASE_PUBLIC_URL"])`), the queries that
produced every number above:

```sql
-- §2 table sizes + row counts + column counts
SELECT count(*) FROM information_schema.columns
  WHERE table_schema = current_schema() AND table_name='jobs';   -- 36 on 2026-08-16
  -- current_schema(), NEVER to_regclass/search_path: search_path falls back to public
  -- and a test in its own schema can then silently read/DELETE real public rows.
SELECT count(*) FROM jobs;                                        -- 10,579
SELECT pg_total_relation_size('jobs'), pg_relation_size('jobs');
SELECT conname, contype FROM pg_constraint WHERE conrelid='jobs'::regclass;

-- §4.1 what purge would actually delete
SELECT count(*) FROM jobs
  WHERE COALESCE(last_seen_at, first_seen)::timestamptz < now() - interval '30 days';

-- §4.2 staleness
SELECT staleness_state, count(*), min(consecutive_misses), max(consecutive_misses)
  FROM jobs GROUP BY 1 ORDER BY 2 DESC;

-- §4.4 deadline by source
SELECT source, count(*) AS n,
       count(deadline) AS with_deadline,
       count(*) FILTER (WHERE deadline_source='listing')     AS structured,
       count(*) FILTER (WHERE deadline_source='description') AS regex
  FROM jobs GROUP BY 1 ORDER BY 2 DESC;

-- §4.5 growth
SELECT left(first_seen,10) AS d, count(*) FROM jobs
  WHERE first_seen >= to_char(now()-interval '14 days','YYYY-MM-DD')
  GROUP BY 1 ORDER BY 1;

-- §5.2 shelf fill (repeat per column, and per source with GROUP BY source)
SELECT count(*) AS n,
       round(100.0*count(*) FILTER (WHERE description <> '')/count(*),1) AS pct_desc,
       round(100.0*count(salary_min)/count(*),1)                          AS pct_salmin,
       round(100.0*count(*) FILTER (WHERE experience_level <> '')/count(*),1) AS pct_exp
  FROM jobs;

-- §5.3 description length
SELECT source, count(*),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY length(description)) AS median,
       round(100.0*count(*) FILTER (WHERE length(description)<300)/count(*),1) AS pct_stub
  FROM jobs GROUP BY 1 ORDER BY 2 DESC;

-- §5.4/5.5 JOB SOURCE ENRICHMENT
SELECT count(*) FROM job_enrichment;                 -- 6,560
SELECT max(enriched_at) FROM job_enrichment;         -- STALL CHECK: 2026-08-08T14:38:24Z
SELECT round(100.0*count(*) FILTER (WHERE visa_sponsorship='unknown')/count(*),1),
       round(100.0*count(*) FILTER (WHERE salary->>'min' IS NULL)/count(*),1)
  FROM job_enrichment;   -- note: salary is JSON-in-TEXT, cast it

-- §5.6 embeddings — THE key health number
SELECT count(*) AS audit_rows,
       count(embedding) AS with_vector,
       count(DISTINCT model_version) AS models
  FROM job_embeddings;                                -- 687 / 454 / 1
```

### B. Code-side counts

```bash
# SOURCE_REGISTRY key count (41 on 2026-08-16) — never quote a doc, parse the file
cd backend && python -X utf8 -c "import re,io; s=io.open('src/main.py',encoding='utf-8').read(); b=re.search(r'SOURCE_REGISTRY[^=]*=\s*\{(.*?)\n\}',s,re.S).group(1); print(len(re.findall(r'^\s*\"[a-z0-9_]+\"\s*:',b,re.M)))"

# ATS request volume per night (297) — sum the real slug lists
cd backend && python -X utf8 -c "from src.core import companies as c; print(sum(len(getattr(c,n)) for n in dir(c) if n.isupper() and isinstance(getattr(c,n),(list,tuple))))"

# B1 — the Dockerfile gap. If the worker line lacks [semantic], embeddings cannot run.
grep -n "pip install" backend/Dockerfile backend/Dockerfile.worker
```

### C. Prod health, without touching the DB

```bash
# is the daily catalog refresh still landing?
railway logs --service worker | head -100        # NOTE: logs STREAM — head, never tail
railway deployment list --service worker --json  # confirm which commit is live
```

### D. Provenance discipline

When you refresh this file: **change the date at the top, and change every number you
re-measured.** If you could not measure something, move it to the UNKNOWNS table — do
not leave the old number sitting there looking fresh.

---

*Related: `docs/pillars/03-job-providers.md` (source-by-source detail),
`docs/pillars/02-search-and-match-engine.md` (the SEARCH side, deliberately excluded here),
`ARCHITECTURE.md` (system overview + env-var table),
`docs/product_design_rules.md` (owner rules #29 / #30 / #31).*
