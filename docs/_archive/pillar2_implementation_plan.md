# Pillar 2 Implementation Plan — Search & Match Engine
<!-- doc: FROZEN -->

**Target:** Upgrade Job360's scoring/matching layer from a 4-dimension regex scorer to a semantic matching engine with gate-pass thresholds, LLM-enriched structured fields, and hybrid retrieval.

**Anchored to:** `docs/product/research/pillar_2_report.md` (18-item ranked backlog).
**Baseline commit:** `main @ 1730bf6` (Pillar 1 closure). 600p/0f/3s on clean baseline.
**Plan author:** session dated 2026-04-20.

---

## §0 — TL;DR

Pillar 2 splits into **four sprints, ten batches**. Sprint 1 is zero-new-dependency data-quality work; Sprint 2 adds LLM job enrichment; Sprint 3 lights up semantic retrieval (re-using the `sentence-transformers` dependency path already reserved by Pillar 1's ESCO scaffold); Sprint 4 adds precision polish.

The plan deliberately **skips** several research-report items (Meilisearch, JobBERT-V3, Learning-to-Rank) because they are either premature at our 50K-job scale or require engagement data we will only start collecting in Batch 4 (freemium).

Current scorer: `backend/src/services/skill_matcher.py` (312 lines). Dual-path: `score_job()` module-level (legacy, hard-coded keywords) + `JobScorer(config).score()` instance-based (SearchConfig-driven). The orchestrator in `src/main.py` selects between them based on `data/user_profile.json` presence.

---

## §1 — What's Already Shipped (evidence)

These Pillar 2 prerequisites landed in earlier pillars and do **not** need re-doing:

| Prereq | Source | Evidence |
|---|---|---|
| 5-column date model (`posted_at`, `first_seen_at`, `last_seen_at`, `last_updated_at`, `date_confidence`, `date_posted_raw`) | Pillar 3 Batch 1 | `backend/src/repositories/database.py:24-49` |
| Ghost-detection 4-state machine (ACTIVE→POSSIBLY_STALE→LIKELY_STALE→CONFIRMED_EXPIRED) | Pillar 3 Batch 1 | `backend/src/services/ghost_detection.py` |
| `update_last_seen()` + `mark_missed_for_source()` wired | Pillar 3 Batch 1 | `database.py:155,169` |
| Indexes on `first_seen`, `last_seen_at`, `staleness_state` | Pillar 3 Batch 1 | `database.py:94-101` |
| Jooble / Greenhouse / NHS already route suspect fields through `posted_at=None, date_confidence="low"` + `date_posted_raw` | Pillar 3 Batch 1 | `sources/apis_keyed/jooble.py:52`, `sources/ats/greenhouse.py:44`, `sources/feeds/nhs_jobs.py:57` |
| ESCO scaffold (lazy-loaded sentence-transformers embedding index over ~13,900 ESCO skills) | Pillar 1 Batch 1.3b | `backend/src/services/profile/skill_normalizer.py` (optional `[esco]` extra) |
| Multi-provider LLM chain (Gemini / Groq / Cerebras) with JSON mode + `llm_extract_validated()` retry loop | Pillar 1 Batch 1.1 | `backend/src/services/profile/llm_provider.py:93-168` |
| Evidence-based skill tiering + `SkillEntry` provenance | Pillar 1 Batches 1.3a + 1.4 | `backend/src/services/profile/*` |
| `SearchConfig` with `core_domain_words` + `supporting_role_words` for domain-aware partial-match in `JobScorer._title_score()` | Pillar 1 | `skill_matcher.py:296-301`, `profile/models.py:289-323` |

**Implication:** Pillar 2 reuses these — batches reference them by path, never re-introduce them.

---

## §2 — What's Still in the Research Report but Not in the Codebase

Concrete gaps, traced file-by-file:

1. **Only 4 sources remain with fabricated dates** (verified by grep 2026-04-20 — see Appendix A): `scrapers/linkedin.py:67`, `ats/workable.py:46`, `ats/personio.py:83`, `ats/pinpoint.py:54`. Each sets `posted_at=None` + `date_confidence="low"` + `date_posted_raw=None` correctly, but labels itself "low" when it has no date field at all — "low" implies weak-but-present signal (Jooble `updated`, Greenhouse `updated_at`, NHS `closingDate`). These four have no signal → should be `date_confidence="fabricated"` so the scorer awards 0 recency rather than the 60% band. The legacy `date_found=now()` column write is harmless (accurately represents first-seen) but should ideally be dropped once frontend/CLI stop reading `date_found`.
2. **No gate-pass scoring** — `skill_matcher.py:322-331` sums all components without early-return suppression. A job with 0 title + 0 skill can still clear `MIN_MATCH_SCORE=30` via location (10) + recency (10) + incidental partial matches.
3. **No static skill synonym table** — `services/skill_matcher.py` uses only word-boundary regex. ESCO scaffold is present but *dormant* (index not built at `backend/data/esco/`).
4. **No source-domain routing** — all 50 sources fire for every user via `_build_sources()` in `src/main.py`. CLAUDE.md § "Sources" enumerates 6 categories (`ats/rss/keyed_api/free_json/scrapers/other`) but there is no **domain** tag (tech/healthcare/finance/general).
5. **No LLM job enrichment** — `llm_provider.py` is only called by CV parsing paths in `services/profile/`. No code enriches job descriptions.
6. **No semantic job retrieval** — no `sentence-transformers` call outside the dormant ESCO scaffold. No ChromaDB/FAISS. No RRF. No cross-encoder.
7. **Scorer has only 4 dimensions, not 7+** — `scorer` awards points for title, skills, location, recency only. No salary, seniority, visa, or workplace-match dimensions (research report item #13). Salary is a *tiebreaker* only, not a scoring dimension.
8. **Dedup has 1 layer, not 4** — `deduplicator.py:49-62` groups on `(normalized_company, normalized_title)` only. No fuzzy (RapidFuzz), no TF-IDF, no same-company repost detection.
9. **No OpenAI provider / no Batch API** — research assumed OpenAI Batch ($54/mo at 10K/day). We route through existing Gemini/Groq/Cerebras free-tier chain instead, avoiding new cost.
10. **ESCO scaffold is dormant, not active for job-side skills** — `services/profile/skill_normalizer.py` exists (Pillar 1 Batch 1.3b) and the `[esco]` extra reserves the `sentence-transformers` dep, but the `backend/data/esco/` index artefacts were never built and no caller canonicalizes job-side skills through it. The research report's "ESCO 13,896 skills × ~130K alternative labels" leverage is unrealised.
11. **No description chunking for asymmetric retrieval** — research report recommends chunking long job descriptions into 300-token windows for short-profile → long-description matching. Not in codebase.
12. **No zero-yield source tracking** — research report recommends auto-disabling sources that consistently return nothing for a user's domain. Not in codebase (and deferred to Batch 4 alongside engagement telemetry — noted here for traceability).

---

## §3 — Dependency Graph Between Remaining Items

```
                   [2.1 date-hygiene]
                          │
                          ▼
                   [2.2 gate-pass] ──────────────┐
                          │                       │
               ┌──────────┴──────────┐            │
               ▼                     ▼            │
      [2.3 synonym table]   [2.4 source routing]  │
               │                     │            │
               └──────────┬──────────┘            │
                          ▼                       │
                  [2.5 LLM job enrichment] ──────►│
                          │                       │
                          ▼                       │
              [2.9 salary normalization+dim] ─────┤
                          │                       │
                          ▼                       │
           [2.6 job embeddings (activate ESCO dep)]│
                          │                       │
                          ▼                       │
                  [2.7 hybrid retrieval + RRF]    │
                          │                       │
                          ▼                       │
                [2.8 cross-encoder rerank] ◄──────┤
                                                  │
              [2.10 4-layer dedup] ◄──────────────┘
```

**Serial critical path:** 2.1 → 2.2 → 2.5 → 2.6 → 2.7 → 2.8.
**Parallel-safe once 2.2 merges:** 2.3, 2.4, 2.10.
**Requires 2.5's enriched fields:** 2.9 (salary), any scoring-formula expansion.

---

## §4 — Batch Breakdown

Each batch uses the Pillar 1 template: **Covers** (report item), **Touches**, **Out of scope**, **Test surface**, **Effort**.

### Batch 2.1 — Date confidence correction for signal-less sources

**Covers:** Report item #1 (fix date accuracy) — **narrowed scope after 2026-04-20 verification**.

**Touches:**
- `sources/scrapers/linkedin.py:67-70`, `sources/ats/workable.py:46`, `sources/ats/personio.py:83-86`, `sources/ats/pinpoint.py:54` — change `date_confidence="low"` → `date_confidence="fabricated"`. These sources have no date field at all; "low" overstates the signal. Keep `date_found=datetime.now(...)` (accurately = first-seen) until a follow-up removes the legacy column entirely.
- `services/skill_matcher.py:195-212` (`recency_score_for_job`) — verify "fabricated" branch exists and returns 0. Add a regression test if missing.

**Out of scope:** The 5-column schema (shipped in Pillar 3 Batch 1). The ghost-detection machine. NHS/Jooble/Greenhouse wrong-field fixes (already live and correctly labeled "low"). Dropping the legacy `date_found` column (defer until frontend/CLI audit).

**Test surface:** `tests/test_sources.py` — add parametrized `test_source_date_confidence_labels` covering all 50 sources; assert the 4 signal-less sources return "fabricated" and the 3 wrong-field sources return "low". New `test_scorer.py::test_recency_score_fabricated_returns_zero`.

**Effort:** **XS** (0.5 engineer-day). 4 one-line diffs + ~6 test cases.

---

### Batch 2.2 — Gate-pass scoring

**Covers:** Report item #2 (gate-pass eliminates false positives).

**Touches:**
- `services/skill_matcher.py:322-331` (`JobScorer.score`) — add `MIN_TITLE_GATE` + `MIN_SKILL_GATE` constants (expressed as fractions of max: default 0.15). If either gate fails, return `max(10, suppressed_linear)` where `suppressed_linear = (title + skills) * 0.25`.
- `core/settings.py` — expose `MIN_TITLE_GATE` and `MIN_SKILL_GATE` as tunable constants.
- `services/skill_matcher.py:259-268` (`score_job` module-level) — apply same gate for legacy path, using hardcoded keyword tiers.

**Out of scope:** Making gates user-configurable (Batch 2.9 or later). Archetype-specific gates (Pillar 1 §9 deferred to Pillar 2, but pushed to Batch 4 here).

**Test surface:** `tests/test_scorer.py` — new class `TestGatePass` with ~12 tests: zero-title-good-skills suppresses to ≤25, zero-skills-good-title same, both-zero-but-good-location-recency suppresses to ≤25, both-above-gate passes full linear sum, exactly-at-gate clears, just-below-gate suppresses.

**Effort:** **S** (1 engineer-day). Most of the work is test coverage.

---

### Batch 2.3 — Static skill synonym table

**Covers:** Report items #3 + partial-#16 (ESCO).

**Touches:**
- **New:** `backend/src/core/skill_synonyms.py` — ~500-entry canonical-form dictionary. `canonicalize_skill(raw: str) -> str`. Bootstrapped from common tech + UK-professional terms; expandable.
- `services/skill_matcher.py:131-142` (`_skill_score`) + `_word_boundary_pattern()` — call `canonicalize_skill()` on both job-side and profile-side tokens before matching.
- `services/profile/keyword_generator.py` — canonicalize skills as they flow from CV → SearchConfig.

**Out of scope:** Activating the ESCO embedding scaffold (Batch 2.6 territory — needs the sentence-transformers dep lit up). Embedding-based skill similarity (Batch 2.6).

**Test surface:** `tests/test_skill_synonyms.py` — ~30 tests: "js" → "javascript", "k8s" → "kubernetes", "aws" → "amazon web services", case-insensitive, unknown terms pass through unchanged, round-trip idempotence.

**Effort:** **S-M** (2-3 engineer-days). Most effort is curating the 500-entry table.

---

### Batch 2.4 — Source routing by domain

**Covers:** Report item #4.

**Touches:**
- `sources/base.py` — add class-level `DOMAINS: set[str] = {"general"}` attribute on `BaseJobSource`.
- Each of 50 source files — declare domain tags (e.g., `bcs_jobs` → `{"tech"}`, `nhs_jobs` → `{"healthcare"}`, `reed` → `{"general"}`).
- **New:** `services/domain_classifier.py` — `classify_user_domain(profile: UserProfile) -> set[str]` using CV keywords + target role. Returns set of domains (a user may span tech+healthcare).
- `src/main.py` (`_build_sources`) — filter sources to those whose `DOMAINS ∩ user_domains ≠ ∅` or `"general" ∈ DOMAINS`.

**Out of scope:** Auto-learning which sources hit which domain (zero-yield tracker — the research report's "Track source-domain hit rates over time and auto-disable sources that consistently return zero results for a given domain" recommendation). Per-source enable/disable UI. Both belong in Batch 4 once engagement telemetry lands. Keep Batch 2.4 purely static config-driven for now.

**Test surface:** `tests/test_domain_classifier.py` — ~20 tests. `tests/test_main.py` — assert a healthcare user skips `bcs_jobs` / `climatebase` and hits `nhs_jobs` + generals. Assert a zero-profile user (no CV) still hits all sources (graceful fallback).

**Effort:** **M** (3-4 engineer-days). Wide-file touch (50 sources), each setting 1-2 tags.

---

### Batch 2.5 — LLM job enrichment pipeline

**Covers:** Report item #5 (highest-impact). Diverges from research: uses existing Gemini/Groq/Cerebras chain, not OpenAI Batch.

**Touches:**
- **New:** `backend/src/services/job_enrichment.py` — `async def enrich_job(job: Job) -> JobEnrichment` wrapping `llm_extract_validated()` from `profile/llm_provider.py:93`.
- **New:** `backend/src/services/job_enrichment_schema.py` — strict Pydantic `JobEnrichment` model with 18 fields: `title_canonical`, `category` (16-enum), `employment_type`, `workplace_type` (Remote/Onsite/Hybrid), `locations` (list), `salary` (nested: min/max/currency/frequency), `required_skills`, `preferred_skills`, `experience_min_years`, `experience_level`, `requirements_summary` (≤250 chars), `language` (ISO 639-1), `employer_type`, `visa_sponsorship` (enum: Yes/No/Unknown), `seniority` (Intern/Junior/Mid/Senior/Staff/Principal/Director), `remote_region` (nullable), `apply_instructions` (nullable), `red_flags` (list — e.g., "requires unpaid work", "MLM signal").
- **New migration:** `migrations/0008_job_enrichment.sql` — `job_enrichment` table keyed by `job_id` FK, one row per job, JSON columns for list fields. Index on `job_id`.
- `services/deduplicator.py` — keep best-enriched job as tiebreaker.
- `src/workers/tasks.py` — new task `enrich_job_task(job_id)` queued post-ingest (after `score_and_ingest`), uses ARQ. Skip if enrichment already exists.

**Out of scope:** Using enrichment fields in the scorer (Batch 2.9 uses salary; Batch 2.8 uses required/preferred split). Backfilling pre-existing jobs (schedule as a one-shot `scripts/backfill_enrichment.py` at rollout).

**Test surface:** `tests/test_job_enrichment.py` — mock `llm_extract_validated()`, assert schema validation, assert enrichment persists to DB, assert dedup uses enriched fields, assert task is idempotent (no double-enrich). ~25 tests.

**Effort:** **M-L** (5-7 engineer-days). Biggest batch in Pillar 2.

**Cost note:** Free-tier Gemini/Groq/Cerebras shared across CV parsing. At current yield (~4% net-new per run, ~2K new jobs/day steady-state), enrichment fits within free-tier limits. If we outgrow, add OpenAI Batch in a follow-up.

---

### Batch 2.6 — Activate sentence-transformers + build job embedding index + light up ESCO

**Covers:** Report item #8 (bi-encoder semantic search) **and** report item #16 (ESCO taxonomy activation). Depends on enriched fields from 2.5.

**Touches:**
- `backend/pyproject.toml` — rename `[esco]` extra to `[semantic]` so one install path covers sentence-transformers + numpy for *both* ESCO skill normalization and job embeddings. No second 300 MB dep pull.
- **New:** `scripts/build_esco_index.py` — build the `backend/data/esco/` artefacts (labels.json + embeddings.npy) from the ESCO CSV. One-shot. Ships ESCO's ~13,896 skills × ~130K alternative labels.
- `services/profile/skill_normalizer.py` — no code change, but the scaffold becomes live once the index is built. Add a `is_available()` helper so downstream callers degrade gracefully when index is missing.
- `services/skill_matcher.py` — when `is_available()`, canonicalize job-side skills through `skill_normalizer.normalize()` *before* matching against the user's canonicalized skills (complements Batch 2.3's static table — ESCO covers the long tail, static table covers the hot tech terms).
- **New:** `backend/src/services/embeddings.py` — `encode_job(job, enrichment) -> np.ndarray` using `all-MiniLM-L6-v2` (384-dim, 80MB, 14K sents/sec CPU). Input text = `title + " | " + requirements_summary + " | " + " ".join(required_skills)`. **Description chunking (report recommendation):** when `enrichment.requirements_summary` exceeds 300 tokens, split into 300-token windows with 50-token overlap and store `max(chunk_similarities)` as the job-level score — handles the short-profile → long-description asymmetry the research report flags.
- **New:** `backend/src/services/vector_index.py` — thin wrapper over ChromaDB persistent collection at `backend/data/chroma/`. Methods: `upsert(job_id, vector, metadata)`, `query(vector, k, filter_metadata)`.
- **New migration:** `migrations/0009_job_embeddings.sql` — `job_embeddings` table (job_id FK, model_version TEXT, embedding_updated_at TEXT) for audit. Actual vectors live in Chroma.
- `src/workers/tasks.py` — new task `embed_job_task(job_id)` queued post-enrichment.
- **New:** `scripts/build_job_embeddings.py` — one-shot backfill.

**Out of scope:** Migrating Chroma to FAISS or Qdrant (premature at 50K). Multi-model ensembles. Swapping to `multi-qa-mpnet-base-dot-v1` for asymmetric search (revisit only if the chunking approach underperforms). Profile-text embeddings (Batch 2.7 handles query-time encoding).

**Test surface:** `tests/test_embeddings.py` — deterministic encoding (same text → same vector), chunking splits long text correctly (≥300 tokens), handles missing enrichment gracefully. `tests/test_vector_index.py` — upsert/query round trip with test collection. `tests/test_skill_normalizer_activation.py` — end-to-end skill canonicalization when ESCO index is present vs absent. ~25 tests total.

**Effort:** **M-L** (6-7 engineer-days). Dependency lift + ESCO index build + one-time backfill are the heavy parts. ESCO activation is +1 day over the original estimate, covering the missing item #16.

---

### Batch 2.7 — Hybrid retrieval with Reciprocal Rank Fusion

**Covers:** Report item #9 (RRF k=60).

**Touches:**
- **New:** `backend/src/services/retrieval.py` — `async def retrieve_for_user(profile, k=100) -> list[Job]`.
  - Stage A: keyword retrieval via existing SQL scorer (top 500 by `match_score`).
  - Stage B: semantic retrieval — encode user profile text, Chroma nearest-neighbour k=500.
  - Stage C: `reciprocal_rank_fusion(stage_a_ids, stage_b_ids, k=60)`.
- `api/routes/jobs.py` — optional `?mode=hybrid` query param; defaults to `hybrid` when embeddings are available, falls back to `keyword` if Chroma empty.
- `services/retrieval.py` exposes `reciprocal_rank_fusion(ranked_lists, k=60)` helper.

**Out of scope:** Personalised ranking from engagement data (LTR, pushed out).

**Test surface:** `tests/test_retrieval.py` — RRF is deterministic and correct on synthetic rankings, hybrid mode returns union with fused ranks, graceful fallback when Chroma is empty. ~15 tests.

**Effort:** **S-M** (2-3 engineer-days).

---

### Batch 2.8 — Cross-encoder rerank

**Covers:** Report item #12.

**Touches:**
- `services/retrieval.py` — after RRF, top-50 go through `cross-encoder/ms-marco-MiniLM-L-6-v2`. Rerank by cross-encoder score.
- `backend/pyproject.toml` — `[semantic]` extra already pulls sentence-transformers (it includes CrossEncoder).

**Out of scope:** Fine-tuning the cross-encoder on domain data (premature without engagement data).

**Test surface:** `tests/test_retrieval.py::test_cross_encoder_rerank` — mock CrossEncoder, assert top-50 are rescored, final top-K monotone by rescored score. ~8 tests.

**Effort:** **S** (1-2 engineer-days).

---

### Batch 2.9 — Multi-dimensional scoring from enriched fields

**Covers:** Report item #10 (salary) **and** report item #13 (expand to 7+ scoring dimensions: seniority match, salary fit, visa compatibility, workplace match). Requires all enriched fields from 2.5.

**Touches:**
- **New:** `backend/src/services/salary.py` — `normalize_salary(salary_obj, to_annual=True, to_currency="GBP") -> tuple[int, int] | None`. Currency detection (ISO 4217), frequency normalisation (hourly×2080, daily×260, weekly×52, monthly×12). Returns `None` when no signal so scoring stays neutral (0.5 band).
- **New:** `backend/src/services/scoring_dimensions.py` — four new scorers that read `job_enrichment` rows:
  - `seniority_score(job_enrichment, profile) -> int (0-8)` — compares enriched `seniority` enum against user's target experience.
  - `salary_score(job_enrichment, profile) -> int (0-10)` — range-overlap against `UserPreferences.target_salary_min/max`; neutral 5/10 when salary is None (per research-report recommendation).
  - `visa_score(job_enrichment, profile) -> int (0-6)` — full points when user needs sponsorship and enrichment's `visa_sponsorship == "Yes"`; zero when user doesn't need sponsorship (no reward for something they don't need).
  - `workplace_score(job_enrichment, profile) -> int (0-6)` — matches enriched `workplace_type` (Remote/Onsite/Hybrid) against `UserPreferences.preferred_workplace`.
- `services/skill_matcher.py` — expand `JobScorer.score()` from 4 components (40/40/10/10 = 100) to 7 components (30/30/10/10/8/10/6/6 weights capping at 100, with salary added outside the cap as a 10-point dim). Old bands re-weighted to make room; gate-pass logic (Batch 2.2) remains on title + skills gates only.
- `core/settings.py` — expose `SALARY_WEIGHT`, `SENIORITY_WEIGHT`, `VISA_WEIGHT`, `WORKPLACE_WEIGHT` as env-overridable constants; defaults sum to ≤100 including headroom.
- `services/profile/models.py` — add `preferred_workplace: str | None`, `needs_visa: bool` to `UserPreferences` (default None/False for backwards compat).

**Out of scope:** Live FX rates (use hard-coded annual rates at `core/fx.py`). Salary history / market comparison. Interview-likelihood / growth-trajectory dims from career-ops (require engagement data, deferred to Batch 4). Archetype-specific weight profiles (§9 deferred).

**Test surface:** `tests/test_salary.py` (~18 tests: currency detection, frequency conversion, overlap-neutral-when-missing) + `tests/test_scoring_dimensions.py` (~20 tests: each of the 4 new scorers at edge cases + integration test asserting a multi-dim job scores higher than a mono-dim job). Update `tests/test_scorer.py::test_score_can_reach_100` to include enrichment fields.

**Effort:** **M-L** (4-6 engineer-days). Re-weighting the scoring formula + 4 new scorers + user-preferences schema extension.

---

### Batch 2.10 — Four-layer deduplication

**Covers:** Report items #7 + #11 + #14.

**Touches:**
- `services/deduplicator.py` — extend `dedup()` with 3 more layers after existing exact-key:
  - **Layer 2:** RapidFuzz `token_set_ratio` on titles ≥80 AND `ratio` on companies ≥85 AND same normalized location.
  - **Layer 3:** TF-IDF (scikit-learn `TfidfVectorizer`) + cosine similarity on `(company + title + description[:200])` ≥0.85.
  - **Layer 4:** Embedding-based repost detection *within same company blocks* — cosine ≥0.92 → repost. Preserve earliest `first_seen_at`.
- `backend/pyproject.toml` — add `rapidfuzz>=3.0`, `scikit-learn>=1.4` to core deps (both are small + C-backed).

**Out of scope:** Cross-session job ID tracking beyond `normalized_key`.

**Test surface:** `tests/test_deduplicator.py` — add ~20 tests across the 3 new layers. Benchmark: dedup over 10K synthetic jobs completes in <5s.

**Effort:** **M** (3-4 engineer-days).

---

## §5 — Parallelism: Which Batches Run Together

With 1 engineer, execute serially in the order in §7. With 2 engineers:

- **Lane A (Data/scoring):** 2.1 → 2.2 → 2.3 → 2.9 → 2.10
- **Lane B (Semantic):** (wait on 2.2) → 2.4 → 2.5 → 2.6 → 2.7 → 2.8

With 3+ engineers, split 2.3 + 2.4 to run in parallel after 2.2, and run 2.10 in parallel with 2.6/2.7 after 2.5 merges.

---

## §6 — Effort Estimates (Summary Table)

| Batch | Effort | Eng-days | Sprint |
|---|---|---|---|
| 2.1 date confidence correction | XS | 0.5 | 1 |
| 2.2 gate-pass | S | 1 | 1 |
| 2.3 synonym table | S-M | 2-3 | 1 |
| 2.4 source routing | M | 3-4 | 1 |
| 2.5 LLM enrichment | M-L | 5-7 | 2 |
| 2.6 embeddings + Chroma + ESCO activation | M-L | 6-7 | 3 |
| 2.7 RRF hybrid | S-M | 2-3 | 3 |
| 2.8 cross-encoder | S | 1-2 | 4 |
| 2.9 multi-dim scoring (salary + seniority + visa + workplace) | M-L | 4-6 | 2 |
| 2.10 4-layer dedup | M | 3-4 | 4 |
| **Total** | | **~27-36 eng-days** | ~5-7 calendar weeks solo |

---

## §7 — Committed execution sequence (architect's decision, 2026-04-20)

**Philosophy:** risk-sequenced, not dependency-order-sequenced. Front-load scoring truth + fast visible quality uplift; push infra-heavy work later so we have confidence the foundation is solid before committing to the 300 MB `sentence-transformers` dependency path.

### Phase 1 — Scoring Truth (Week 1, ~3.5 eng-days)
Fix dishonest scoring without adding a single dependency. Everything here is reversible with a one-line revert.

1. **Batch 2.2 gate-pass** (1d) — **biggest single quality uplift in Pillar 2**; closes the "0-title + 0-skill + good-location → passes MIN_MATCH_SCORE" pathology. Zero new deps. Start here.
2. **Batch 2.1 date confidence correction** (0.5d) — 4 one-line diffs (`linkedin/workable/personio/pinpoint` → `date_confidence="fabricated"`). Slots in trivially after 2.2.
3. **Batch 2.3 skill synonym table** (2-3d) — `js↔javascript`, `k8s↔kubernetes`, etc. Makes the existing regex scorer smarter before we spend real money on embeddings.

**Ship checkpoint:** at end of Phase 1, run full pipeline on a real user profile; expect visible quality uplift in top-50 results.

### Phase 2 — Source Focus (Week 2, ~3 eng-days)
Narrow the input funnel once the matcher is honest.

4. **Batch 2.4 source routing by domain** (3d) — why after 2.3 not before: source routing is a sharper blade once synonyms + gate-pass are in. Doing it earlier would compensate for matcher weakness and mask whether routing is actually right.

**Ship checkpoint:** runtime per scrape cycle should drop ~40-60% for domain-specific users.

### Phase 3 — Structured Enrichment (Week 3-4, ~8 eng-days)
The fulcrum of Pillar 2. Structured fields unlock every batch in Phases 4-5.

5. **Batch 2.5 LLM job enrichment** (5-7d) — route through existing Gemini/Groq/Cerebras chain, NOT OpenAI Batch. Feature-flag behind `ENRICHMENT_ENABLED=true` so we can kill-switch if free-tier quotas burn out.
   - **Spike gate before full build:** day 1 = enrich 100 sample jobs, measure quality + quota burn. Go/no-go on full batch.
6. **Batch 2.9 salary normalization + scoring dim** (2-3d) — salary scoring is a by-product of 2.5's enriched `salary` object. Doing it immediately after 2.5 means the field isn't sitting unused.

**Ship checkpoint:** ≥90% of jobs older than 1 day have `job_enrichment` row. `scorer` now has 5 dimensions (title, skills, location, recency, salary).

### Phase 4 — Semantic Lift (Week 5-6, ~8 eng-days)
Only now do we pay the `sentence-transformers` dep cost, because Phases 1-3 prove the foundation is sound.

7. **Batch 2.6 embeddings + ChromaDB** (5-6d) — activate `[semantic]` pyproject extra. Encode all jobs offline via worker. Chroma local persistent collection.
8. **Batch 2.7 RRF hybrid retrieval** (2-3d) — combine keyword + semantic top-100 with Reciprocal Rank Fusion k=60.

**Ship checkpoint:** `/api/jobs?mode=hybrid` returns results with +15-30% recall on synonym/abbreviation queries.

### Phase 5 — Precision Polish (Week 7, ~5 eng-days)
Final quality layer on top of Phases 1-4.

9. **Batch 2.8 cross-encoder rerank** (1-2d) — `ms-marco-MiniLM-L-6-v2` rescoring top-50 from Phase 4. +33% accuracy on typical cases.
10. **Batch 2.10 four-layer dedup** (3-4d) — RapidFuzz (Layer 2) + TF-IDF (Layer 3) + same-company embedding repost detection (Layer 4). The embedding layer reuses Phase 4's model + Chroma — no incremental infra.

**Ship checkpoint:** manual audit of 100 dedup decisions shows ≤2 false merges. Pillar 2 done.

---

### Why this order and not the dependency graph's natural order

| Decision | Reason |
|---|---|
| Start with **2.2, not 2.1** | Gate-pass is 5× the scoring quality impact of the date-label fix; 2.1 is 0.5d so no harm waiting. |
| **2.3 (synonyms) before 2.4 (routing)** | Routing narrows recall; we need the matcher to be smart first so routing decisions are measurable. |
| **2.5 before 2.9** but 2.9 right after | Salary scoring needs enriched salary. Not waiting 2 weeks to cash in. |
| **2.6 before 2.7 before 2.8** | Hard dependency chain; no flexibility. |
| **2.4 earlier than §3 dep-graph suggests** | Dependency graph allows 2.4 to parallelize after 2.2, but running it in Phase 2 (before the big 2.5 enrichment push) limits blast radius if domain classification needs tuning. |
| **2.10 last** | Needs Phase 4's embedding infrastructure for Layer 4 (same-company repost detection). Doing it earlier would force a second embedding pipeline. |

### Total calendar

~**27-29 eng-days solo**, distributed across **~7 calendar weeks**. Parallelisable to ~4 weeks with 2 engineers (Phase 3 is the serial bottleneck).

---

## §8 — Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM enrichment hits free-tier rate limits at scale | Medium | Batch 2.5 uses multi-provider fallback chain (Gemini→Groq→Cerebras). If all three exhausted, job is queued to a retry DLQ, never blocking pipeline. |
| `sentence-transformers` adds ~300MB to image / slow cold-start | High | Make `[semantic]` an optional extra. Code paths degrade gracefully to keyword-only retrieval when `import sentence_transformers` fails. Warm the model via worker startup hook, not per-request. |
| ChromaDB corrupts / locks at 50K-100K scale | Low | Use persistent collection with WAL. Nightly backup of `backend/data/chroma/` to S3. Rebuild script `scripts/build_job_embeddings.py` is idempotent + resumable. |
| Gate-pass over-suppresses legitimate matches for sparse profiles | Medium | Make gate thresholds tunable via `MIN_TITLE_GATE`/`MIN_SKILL_GATE` in `core/settings.py`. Log suppression events; dashboard review first 2 weeks. |
| Cross-encoder rerank adds 100-200ms latency to `/api/jobs` | Low | Rerank only when `?mode=hybrid`. Cache scores for repeat queries within session. Stage rerank into worker, not request path, if latency becomes an issue. |
| Dedup Layer 4 (embedding repost) O(n²) within a company | Low | Block by company, then pairwise within block. Rare company has >50 jobs. Fall back to sampling if a block exceeds 200. |

---

## §9 — Out of Scope for Pillar 2

Explicitly deferred:

- **Meilisearch / Typesense** — premature at 50K jobs. Revisit at 500K.
- **Learning-to-Rank (LambdaMART)** — requires engagement data from Batch 4 freemium. Revisit post-Batch-4.
- **Multilingual (JobBERT-V3, multilingual-e5)** — UK-focused product; negligible non-English volume.
- **Torre-style uncertainty quantification** (score ranges) — nice-to-have; cold-start is bounded by Pillar 1 CV completeness.
- **Archetype-aware scoring weights** — deferred from Pillar 1 Batch 1.10; revisit after Pillar 2's enrichment schema is stable. Needs `category` enum from 2.5 to be trustworthy first.
- **OpenAI Batch API** — zero-cost philosophy. Can add in a follow-up if Gemini/Groq/Cerebras quotas are insufficient at scale.
- **Full-stack re-indexing in PostgreSQL with pgvector** — SQLite is adequate for 50K; migrate only when forced by concurrent-write pressure or vector scale.
- **Per-user MIN_MATCH_SCORE UI** — backend constant is enough; UI surface goes with Batch 4 settings work.

---

## §10 — Acceptance Signals

Pillar 2 is **done** when:

- Test suite ≥ 700 passing, 0 failing (baseline today: 600p/0f/3s). Each batch adds 10-30 tests.
- `docs/harness/IMPLEMENTATION_LOG.md` has entries for every batch 2.1-2.10 with scope + test delta.
- `MEMORY.md` updated with links to `project_pillar2_batch_*_done.md` files.
- Scorer changelog: title-only or skills-only matches no longer clear `MIN_MATCH_SCORE=30` (gate-pass verified).
- Enrichment coverage: ≥90% of jobs older than 1 day have a `job_enrichment` row.
- Retrieval coverage: `/api/jobs?mode=hybrid` returns results with Chroma collection ≥ `0.9 × jobs_count`.
- Dedup precision: manual audit of 100 random dedup decisions on a real scrape run shows ≤2 false merges.
- No regression on existing `test_profile.py` or `test_scorer.py` cases.

---

## §11 — Self-review

**Does this plan avoid rule breaks from CLAUDE.md?**

- ✅ Rule #1 (`normalized_key()`) — untouched; dedup adds *layers*, doesn't change the key.
- ✅ Rule #2 (`BaseJobSource`) — touched only for `DOMAINS` class attr, additive. All subclasses inherit.
- ✅ Rule #3 (`purge_old_jobs`) — untouched.
- ✅ Rule #4 (mock HTTP in tests) — all new enrichment/embedding/retrieval tests mock LLM + Chroma.
- ✅ Rule #8 (adding sources) — no new sources in Pillar 2.
- ✅ Rules #10-#12 (multi-tenant scoping) — new `job_enrichment` / `job_embeddings` tables are shared catalog (no `user_id`). Retrieval endpoints gate via `require_user` following existing pattern.
- ✅ Rule #13 (5 surfaces for source count) — untouched; no source changes.
- ✅ Rule #14 (conditional fetch) — untouched.
- ✅ Rule #15 (source tier) — untouched.

**Does this plan reuse existing utilities?**

- ✅ Reuses `llm_extract_validated()` from `profile/llm_provider.py` — no new LLM client.
- ✅ Reuses `sentence-transformers` from Pillar 1's ESCO extra — no second ML dep.
- ✅ Reuses ARQ worker pattern from Batch 3.5 — no new background infra.
- ✅ Reuses `SearchConfig` flow — no new config object.
- ✅ Reuses `MigrationRunner` from `migrations/runner.py` — no new migration system.

**Biggest single risk?** Batch 2.5's LLM enrichment cost/quota at scale. Mitigated by multi-provider fallback + DLQ + free-tier metering. If it becomes a blocker, OpenAI Batch API can be slotted in via an additional `LLMProvider` subclass without restructuring.

**Biggest single unknown?** Whether ChromaDB is stable enough at our scale or if we'll need to migrate to FAISS mid-Pillar-2. Covered by keeping `vector_index.py` as a thin wrapper (easy swap).

---

## Appendix A — One-shot investigation to verify before Batch 2.1

Before touching the 22 sources in Batch 2.1, run a fresh grep to confirm the fabricator list is still accurate post-main. The Pillar 2 research report predates Pillar 3 Batch 1 which shipped partial fabricator fixes — the true count may be lower. Use:

```bash
rg -n 'date_found.*(datetime\.now|datetime\.utcnow)' backend/src/sources/
rg -n 'date_confidence.*fabricated' backend/src/sources/
```

Expected outcome: the first grep returns the sources needing fixing; the second returns the sources already using the "fabricated" label. Reconcile against memory note "Re-audit 2026-04-19 — 0 fabricators (not 44)" — if truly zero, Batch 2.1 collapses to a verification-only batch.

---

## Appendix B — Environment variables added by Pillar 2

| Var | Required | Default | Used by |
|---|---|---|---|
| `ENRICHMENT_ENABLED` | No | `true` | Batch 2.5 — kill-switch for LLM enrichment |
| `ENRICHMENT_PROVIDER_ORDER` | No | `gemini,groq,cerebras` | Batch 2.5 — override fallback chain |
| `SEMANTIC_ENABLED` | No | `true` if `sentence-transformers` importable else `false` | Batch 2.6-2.8 — gates hybrid mode |
| `CHROMA_PATH` | No | `backend/data/chroma/` | Batch 2.6 — Chroma persistent collection dir |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Batch 2.6 — swap in larger model if desired |
| `CROSS_ENCODER_MODEL` | No | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Batch 2.8 |
| `MIN_TITLE_GATE` | No | `0.15` | Batch 2.2 |
| `MIN_SKILL_GATE` | No | `0.15` | Batch 2.2 |
| `SALARY_WEIGHT` | No | `10` | Batch 2.9 |

---

## Appendix C — New migrations

| # | Name | Batch |
|---|---|---|
| `0008_job_enrichment.sql` | `job_enrichment` table keyed by `job_id` | 2.5 |
| `0009_job_embeddings.sql` | `job_embeddings` audit table; vectors live in Chroma | 2.6 |

Starting slot `0008` is correct per exploration (existing migrations 0000-0007).

---

## Appendix D — File-touch summary (critical paths only)

| File | Batches touching it |
|---|---|
| `backend/src/services/skill_matcher.py` | 2.2, 2.3, 2.9 |
| `backend/src/services/deduplicator.py` | 2.5, 2.10 |
| `backend/src/main.py` (`_build_sources`) | 2.4 |
| `backend/src/sources/base.py` | 2.4 |
| All 50 `backend/src/sources/**/*.py` | 2.1 (fabricator sweep), 2.4 (domain tags) |
| `backend/src/services/profile/llm_provider.py` | 2.5 (reuse only — no modification) |
| `backend/pyproject.toml` | 2.6 (`[semantic]` extra), 2.10 (rapidfuzz + sklearn) |
| `backend/migrations/0008_job_enrichment.sql` | 2.5 (new) |
| `backend/migrations/0009_job_embeddings.sql` | 2.6 (new) |
| `backend/src/core/settings.py` | 2.2 (gate consts), 2.9 (salary weight) |

---

## Appendix E — Report coverage matrix (audit trail)

Every item from `docs/product/research/pillar_2_report.md` mapped to a plan batch or explicit deferral. Built 2026-04-20 to close the gap audit; update when the plan is revised.

### The 18-item ranked backlog (from research report §"Ranked improvements")

| # | Item | Plan landing | Status |
|---|---|---|---|
| 1 | Fix date accuracy (fabricators + wrong fields + first_seen/last_seen/date_confidence) | Batch 2.1 (label fix) + §1 already-shipped (schema + wrong-field fixes from Pillar 3 B1) | ✅ Covered |
| 2 | Gate-pass scoring | Batch 2.2 | ✅ Covered |
| 3 | Static skill synonym table (~500 entries) | Batch 2.3 | ✅ Covered |
| 4 | Source routing by domain | Batch 2.4 | ✅ Covered |
| 5 | LLM enrichment via GPT-4o-mini Batch API (20 fields) | Batch 2.5 — reusing Gemini/Groq/Cerebras chain (18-field schema; see §9 on OpenAI Batch deferral rationale) | ✅ Covered |
| 6 | Ghost detection via disappearance tracking | §1 already-shipped (Pillar 3 Batch 1: `first_seen`/`last_seen`/`consecutive_misses`/`staleness_state` + 4-state machine) | ✅ Covered |
| 7 | TF-IDF content-based dedup | Batch 2.10 Layer 3 | ✅ Covered |
| 8 | Semantic matching — bi-encoder (`all-MiniLM-L6-v2` + ChromaDB) | Batch 2.6 | ✅ Covered |
| 9 | Hybrid retrieval with RRF (k=60) | Batch 2.7 | ✅ Covered |
| 10 | Salary normalization + scoring | Batch 2.9 (merged with item #13 into multi-dim scoring) | ✅ Covered |
| 11 | Ghost detection — embedding-based repost detection | Batch 2.10 Layer 4 | ✅ Covered |
| 12 | Cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) | Batch 2.8 | ✅ Covered |
| 13 | Expand to 7+ scoring dimensions (seniority, salary, visa, workplace) | Batch 2.9 (expanded 2026-04-20) | ✅ Covered |
| 14 | Fuzzy dedup with RapidFuzz | Batch 2.10 Layer 2 | ✅ Covered |
| 15 | Configurable `MIN_MATCH_SCORE` per user | §9 deferred — backend constant suffices; UI settings surface belongs in Batch 4 | ⏸️ Deferred with rationale |
| 16 | ESCO taxonomy integration | Batch 2.6 (ESCO activation added 2026-04-20) | ✅ Covered |
| 17 | Learning-to-Rank (LambdaMART) | §9 deferred — requires engagement data from Batch 4 freemium | ⏸️ Deferred with rationale |
| 18 | Multilingual embeddings (JobBERT-V3, multilingual-e5) | §9 deferred — UK-focused product; negligible non-English volume | ⏸️ Deferred with rationale |

### Narrative-section items (beyond the 18-item table)

| Report recommendation | Plan landing | Status |
|---|---|---|
| HiringCafe 20-field structured enrichment schema (`title`, `category` 16-enum, `employment_type`, `workplace_type`, `locations`, nested `salary`, `skills`, `experience_min_years`, `requirements_summary`, `language`, `employer_type`, `visa_sponsorship`, seniority, red_flags) | Batch 2.5 (18-field variant extended with `required_skills`/`preferred_skills` split and `red_flags`) | ✅ Covered |
| HiringCafe `date_confidence` concept | §1 already-shipped (5-state: high/medium/low/fabricated/repost_backdated) | ✅ Covered |
| Career-ops gate-pass architecture | Batch 2.2 | ✅ Covered |
| Career-ops 10-dimension scoring (interview likelihood, company stage, product-market fit, growth trajectory) | 7 dims covered in 2.9; interview-likelihood / company-stage / PMF / growth-trajectory deferred to Batch 4+ (require engagement + funding/trajectory data not in the current enrichment schema) | ⏸️ Partial + deferred |
| Career-ops archetype classification | §9 deferred — needs stable `category` enum from 2.5 first | ⏸️ Deferred with rationale |
| JobFunnel 3-tier deduplication (source-id / URL / TF-IDF) | Batch 2.10 Layers 1-3 | ✅ Covered |
| JobSpy Pydantic model + per-source date parsers | Existing `Job` dataclass + Pillar 1 Batch 1.1 Pydantic schema | ✅ Covered |
| Levergreen scrape-and-diff + `compare_workflow_success` scrape-completeness check | §1 already-shipped (Pillar 3 Batch 1 ghost detection + scrape completeness gating) | ✅ Covered |
| Tiered ghost probability (1-day / 3+ day / 7+ day flags) | §1 already-shipped (4-state machine ACTIVE → POSSIBLY_STALE → LIKELY_STALE → CONFIRMED_EXPIRED) | ✅ Covered |
| Bi-encoder → cross-encoder two-stage pipeline | Batches 2.6 + 2.8 | ✅ Covered |
| Asymmetric search via `multi-qa-mpnet-base-dot-v1` OR 300-token chunking | Batch 2.6 (chunking path; mpnet swap in §9 deferred) | ✅ Covered (chunking path) |
| Description chunking into 300-token segments (50-token overlap, `max(chunk_similarities)`) | Batch 2.6 (added 2026-04-20) | ✅ Covered |
| Required vs preferred skills split in scoring | Batch 2.5 schema + Batch 2.9 weighted as distinct signals | ✅ Covered |
| Salary neutral 0.5 band when data missing | Batch 2.9 (explicit in `salary_score()` spec) | ✅ Covered |
| Engelbach et al. F1=0.94 three-component hybrid dedup (string + embedding + skill-list) | Batch 2.10 (4 layers cover string + embedding; skill-list merges with Batch 2.3's synonyms) | ✅ Covered |
| RapidFuzz 10× over fuzzywuzzy | Batch 2.10 Layer 2 | ✅ Covered |
| ESCO 13,896 skills × ~130K alternative labels | Batch 2.6 ESCO activation (added 2026-04-20) | ✅ Covered |
| Nesta `ojd_daps_skills` library pattern | Batch 2.6 reuses the same ESCO CSV + embedding-lookup approach via `skill_normalizer.py` | ✅ Covered (equivalent implementation) |
| ChromaDB over FAISS/Qdrant at 50K scale | Batch 2.6 | ✅ Covered |
| Meilisearch / Typesense for search infra | §9 deferred — premature at 50K jobs | ⏸️ Deferred with rationale |
| pg_trgm + PostgreSQL FTS | Implicitly N/A — on SQLite; pgvector migration deferred in §9 | ⏸️ Deferred with rationale |
| Torre.ai Random Forest Score → Filter → Rank | §9 deferred — requires engagement data | ⏸️ Deferred with rationale |
| Torre uncertainty quantification (score *range* for sparse profiles) | §9 deferred — cold-start bounded by Pillar 1 CV completeness | ⏸️ Deferred with rationale |
| Source auto-disable for zero-yield domains | Batch 2.4 out-of-scope note; deferred to Batch 4 alongside engagement telemetry | ⏸️ Deferred with rationale |
| Scrape completeness check before flagging disappearances | §1 already-shipped (Pillar 3 Batch 1) | ✅ Covered |
| `INSERT OR IGNORE` → upsert-with-last-seen | §1 already-shipped (Pillar 3 Batch 1 `update_last_seen()`) | ✅ Covered |

### Summary of audit

| Outcome | Count |
|---|---|
| ✅ Covered by a Pillar 2 batch | 14 (items 1-14 minus 15, plus 16) |
| ✅ Covered by prior-pillar ship | 3 (items 1 partial, 6, Levergreen, JobSpy, date_confidence, ghost tiers, scrape completeness, last_seen upsert) |
| ⏸️ Deferred with explicit rationale | 8 (items 15, 17, 18; career-ops archetype; Meilisearch; pg_trgm; Torre 2×; auto-disable) |
| 🔴 Unaddressed | **0** |

**Audit conclusion:** Every concrete recommendation in `docs/product/research/pillar_2_report.md` is either (a) landing in a Pillar 2 batch, (b) already shipped in a prior pillar, or (c) explicitly deferred with a stated reason. Nothing in the report is silently dropped.

---

**End of plan.**
