# Pillar 2 — The Search & Match Engine

> **Audience.** Read this if you want to understand what happens *between* "a job posting exists on the internet" (Pillar 3 fetches it) and "a job appears on a user's dashboard ranked 87/100" (Pillar 1 shows it). The engine is the brain — it takes raw postings, filters out the irrelevant ones, scores the survivors against the user, deduplicates near-duplicate listings, enriches the high-scorers with LLM-extracted structured data, and writes everything to the shared `jobs` catalog.
>
> **Scope.** Covers code on `main` as of 2026-05-28 (HEAD `a7a2268`). The engine ships with two opt-in feature flags (`ENRICHMENT_ENABLED`, `SEMANTIC_ENABLED`) that gate the LLM enrichment and embedding/semantic-retrieval paths. **Both default OFF** (CLAUDE.md rule #18) — the doc treats them as "advanced surfaces" and clearly labels what's on/off by default.

---

## 1. TL;DR — what the engine does

> *Once per scheduled tick (or once-shot from the CLI), the engine asks every source for new jobs, runs each posting through a 3-stage prefilter that drops ~99% of noise, scores the survivors on up to **9 dimensions** (4 classic + 4 multi-dim + a combined `match_score`) against the user's profile, deduplicates the result through a 4-layer cascade, optionally has an LLM extract 18 structured fields, optionally encodes a semantic embedding, and finally writes each unique row into the shared `jobs` table.*

The pipeline is a **6-stage straight line**, with the scheduler + circuit breakers wrapped around stage 1:

```
   ┌──── Scheduler + Breakers ────┐
   │                              │
1. │  FETCH    sources → raw jobs │
   └──────────────┬───────────────┘
                  │
2. PREFILTER     3-stage cascade (location → experience → skill)
                  │
3. SCORE         JobScorer.score() → ScoreBreakdown (9 fields)
                  │
4. DEDUP         4-layer (exact → fuzzy → tf-idf → embedding-repost)
                  │
5. ENRICH        [opt-in] LLM → JobEnrichment (18 fields)
                  │
6. STORE         insert into shared catalog `jobs`
                  │
                  └─→ [opt-in] encode embedding → ChromaDB
```

### The one fact that changes everything (2026-04-09)

`backend/src/core/keywords.py` was **emptied** in commit `3ba1342`. Every default skill/title list is `[]`. Read the file's own docstring:

> *"All AI/ML default lists have been removed. The system now requires a user profile (CV upload or manual preferences) — there are no domain-biased defaults to fall back on."*

What survives in `keywords.py`: only `LOCATIONS` (25 UK places) and `VISA_KEYWORDS` (8 phrases) — both genuinely domain-agnostic.

**Implication:** the legacy module-level `score_job(job)` function in `skill_matcher.py:391` is essentially dead code — it still runs, but it scores against empty lists. Every meaningful scoring path in the live system goes through `JobScorer(config, user_preferences, enrichment_lookup)` (Pillar 2 Batch 2.9), and `run_search()` always instantiates it with all three kwargs (`backend/src/main.py:389`).

---

## 2. The Orchestrator — `backend/src/main.py::run_search()`

The 6 stages live inside one async function (`main.py:321-690`). Walking it from top to bottom:

### Stage 0 — Init (`main.py:328-371`)

- Set a per-run `run_uuid` correlation ID via a `contextvar` — every log line and DB write for this run carries it.
- Load the user profile via `storage.load_profile()`. **Fail fast** if no profile exists (exit code 2 from the CLI) — there is no anonymous mode any more.
- Call `generate_search_config(profile)` → `SearchConfig` (job_titles, primary/secondary/tertiary skills, relevance_keywords, locations, search_queries). This is the bridge from Pillar 1 (profile) to Pillar 2 (engine).
- `JobDatabase(path)` → `init_db()` → `_migrate()`. Applies all 14 migrations forward-compatibly with `ALTER TABLE ADD COLUMN` for schema drift.

### Stage 1 — Fetch (`main.py:373-522`)

- **Auto-purge** jobs older than 30 days via `db.purge_old_jobs(days=30)` (CLAUDE.md rule #3 — never change this without confirmation).
- **Instantiate the scorer once**: `scorer = JobScorer(search_config, user_preferences, enrichment_lookup)` (`main.py:389`). All three kwargs — satisfies rule #20.
- **Build sources** via `_build_sources(search_config, ...)` (`main.py:230-318`):
  - Domain-filtered: `classify_user_domain(profile)` returns a set like `{"tech"}` or `{"healthcare", "academia"}`; sources whose `DOMAINS` don't overlap are skipped. Sources marked `"general"` are always included.
  - Yields **49 instances** from a 50-key `SOURCE_REGISTRY` (indeed/glassdoor share the `JobSpySource` class).
- **Snapshot the breaker registry** before dispatch so the run log can show which breakers opened.
- **Dispatch** via `TieredScheduler.tick(force=True)` — the `force=True` bypasses per-source interval timers (the CLI is a one-shot, not a long-running poller).
- **Per-result handling**: each source's outcome is either a `list[Job]` (call `breaker.record_success()`) or an `Exception` (call `breaker.record_failure()`).
- **Ghost-detection sweep** (Pillar 3 Batch 1): jobs that were seen last run but not this run have their `staleness_state` flipped to `'missed'`; jobs seen this run get `last_seen_at = now()`.

### Stage 2 — Prefilter (`backend/src/services/prefilter.py`)

Cheap-to-evaluate gates that eliminate ~99% of postings before the expensive scoring step (blueprint §2):

| Gate | Eliminates | Logic |
| --- | --- | --- |
| **Location + arrangement** | ~70 % | Pass if (a) remote job and user accepts remote/hybrid, (b) job location substring-matches one of `preferred_locations`, or (c) user has no location preference (permissive fallback). |
| **Experience level** | ~50 % of remainder | Infer job level from title regex tokens (`junior`, `senior`, `staff`, `principal`); keep jobs within ±1 band of user's level. |
| **Skill overlap** | ~60–80 % of remainder | At least one skill from the user's profile must appear in title + description (case-insensitive substring). Empty skills → pass. |

Compound effect: ~28 % of raw jobs survive to scoring (empirical numbers deferred to Batch 4 observability work — the "99 %" headline is the cascade upper bound).

### Stage 3 — Score (`backend/src/services/skill_matcher.py`)

This is the heart of the engine. The post-Batch-2.9 scorer returns a 9-field `ScoreBreakdown` dataclass (`scoring_dimensions.py:40-65`):

```python
@dataclass(frozen=True)
class ScoreBreakdown:
    # Classic 4 components (always populated)
    title_score:     int   # 0–40
    skill_score:     int   # 0–40
    location_score:  int   # 0–10
    recency_score:   int   # 0–10
    # Batch 2.9 dimensions (populated only when user_preferences + enrichment present)
    seniority_score: int   # 0–SENIORITY_WEIGHT (default 8)
    salary_score:    int   # 0–SALARY_WEIGHT    (default 10)
    visa_score:      int   # 0–VISA_WEIGHT      (default 6)
    workplace_score: int   # 0–WORKPLACE_WEIGHT (default 6)
    # Final clamped total
    match_score:     int   # min(0, sum - penalties, 100)
```

#### 3.1 Classic 4 components (always on)

- **Title (0–40)** — exact match against `SearchConfig.job_titles` → 40 pts; partial → 20; nothing → 0.
- **Skill (0–40)** — sum across all skills found in `title + description` via word-boundary regex, with `_text_contains_skill()` expanding each skill through `skill_synonyms.aliases_for()` (e.g. "k8s" counts for "kubernetes"). Tiers: primary = 3 pts, secondary = 2, tertiary = 1, capped at 40.
- **Location (0–10)** — remote → 8, UK city/region → 10, anything else → 0. Normalises via alias map first.
- **Recency (0–10)** — driven by the **5-column date model** added in Pillar 3 Batch 1 (`posted_at`, `date_found`, `date_confidence`, `date_posted_raw`). High-confidence `posted_at` gets the full band; fabricated dates (negative or future) score 0; low-confidence falls to 60 % of the band. This is the *anti-staleness* signal — old reposted jobs don't game freshness.

#### 3.2 Batch 2.9 multi-dimensions (active only with `user_preferences` + `enrichment_lookup`)

Each scorer reads from the `JobEnrichment` row (a no-op if the row is missing or the user kwarg is None — silent zero-padding is the rule-#20 footgun the next contributor must avoid):

- **`seniority_score`** (`scoring_dimensions.py:104-129`) — maps job's `seniority` enum (intern → director, 0–6) and user's `experience_level` to the 0–6 scale. Curve: 0-diff → full, 1-diff → 62 %, 2-diff → 25 %, 3+ → 0. Missing signal → 50 %.
- **`salary_score`** (`scoring_dimensions.py:137-182`) — band-overlap ratio between job's `SalaryBand` (normalised to annual GBP via `salary.normalize_salary()`) and user's `salary_min`/`salary_max`. No overlap → 0; missing data → 50 %.
- **`visa_score`** (`scoring_dimensions.py:190-206`) — if `user.needs_visa=False`, score is 0 (irrelevant). If True: job-`visa_sponsorship=yes` → full, `=no` → 0, `=unknown` → 50 %.
- **`workplace_score`** (`scoring_dimensions.py:221-249`) — exact (remote/onsite/hybrid) → full; hybrid-vs-remote or hybrid-vs-onsite → 50 % compromise; remote-vs-onsite → 0; missing → 50 %.

#### 3.3 Penalties + gates

- **Negative title** (`-30`) — title contains a word from `NEGATIVE_TITLE_KEYWORDS`. *Note:* the list is empty by default now, so this fires only when the user populates negative keywords on their profile.
- **Foreign location** (`-15`) — location matches `FOREIGN_INDICATORS` (63 entries — countries, major non-UK cities, US state abbreviations like `, CA`).
- **Title-gate / Skill-gate** (`MIN_TITLE_GATE=0.15`, `MIN_SKILL_GATE=0.15`) — if either component is below 15 % of its max (6 pts of 40), the entire score collapses to `max(10, (title+skill)*0.25)`. This prevents a perfect location + recency from elevating an obviously-irrelevant job.

#### 3.4 Final clamp

```python
match_score = max(0, min(100, sum_of_all_dim_scores - penalties))
```

The 8 dimensions can sum above 100 (max raw is 40+40+10+10 + 8+10+6+6 = **130**), so the clamp protects the [0, 100] contract that everything downstream depends on.

### Stage 4 — Dedup (`backend/src/services/deduplicator.py`)

Four layers run in sequence; each layer collapses near-duplicates into the highest-ranked survivor. Lazy imports per CLAUDE.md rule #16 — none of the heavy deps load unless their layer fires.

| Layer | Heavy dep | Default | Threshold / rule |
| --- | --- | --- | --- |
| **1. Exact normalised key** | — | always on | Group by `(normalized_company, _normalize_title(title))`. `_normalize_title` is *wider* than the DB's `normalized_key()` — strips seniority prefixes, trailing job codes, parentheticals, marketing suffixes. (Per-run dedup is allowed to be more aggressive than cross-run DB uniqueness.) |
| **2. RapidFuzz fuzzy** | `rapidfuzz` | on | `token_set_ratio(title_a, title_b) ≥ 80` AND `ratio(company_a, company_b) ≥ 85` AND normalised location matches. Skipped silently if `rapidfuzz` not installed. |
| **3. TF-IDF cosine** | `scikit-learn` | on | Document = `company + title + description[:200]`. Cosine ≥ 0.85 clusters merge via union-find. |
| **4. Embedding repost** | `sentence_transformers` + `chromadb` | **opt-in** | Within same company, cosine ≥ 0.92 → dedup. Requires an `embedding_lookup: dict[job_id, vector]` to be passed in. Preserves earliest `first_seen_at`. |

**Tie-break ranking** (used in every layer):
1. `match_score` (primary)
2. **Enrichment bonus** (+5 if the job has a `job_enrichment` row — encourages enriched candidates to win, so structured data survives downstream)
3. **Completeness** (+10 each for salary_min/max, +5 for location, +min(len(desc), 20) for description)

### Stage 5 — Enrich (opt-in, `ENRICHMENT_ENABLED=true`)

- Gate: only jobs with `match_score >= ENRICHMENT_THRESHOLD` (default 60) are sent to the LLM.
- `enrich_batch(jobs, semaphore_limit=10, skip_existing=True)` (`job_enrichment.py`) runs asyncio-parallel LLM calls capped at 10 concurrent.
- Each call goes through `llm_extract_validated(prompt, JobEnrichment, max_retries=2)`:
  - **Provider chain**: Gemini (`gemini-2.0-flash`, best JSON) → Groq (`llama-3.3-70b-versatile`) → Cerebras (`llama3.1-8b`, fastest).
  - **Self-correction loop**: on Pydantic `ValidationError`, the first 5 error messages are appended to the prompt and the call retries up to 2 more times.
  - If all providers fail or retries exhaust → `RuntimeError` (logged, no partial row written — atomicity over best-effort).
- Output: a `JobEnrichment` row with **18 strict-typed fields** (see §3.3 below).
- DB persistence: `INSERT OR REPLACE` into `job_enrichment` table (migration `0008`). **Shared catalog** — no `user_id` column, per CLAUDE.md rule #17 (the same enriched fields apply to every user; per-user scoring against the enrichment happens at read time).

### Stage 6 — Store + (opt-in) embed

- `db.insert_job(job)` does `INSERT OR IGNORE` on `(normalized_company, normalized_title)` UNIQUE — returns `True` for new rows, `False` for cross-run duplicates already in the catalog. **Never touch `normalized_key()`** without checking the dedup chain (CLAUDE.md rule #1).
- If `SEMANTIC_ENABLED=true`, lazy-import `embeddings` + `vector_index`, encode each newly-inserted job via `encode_job(job, enrichment)` and `VectorIndex.upsert(job_id, vector)`.
- `db.log_run(stats, run_uuid, per_source_errors, per_source_duration, total_duration)` writes the `run_log` row.
- Finally: CSV export → Markdown report → channel notifications (the *old* per-source notification system from `services/notifications/`; the per-user `channels/dispatcher` runs only under the ARQ worker, not the CLI).

---

## 3. Detail surfaces — what each component actually contains

### 3.1 The `JobScorer` class — `backend/src/services/skill_matcher.py:416-542`

Two distinct call signatures, and the difference is the difference between "legacy" and "Pillar-2-active":

```python
# Legacy — still callable, but produces zero-padded dim scores
scorer = JobScorer(search_config)
breakdown = scorer.score(job)
# breakdown.seniority_score == 0, .salary_score == 0, etc.

# Pillar-2-active — what run_search() and the worker use
scorer = JobScorer(search_config, user_preferences=prefs, enrichment_lookup=lookup)
breakdown = scorer.score(job)
# All 9 fields populated from real data
```

**Rule #20 is the footgun**: passing `user_preferences` but *not* `enrichment_lookup` (or vice-versa) silently produces zero-padded dim scores — the columns look populated in the DB but mean nothing. Pass both or neither.

### 3.2 The 18-field `JobEnrichment` schema — `backend/src/services/job_enrichment_schema.py`

| # | Field | Type | Notes |
| --- | --- | --- | --- |
| 1 | `title_canonical` | str (1–200) | LLM-rewritten title for matching |
| 2 | `category` | `JobCategory` enum | 16 values (software_engineering, data_science, healthcare, …, other) |
| 3 | `employment_type` | `EmploymentType` enum | full_time / part_time / contract / internship / temporary / apprenticeship / freelance / unknown |
| 4 | `workplace_type` | `WorkplaceType` enum | remote / onsite / hybrid / unknown |
| 5 | `locations` | list[str] ≤10 | Free-form place strings, stripped + deduped |
| 6 | `salary` | `SalaryBand` (nested) | min, max, currency (uppercased), frequency (hourly/daily/monthly/annual/unknown) |
| 7 | `required_skills` | list[str] ≤30 | Curated, not exhaustive |
| 8 | `preferred_skills` | list[str] ≤30 | Nice-to-haves |
| 9 | `experience_min_years` | int 0–40 \| null | Minimum years |
| 10 | `experience_level` | `ExperienceLevel` enum | entry / mid / senior / unknown |
| 11 | `requirements_summary` | str ≤250 | Condensed — also fed to the embedding encoder |
| 12 | `language` | str (ISO 639-1) | Default `"en"` |
| 13 | `employer_type` | `EmployerType` enum | startup / scaleup / enterprise / agency / nonprofit / government / academic / healthcare / other / unknown |
| 14 | `visa_sponsorship` | `VisaSponsorship` enum | yes / no / unknown |
| 15 | `seniority` | `SeniorityLevel` enum | intern / junior / mid / senior / staff / principal / director / unknown |
| 16 | `remote_region` | str ≤60 \| null | Geographic scope for remote roles |
| 17 | `apply_instructions` | str ≤500 \| null | URL or notes |
| 18 | `red_flags` | list[str] ≤10 | Warning signals ("requires unpaid work", "MLM signal") |

All length-bounded to prevent DB bloat from a malformed LLM response.

### 3.3 Skill synonyms — `backend/src/core/skill_synonyms.py`

A flat 529-entry alias dict (`_ALIASES_TO_CANONICAL`) makes `"k8s"`, `"kube"`, and `"kubernetes"` interchangeable everywhere skill matching happens. Two helpers:

- `canonicalize_skill(raw) -> str` — case-fold, whitespace-normalise, look up; unknown terms pass through unchanged.
- `aliases_for(skill) -> tuple[str, ...]` — returns canonical + all known surface strings (for word-boundary regex grep in job text).

Coverage spans programming languages, frameworks, databases, cloud platforms, DevOps tools, AI/ML, data engineering, security/compliance, medical, finance, legal, HR/PM, and marketing.

### 3.4 Salary normalisation — `backend/src/services/salary.py` + `backend/src/core/fx.py`

`normalize_salary(SalaryBand, to_currency="GBP")` rolls *any* posted salary to **annual GBP** (`int_min, int_max`):

| Frequency | Multiplier |
| --- | --- |
| hourly | × 2080 (40 h × 52 wk) |
| daily | × 260 (5 d × 52 wk) |
| weekly | × 52 |
| monthly | × 12 |
| annual | × 1 |
| unknown | × 1 (safe default) |

Then `to_gbp(amount, currency)` converts via a **21-currency** static rate table (`fx.py`, Q1 2026 averages — USD=0.79, EUR=0.86, JPY=0.0053, …). Unknown currency → ×1.0 (treated as already-GBP — safe degraded mode).

If only one bound is posted, the other is backfilled (single-point band). Returns `None` if both bounds are absent — downstream `salary_score()` then awards the neutral 50 %.

### 3.5 Domain classifier — `backend/src/services/domain_classifier.py`

`classify_user_domain(profile)` inspects the merged profile (`target_job_titles`, `additional_skills`, CV titles + skills, LinkedIn industry + positions) against five keyword sets:

| Domain | Keywords | Example matches |
| --- | --- | --- |
| `tech` | 37 | engineer, developer, python, react, kubernetes, aws |
| `healthcare` | 26 | nurse, doctor, gp, paramedic, nhs, pharmacist |
| `academia` | 13 | professor, postdoc, phd, research fellow, university |
| `education` | 11 | teacher, headteacher, tutor, qts, pgce, apprenticeship |
| `climate` | 11 | climate, sustainability, renewable, carbon, net zero, esg |

Returns a set; the source builder then keeps only sources whose own `DOMAINS` overlap (or are flagged `"general"`). Empty user-domain set → keep all sources (permissive fallback).

---

## 4. Cross-cutting infrastructure

### 4.1 Tiered scheduler — `backend/src/services/scheduler.py`

Per-source polling cadence:

| Tier | Interval | Examples |
| --- | --- | --- |
| `ats` | 60 s | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Pinpoint, Personio, Rippling, Comeet |
| `reed` (name override) | 5 min | (Reed's quota is 2000 req/hr, hand-tuned) |
| `workday` (name override) | 15 min | (anti-scraper) |
| `rss` | 15 min | jobs.ac.uk, NHS Jobs, WeWorkRemotely, FindAJob, BioSpace, … |
| `scrapers` | 60 min | LinkedIn, JobTensor, BCSJobs, AIJobs.* |
| `keyed_api` | 60 min | Adzuna, JSearch, Jooble, Careerjet, Findwork, Google Jobs (SerpApi) |
| `free_json` | 60 min | Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITJobs, LandingJobs |
| `default` | 60 min | Anything un-categorised falls here |

`TieredScheduler.tick(force=False)` filters `due_sources(now)`, consults the breaker registry for each (`breaker.can_proceed()`), and `asyncio.gather`s the survivors. CLI uses `force=True` for one-shot dispatch.

> **Rule #15**: new sources MUST set `.category` to a tier key (or add a `NAME_TIER[source.name]` override). Un-tiered sources fall to the 60-min default — harmless but wastes the freshness upside.

### 4.2 Circuit breakers — `backend/src/services/circuit_breaker.py`

Per-source state machine:

```
CLOSED ─ 5 consecutive failures ──▶ OPEN ─ 300 s cooldown ──▶ HALF_OPEN
   ▲                                                              │
   └─────── success ─────────── HALF_OPEN ─ failure ──▶ OPEN (fresh)
```

- Defaults: `failure_threshold=5`, `cooldown_seconds=300`.
- `BreakerRegistry.get(name)` — lazy per-source factory.
- `default_registry()` — module-level singleton shared by `run_search()`.
- Snapshot via `.snapshot() -> {name: state}` for run-log observability.

### 4.3 Conditional cache — `backend/src/services/conditional_cache.py`

A 256-entry FIFO `OrderedDict` keyed by `(url, params_tuple)` storing `(body, etag, last_modified)`. Sources opt in by calling `self._get_json_conditional(url)` instead of `self._get_json(url)` (CLAUDE.md rule #14). On a hit with a matching validator the upstream returns 304 and the cached body is replayed; a miss falls through to a normal GET with all retry/backoff machinery intact.

> **Use sparingly**: only worth it for upstreams that honour ETag/Last-Modified (ATS boards with CDN fronts, well-behaved RSS feeds). A source polling 60 s on an endpoint without validators will just thrash the cache.

### 4.4 Database schema — `backend/src/repositories/database.py`

Engine-relevant tables and columns:

| Table | Key columns | Purpose |
| --- | --- | --- |
| `jobs` | `(normalized_company, normalized_title) UNIQUE` | Shared catalog (no `user_id`). Score columns (`match_score`, `role`, `skill`, `seniority_score`, …), date columns (`posted_at`, `date_found`, `date_confidence`, `date_posted_raw`), lifecycle (`first_seen_at`, `last_seen_at`, `last_updated_at`, `staleness_state`). |
| `run_log` | `(timestamp, run_uuid)` | Per-run stats: `total_found`, `new_jobs`, `sources_queried`, `per_source` (JSON), `per_source_errors`, `per_source_duration`, `total_duration` (migration `0010`). |
| `job_enrichment` | `job_id PK FK → jobs(id)` | 18 enrichment fields + `enriched_at` (migration `0008`). Shared catalog (rule #17). |
| `job_embeddings` | `job_id PK FK → jobs(id)` | Audit only: `model_version`, `embedding_updated_at`. Actual vectors in ChromaDB at `backend/data/chroma/` (migration `0009`). |

`_migrate()` in `database.py:90-197` is **forward-compat-only** — applies `ALTER TABLE ADD COLUMN` for missing columns so an older DB on disk auto-upgrades. The migration runner in `backend/migrations/runner.py` is the new system (Batch 2+) and applies the numbered `.up.sql` / `.down.sql` files.

---

## 5. The opt-in advanced surfaces (off by default)

Both flags default `false` per CLAUDE.md rule #18, and the **no-op path must exactly match pre-Pillar-2 behaviour**. Documented here for completeness.

### 5.1 `ENRICHMENT_ENABLED=true` → LLM enrichment

When on:
- Stage 5 runs (see §2).
- `JobScorer` gains a populated `enrichment_lookup` and the Batch 2.9 dimension scorers actually fire.
- Dedup tie-breaker uses the `+5` enrichment bonus.

When off:
- `enrichment_lookup` is an empty dict → all four dim scorers return 0 → `JobScorer` effectively reverts to the legacy 4-component formula.
- No LLM API calls, no `job_enrichment` DB writes.

### 5.2 `SEMANTIC_ENABLED=true` → embeddings + hybrid retrieval

When on:
- Stage 6 encodes each new job via `encode_job(job, enrichment)`:
  - Model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, lazy-loaded).
  - Base text: `title | requirements_summary | required_skills_joined`.
  - Long descriptions (>300 words) are chunked 300/50 and max-pooled (the "asymmetric short-query-long-document" pattern).
- Vector is `VectorIndex.upsert(job_id, vector)` into ChromaDB at `backend/data/chroma/`.
- API queries can pass `?mode=hybrid` to invoke `retrieve_for_user()` which:
  1. Pulls keyword top-500 from SQL (`JobScorer.match_score` ranking).
  2. Pulls semantic top-500 from ChromaDB (nearest-neighbour on the encoded profile).
  3. Fuses via **Reciprocal Rank Fusion** with `k=60`: `score(item) = Σ 1 / (k + rank_i + 1)` across all input lists.
  4. Optionally reranks the top-50 with the **cross-encoder** `cross-encoder/ms-marco-MiniLM-L-6-v2` (Batch 2.8).
- ESCO skill normalisation in CV parsing also flips on (Pillar 1 Ring 2 §3.2).

When off:
- No `sentence_transformers` import at all (saves ~150 ms–2 s startup).
- No ChromaDB queries; retrieval is keyword-only.
- `is_hybrid_available(vector_index_count)` returns `False` → API defaults `mode=keyword`.

### 5.3 The lazy-import rule (CLAUDE.md #16)

Heavy deps are *only* imported inside the functions that need them:

- `apprise` — in `channels/dispatcher.py` (Pillar 1 Ring 3)
- `sentence_transformers` — in `embeddings._load_encoder()` and `retrieval.cross_encoder_rerank()`
- `chromadb` — in `vector_index._make_client()`
- `rapidfuzz` — in `deduplicator._merge_fuzzy()`
- `sklearn` — in `deduplicator._merge_tfidf()`

A top-level import would pay the cost on every pytest collection, every CLI invocation, every API process. The pattern is non-negotiable.

---

## 6. Current status — what works, what's incomplete

Legend: ✅ done & wired · 🟡 partial · ❌ planned but not built · ⚠️ subtle gap

### 6.1 Scoring core

| Surface | Status | Notes |
| --- | --- | --- |
| Classic 4-component scoring (title / skill / location / recency) | ✅ | `skill_matcher.py:391` (`score_job`) + `JobScorer.score()` |
| Title-gate / skill-gate (`MIN_TITLE_GATE=0.15`) | ✅ | prevents location-only inflation |
| Negative-title penalty (-30), foreign-location penalty (-15) | ✅ | `UK_TERMS` (19), `REMOTE_TERMS` (4), `FOREIGN_INDICATORS` (63) |
| Batch 2.9 multi-dim (seniority / salary / visa / workplace) | ✅ | `scoring_dimensions.py`, env-tunable weights |
| 9-field `ScoreBreakdown` dataclass | ✅ | replaces flat int return |
| Skill synonym expansion (529 aliases) | ✅ | `skill_synonyms.py` + `_text_contains_skill` |
| Word-boundary skill matching | ✅ | prevents "rust" matching "trust" |
| 5-column date-confidence model for recency | ✅ | Pillar 3 Batch 1 — fabricated dates score 0 |
| Empty `keywords.py` (user-profile mandatory) | ✅ | architectural inflection 2026-04-09 |
| Legacy `score_job()` still callable | ⚠️ | scores against empty lists → near-zero. Dead in practice but not removed for back-compat with old imports |
| Per-dimension weights configurable via env vars | ✅ | `SALARY_WEIGHT`, `SENIORITY_WEIGHT`, `VISA_WEIGHT`, `WORKPLACE_WEIGHT` |
| Step-1.5 DB round-trip + HTTP value-presence tests for dim columns | ✅ | rule #21 — `test_database.py::test_dim_columns_round_trip` + `test_api.py::test_jobs_response_includes_score_dim_breakdown` |
| `user_preferences` + `enrichment_lookup` co-required (rule #20) | ✅ | enforced by `run_search` and `score_and_ingest` (both pass both) |

### 6.2 Prefilter + Dedup

| Surface | Status | Notes |
| --- | --- | --- |
| 3-stage prefilter cascade | ✅ | `prefilter.py` |
| "99 %" elimination empirical metric | 🟡 | upper-bound math is in code, observability is Batch-4 scope |
| Dedup Layer 1 — exact normalised key | ✅ | wider than DB `normalized_key()` |
| Dedup Layer 2 — RapidFuzz | ✅ | thresholds 80/85; lazy import, graceful degradation |
| Dedup Layer 3 — TF-IDF cosine 0.85 | ✅ | sklearn lazy import |
| Dedup Layer 4 — embedding repost 0.92 | 🟡 | opt-in via `enable_embedding_repost=True`; needs caller to pass `embedding_lookup` |
| Tie-break with `+5` enrichment bonus | ✅ | enriched jobs win ties |

### 6.3 Enrichment (opt-in)

| Surface | Status | Notes |
| --- | --- | --- |
| `JobEnrichment` 18-field schema + 8 enums + `SalaryBand` | ✅ | `job_enrichment_schema.py`, all length-bounded |
| `enrich_batch()` with `asyncio.Semaphore(10)` | ✅ | per-job error isolation |
| `INSERT OR REPLACE` upsert into `job_enrichment` table | ✅ | migration `0008`, shared catalog |
| Multi-provider LLM fallback (Gemini → Groq → Cerebras) | ✅ | `llm_provider.llm_extract` |
| Self-correction loop (max 2 retries with appended errors) | ✅ | `llm_extract_validated` |
| `_build_enrichment_lookup()` bulk-load for scoring | ✅ | graceful empty-dict on missing table |
| `ENRICHMENT_THRESHOLD=60` gate | ✅ | only high-scoring jobs sent to LLM |
| `ENRICHMENT_ENABLED` flag defaults `false` | ✅ | rule #18 |
| Cost tracking per provider call | ❌ | no `llm_usage` table yet |

### 6.4 Embeddings + Retrieval (opt-in)

| Surface | Status | Notes |
| --- | --- | --- |
| `encode_job()` 384-dim with 300/50 chunking | ✅ | `sentence-transformers/all-MiniLM-L6-v2`, lazy |
| `VectorIndex` ChromaDB wrapper | ✅ | persistent at `backend/data/chroma/` |
| `job_embeddings` audit table | ✅ | migration `0009` (vectors in ChromaDB, audit in SQLite) |
| `reciprocal_rank_fusion(k=60)` | ✅ | pure function, deterministic tiebreaker |
| `retrieve_for_user()` hybrid orchestrator | ✅ | injectable keyword_fn / semantic_fn for testability |
| Cross-encoder rerank top-50 | ✅ | `cross-encoder/ms-marco-MiniLM-L-6-v2`, lazy |
| `is_hybrid_available()` API guard | ✅ | falls back to keyword-only |
| `SEMANTIC_ENABLED` flag defaults `false` | ✅ | rule #18 |
| Background re-embedding when model_version changes | ❌ | manual rebuild only |
| Vector quantisation for storage | ❌ | full-precision float32 |

### 6.5 Orchestrator + Scheduler

| Surface | Status | Notes |
| --- | --- | --- |
| `run_search()` 6-stage pipeline | ✅ | `main.py:321-690` |
| Domain-filtered source build | ✅ | `classify_user_domain` × source `.DOMAINS` |
| 49 source instances from 50-key registry | ✅ | `SOURCE_INSTANCE_COUNT = 49` |
| `TieredScheduler.tick(force=True)` one-shot dispatch | ✅ | CLI path |
| `TieredScheduler.run_forever()` long-running poller | 🟡 | written but not wired to systemd (Batch 4 scope) |
| `CircuitBreaker` 5-fail/300s state machine | ✅ | per-source registry |
| `BreakerRegistry.snapshot()` for run-log | ✅ | logged at run end |
| `ConditionalCache` 256-entry FIFO | ✅ | opt-in per-source via `_get_json_conditional()` |
| Per-run `run_uuid` correlation in contextvar | ✅ | every log line + DB write tagged |
| Ghost-detection (`last_seen_at` + `staleness_state='missed'`) | ✅ | per-run pass + `nightly_ghost_sweep` worker task |
| Auto-purge >30 days via `purge_old_jobs(days=30)` | ✅ | rule #3 — never touch without confirmation |
| `forward-compat` `_migrate()` for ALTER ADD COLUMN | ✅ | `database.py:90-197` |
| `run_log` observability columns (errors, durations) | ✅ | migration `0010` |

### 6.6 Worker path

| Surface | Status | Notes |
| --- | --- | --- |
| `score_and_ingest(ctx, job_id, users_override)` | ✅ | per-user scoring, multi-tenant |
| Enrichment fired once per job (not per user) | ✅ | rule #17 — shared catalog |
| ARQ + Redis production wiring | 🟡 | `workers/settings.py` exists; deployment is install-dependent |
| Test path uses pure async, no Redis | ✅ | rule #11 — no top-level `arq` import |

---

## 7. Quick reference — every file in the Engine pillar

```
backend/
├── src/
│   ├── main.py                                — run_search() + SOURCE_REGISTRY + _build_sources()
│   ├── cli.py                                 — `run` command entry
│   ├── core/
│   │   ├── keywords.py                        — empty defaults + LOCATIONS + VISA_KEYWORDS (post-3ba1342)
│   │   ├── settings.py                        — MIN_MATCH_SCORE, weights, gates, feature flags
│   │   ├── skill_synonyms.py                  — 529-entry alias dict
│   │   └── fx.py                              — 21-currency → GBP rates
│   ├── services/
│   │   ├── skill_matcher.py                   — JobScorer + score_job + helpers (legacy + Batch 2.9 paths)
│   │   ├── scoring_dimensions.py              — seniority / salary / visa / workplace scorers + ScoreBreakdown
│   │   ├── prefilter.py                       — 3-stage cascade
│   │   ├── deduplicator.py                    — 4-layer dedup (exact / fuzzy / TF-IDF / embedding)
│   │   ├── domain_classifier.py               — tech / healthcare / academia / education / climate
│   │   ├── salary.py                          — normalize_salary() hourly→annual GBP
│   │   ├── job_enrichment.py                  — enrich_batch() + DB helpers (opt-in)
│   │   ├── job_enrichment_schema.py           — 18-field Pydantic JobEnrichment
│   │   ├── embeddings.py                      — encode_job() (opt-in, lazy)
│   │   ├── vector_index.py                    — ChromaDB wrapper (opt-in, lazy)
│   │   ├── retrieval.py                       — RRF fusion + cross-encoder rerank (opt-in)
│   │   ├── scheduler.py                       — TieredScheduler + TIER_INTERVALS_SECONDS
│   │   ├── circuit_breaker.py                 — 5-fail/300s state machine + BreakerRegistry
│   │   ├── conditional_cache.py               — 256-entry FIFO for ETag/Last-Modified
│   │   └── profile/llm_provider.py            — Gemini/Groq/Cerebras chain + llm_extract_validated
│   ├── workers/tasks.py                       — score_and_ingest (per-user worker path)
│   ├── repositories/database.py               — JobDatabase + 14-migration forward-compat schema
│   └── api/routes/jobs.py                     — exposes match_score + 9-field breakdown to API
└── migrations/
    ├── 0008_job_enrichment.up.sql             — shared-catalog enrichment table
    ├── 0009_job_embeddings.up.sql             — audit row (vectors live in ChromaDB)
    ├── 0010_run_log_observability.up.sql      — per-source errors/durations, run_uuid
    └── 0011_score_dimensions.up.sql           — 9 dim columns on jobs table

backend/data/chroma/                            — (gitignored) ChromaDB persistent collection
```

Test coverage (relevant files):

```
tests/
├── test_scorer.py                              — score_job + JobScorer + gates + penalties
├── test_scoring_dimensions.py                  — seniority / salary / visa / workplace dim logic
├── test_skill_synonyms.py                      — canonicalize_skill + aliases_for + table size
├── test_salary.py                              — frequency annualization + FX conversion
├── test_domain_classifier.py                   — classify_user_domain + source_matches_user_domains
├── test_skill_tiering.py                       — primary/secondary/tertiary tiering
├── test_skill_normalizer.py                    — ESCO path (SEMANTIC_ENABLED)
├── test_job_enrichment.py                      — enrich_batch + DB helpers + mocked LLM
├── test_embeddings.py                          — encode_job + chunking + fake encoder factory
├── test_vector_index.py                        — upsert/query/delete + fake ChromaDB client
├── test_retrieval.py                           — RRF + retrieve_for_user + rerank + fallback
├── test_retrieval_integration.py               — real scorer + semantics
├── test_deduplicator.py                        — all 4 layers + graceful degradation
├── test_llm_provider.py                        — provider chain + retry/validation loop
├── test_prefilter.py                           — 3-stage cascade rules
├── test_main.py                                — full pipeline integration + SOURCE_INSTANCE_COUNT
├── test_scheduler.py                           — tier resolution + due-source filter + tick
├── test_circuit_breaker.py                     — CLOSED → OPEN → HALF_OPEN transitions
├── test_conditional_fetch.py                   — ETag/Last-Modified + FIFO eviction
├── test_worker_tasks.py                        — score_and_ingest per-user path
└── test_database.py                            — schema + migrations + insert/purge/log_run
```

---

## 8. What this pillar does *not* cover

- **The user's preferences & CV that feed the engine** → Pillar 1 (User Side) — `services/profile/`, `keyword_generator.py`, the bridge into `SearchConfig`.
- **Where the raw job postings come from** → Pillar 3 (Job Providers) — the 49 source classes, `BaseJobSource`, the rotation history, ATS company slug catalog, conditional-fetch opt-ins per source.
- **How a scored job reaches a specific user's inbox** → Pillar 1 Ring 3 — `user_feed`, `notification_ledger`, `dispatcher.py`, ARQ workers.

---

## 9. Architectural rules touched by this pillar

The CLAUDE.md project rules that are *load-bearing* for the engine (don't violate without coordinated change):

- **#1** — never touch `normalized_key()` in `models.py` without checking the deduplicator and the DB UNIQUE constraint.
- **#3** — never touch `purge_old_jobs` without confirmation.
- **#9** — scoring changes require test verification across 53 scorer tests + 55 profile tests.
- **#10** — `jobs` table has no `user_id` by design. Per-user scoring against the shared catalog is done at read-time.
- **#16** — heavy deps (`sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`) MUST be lazy-imported.
- **#17** — `job_enrichment` and `job_embeddings` must NOT gain a `user_id` column.
- **#18** — `ENRICHMENT_ENABLED` and `SEMANTIC_ENABLED` default off. No-op path must match pre-Pillar-2 behaviour.
- **#19** — `JobScorer` gained optional multi-dim kwargs. Don't flip defaults silently.
- **#20** — multi-dim scoring requires *both* `user_preferences` AND `enrichment_lookup`. Passing only one silently zero-pads.
- **#21** — value-presence > schema-presence. New engine-side fields need a real round-trip test, not just a schema assertion.

---

*Last updated 2026-05-28. HEAD `a7a2268`. Backend test baseline 600p/0f/3s.*
