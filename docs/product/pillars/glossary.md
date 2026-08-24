<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Glossary — Job360 Vocabulary

A reference for terms used across the three pillar docs. Cross-cutting; not pillar-specific. When in doubt about a word, look here first.

> **How to use**: jump by the term alphabetically, or `grep` for the word you need. Each entry says (a) what it means in plain English, (b) where it lives in the code, (c) which pillar(s) own it.

---

### Apprise

Open-source library that lets one Python call fan out to ~80 notification services (email, Slack, Discord, Telegram, Pushover, SMS providers, …) by passing a URL like `slack://tok/tok/tok`. Job360 uses it as the per-user channel delivery layer.
**Code:** `backend/src/services/channels/dispatcher.py` (lazy-imported per rule #11) · **Pillar 1**

### Argon2id

Modern password-hashing algorithm. OWASP-recommended defaults: `time_cost=3, memory_cost=64 MiB, parallelism=4`. Returns a PHC-format string that already contains the salt; verification is constant-time.
**Code:** `backend/src/services/auth/passwords.py` · **Pillar 1**

### ARQ

Async Redis-backed task queue Job360 uses for the notification worker. Worker code (`workers/tasks.py`) is pure async with no top-level `arq` import — tests bypass Redis entirely; production runs `arq src.workers.settings.WorkerSettings`.
**Code:** `backend/src/workers/tasks.py` · **Pillar 1**

### ATS (Applicant Tracking System)

A job board hosted by a specific company on a SaaS platform (Greenhouse, Lever, Workable, Ashby, …). Job360 polls a *known list of company slugs* on each platform rather than searching — see `companies.py` (~256 slugs across 11 platforms).
**Code:** `backend/src/sources/ats/` · **Pillar 3**

### Batch (Batch 1, Batch 2, Batch 3, …)

Numbered work-units in the Job360 development history. Batch 2 added multi-user delivery; Batch 3 rotated the source roster (−3, +5) and added the tiered scheduler. Pillar 2 added 10 sub-batches (2.1–2.10) for the search-and-match engine upgrade. See `docs/harness/IMPLEMENTATION_LOG.md`.

### Breaker (Circuit Breaker)

Per-source state machine: `CLOSED` (healthy) → 5 failures → `OPEN` (skip for 300 s) → `HALF_OPEN` (one probe call) → success → `CLOSED`. Prevents a misbehaving source from burning the whole run.
**Code:** `backend/src/services/circuit_breaker.py` · **Pillar 2**

### Bucket (Time bucket)

Tag attached to each job in `user_feed` saying how fresh it is from this user's perspective: `24h`, `48h`, `3d`, `5d`, `7d`, `all`. Drives the dashboard's pill-filter UI.
**Code:** `backend/src/utils/time_buckets.py` · **Pillars 1 + 2**

### Catalog (Shared catalog)

The `jobs`, `job_enrichment`, and `job_embeddings` tables — *no* `user_id` column by design (rules #1, #10, #17). The same job row serves every user; per-user state lives in separate overlay tables (`user_feed`, `user_actions`, `applications`).
**Pillar 2 owns the writes; Pillar 1 owns the reads.**

### Channel

A user's configured notification destination — one row in `user_channels` with a Fernet-encrypted Apprise URL. Five types today: `email`, `slack`, `discord`, `telegram`, `webhook`.
**Code:** `backend/src/services/channels/`, table `user_channels` (migration `0005`) · **Pillar 1**

### ChromaDB

The vector database storing job embeddings on disk at `backend/data/chroma/`. Wrapped by `VectorIndex`. Lazy-imported only when `SEMANTIC_ENABLED=true`.
**Code:** `backend/src/services/vector_index.py` · **Pillar 2**

### Conditional fetch

HTTP technique using `If-None-Match` / `If-Modified-Since` headers so a repeat call to an unchanged feed returns 304 with no body. Job360 has machinery for it (`_get_json_conditional`, `ConditionalCache` 256-entry FIFO) but only one source opts in today (`nhs_jobs_xml`).
**Code:** `backend/src/services/conditional_cache.py` · **Pillar 3**

### Cross-encoder rerank

Second-pass reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`) that takes the top-50 hits from RRF and rescores them more precisely. Lazy-imported, opt-in via `SEMANTIC_ENABLED`.
**Code:** `backend/src/services/retrieval.py` · **Pillar 2**

### CV parsing

Turning a CV PDF/DOCX into structured `CVData`. PDF text extraction via `pdfplumber`, DOCX via `python-docx`, then **LLM-only** structuring (Gemini → Groq → Cerebras fallback) — the regex `KNOWN_SKILLS` approach was removed in commit `804725c`.
**Code:** `backend/src/services/profile/cv_parser.py` · **Pillar 1**

### DEFAULT_TENANT_ID

The placeholder user UUID `00000000-0000-0000-0000-000000000001` owning rows created by the CLI (pre-auth) or migrated from the pre-Batch-2 single-tenant world. Cannot be logged into (`password_hash = "!"`).
**Code:** `backend/src/core/tenancy.py` · **Pillar 1**

### Dedup (Deduplicator)

Four-layer cascade that collapses near-duplicate jobs after scoring: (1) exact `normalized_key`, (2) RapidFuzz fuzzy ≥80/85, (3) TF-IDF cosine ≥0.85, (4) embedding repost ≥0.92 (opt-in). All heavy deps lazy-imported.
**Code:** `backend/src/services/deduplicator.py` · **Pillar 2**

### Digest

A *batched* notification sent at a scheduled time (e.g. 08:00 in user's timezone) instead of instant-per-match. Jobs queue in `user_notification_digests`; a worker drains them and writes one ledger row per channel.
**Code:** migrations `0012` + `0013`, `workers/tasks.send_daily_digest` · **Pillar 1**

### ESCO

European Skills, Competences, Qualifications and Occupations — a multilingual standard taxonomy. Job360 uses ESCO URIs to canonicalise CV skills when `SEMANTIC_ENABLED=true`.
**Code:** `_maybe_normalise_skills_via_esco()` in `cv_parser.py` · **Pillars 1 + 2**

### Feature flags

Two env-var booleans that gate Pillar-2's advanced features, both **default off** (rule #18):
- `ENRICHMENT_ENABLED` — LLM enrichment + DB writes + multi-dim scoring activation
- `SEMANTIC_ENABLED` — embeddings + ChromaDB + hybrid retrieval + ESCO

### Feed (user_feed)

The per-user "what should this person see" table — SSOT (single source of truth) shared by dashboard and notification worker. UNIQUE `(user_id, job_id)`. Status values: `active`, `skipped`, `stale`, `applied`.
**Code:** `backend/src/services/feed.py`, table from migration `0003` · **Pillar 1**

### Fernet

Symmetric encryption format (AES-128-CBC + HMAC-SHA256) from the `cryptography` library. Job360 uses it to encrypt channel credentials at rest. Key from `CHANNEL_ENCRYPTION_KEY` env var; fail-closed if unset.
**Code:** `backend/src/services/channels/crypto.py` · **Pillar 1**

### FX (Foreign exchange)

Static 18-currency → GBP rate table (Q1 2026 averages) used to roll non-GBP salaries to GBP. Unknown currency → ×1.0 (treated as GBP, safe degraded mode).
**Code:** `backend/src/core/fx.py` · **Pillar 2**

### Ghost detection

Mechanism that finds jobs that have *quietly disappeared* from their source (job posting deleted upstream). Per-run: jobs not seen this tick get `staleness_state='missed'`. Nightly `nightly_ghost_sweep` worker promotes `missed` → `confirmed_expired` after N consecutive misses. Stops users applying to dead listings.
**Code:** `backend/src/services/ghost_detection.py`, `last_seen_at`/`staleness_state` columns · **Pillars 2 + 3**

### Hybrid retrieval

Combination of keyword search (SQL `match_score` rank) + semantic search (ChromaDB k-NN) fused via RRF, optionally reranked by a cross-encoder. The `?mode=hybrid` query param invokes it. Opt-in via `SEMANTIC_ENABLED`.
**Code:** `backend/src/services/retrieval.py:retrieve_for_user()` · **Pillar 2**

### Idempotency key

Deterministic SHA1 of `{user_id}:{job_id}:{channel}`. The `notification_ledger.UNIQUE(user_id, job_id, channel)` constraint enforces it at the DB level — same key can never be sent twice even under concurrent worker retries.
**Code:** `backend/src/workers/tasks.py:idempotency_key()` · **Pillar 1**

### Job (the dataclass)

The canonical posting shape every source must produce: ~27 fields including `title`, `company`, `apply_url`, score columns, lifecycle columns. `normalized_key()` is its dedup tuple.
**Code:** `backend/src/models.py` · **Pillar 3**

### JobEnrichment

LLM-extracted structured shape with 18 strict-typed fields, 8 enums, nested `SalaryBand`. One row per `job_id` in the `job_enrichment` table — **shared catalog** (no `user_id`).
**Code:** `backend/src/services/job_enrichment_schema.py`, migration `0008` · **Pillar 2**

### JobScorer

The Pillar-2 scoring class. Two call signatures: `JobScorer(config)` (legacy 4-component) vs `JobScorer(config, user_preferences, enrichment_lookup)` (9-field `ScoreBreakdown` with Batch-2.9 multi-dim). The two kwargs are **co-required** (rule #20).
**Code:** `backend/src/services/skill_matcher.py:416-542` · **Pillar 2**

### Ledger (notification_ledger)

Per-(user, job, channel) row tracking notification status: `queued` / `sent` / `failed` / `dlq`. The idempotency table — UNIQUE constraint prevents duplicate sends.
**Code:** migration `0004` · **Pillar 1**

### LLM provider chain

Job360's CV-parsing / enrichment fallback: Gemini (`gemini-2.0-flash`, best JSON) → Groq (`llama-3.3-70b-versatile`) → Cerebras (`llama3.1-8b`, fastest). All-fail → `RuntimeError`. `llm_extract_validated()` adds a self-correction retry loop (max 2× with appended Pydantic errors).
**Code:** `backend/src/services/profile/llm_provider.py` · **Pillars 1 + 2**

### normalized_key

`(normalized_company, normalized_title)` tuple — strips legal suffixes (Ltd/Inc/PLC/…), region suffixes (UK/US/EMEA/…), lowercases. The DB UNIQUE constraint on `jobs` + the dedup Layer-1 key. **Never change without checking both** (rule #1).
**Code:** `backend/src/models.py:83-87` · **Pillar 3**

### Pillar

Job360's three architectural divisions: **Pillar 1 (User)**, **Pillar 2 (Engine)**, **Pillar 3 (Providers)**. See each doc under `docs/pillars/`.

### Prefilter

The 3-stage cascade that drops ~99% of postings before scoring (cheap before expensive): location → experience-level → skill-overlap.
**Code:** `backend/src/services/prefilter.py` · **Pillar 2**

### Profile (UserProfile)

Composite of `CVData` (from CV/LinkedIn/GitHub) + `UserPreferences` (form fields). `is_complete` returns True only when both halves are populated. Stored per-user in `user_profiles` (migration `0006`) with last-10 version history (migration `0007`).
**Code:** `backend/src/services/profile/models.py` · **Pillar 1**

### Rate limiter

Per-source `asyncio.Semaphore(concurrent) + sleep(delay)` pair. Configured by `RATE_LIMITS` dict in `settings.py` (46 entries, one per registry key). *In-request* concurrency; separate from the *between-runs* scheduler cadence.
**Code:** `backend/src/utils/rate_limiter.py` · **Pillar 3**

### Recency scoring

Job-freshness component (0–10 pts) driven by the 5-column date model (`posted_at`, `date_found`, `date_confidence`, `date_posted_raw`). Fabricated/negative dates score 0; low-confidence falls to 60% of band. Anti-staleness signal.
**Code:** `backend/src/services/skill_matcher.py:313-330` · **Pillar 2**

### RRF (Reciprocal Rank Fusion)

Algorithm for merging multiple ranked lists without score calibration: `score(item) = Σ 1/(k + rank_i + 1)` over all input lists, with `k=60`. Used to fuse keyword + semantic search results.
**Code:** `backend/src/services/retrieval.py:reciprocal_rank_fusion()` · **Pillar 2**

### Ring

The four concentric layers of the User pillar: Identity → Profile → Delivery → UI. Each ring assumes the previous. See `01-user-pillar.md`.

### Run (run_search / run_log)

A single end-to-end pass of the pipeline. Tagged with a `run_uuid` correlation id in a `contextvar`; logged to `run_log` with per-source error counts and durations (migration `0010`). Surfaced via `GET /api/runs`.
**Code:** `backend/src/main.py:run_search()` · **Pillar 2**

### ScoreBreakdown

9-field frozen dataclass returned by `JobScorer.score()`: title, skill, location, recency (classic 4) + seniority, salary, visa, workplace (Batch-2.9 multi-dim) + the clamped `match_score` total.
**Code:** `backend/src/services/scoring_dimensions.py:40-65` · **Pillar 2**

### SearchConfig

The output of `generate_search_config(profile)` — the bridge from Pillar 1's `UserProfile` to Pillar 2's engine. Contains `job_titles`, tiered skills, `relevance_keywords`, `locations`, `search_queries`. When no profile exists, `SearchConfig.from_defaults()` returns the (now-empty post-3ba1342) hard-coded defaults.
**Code:** `backend/src/services/profile/keyword_generator.py` · **Pillars 1 + 2**

### Session (signed cookie)

The auth artefact set after login: cookie value is `<session_id>.<hmac>` signed with `itsdangerous.TimestampSigner(SESSION_SECRET, salt="job360.session")`. 30-day absolute TTL. Cookie name: `job360_session`.
**Code:** `backend/src/services/auth/sessions.py`, table `sessions` (migration `0001`) · **Pillar 1**

### Skill synonyms (canonicalize_skill / aliases_for)

493-entry static alias dict making `k8s`, `kube`, `kubernetes` interchangeable everywhere skill matching happens.
**Code:** `backend/src/core/skill_synonyms.py` · **Pillar 2**

### SOURCE_REGISTRY

The 47-key dict in `main.py` mapping source-name to class. Builds 46 instances (indeed+glassdoor share `JobSpySource`). The *test* assertion `len(SOURCE_REGISTRY) == 47` in `test_cli.py` is one of five load-bearing surfaces (rule #13).
**Code:** `backend/src/main.py:106-159` · **Pillar 3**

### Stale (vs Confirmed Expired)

Two stages of a posting's death: **`stale`** = not seen in this run's source pull (might come back); **`confirmed_expired`** = ghost-detection has decided the upstream truly removed it (sticky — never resurrected).
**Code:** `backend/src/services/ghost_detection.py` · **Pillar 2**

### Stage (pipeline stage)

Where in the application funnel a user's `applications` row lives: `applied` → `outreach` → `interview` → `offer` → `rejected`. Transitions logged to `application_stage_history` (migration `0014`).
**Code:** `backend/src/api/routes/pipeline.py` · **Pillar 1**

### Tier (scheduler tier)

Polling-cadence bucket for sources. Today: `ats`=60s, `reed`=5min, `workday`=15min, `rss`=15min, `scrapers`=60min, `keyed_api`=60min, `free_json`=60min, `default`=60min. Source's `.category` attribute picks the tier; `NAME_TIER` dict has name-level overrides.
**Code:** `backend/src/services/scheduler.py:TIER_INTERVALS_SECONDS` · **Pillar 2**

### Worker (ARQ tasks)

The background process that runs `score_and_ingest`, `send_notification`, `send_daily_digest`, `nightly_ghost_sweep`, `enrich_job_task`. Tests call these as pure async functions; production runs them under ARQ + Redis.
**Code:** `backend/src/workers/tasks.py` · **Pillars 1 + 2**

---

*Last updated 2026-05-28. HEAD `cb52eb7`.*
