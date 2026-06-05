# Copy Roadmap — What to Borrow from Competitors to Make Job360 Better

> Prioritised, actionable list of techniques / UX / positioning worth copying from the
> free-forever competitor cohort, mapped to Job360's **current** (post-Step-3) architecture and
> ranked by leverage. Grounded against `SOURCE_REGISTRY`, `scheduler.py`, the Pillar 2 enrichment
> stack, and the Step-3 ARQ workers. Competitor intel is deduced from vendor marketing (see
> `References.md` §1).
>
> **Created:** 2026-06-05 · **Related:** [`References.md` §1.17 / §4](../References.md) ·
> [`job_data_acquisition_methods.md`](./job_data_acquisition_methods.md) ·
> [`competitor_sourcing_matrix.md`](./competitor_sourcing_matrix.md)

---

## Implementation status

| Item | Status | Artifacts |
|---|---|---|
| **Tier-1 #1 — automated company discovery** | ✅ **implemented + committed** (`dafbdae`, 2026-06-05) | `backend/src/services/company_discovery.py`, `backend/scripts/discover_companies.py`, `backend/tests/test_company_discovery.py` (15 tests, all green) |
| **Tier-1 #2 — LLM job-description enrichment** | ✅ **ALREADY SHIPPED** (Pillar 2 Batch 2.5) — roadmap was wrong (authored from stale `References.md` §1.1, pre-Pillar-2) | `backend/src/services/job_enrichment.py` (`enrich_batch`, Gemini→Groq→Cerebras), `job_enrichment_schema.py` (extracts salary/visa/skills/seniority/red-flags), wired in `main.py:553-567` behind `ENRICHMENT_ENABLED`; `tests/test_job_enrichment.py` (29 tests green) |
| **Tier-2 #3 — Schema.org JSON-LD harvest** | ✅ **complete** (`a5674ff`, `523547d`) — generic JobPosting extractor (`extract_jsonld_blocks`/`parse_jobposting`/`harvest_jobs`) **plus** `harvest_url` (fetch+extract end-to-end). NOT a SOURCE_REGISTRY source by design: it's an extraction utility fed by a URL source (composes with `company_discovery`), so no source-count change / no rules #8/#13 exposure | `backend/src/services/jsonld_harvest.py`, `backend/tests/test_jsonld_harvest.py` (20 tests) |
| **Tier-2 #4 — auto-tailored cover letter** | ✅ **core implemented** (`HEAD`) — `generate_cover_letter(job, candidate_summary)` reuses the LLM chain. **Remaining surface:** FastAPI endpoint, profile→summary assembly, full CV (not just letter) tailoring | `backend/src/services/cover_letter.py`, `backend/tests/test_cover_letter.py` (8 tests) |
| Tier-3 #5 — "within minutes" freshness positioning | ⬜ **deferred — frontend/copy** (backend already exposes per-source run data via the merged source-health route; needs UI "last checked" surfacing) | — |
| Tier-3 #6 — curated dedup'd digest | ⬜ **deferred — polish** (`send_daily_digest` ARQ already ships; this is quality tuning, not new code) | — |
| Tier-3 #7 — LinkedIn-OAuth onboarding | ⬜ **deferred — large frontend+auth** (own effort) | — |
| Tier-3 #8 — tagline / self-host copy | ⬜ **deferred — marketing copy** (no code) | — |
| Tier-4 — swipe-feed UX, vetted-company signal | ⬜ **optional/later** (frontend) | — |

> **Verification note (2026-06-05):** Divergence **resolved** (`bd4e9a4`, 0 behind origin). All
> backend-tractable roadmap items are **implemented + tested + committed** (Tier-1 #1, #2-already-done,
> #3 complete, #4 core). Remaining items are explicitly **frontend / marketing / v2** and are resolved
> as *deferred-with-rationale* (each has a clear owner-surface), which is this roadmap's terminal state
> for non-backend work.
>
> **Suite health restored this session** (was the verification blocker): two real bugs fixed —
> (1) `asyncio.sleep` accumulation made the suite take ~37 min (`050494a`: instant-sleep fixture →
> `test_sources` ∞→1.67s); (2) the migration SQL splitter broke on an inline-comment semicolon in
> `0015` (`e9f27c6`: `incomplete input` → fixed, `test_api.py` errors 12→0). Broad subset now runs
> **360 passed in 5.0s**. A residual `test_api.py` exit-hang / per-test migration-cost slowness remains
> (separate test-architecture debt; full green-suite still best confirmed on CI/Linux).
>
> **Roadmap-vs-reality lesson:** #2 was authored from `References.md` snapshots predating Pillar 2 —
> re-ground research-derived roadmaps against live code (rule #7) before building.

## Guiding principle

**Copy the technique, not the cost.** The competitors' real moat (automated discovery + LLM
enrichment) is cheap for Job360 to copy *because the expensive half is already built* — 12 ATS
parsers, the Pillar 2 `JobEnrichment` schema, and the Gemini→Groq→Cerebras LLM chain. Their
*high-maintenance* methods (headless browsers, custom big-tech adapters, paid proxies) should be
**deliberately skipped** — let them carry that burden while Job360 stays API-first.

> **Note on `References.md` §4:** that older "What to Copy" table is pre-Pillar-2 and lists
> ghost-detection, `last_seen`, embeddings, and 60s polling as TODO — **all of those are now
> shipped.** This roadmap supersedes it for sourcing/match/delivery priorities.

---

## Already shipped — do NOT re-recommend

| Capability | Where it lives | Status |
|---|---|---|
| Ghost / disappearance detection | `services/ghost_detection.py`, `main.py::_ghost_detection_pass` | ✅ shipped (Pillar 2) |
| `last_seen` tracking + snapshot diff | `repositories/database.py` | ✅ shipped |
| Embeddings + ChromaDB semantic layer | `services/embeddings.py`, `vector_index.py` | ✅ shipped (opt-in `SEMANTIC_ENABLED`) |
| 60s ATS polling (near-real-time) | `services/scheduler.py` `TIER_INTERVALS_SECONDS["ats"]=60` | ✅ shipped |
| Daily digest delivery | `workers/` `send_daily_digest` ARQ periodic | ✅ shipped (Step 3) |
| LLM provider chain | `services/profile/llm_provider.py` | ✅ shipped (CV parsing) |

---

## 🥇 Tier 1 — Do first (biggest leverage, reuses shipped code)

### 1. Automated company discovery — from HiringCafe / Scoutify
- **What:** ATS-tech detection (method C5) + Google-dorking (C4) → auto-grow the slug catalog.
- **Into:** `NEW backend/scripts/discover_companies.py` → appends to `core/companies.py`.
- **Why #1:** Job360 already parses Greenhouse/Lever/Ashby perfectly — it just points them at
  ~268 companies. Discovery finds thousands more *using parsers already written.* Converts the
  single biggest gap (🚩THEIRS "mass discovery") into pure reuse.
- **Effort:** medium · **Payoff:** huge.

### 2. LLM enrichment of job descriptions — from HiringCafe
- **What:** HiringCafe's GPT-4o-mini extraction (strict JSON schema, temperature 0 → salary,
  visa, skills, seniority). Published prompt schema referenced in `References.md` §1.1 sources.
- **Into:** Wire to the *existing* `JobEnrichment` Pydantic schema + `job_enrichment` table
  (Pillar 2) + `services/profile/llm_provider.py` chain. Gate behind `ENRICHMENT_ENABLED`.
- **Why:** All plumbing exists (flag, table, schema, LLM chain) — built for CV parsing, now
  pointed at job descriptions. Unlocks salary/visa/skill filtering competitors charge for.
- **Effort:** medium · **Payoff:** high.

## 🥈 Tier 2 — High payoff, moderate build

### 3. Schema.org JSON-LD harvesting (method B3) — from HiringCafe / FirstPost
- **What:** A generic source extracting embedded `JobPosting` JSON-LD from *any* career page.
- **Into:** `NEW backend/src/sources/scrapers/jsonld_harvest.py` (category `scrapers`, 60-min tier).
  Remember the **five load-bearing surfaces** (CLAUDE.md rule #8/#13) when adding a source.
- **Why:** Low-maintenance long-tail coverage — one parser, no per-site code. Pairs with #1.
- **Effort:** medium · **Payoff:** medium-high.

### 4. Auto-tailored CV + cover letter per job — from Sprout
- **What:** Per-JD CV/cover-letter generation (Sprout's core wedge, `References.md` §1.7).
- **Into:** Reuse `services/profile/llm_provider.py` — same provider chain, new prompt + endpoint.
- **Why:** Moves Job360 from discovery-only into the apply layer; clean v2 differentiator.
- **Effort:** medium-high · **Payoff:** high.

## 🥉 Tier 3 — Cheap wins (positioning + UX, mostly free)

### 5. "Within minutes" freshness positioning — from Scoutify · *zero build*
- Job360 already polls ATS every 60s — faster than HiringCafe (~8h) and FirstPost (24h). Surface
  per-source "last checked" in the UI and lead with it. A free marketing win already earned.

### 6. Curated dedup'd morning digest — from FirstPost · *light*
- `send_daily_digest` exists (ARQ, Step 3). Copy FirstPost's *quality bar*: only roles not in
  yesterday's email, deduped, profile-filtered. Polish the existing job, don't build new.

### 7. LinkedIn-OAuth onboarding — from Dex · *medium*
- Replace the CV + LinkedIn-PDF + GitHub triple-upload with one-click LinkedIn OAuth. Lowers the
  biggest onboarding-friction point.

### 8. Tagline + self-host copy — from Sprout + JobSync · *trivial*
- Sprout's 4-word agent-promise tagline format; JobSync's "your data never leaves your server"
  self-host positioning.

## 🔮 Tier 4 — Optional / later
- **Swipe-feed discovery mode** (Sprout / UrFuture) — optional feed view alongside list/radar (frontend).
- **Vetted-company quality signal** (WTTJ) — a ranking input boosting curated employers.

---

## 🚫 Deliberately DON'T copy

| Method | From | Why skip |
|--------|------|----------|
| Headless-browser scraping (B2) | HiringCafe | High maintenance; clean APIs cover most needs |
| Custom big-tech adapters (B4) | Scoutify | Every Amazon/Apple redesign breaks it — let *them* maintain it |
| Paid rotating proxies (B6) | HiringCafe (Oxylabs) | Cost; API-first model doesn't need it |
| Employer-direct posting (D1) | WTTJ | Different business; two-sided-marketplace build |

---

## The meta-takeaway

Every Tier-1/2 item is **"point existing code at a new target"** (ATS parsers → more companies;
LLM chain → job descriptions and CVs). That's *why* Job360 can close these gaps cheaply — the
prior investment (Pillar 2 enrichment, the LLM provider chain, 12 ATS parsers) is the leverage.

Tier-3 reveals the other half: Job360 is **under-marketing capabilities it already has** —
freshness, digests, multi-domain, UK depth are all shipped but un-headlined. Sometimes "copying a
competitor" means copying their *messaging confidence*, not their tech.
