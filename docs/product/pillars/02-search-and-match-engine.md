<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Pillar 2 — The Search & Match Engine

> **Audience.** Read this if you want to understand what happens *between* "a job posting exists on the internet" (Pillar 3 fetches it) and "a job appears on a user's dashboard ranked 87/100" (Pillar 1 shows it). The engine is the brain — it takes raw postings, filters out the irrelevant ones, scores the survivors against the user, deduplicates near-duplicate listings, enriches the high-scorers with LLM-extracted structured data, and writes everything to the shared `jobs` catalog.
>
> **Scope.** Covers code on `main` as of 2026-05-28 (HEAD `a7a2268`). The engine ships with two opt-in feature flags (`ENRICHMENT_ENABLED`, `SEMANTIC_ENABLED`) that gate the LLM enrichment and embedding/semantic-retrieval paths. **Both default OFF** (CLAUDE.md rule #18) — the doc treats them as "advanced surfaces" and clearly labels what's on/off by default.

---

## 1. TL;DR — what the engine does

> *Once per scheduled tick (or once-shot from the CLI), the engine asks every source for new jobs, runs each posting through a 3-stage prefilter that drops ~99% of noise, scores the survivors on up to **9 dimensions** (4 classic + 4 multi-dim + a combined `match_score`) against the user's profile, deduplicates the result through a 4-layer cascade, optionally has an LLM extract 16 structured fields, optionally encodes a semantic embedding, and finally writes each unique row into the shared `jobs` table.*

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
5. ENRICH        [opt-in] LLM → JobEnrichment (16 fields)
                  │
6. STORE         insert into shared catalog `jobs`
                  │
                  └─→ [opt-in] encode embedding → job_embeddings.embedding (pgvector)
```

### The one fact that changes everything (2026-04-09)

`backend/src/core/keywords.py` was **emptied** in commit `3ba1342`. Every default skill/title list is `[]`. Read the file's own docstring:

> *"All AI/ML default lists have been removed. The system now requires a user profile (CV upload or manual preferences) — there are no domain-biased defaults to fall back on."*

What survives in `keywords.py`: only `LOCATIONS` (25 UK places) and `VISA_KEYWORDS` (8 phrases) — both genuinely domain-agnostic.

**Implication:** the legacy module-level `score_job(job)` function in `skill_matcher.py:416` is essentially dead code — it still runs, but it scores against empty lists. Every meaningful scoring path in the live system goes through `JobScorer(config, user_preferences, enrichment_lookup)` (Pillar 2 Batch 2.9), and `run_search()` always instantiates it with all three kwargs (`backend/src/main.py:857`).

---

## Walkthrough — Trace one posting through the engine (worked example)

> A concrete trace through all six stages with one made-up but realistic posting and one made-up user profile. Every step shows the numeric output you'd see in the DB, so you can sanity-check the formula on the way through.

### The inputs

**Posting (returned by `GreenhouseSource.fetch_jobs()` from company `acme-corp`):**

```python
Job(
    title="Senior Python Engineer",
    company="Acme Corp Ltd",
    apply_url="https://boards.greenhouse.io/acme-corp/jobs/12345",
    source="greenhouse",
    location="London, UK (Hybrid)",
    description="We're hiring a senior backend engineer. Required: Python, AWS, "
                "Docker. Bonus: Kubernetes, PostgreSQL. 5+ years experience. "
                "£70k–£90k. We sponsor visas.",
    date_found="2026-05-28T14:30:00Z",
    posted_at="2026-05-28T09:00:00Z",
    date_confidence="high",
)
```

**User profile (Alice):**

```python
UserPreferences(
    target_job_titles=["Senior Python Engineer", "Staff Engineer"],
    additional_skills=["python", "aws", "docker", "postgres"],
    preferred_locations=["London", "Remote"],
    salary_min=60000, salary_max=100000,
    work_arrangement="remote",  # she prefers fully remote
    experience_level="senior",
    needs_visa=False,
    work_arrangement="remote",   # preferred_workplace derives from this
)
```

`generate_search_config(profile)` produces a `SearchConfig` with `job_titles=["Senior Python Engineer", "Staff Engineer"]`, primary skills `["python", "aws", "docker"]`, locations `["London", "Remote", ...]`, etc.

### Stage 1 — Fetch

`TieredScheduler.tick()` saw `greenhouse.category="ats"` (60s tier) was due. Breaker `CLOSED` → dispatched. `GreenhouseSource.fetch_jobs()` iterated 82 company slugs, found this one among the results, returned the `Job` above. `breaker.record_success()`.

### Stage 2 — Prefilter (3 gates)

`prefilter.passes_prefilter(alice_profile, job)`:

| Gate | Logic | Outcome |
| --- | --- | --- |
| Location | `"london"` substring matches Alice's `preferred_locations[0]` | ✅ pass |
| Experience | Job title contains `"senior"` → seniority `senior`; Alice is `senior` → diff 0 (≤ ±1) | ✅ pass |
| Skill overlap | `"python"` and `"aws"` and `"docker"` in title+description match Alice's `additional_skills` | ✅ pass |

Survives. Goes to scoring. (Per blueprint §2, ~99% of jobs are eliminated *before* this point.)

### Stage 3 — Score (9-dim `ScoreBreakdown`)

`scorer.score(job)` — `scorer` was instantiated as `JobScorer(search_config, user_preferences=alice.prefs, enrichment_lookup=lookup)`. `user_preferences` alone is what activates the four Batch-2.9 dims (rule #20); the lookup only decides whether they see real data or fall back to their neutral halves.

**Classic 4 components:**

| Component | Calculation | Score |
| --- | --- | --- |
| Title | Exact match against `search_config.job_titles[0]` ("Senior Python Engineer") | **40** / 40 |
| Skill | title+desc grep with `aliases_for()` expansion: `python` (primary +3), `aws` (primary +3), `docker` (primary +3), `kubernetes` aka `k8s` (secondary +2), `postgres`→`postgresql` (tertiary +1) | **12** / 40 |
| Location | "London, UK" matches `LOCATIONS` after alias normalisation | **10** / 10 |
| Recency | `date_confidence="high"` + `posted_at` is today → full band | **10** / 10 |

**Batch-2.9 multi-dim** (fires because `user_preferences` was passed; the enrichment row below is what lifts each dim off its neutral half):

Assume LLM enrichment ran and produced:

```python
JobEnrichment(
    seniority="senior",
    salary=SalaryBand(min=70000, max=90000, currency="GBP", frequency="annual"),
    visa_sponsorship="yes",
    workplace_type="hybrid",
    employment_type="full_time",
    ...
)
```

| Dim | Calculation | Score |
| --- | --- | --- |
| seniority_score | Both ranked `senior` → diff 0 → full weight | **8** / 8 |
| salary_score | Job £70–90k overlaps Alice's £60–100k entirely (band overlap ratio = 1.0) | **10** / 10 |
| visa_score | Alice `needs_visa=False` → irrelevant → 0 (not a penalty, just absent) | **0** / 6 |
| workplace_score | Job `hybrid` vs Alice `remote` → 50% compromise | **3** / 6 |

**Penalties / gates:**

- Title-gate check: 40 ≥ 6 (MIN_TITLE_GATE × 40) ✓
- Skill-gate check: 12 ≥ 6 (MIN_SKILL_GATE × 40) ✓
- Negative title keywords: none present → no –30
- Foreign location: no such penalty exists (deleted 2026-08-12, rule #30) — the UK gate refuses foreign jobs at ingestion instead

**Sum:** 40 + 12 + 10 + 10 + 8 + 10 + 0 + 3 = **93**, clamped to [0, 100] → **`match_score = 93`**.

The 9 dim columns on the `jobs` row look like: `role=40, skill=12, location_score=10, recency=10, seniority_score=8, salary_score=10 [stored to salary column in actuality], visa_score=0, workplace_score=3, semantic=0, penalty=0, match_score=93`.

### Stage 4 — Dedup (4 layers)

For each layer the deduplicator asks: is there another job that should collapse into this one?

| Layer | Behaviour for this posting |
| --- | --- |
| 1. Exact `normalized_key` | `(_normalize_title("Senior Python Engineer"), normalize("Acme Corp Ltd"))` → `("python engineer", "acme")` (after stripping "senior" prefix and "Ltd" suffix). No other job has the same key → no merge |
| 2. RapidFuzz (≥80/85) | No similar-titled jobs at the same company in this run → no merge |
| 3. TF-IDF cosine (≥0.85) | Document `"acme | senior python engineer | we're hiring a senior backend ..."`. No 0.85+ neighbour → no merge |
| 4. Embedding repost (opt-in, requires `SEMANTIC_ENABLED=true` + `enable_embedding_repost=True`) | Skipped by default |

Survives unchanged.

### Stage 5 — Enrich (opt-in, `ENRICHMENT_ENABLED=true`)

`match_score=93 ≥ ENRICHMENT_THRESHOLD=10` → eligible (the default is **10**, inherited from `ENRICHMENT_MIN_SCORE` at `settings.py:152-155`; the docs said 60 for months and the code has never used it — and `ENRICHMENT_MAX_JOBS=20` is the real selection lever). The enrichment dict already had a row from a prior run (`skip_existing=True`), so no new LLM call this pass. If it were a fresh job: `llm_extract_validated(prompt, JobEnrichment, max_retries=2)` would have produced the structured object via the OpenAI → Gemini → Groq → Cerebras chain. Stored to `job_enrichment` table (shared catalog, no `user_id`).

### Stage 6 — Store

`db.insert_job(job)` does `INSERT OR IGNORE` on `UNIQUE(normalized_company, normalized_title)`:
- New row → returns `True`; the 9 dim columns and `staleness_state='active'` and `first_seen_at=now()` are persisted.
- Cross-run duplicate → returns `False`; `last_seen_at` is bumped instead.

If `SEMANTIC_ENABLED=true`: `encode_job(job, enrichment)` runs (lazy-imports `sentence_transformers`, splits long description 300/50, max-pools), then `PgVectorIndex().upsert(job_id, vector)` writes the vector AND its audit stamp into the same `job_embeddings` row in one statement — `model_version` is taken from `embeddings.MODEL_NAME` inside the method and `embedding_updated_at` is set to `now()` (`backend/src/main.py:1295-1298`, `services/pg_vector_index.py:99-123`).

> **The store is Postgres, not ChromaDB.** Migration `0027` moved the vector into `job_embeddings.embedding` (`vector` type, pgvector) on 2026-08-07, because the Chroma store sat on the BACKEND container's local disk while the only scheduled pipeline runs on the WORKER — so the scheduled run could never ADD one. Coverage froze: the catalog grew 7,761 → 8,184 overnight while the embedding count stayed at exactly 284. `services/vector_index.py` (the Chroma wrapper) still exists and still builds a `chromadb.PersistentClient` when called (`vector_index.py:39-45`) — but **no production call site constructs it**. Its only remaining callers are two scripts (`backend/scripts/build_job_embeddings.py:73`, `backend/scripts/eval_v2_pool.py:125`) and two tests (`test_embeddings.py:22`, `test_vector_index_path.py:18`).

`db.log_run(stats, run_uuid, per_source_errors={...}, per_source_duration={greenhouse: 12.4}, total_duration=68.2)` writes the run row (migration `0010`).

### What Alice's worker sees

When `score_and_ingest(ctx, job_id=<this>, users=[alice])` runs (whether immediately under ARQ, or on next CLI pass):

1. Re-scores **for Alice specifically** (same scorer, same 93).
2. `FeedService.upsert_feed_row(alice.id, job.id, 93, bucket="24h")`.
3. 93 ≥ Alice's email rule threshold of 80 → enqueues `send_notification(alice.id, job.id, "instant")`. (See Pillar 1 walkthrough for what happens next.)

### Why this trace matters

If you change anything in the engine — a weight, a threshold, an enum value — this same trace tells you what should still come out. Re-running it mentally is the fastest sanity check before opening tests.

---

## 2. The Orchestrator — `backend/src/main.py::run_search()`

The 6 stages live inside one async function (`main.py:741-1450`). Walking it from top to bottom:

### Stage 0 — Init (`main.py:741-838`)

- Set a per-run `run_uuid` correlation ID via a `contextvar` — every log line and DB write for this run carries it.
- Load the user profile via `storage.load_profile()`. **Fail fast** if no profile exists (exit code 2 from the CLI) — there is no anonymous mode any more.
- Call `generate_search_config(profile)` → `SearchConfig` (job_titles, primary/secondary/tertiary skills, relevance_keywords, locations, search_queries). This is the bridge from Pillar 1 (profile) to Pillar 2 (engine).
- `JobDatabase(path)` → `init_db()` → `_migrate()`. Forward-compat `ALTER TABLE ADD COLUMN` for schema drift. The numbered SQL migrations are a *separate* system (`backend/migrations/`, runner `migrations/runner.py`), currently at head **0030**.

### Stage 1 — Fetch (`main.py:840-1036`)

- **Auto-purge** jobs older than 30 days via `db.purge_old_jobs(days=30)` (CLAUDE.md rule #3 — never change this without confirmation).
- **Instantiate the scorer once**: `scorer = JobScorer(search_config, user_preferences, enrichment_lookup)` (`main.py:857`). All three kwargs — satisfies rule #20.
- **Build sources** via `_build_sources(search_config, ...)` (`main.py:233-315`):
  - Domain-filtered: `classify_user_domain(profile)` returns a set like `{"tech"}` or `{"healthcare", "academia"}`; sources whose `DOMAINS` don't overlap are skipped. Sources marked `"general"` are always included.
  - Yields **40 instances** from a 41-key `SOURCE_REGISTRY` (indeed/glassdoor share the `JobSpySource` class); `SOURCE_INSTANCE_COUNT = 40` at `main.py:168` pins it.
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

This is the heart of the engine. The post-Batch-2.9 scorer returns a 9-field `ScoreBreakdown` dataclass (`scoring_dimensions.py:49-73`):

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

#### 3.2 Batch 2.9 multi-dimensions (active whenever `user_preferences` is passed)

All four are called **unconditionally** once `user_preferences` is present, with whatever `enrichment_lookup` returns — including `None`. A missing `JobEnrichment` row therefore yields each dim's NEUTRAL half-weight, never a zero (rule #29). **One exception:** `visa_score` returns a real `0` when `needs_visa=False`, and that is not a penalty — it is "no reward for something irrelevant", checked before the enrichment test (`scoring_dimensions.py:242`):

- **`seniority_score`** (`scoring_dimensions.py:138-173`) — maps job's `seniority` enum (intern → director, 0–6) and user's `experience_level` to the 0–6 scale. Curve: 0-diff → full, 1-diff → 62 %, 2-diff → 25 %, **3-diff → −50 %, 4+ → −100 %** (a real mismatch is a penalty, not merely "no reward"). Missing signal → 50 %.
- **`salary_score`** (`scoring_dimensions.py:181-226`) — band-overlap ratio between job's `SalaryBand` (normalised to annual GBP via `salary.normalize_salary()`) and user's `salary_min`/`salary_max`. No overlap → 0; missing data → 50 %.
- **`visa_score`** (`scoring_dimensions.py:234-250`) — if `user.needs_visa=False`, score is 0 (irrelevant). If True: job-`visa_sponsorship=yes` → full, `=no` → 0, `=unknown` **or no enrichment** → 50 %.
- **`workplace_score`** (`scoring_dimensions.py:265-293`) — exact (remote/onsite/hybrid) → full; hybrid-vs-remote or hybrid-vs-onsite → 50 % compromise; remote-vs-onsite → 0; missing → 50 %.

#### 3.3 Penalties + gates

- **Negative title** (`-30`) — title contains a word from `NEGATIVE_TITLE_KEYWORDS`. *Note:* the list is empty by default now, so this fires only when the user populates negative keywords on their profile.
- **Foreign location** — REMOVED 2026-08-12 (rule #30). `FOREIGN_INDICATORS` is deleted; `services/uk_gate.check_uk` refuses non-UK jobs before storage.
- **Title-gate / Skill-gate** (`MIN_TITLE_GATE=0.15`, `MIN_SKILL_GATE=0.15`) — if **both** components are below 15 % of their max (6 pts of 40 each — an AND, not OR; `skill_matcher.py:396`), the entire score collapses to `max(10, (title+skill)*0.25)`. This prevents a perfect location + recency from elevating an obviously-irrelevant job.

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
| **4. Embedding repost** | none of its own — `deduplicator.py` imports only `re` / `rapidfuzz` / `sklearn`; the vectors are handed in | **opt-in** | Within same company, cosine ≥ 0.92 → dedup. Requires an `embedding_lookup: dict[job_id, vector]` to be passed in. Preserves earliest `first_seen_at`. |

**Tie-break ranking** (used in every layer):
1. `match_score` (primary)
2. **Enrichment bonus** (+5 if the job has a `job_enrichment` row — encourages enriched candidates to win, so structured data survives downstream)
3. **Completeness** (+10 each for salary_min/max, +5 for location, +min(len(desc), 20) for description)

### Stage 5 — Enrich (opt-in, `ENGINE2_ENABLED` **or** `ENRICHMENT_ENABLED`)

- Gate: **a budget, not a threshold** (`main.py:1137-1163`). Jobs must clear a low floor — `match_score >= ENRICHMENT_MIN_SCORE` (default **10**, `settings.py:152`) — and then the best `ENRICHMENT_MAX_JOBS` (default **20**) are sent to the LLM. `ENRICHMENT_THRESHOLD` is not read at this call site — it defaults to `ENRICHMENT_MIN_SCORE` when unset (`settings.py:155`) and survives only for old `.env` files. Why the change: the scorer's contract is 0–100, but the highest `match_score` measured across the whole 3,342-row prod feed on 2026-07-28 was **58**, so a threshold of 60 selected nothing and the stage had never run (`main.py:1138-1142` records the measurement). A budget makes no claim about the distribution, so it cannot go stale.
- `enrich_batch(jobs, semaphore_limit=10, skip_existing=True)` (`job_enrichment.py`) runs asyncio-parallel LLM calls capped at 10 concurrent.
- Each call goes through `llm_extract_validated(prompt, JobEnrichment, max_retries=2)`:
  - **Provider chain** (`llm_provider.py:329-334`): **OpenAI (`gpt-4o-mini`, PRIMARY)** → Gemini (`gemini-3.7-flash`) → Groq (`llama-3.3-70b-versatile`) → Cerebras (`gpt-oss-120b`). Every model id is env-overridable.
  - **Self-correction loop**: on Pydantic `ValidationError`, the first 5 error messages are appended to the prompt and the call retries up to 2 more times.
  - If all providers fail or retries exhaust → `RuntimeError` (logged, no partial row written — atomicity over best-effort).
- Output: a `JobEnrichment` row with **16 strict-typed fields** (see §3.2 below). The `job_enrichment` TABLE still has 18 enrichment columns — `employer_type` and `locations` were retired from the schema in 2026-08 but never dropped from the DB, so nothing writes them — `save_enrichment` excludes both from its column list (`job_enrichment.py:248-254`), and the read paths skip them too (`:329`, `:401`).
- DB persistence: `INSERT OR REPLACE` into `job_enrichment` table (migration `0008`). **Shared catalog** — no `user_id` column, per CLAUDE.md rule #17 (the same enriched fields apply to every user; per-user scoring against the enrichment happens at read time).

### Stage 6 — Store + (opt-in) embed

- `db.insert_job(job)` does `INSERT OR IGNORE` on `(normalized_company, normalized_title)` UNIQUE — returns `True` for new rows, `False` for cross-run duplicates already in the catalog. **Never touch `normalized_key()`** without checking the dedup chain (CLAUDE.md rule #1).
- If `SEMANTIC_ENABLED=true`, lazy-import `embeddings` + `pg_vector_index`, encode each newly-inserted job via `encode_job(job, enrichment)` and `PgVectorIndex().upsert(job_id, vector)` (`main.py:1292-1298`). This write path reads `SEMANTIC_ENABLED` **alone** — `ENGINE3_ENABLED` does not open it.
- `db.log_run(stats, run_uuid, per_source_errors, per_source_duration, total_duration)` writes the `run_log` row.
- Finally: CSV export → Markdown report → channel notifications (the *old* per-source notification system from `services/notifications/`; the per-user `channels/dispatcher` runs only under the ARQ worker, not the CLI).

---

## 3. Detail surfaces — what each component actually contains

### 3.1 The `JobScorer` class — `backend/src/services/skill_matcher.py:440-643`

Two distinct call signatures, and the difference is the difference between "legacy" and "Pillar-2-active":

```python
# Legacy — no user_preferences, so the four Batch-2.9 dim slots stay at 0
scorer = JobScorer(search_config)
breakdown = scorer.score(job)
# breakdown.seniority_score == 0, .salary_score == 0, etc.

# Pillar-2-active — what run_search() and the worker use
scorer = JobScorer(search_config, user_preferences=prefs, enrichment_lookup=lookup)
breakdown = scorer.score(job)
# All 9 fields populated from real data
```

**Rule #20, as the code actually behaves** (`skill_matcher.py:587`): the multi-dim path is gated on `user_preferences` **alone**. `enrichment_lookup` is optional — pass `user_preferences` without it and every dim function is still called, each returning its documented NEUTRAL half-weight rather than a zero (the one exception is `visa_score` with `needs_visa=False`, which is a deliberate 0) (rule #29: an absent input is never a per-job penalty). That was a real bug fix: the old `if enrichment is not None:` gate left all four dims at 0 for any job the enrichment pipeline had not reached yet, so a fresh, correctly-un-enriched job scored 30 points below an identical enriched one. Guard: `tests/test_scorer.py::test_dims_neutral_not_zero_when_enrichment_missing`.

### 3.2 The 16-field `JobEnrichment` schema — `backend/src/services/job_enrichment_schema.py`

| # | Field | Type | Notes |
| --- | --- | --- | --- |
| 1 | `title_canonical` | str (1–200) | LLM-rewritten title for matching |
| 2 | `category` | `JobCategory` enum | 16 values (software_engineering, data_science, healthcare, …, other) |
| 3 | `employment_type` | `EmploymentType` enum | full_time / part_time / contract / internship / temporary / apprenticeship / freelance / unknown |
| 4 | `workplace_type` | `WorkplaceType` enum | remote / onsite / hybrid / unknown |
| 5 | `salary` | `SalaryBand` (nested) | min, max, currency (uppercased), frequency (hourly/daily/monthly/annual/unknown) |
| 6 | `required_skills` | list[str] ≤30 | Curated, not exhaustive |
| 7 | `preferred_skills` | list[str] ≤30 | Nice-to-haves |
| 8 | `experience_min_years` | int 0–40 \| null | Minimum years |
| 9 | `experience_level` | `ExperienceLevel` enum | entry / mid / senior / unknown |
| 10 | `requirements_summary` | str ≤250 | Condensed — also fed to the embedding encoder |
| 11 | `language` | str (ISO 639-1) | Default `"en"` |
| 12 | `visa_sponsorship` | `VisaSponsorship` enum | yes / no / unknown |
| 13 | `seniority` | `SeniorityLevel` enum | intern / junior / mid / senior / staff / principal / director / unknown |
| 14 | `remote_region` | str ≤60 \| null | Geographic scope for remote roles |
| 15 | `apply_instructions` | str ≤500 \| null | URL or notes |
| 16 | `red_flags` | list[str] ≤10 | Warning signals ("requires unpaid work", "MLM signal") |

All length-bounded to prevent DB bloat from a malformed LLM response.

> **Retired 2026-08** (measured on 3,119 live enriched rows, see the module docstring): `employer_type` — with its `EmployerType` enum — and `locations` are **gone from the schema**. `employer_type` came back `'unknown'` 100 % of the time and `locations` was never populated. The `job_enrichment.employer_type` / `.locations` DB columns still exist but nothing writes them. Seven enums remain, not eight.

### 3.3 Skill synonyms — `backend/src/core/skill_synonyms.py`

A flat 493-entry alias dict (`_ALIASES_TO_CANONICAL`) makes `"k8s"`, `"kube"`, and `"kubernetes"` interchangeable everywhere skill matching happens. Two helpers:

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

Then `to_gbp(amount, currency)` converts via an **18-currency** static rate table (`fx.py`, Q1 2026 averages — USD=0.79, EUR=0.86, JPY=0.0053, …). Unknown currency → ×1.0 (treated as already-GBP — safe degraded mode).

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
| `ats` | 60 s | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Pinpoint, Personio, Rippling |
| `reed` (name override) | 5 min | (Reed's quota is 2000 req/hr, hand-tuned) |
| `workday` (name override) | 15 min | (anti-scraper) |
| `rss` | 15 min | jobs.ac.uk, NHS Jobs, WeWorkRemotely, FindAJob, BioSpace, … |
| `scrapers` | 60 min | LinkedIn, Climatebase, 80000Hours, BCSJobs, AIJobs.ai |
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
| `job_embeddings` | `job_id PK FK → jobs(id)` | `model_version`, `embedding_updated_at` **and the vector itself** — `embedding` is a pgvector `vector` column added by migration `0027` (table created by `0009`). It is no longer audit-only, and there is no separate vector store. |

`_migrate()` in `database.py:162-342` is **forward-compat-only** — applies `ALTER TABLE ADD COLUMN` for missing columns so an older DB on disk auto-upgrades. The migration runner in `backend/migrations/runner.py` is the new system (Batch 2+) and applies the numbered `.up.sql` / `.down.sql` files.

---

## 5. The opt-in advanced surfaces (off by default)

Both flags default `false` per CLAUDE.md rule #18, and the **no-op path must exactly match pre-Pillar-2 behaviour**. Documented here for completeness.

### 5.1 `ENGINE2_ENABLED` **or** `ENRICHMENT_ENABLED` → LLM enrichment

Either name opens this surface — every E2 call site reads `ENGINE2_ENABLED or ENRICHMENT_ENABLED` (`main.py:853`, `main.py:1137`, `rescore.py:85`, `api/routes/jobs.py:779`, `workers/tasks.py:237`), rule #18.

When on:
- Stage 5 runs (see §2).
- `JobScorer` gains a populated `enrichment_lookup`, so the Batch 2.9 dimension scorers have real data instead of their neutral halves.
- Dedup tie-breaker uses the `+5` enrichment bonus.

When off:
- `enrichment_lookup` is an empty dict, so every lookup returns `None`. The four dim scorers still RUN — the path is gated on `user_preferences` alone (`skill_matcher.py:587`, rule #20) — and each returns its documented **neutral half weight**, never a zero: seniority 4 (`scoring_dimensions.py:157`), salary 5 (`:198-200`), workplace 3 (`:278`), visa 3 (`:245`), so **+15** rather than the +30 a fully enriched job can reach (rule #29; `visa_score` is the one exception, returning 0 at `:242` when the user does not need sponsorship). A user with no preferences at all gets the legacy 4-component formula; a user with preferences does not.
- No LLM API calls, no `job_enrichment` DB writes.

### 5.2 `SEMANTIC_ENABLED=true` → embeddings + hybrid retrieval

When on:
- Stage 6 encodes each new job via `encode_job(job, enrichment)`:
  - Model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, lazy-loaded).
  - Base text: `title | requirements_summary | required_skills_joined`.
  - Long descriptions (>300 words) are chunked 300/50 and max-pooled (the "asymmetric short-query-long-document" pattern).
- Vector is `PgVectorIndex.upsert(job_id, vector)` into `job_embeddings.embedding` in Postgres (pgvector, migration `0027`).
- API queries can pass `?mode=hybrid` to invoke `retrieve_for_user()` which:
  1. Pulls keyword top-500 from SQL (`JobScorer.match_score` ranking).
  2. Pulls semantic top-500 from `job_embeddings.embedding` (cosine distance `<=>`, exact scan — no ANN index yet, see `0027`).
  3. Fuses via **Reciprocal Rank Fusion** with `k=60`: `score(item) = Σ 1 / (k + rank_i + 1)` across all input lists.
  4. Optionally reranks the top-50 with the **cross-encoder** `cross-encoder/ms-marco-MiniLM-L-6-v2` (Batch 2.8).
- ESCO skill normalisation in CV parsing does **not** flip on. `_maybe_normalise_skills_via_esco()` is double-gated: `SEMANTIC_ENABLED` **and** `is_available()` (`cv_parser.py:821,830`), and the index artefacts have never been built, so it stays an identity transform either way (rule #28; `docs/product/PILLAR1_EXTRACTION_AUDIT.md`).

When off:
- No `sentence_transformers` import at all (saves ~150 ms–2 s startup).
- No vector queries; retrieval is keyword-only.
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

## Environment variables — every var the engine reads

Defaults in `backend/src/core/settings.py`. Anything below labelled "weight" goes into the final clamp at 130-max-pre-clamp (rule #23).

| Var | Default | What it controls | Effect of changing |
| --- | --- | --- | --- |
| `MIN_MATCH_SCORE` | `30` | Jobs below this score are omitted from the user's feed entirely | Raise to be stricter (fewer results), lower for permissive feed |
| `MIN_TITLE_GATE` | `0.15` (= 6 pts of 40) | Title-component floor; below it the whole score collapses to suppression | Raise to require closer title matches; lower to admit weaker title alignments |
| `MIN_SKILL_GATE` | `0.15` (= 6 pts of 40) | Skill-component floor; same collapse behaviour | Same as above for skill alignment |
| `SALARY_WEIGHT` | `10` | Salary dimension max (Batch 2.9) | Raise to weight salary fit more heavily in final score |
| `SENIORITY_WEIGHT` | `8` | Seniority dimension max | Raise to penalise mismatched levels harder |
| `VISA_WEIGHT` | `6` | Visa dimension max | Only meaningful when users have `needs_visa=True` |
| `WORKPLACE_WEIGHT` | `6` | Workplace (remote/hybrid/onsite) dimension max | Raise to make workplace preference more decisive |
| `ENRICHMENT_MIN_SCORE` | `10` | The low floor a job must clear to be enrichment-eligible (`settings.py:152`) | Raise only to skip obvious junk — the budget below is the real lever |
| `ENRICHMENT_MAX_JOBS` | `20` | Per-run budget: the best N eligible jobs are enriched (`settings.py:151`) | Raise to enrich more per run; this is the hard cost ceiling |
| `ENRICHMENT_THRESHOLD` | `10` | Back-compat name: when unset it **defaults to** `ENRICHMENT_MIN_SCORE`, and when set it takes that value (`settings.py:155`). `run_search`'s selection does **not** read it — that gate is `ENRICHMENT_MIN_SCORE` + `ENRICHMENT_MAX_JOBS` — but the worker's per-job enqueue path still does (`workers/tasks.py:237`) | Not inert — leave it at the default. Raising it silently stops the worker fanning out `enrich_job_task` while the CLI path carries on |
| `ENRICHMENT_ENABLED` | `false` | Legacy switch for LLM enrichment; `ENGINE2_ENABLED` opens the same gate (`ENGINE2_ENABLED or ENRICHMENT_ENABLED`). It switches the enrichment DATA on, **not** the dim scorers — those run on `user_preferences` alone (rule #20) | Flip on after setting LLM keys — see rule #18 |
| `SEMANTIC_ENABLED` | `false` | Writes embeddings into the pgvector store (`main.py:1292,1348` read this name ALONE). Hybrid retrieval is gated on `ENGINE3_ENABLED or SEMANTIC_ENABLED` (`api/routes/jobs.py:368-369`), so `ENGINE3_ENABLED` alone queries an index nothing fills. It does **not** switch ESCO on: that also needs `is_available()` (`cv_parser.py:821,830`) and the index artefacts have never been built | Flip on after `pip install ".[semantic]"`; ~300 MB of deps |
| `TARGET_SALARY_MIN` / `_MAX` | `40000` / `120000` | Salary-range *tiebreaker* (not scoring) for sort order on the dashboard | Display preference only |
| `OPENAI_API_KEY` | (unset) | **PRIMARY** LLM provider — heads the chain (`llm_provider.py:329-334`) | Unset → falls to Gemini |
| `GEMINI_API_KEY` | (unset) | Second-choice LLM | Unset → falls to Groq |
| `GROQ_API_KEY` | (unset) | Third-choice LLM | Unset → falls to Cerebras |
| `CEREBRAS_API_KEY` | (unset) | Fourth-choice LLM | All four unset → enrichment + LLM-CV-parse both raise `RuntimeError` |
| Source-keyed APIs (`REED_API_KEY`, `ADZUNA_APP_ID`+`ADZUNA_APP_KEY`, `JSEARCH_API_KEY`, `JOOBLE_API_KEY`, `SERPAPI_KEY`, `CAREERJET_AFFID`, `FINDWORK_API_KEY`, `DFE_APPRENTICESHIPS_API_KEY`) | (unset) | The 8 keyed sources from Pillar 3 (`sources/apis_keyed/`) | Each unset source `return []` silently (logged at INFO) |

> Tuning recipe — *"feed too noisy"*: raise `MIN_MATCH_SCORE` (e.g. 40), or raise `MIN_TITLE_GATE`/`MIN_SKILL_GATE` (e.g. 0.25). *"Feed too sparse"*: lower the same, or expand `additional_skills` on the user profile (cheaper than tuning). *"Want more weight on salary fit"*: bump `SALARY_WEIGHT` to 15, but watch the [0,100] clamp — rule #27.

---

## Failure modes — when things go wrong

| Symptom | Root cause | Where it surfaces | Fix |
| --- | --- | --- | --- |
| All scores are 0 / suspiciously low | No user profile loaded → `score_job()` legacy path firing against empty `keywords.py` | `match_score` column near 0 for every row | Run `setup-profile`. The "empty keywords.py" inflection from 2026-04-09 means a profile is mandatory now |
| Dim columns all zero except classic 4 | `user_preferences` was **not** passed at all — that, not the lookup, is the gate (rule #20) | DB inspection of `seniority_score`/`salary_score`/etc all zero despite enrichment rows existing. Note a *neutral half* (e.g. visa 3 of `VISA_WEIGHT=6`) is correct, not a bug — as is a visa `0` when the user does not need sponsorship — only a flat 0 across all four points at a missing `user_preferences` | Confirm the caller passes `user_preferences`; `main.py:857` and `workers/tasks.py::score_and_ingest` show the correct pattern |
| Enrichment never runs | Both `ENGINE2_ENABLED` and `ENRICHMENT_ENABLED` false (the default), or all 4 LLM providers unset, or no job clears `ENRICHMENT_MIN_SCORE` (default 10), or `ENRICHMENT_MAX_JOBS=0` — a zero budget selects nothing even when everything else is satisfied (`main.py:1165`) | `job_enrichment` table stays empty | Check those four preconditions in that order. A selected-zero run logs `Enrichment selected 0 jobs …` at WARNING (`main.py:1172`) |
| Enrichment runs but every row is `category="other"` etc | LLM returning generic enum values; the validation loop converged on weak output | `SELECT category, COUNT(*) FROM job_enrichment GROUP BY category` shows skewed dist | Prompt-engineering territory — see `job_enrichment.py` system prompt; try forcing Gemini-only by unsetting the others |
| Cross-encoder rerank takes too long | First call on each process initialises the model (~2 s download + load) | API request latency spike | Pre-warm via a startup hook, or accept the cold-start cost once per worker process |
| Vector query returns 0 even with `SEMANTIC_ENABLED=true` | No rows carry a vector (`PgVectorIndex().count() == 0`, i.e. `SELECT count(*) FROM job_embeddings WHERE embedding IS NOT NULL` is 0) — embeddings never built, **or** this Postgres has no pgvector so `0027` was a no-op and every method degrades to empty | Hybrid retrieval falls back to keyword-only silently | Run a CLI pass with the flag on to populate (`main._embed_backfill_budget`, `backend/src/main.py:548`, re-fills rows where `e.job_id IS NULL OR e.embedding IS NULL`). To force re-embed: `UPDATE job_embeddings SET embedding = NULL;` and re-run. **Deleting `data/chroma/` does nothing** — that store is not read any more |
| `nightly_ghost_sweep` marks healthy jobs as `confirmed_expired` | Source returned 0 results for N consecutive runs (e.g. credentials lapsed silently) | Users start getting 410s on real apply links | Check the source's run_log entries; if the source has been failing, the sweep is doing the right thing — fix the source first |
| Circuit breaker stays OPEN forever | Per-source `failure_threshold` (5) hit; cooldown is per-process | Source skipped on every tick | Breakers are in-memory only — **restart the process** (CLI/API/worker) to reset |
| Same job re-scored to a different value across two runs | Profile changed between runs (user updated prefs/CV) — expected behaviour | `match_score` differs in `jobs` row between runs | Not a bug. To audit which version of the profile produced a score, cross-reference `user_profile_versions.created_at` with `run_log.timestamp` |
| Dedup is too aggressive — losing legit different roles | Layer-3 TF-IDF clustering too loose, or Layer-1 `_normalize_title` strips too much | Postings disappear that should appear separately | Inspect `_normalize_title()` regex; consider tightening the 0.85 cosine threshold via env override if exposed (currently hard-coded) |
| LLM provider chain exhausts mid-batch | All 4 providers' quotas hit (OpenAI → Gemini → Groq → Cerebras) | `enrich_batch` logs per-job errors; `RuntimeError` raised per failing job (caught at batch level) | Wait for the daily quota reset; or top up a paid tier; the engine continues — only the enrichment column is null for those jobs |
| Conditional fetch cache grows without bound | `ConditionalCache` is 256-entry FIFO; eviction is automatic | Memory stable around the bound | Not a failure — by design. To see hit/miss rates: `cache.get_metrics()` returns `{hits, misses, size}` |
| `run_log.total_duration` >> sum of per_source_durations | Time is being spent in scoring/dedup/enrichment/store stages, not fetch | Run log row | Expected — sources run concurrently via `asyncio.gather`; serial post-stages dominate total |

For operational queries (re-embed a specific job, inspect breaker state, drop the conditional cache), see [`runbook.md`](./runbook.md). For unfamiliar terminology, see [`glossary.md`](./glossary.md).

---

## 6. Current status — what works, what's incomplete

Legend: ✅ done & wired · 🟡 partial · ❌ planned but not built · ⚠️ subtle gap

### 6.1 Scoring core

| Surface | Status | Notes |
| --- | --- | --- |
| Classic 4-component scoring (title / skill / location / recency) | ✅ | `skill_matcher.py:416` (`score_job`) + `JobScorer.score()` |
| Title-gate / skill-gate (`MIN_TITLE_GATE=0.15`) | ✅ | prevents location-only inflation |
| Negative-title penalty (-30) only — the foreign-location penalty was deleted 2026-08-12 | ✅ | `REMOTE_TERMS` (4); UK/foreign matching lives in `uk_gate` data |
| Batch 2.9 multi-dim (seniority / salary / visa / workplace) | ✅ | `scoring_dimensions.py`, env-tunable weights |
| 9-field `ScoreBreakdown` dataclass | ✅ | replaces flat int return |
| Skill synonym expansion (493 aliases) | ✅ | `skill_synonyms.py` + `_text_contains_skill` |
| Word-boundary skill matching | ✅ | prevents "rust" matching "trust" |
| 5-column date-confidence model for recency | ✅ | Pillar 3 Batch 1 — fabricated dates score 0 |
| Empty `keywords.py` (user-profile mandatory) | ✅ | architectural inflection 2026-04-09 |
| Legacy `score_job()` still callable | ⚠️ | scores against empty lists → near-zero. Dead in practice but not removed for back-compat with old imports |
| Per-dimension weights configurable via env vars | ✅ | `SALARY_WEIGHT`, `SENIORITY_WEIGHT`, `VISA_WEIGHT`, `WORKPLACE_WEIGHT` |
| Step-1.5 DB round-trip + HTTP value-presence tests for dim columns | ✅ | rule #21 — `test_database.py::test_dim_columns_round_trip` + `test_api.py::test_jobs_response_includes_score_dim_breakdown` |
| Multi-dim gated on `user_preferences` alone; missing `enrichment_lookup` → neutral halves, never zeros (rule #20 + #29) | ✅ | `skill_matcher.py:587`; guard `test_scorer.py::test_dims_neutral_not_zero_when_enrichment_missing` |

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
| `JobEnrichment` 16-field schema + 7 enums + `SalaryBand` | ✅ | `job_enrichment_schema.py`, all length-bounded (`employer_type` + `locations` retired 2026-08) |
| `enrich_batch()` with `asyncio.Semaphore(10)` | ✅ | per-job error isolation |
| `INSERT OR REPLACE` upsert into `job_enrichment` table | ✅ | migration `0008`, shared catalog |
| Multi-provider LLM fallback (OpenAI → Gemini → Groq → Cerebras) | ✅ | `llm_provider.llm_extract` (`:329-334`) |
| Self-correction loop (max 2 retries with appended errors) | ✅ | `llm_extract_validated` |
| `_build_enrichment_lookup()` bulk-load for scoring | ✅ | graceful empty-dict on missing table |
| Enrichment selection = `ENRICHMENT_MIN_SCORE` floor (10) + `ENRICHMENT_MAX_JOBS` budget (20) | ✅ | a budget, not a threshold — the old `ENRICHMENT_THRESHOLD=60` gate selected nothing against a measured prod maximum of 58 and never fired (`main.py:1137-1163`) |
| `ENRICHMENT_ENABLED` flag defaults `false` | ✅ | rule #18 |
| Cost tracking per provider call | ❌ | no `llm_usage` table yet |

### 6.4 Embeddings + Retrieval (opt-in)

| Surface | Status | Notes |
| --- | --- | --- |
| `encode_job()` 384-dim with 300/50 chunking | ✅ | `sentence-transformers/all-MiniLM-L6-v2`, lazy |
| `PgVectorIndex` — the live vector store | ✅ | `job_embeddings.embedding`, pgvector, migration `0027`. The older `VectorIndex` ChromaDB wrapper still works when called — from two scripts and two tests — but no production call site builds it |
| `job_embeddings` table | ✅ | migration `0009` (row) + `0027` (the `embedding` column). Vector and audit stamp share one row in Postgres — they cannot desync |
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
| `run_search()` 6-stage pipeline | ✅ | `main.py:741-1450` |
| Domain-filtered source build | ✅ | `classify_user_domain` × source `.DOMAINS` |
| 40 source instances from 41-key registry | ✅ | `SOURCE_INSTANCE_COUNT = 40` (`main.py:168`) |
| `TieredScheduler.tick(force=True)` one-shot dispatch | ✅ | CLI path |
| `TieredScheduler.run_forever()` long-running poller | 🟡 | written but not wired to systemd (Batch 4 scope) |
| `CircuitBreaker` 5-fail/300s state machine | ✅ | per-source registry |
| `BreakerRegistry.snapshot()` for run-log | ✅ | logged at run end |
| `ConditionalCache` 256-entry FIFO | ✅ | opt-in per-source via `_get_json_conditional()` |
| Per-run `run_uuid` correlation in contextvar | ✅ | every log line + DB write tagged |
| Ghost-detection (`last_seen_at` + `staleness_state='missed'`) | ✅ | per-run pass + `nightly_ghost_sweep` worker task |
| Auto-purge >30 days via `purge_old_jobs(days=30)` | ✅ | rule #3 — never touch without confirmation |
| `forward-compat` `_migrate()` for ALTER ADD COLUMN | ✅ | `database.py:162-342` |
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
│   │   ├── skill_synonyms.py                  — 493-entry alias dict
│   │   └── fx.py                              — 18-currency → GBP rates
│   ├── services/
│   │   ├── skill_matcher.py                   — JobScorer + score_job + helpers (legacy + Batch 2.9 paths)
│   │   ├── scoring_dimensions.py              — seniority / salary / visa / workplace scorers + ScoreBreakdown
│   │   ├── prefilter.py                       — 3-stage cascade
│   │   ├── deduplicator.py                    — 4-layer dedup (exact / fuzzy / TF-IDF / embedding)
│   │   ├── domain_classifier.py               — tech / healthcare / academia / education / climate
│   │   ├── salary.py                          — normalize_salary() hourly→annual GBP
│   │   ├── job_enrichment.py                  — enrich_batch() + DB helpers (opt-in)
│   │   ├── job_enrichment_schema.py           — 16-field Pydantic JobEnrichment
│   │   ├── embeddings.py                      — encode_job() (opt-in, lazy)
│   │   ├── pg_vector_index.py                 — THE vector store: job_embeddings.embedding (pgvector)
│   │   ├── vector_index.py                    — legacy ChromaDB wrapper; scripts + tests only, no production caller
│   │   ├── retrieval.py                       — RRF fusion + cross-encoder rerank (opt-in)
│   │   ├── scheduler.py                       — TieredScheduler + TIER_INTERVALS_SECONDS
│   │   ├── circuit_breaker.py                 — 5-fail/300s state machine + BreakerRegistry
│   │   ├── conditional_cache.py               — 256-entry FIFO for ETag/Last-Modified
│   │   └── profile/llm_provider.py            — OpenAI (PRIMARY) → Gemini → Groq → Cerebras chain + llm_extract_validated
│   ├── workers/tasks.py                       — score_and_ingest (per-user worker path)
│   ├── repositories/database.py               — JobDatabase + 31-migration forward-compat schema
│   └── api/routes/jobs.py                     — exposes match_score + 9-field breakdown to API
└── migrations/
    ├── 0008_job_enrichment.up.sql             — shared-catalog enrichment table
    ├── 0009_job_embeddings.up.sql             — the audit row (model_version, embedding_updated_at)
    ├── 0010_run_log_observability.up.sql      — per-source errors/durations, run_uuid
    ├── 0011_score_dimensions.up.sql           — 9 dim columns on jobs table
    └── 0027_job_embedding_vectors.up.sql      — adds job_embeddings.embedding (pgvector); tolerant no-op without the extension

backend/data/chroma/                            — (gitignored) LEGACY ChromaDB collection; the production pipeline and API never read it (two scripts still can)
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
├── test_pg_vector_index.py                     — upsert/query/delete/count against job_embeddings.embedding
├── test_vector_index_path.py                   — the legacy Chroma wrapper's persist path
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
- **Where the raw job postings come from** → Pillar 3 (Job Providers) — the 45 source classes, `BaseJobSource`, the rotation history, ATS company slug catalog, conditional-fetch opt-ins per source.
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
- **#20** — multi-dim scoring is gated on `user_preferences` ALONE. A missing `enrichment_lookup` gives each dim its NEUTRAL half, never a zero (#29).
- **#21** — value-presence > schema-presence. New engine-side fields need a real round-trip test, not just a schema assertion.

---

*Last updated 2026-05-28. HEAD `a7a2268`. Backend test baseline 600p/0f/3s.*
