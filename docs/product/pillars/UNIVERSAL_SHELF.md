# The Universal Shelf — Catalog Field Contract
<!-- doc: LIVING -->

> **Audience.** Read this to understand what fields every job in the shared CATALOG must carry, how each field gets filled, and how we always know *how* it was filled. This is the design the owner asked for: *"You build the shelves, and the catalog is nothing but a combination of N shelves. The shelves should be the right amount of shelves that are relevant, so we do better search, match and score for the users."*
>
> **Scope.** CATALOG only. A SHELF is one field on a job we can fill and later match on. The UNIVERSAL SHELF is the fixed set every job carries, whatever its source. JOB SOURCE ENRICHMENT is an LLM reading a job ad to extract facts about the JOB (identical for all users — catalog work). This doc does not design search or matching; §9 only names what search gains.

---

## 1. The shelf list

Discipline rule: a shelf earns its place only if (a) at least one real source can fill it and (b) a named consumer reads it. A shelf nobody fills or nothing consumes is dead weight — see the rejected list below.

The three owner-named shelves are **DEADLINE**, **SALARY**, **VISA SPONSORSHIP**. Four shelves (salary, seniority, visa, workplace) already have paid-for consumers waiting: the multi-dim scorer weights them, in `core/settings.py`.

### Identity block (not matchable shelves, but every row needs them)

| Field | Type | Why |
|---|---|---|
| `source` | text | Which fetcher produced the row |
| `apply_url` | text | Where the user applies; part of dedup |
| `first_seen_at` / `last_seen_at` / `staleness_state` | timestamps + enum | Lifecycle + ghost detection |
| (recommended) `source_job_id` | text | Stable upstream id — stronger dedup than `(company,title)`. Does not exist today. |

### The 13 universal shelves

The authoritative list is the frozen `UNIVERSAL_SHELF` tuple in `models.py`; `tests/test_universal_shelf.py::test_gate_accounts_for_every_shelf` fails if the gate and the tuple disagree.

| # | Shelf | Why it matters for matching a person to a job | Consumer that reads it |
|---|---|---|---|
| 1 | **title** | Primary role match — Title component is 40 of 100 legacy points | `JobScorer` title component |
| 2 | **company** | Identity, dedup key half, display trust | dedup (`normalized_key`), UI |
| 3 | **location** | UK door (rule #30) + location score 10 pts | `uk_gate.check_uk`, location component |
| 4 | **description** | Raw material for skill match (40 pts), JOB SOURCE ENRICHMENT, and embeddings. Everything downstream eats this. | skill matcher, enrichment, embeddings |
| 5 | **posted_at** (+ `date_confidence`, `date_posted_raw`) | Recency score 10 pts; honesty about date trust | recency component, UI |
| 6 | **deadline** (+ `deadline_source`) | OWNER-NAMED. "Closing soon" urgency, expired-job removal — an application after the deadline is wasted user effort | UI, feed ordering (unblocks §9) |
| 7 | **salary** (`salary_min`/`max`, `salary_currency`, `salary_period`, `salary_is_estimated`, derived `salary_*_gbp_annual`) | OWNER-NAMED. Salary dim is 10 pts; salary filter is a top-3 job-board filter. Without currency+period the numbers are lies: one source can store EUR, BRL and USD figures all labelled GBP, and another stores hourly and annual in the same field | salary dim (`services/salary`), display (`api/routes/jobs`) |
| 8 | **visa_status** | OWNER-NAMED. Rule #31: visa is a SPOTLIGHT, not a wall — sponsors ranked up + badged, catalog never shrinks. The bool `visa_flag` conflates "says no" with "never mentioned" | visa dim (6 pts), badge UI |
| 9 | **employment_type** | Contract vs permanent is a hard user constraint, not a preference. Widest source coverage of any shelf added by the frame | new search filter + prefilter (§9) |
| 10 | **seniority** | Seniority dim is 8 pts; entry-level users drown in senior roles and vice versa | seniority dim, prefilter |
| 11 | **workplace_mode** | Remote/hybrid/onsite is a hard constraint for most users | workplace dim (6 pts), filter |
| 12 | **skills** = `source_tags` (catalog) + LLM `required_skills`/`preferred_skills` (in `job_enrichment`) | Direct fodder for the 40-pt skill component and embeddings; source tags are the job's own vocabulary, no guessing | skill matcher via `enrichment_lookup`, embeddings |
| 13 | **category** | Domain facet ("IT vs teaching vs healthcare") for retrieval prefilter and dashboard facets | retrieval prefilter, facets |

**Absent everywhere = column NULL.** The string `"unknown"` is an LLM-contract value; the catalog stores NULL plus a provenance reason (§3, §4).

### Rejected shelves (deliberately NOT in the universal set)

| Candidate | Why rejected |
|---|---|
| language | Only arbeitnow is meaningfully non-English; no consumer; LLM contract already carries it if ever needed |
| benefits / perks | Prose, no consumer, no comparable structure |
| company metadata (size, rating, logo, industry of company) | Display sugar, not match signal; jsearch/indeed carry it but nothing reads it |
| lat/long, postcode | Finer than any consumer needs; `uk_gate` works on names |
| education requirements | Sub-signal of seniority; fold into seniority evidence, not its own shelf |
| red_flags, apply_instructions, remote_region, requirements_summary | Stay in `job_enrichment` (LLM working data), not promoted to catalog columns until a consumer exists |

---

## 2. The fill chain per shelf

Trust order, fixed for every shelf: **structured source field → free derivation (existing detectors, no LLM) → JOB SOURCE ENRICHMENT (LLM reads the ad) → ABSENT.** A lower layer never overwrites a higher one. Every hop is recorded in provenance (§3).

Which upstream field feeds which shelf is stated, per source, in the source file itself. Do not keep a second copy of it here: a per-source inventory in a doc is a list that goes stale the first time anyone opens a mapper, and one shipped in exactly that state.

Two chain rules that are *policy*, not mapping, and therefore live here:

- **DEADLINE ends at derived.** `JobEnrichment` has no deadline field and must not get one: `services/deadline.extract_deadline` covers prose dates, and an LLM-guessed date is exactly the fabrication rule #29 bans.
- **SALARY annualises and currency-tags BEFORE it sanity-clamps.** A unit-blind clamp destroys every honest hourly/daily/monthly figure. `shelf_gate._fill_salary` owns that order.

---

## 3. Provenance — every shelf records HOW it was filled

Without this we cannot tell "no salary offered" (a fact about the job) from "nobody looked" (a fact about our pipeline). Those are different facts and they route different work: the first is done, the second goes back in a queue.

### Storage: one JSONB column on `jobs` — `shelf_provenance`

```json
{
  "salary":          {"how": "source",  "field": "compensation.min/max", "at": "2026-08-16T10:12:00Z"},
  "deadline":        {"how": "derived", "by": "deadline.extract_deadline@v1", "at": "2026-08-16T10:12:00Z"},
  "employment_type": {"how": "llm",     "by": "gpt-4o-mini", "raw": ["PART_TIME","TEMPORARY"], "at": "2026-08-17T02:00:00Z"},
  "visa_status":     {"how": "absent",  "why": "not_stated"},
  "description":     {"how": "absent",  "why": "stub"}
}
```

Contract: keys are exactly the `UNIVERSAL_SHELF` tuple (§5) — no more, no fewer. `how` ∈ `source | derived | llm | absent`. `absent` carries `why` (§4). `raw` optionally preserves the pre-normalisation source value (e.g. the multi-valued employment list, the original currency string).

### Why JSONB (and why not the alternatives)

| Option | Verdict | Reason |
|---|---|---|
| **JSONB column on `jobs`** | **CHOSEN** | 13 shelves × 3-4 subkeys would be ~40 typed columns and a migration per new shelf. Provenance is telemetry/audit/absent-reason display — never hot-path scoring — so JSON access cost is irrelevant. 1:1 with the job, always read with the row. |
| Typed columns per shelf | rejected | Column explosion; every new shelf = schema change in two places |
| Separate `shelf_provenance` table | rejected | 1:1 with jobs; a join buys nothing and adds a place to forget writes |
| Reuse `job_enrichment` | rejected | That table is the LLM layer's OWN store (raw model output). Provenance must also describe source/derived/absent fills that never touch the LLM. Rules #10/#17 unaffected: everything here is catalog-shared, no `user_id` anywhere. |

**`pg.py` shim compatibility:** the repository layer reads/writes the whole JSON via `json.dumps`/`json.loads` in Python — no SQL-side JSON operators in any hot path — so `translate()` (`src/repositories/pg.py`) needs no new grammar and the SQLite-shimmed test suite keeps working. Postgres can add a GIN index later if a filter ever needs one.

**Values live in typed columns; provenance lives in JSONB.** The shelf VALUES (salary_min, deadline, employment_type…) stay real, indexable columns on `jobs` — filterable and cheap. Only the how-it-got-there metadata goes to JSONB. The columns were opened by `backend/migrations/0032_universal_shelf.up.sql`.

**Where LLM values land:** `job_enrichment` remains the LLM layer's append store (versioned raw output, idempotent per `job_id`). The enrichment sweep then WRITES BACK through the gate (§5) into still-NULL `jobs` columns with `how:"llm"`. Rejected alternative — virtual shelves via `COALESCE(jobs.col, enrichment.col)` at read time — would force every consumer (filters, serializers, embeddings) to replicate the join+merge logic, which is exactly the per-source scatter this design exists to kill.

---

## 4. ABSENT is a real value

Rule #29 for the catalog side: an empty shelf must never become a guess, a zero, or a penalty. Absent is stored, typed, and explained.

**Storage:** value column = NULL, provenance = `{"how": "absent", "why": <reason>}`.

| `why` | Meaning | Who can change it |
|---|---|---|
| `not_mapped` | Nobody looked — the source handed nothing and no derivation ran (the gate's default stamp for a fresh gap) | free recovery work, next gate pass |
| `source_lacks_field` | We looked — this source's schema structurally has no such field | only a richer fetch (detail call) or the LLM reading the ad |
| `stub` | LLM pass was BLOCKED: description is a stub/<600 chars — answering would be fabrication | description recovery, then re-queue |
| `not_stated` | A real description was read (by detector or LLM) and the ad genuinely does not say | nothing — this is a final fact about the job |
| `implausible` | A value ARRIVED and the gate refused it — today only salary, when the annualised GBP band falls outside the plausibility band. The original figures are kept in `raw`, so the refusal is auditable | a better unit/currency mapping upstream, or a change to the plausibility band |

**What consumers MUST do with absent** (the catalog-side mirror of rule #29):

| Consumer | Rule on NULL shelf |
|---|---|
| Dim scorers | Return the neutral constant — never a per-job zero |
| Prefilters | Pass the job through. A filter "salary ≥ £50k" excludes NULL-salary jobs ONLY if the user explicitly ticks "only show jobs that state pay" — default keeps them, ranked by everything else |
| Visa consumers | `unknown` = visible, unbadged, unpenalised (rule #31 — refusal is tested before offer) |
| Deadline consumers | NULL ≠ expired. Never sort NULL to "closing soon" or "expired"; UI shows the existing fallback |
| JOB SOURCE ENRICHMENT | Never asked to fill a shelf from a `stub` job; the prompt mandates explicit `unknown` over invention |
| Frontend | Show "—" / omit the chip. Never render 0, never a red state |
| Telemetry | Per-shelf, per-source fill-rate split BY `why` — the empty-shelf-three-causes rule: broken extractor vs merge-dropped vs no front door look identical in a bare NULL; `why` separates them |

---

## 5. The chokepoint — one door, every job

Before the gate, every source hand-wrote its own `Job(...)` and filled whatever it happened to have; a source could silently forget a shelf and nothing noticed. The fix is one function every job passes through, on a path no source can skip.

### Design

`src/services/shelf_gate.py` — `fill_shelves(job: Job) -> Job` (sync, no I/O, no await):

1. **Sources are dumb mappers.** A source's only duty is copying upstream keys onto `Job` fields. No policy, no normalisation, no defaults in source files.
2. **The gate owns all policy:** enum-normalise employment/workplace/seniority strings ('Full time', 'FULLTIME', 'permanent' → `full_time` — a CLOSED set, so enumeration is legal under rule #30's bounded-set law, raw value preserved in provenance); annualise+currency-tag salary then clamp; run `detect_visa_status`; run `extract_deadline`; detect stub descriptions.
3. **Stamp provenance for EVERY shelf, always** — filled or absent. The invariant is not "every shelf filled" (impossible); it is "every shelf ACCOUNTED FOR."
4. Two entry points, same core: `fill_shelves(job)` at ingest; `apply_enrichment(job_row, enrichment)` in the sweep write-back — so `how:"llm"` rows get identical normalisation and never overwrite `source`/`derived` fills.

### Where it sits

Inside `_score_dedup_and_filter()` in `src/main.py` — the single synchronous stage every run already passes through (score → dedup → store, threaded off the loop). The gate runs FIRST in that function, before scoring, so the scorer reads normalised shelves. No source can bypass it because sources don't call it — the orchestrator does, downstream of all of them.

### Enforcement — `tests/test_universal_shelf.py`

| Test | Asserts |
|---|---|
| `test_gate_accounts_for_every_shelf` | `fill_shelves(Job(minimal))` → `set(job.shelf_provenance) == set(UNIVERSAL_SHELF)` exactly. `UNIVERSAL_SHELF` is a frozen tuple in `models.py` — the single source of truth the gate, migration, and tests all import. Add a shelf to the tuple without teaching the gate → this test fails. |
| `test_pipeline_round_trip` | Fake source → `run_search` → stored row: full provenance present AND one non-default value round-trips (value-presence, not schema-presence — rule #21) |
| `test_absent_is_typed` | A job with no salary stores NULL + `{"how":"absent"}` — never 0, never a guess |
| `test_llm_blocked_on_stub` | A stub-description job is never handed to `enrich_job` |
| `test_a_stub_description_is_never_sent_to_the_llm` | End to end: the sweep's mocked LLM records every PROMPT, and the stub job's text appears in none of them — proof it was never SENT, not merely that its answer was discarded |
| `test_an_llm_value_never_overwrites_a_source_filled_shelf` | Trust order §2 holds under a real write: source values survive, and the empty shelf beside them still gets filled |
| `test_provenance_says_llm_only_for_shelves_the_llm_filled` | A shelf the model left `unknown` keeps `absent` — `how:"llm"` is never stamped on a shelf the LLM did not fill |
| `test_the_budget_cap_stops_the_sweep_and_says_so_loudly` / `test_the_spend_cap_bites_before_the_job_cap_when_it_is_tighter` | Both ceilings are hard stops on CALLS, and hitting one logs at ERROR |
| `test_the_spend_is_written_to_run_log` | One `run_log` row per sweep with the real spend — "what did last night cost?" is answerable |
| `test_pass1_fills_shelves_without_spending_anything` / `test_pass1_never_removes_a_value` | The free pass really is free (zero LLM calls, $0) and only ever ADDS |
| `test_re_running_the_gate_does_not_relabel_an_llm_fill_as_source` | Provenance cannot be laundered by a second sweep |
| `test_with_the_flags_off_the_nightly_path_never_sweeps` | Rule #18 — with `ENGINE2_ENABLED` and `ENRICHMENT_ENABLED` both off the sweep is not called at all |

---

## 6. JOB SOURCE ENRICHMENT — the two-pass sweep

`services/shelf_enrichment.py`, wired into `workers/tasks.refresh_catalog` after the nightly fetch, behind `ENGINE2_ENABLED OR ENRICHMENT_ENABLED`:

| Pass | Cost | What it does |
|---|---|---|
| **1** | **$0** | Re-runs `shelf_gate.fill_shelves` over rows ALREADY stored — every row written before the gate existed never saw it. Also writes back `job_enrichment` rows already paid for whose facts never reached the `jobs` columns (§3 "Where LLM values land"). **Only ever ADDS**: a re-derivation that yields NULL never deletes a stored value. |
| **2** | LLM | Only on rows that survive three filters: description is not a stub (`is_stub_description` — the fabrication block), ≥ `SHELF_ENRICHMENT_MIN_ABSENT_SHELVES` consumer shelves honestly absent, no existing `job_enrichment` row (unless `force`). Writes through `shelf_gate.apply_enrichment`, which fills ONLY honestly-absent shelves and stamps `how:"llm"` for exactly what it filled. |

An LLM `category` of `other` is **refused** — `JobCategory` is the one contract enum with no `unknown` member, so a model that cannot classify is forced into `other` and that value carries no information (rule #29).

**Pass 1 fills nothing on a freshly-gated catalog, and that is the gate working.** Every row written after `fill_shelves` was wired already went through it, so the free pass has nothing left to recover. **Pass 1 earns its keep on exactly two populations:** rows stored BEFORE the gate was wired, and rows whose already-paid-for `job_enrichment` facts never reached the `jobs` columns. The consequence to state plainly: on a freshly-gated catalog, the flat `visa_status` / `seniority` / `category` fill rates can only be moved by **pass 2** — the free detectors have already had their turn.

**Text recovery MUST precede LLM shelf-filling, per job. Two independent proofs:**

1. **Fabrication is cached.** A confident LLM answer extracted from a 453-char teaser, or from a description that is just the title, is a fabrication — and `enrich_job` is idempotent (a second call on an enriched `job_id` is a no-op unless `force=True`), so the wrong enum is PERMANENT until someone force-re-runs. Recover text first and the poison never enters.
2. **Prod already measured the correlation.** 1,311 active jobs (30% of catalog) carried <200 chars of description, and coverage of every enriched field (workplace/seniority/visa) tracked description length almost perfectly (measured 2026-08-07). Enriching thin jobs buys `unknown`s at full token price.

The ordering is per-job, enforced by the gate's `stub` block — not a calendar phase.

---

## 7. Cost of JOB SOURCE ENRICHMENT

**Model:** the enrichment chain is OpenAI-primary — `llm_extract_validated` → `OPENAI_MODEL` (paid, deterministic), then the free-tier Gemini → Groq → Cerebras fallbacks in `services/profile/llm_provider.llm_extract`. Fallbacks only fire on OpenAI failure.

**Two hard ceilings**, both from settings, both checked BEFORE each call; the sweep stops at whichever bites first: `SHELF_ENRICHMENT_MAX_JOBS` and `SHELF_ENRICHMENT_MAX_SPEND_USD`. A job cap alone cannot bound cost — the same number of jobs costs several times more when the ads are long — so the spend cap is the real rail. Hitting either logs at ERROR with how many eligible jobs went unread; a cap that trims silently is a cap nobody can act on (same lesson as `MAX_REFRESH_INGEST_IDS`). Prices are `LLM_INPUT_USD_PER_1M` / `LLM_OUTPUT_USD_PER_1M`, env-overridable for the same reason `OPENAI_MODEL` is — a stale hardcoded price is a silent lie. Input tokens are measured from the real prompt at ~4 chars/token (deliberately conservative, so the cap trips early not late); output is the fixed JSON shape, which cannot be measured without making the call.

**And it is now ANSWERABLE.** `backend/migrations/0033_run_log_enrichment_stats.up.sql` adds `run_log.enrichment_stats`; every sweep writes one row (`run_uuid LIKE 'shelf-enrichment-%'`) carrying jobs read, tokens in/out, estimated USD and whether a cap bit. Before this, nothing in the system could answer *"what did last night cost?"*.

**Absence is CORRELATED.** Measured over a live 2,915-job catalog (2026-08-17): 99.6% of eligible jobs were missing 2+ shelves and 78% were missing 4+ — so raising `SHELF_ENRICHMENT_MIN_ABSENT_SHELVES` from 1 to 4 saved only ~17% of spend while dropping 15% of the jobs. The stub block, not the shelf threshold, is where the money-losing mistake was.

Never run this on the search hot path — the event loop has been frozen by catalog-scale work before.

---

## 8. Embeddings — what text represents a job

Catalog-side only. Infra exists: `job_embeddings` (no user_id — rule #17), `SEMANTIC_ENABLED` default off, convergence backfill `EMBED_BACKFILL_PER_RUN`. The enrichment `requirements_summary` field was designed as embedding input.

**Embed this per job, in this order (stable template):**

```
{title} at {company}
{seniority} | {employment_type} | {workplace_mode} | {location}
Skills: {source_tags + required_skills + preferred_skills, deduped, csv}
{requirements_summary, when enrichment exists}
{description — HTML-stripped, first ~4,000 chars}
```

| Rule | Why |
|---|---|
| Never embed a stub description (`absent:stub`) — embed the header lines only | description==title would double-count the title and embed noise |
| Never embed salary numbers or deadline dates | numerics are noise to a sentence encoder; structured columns own filtering on them |
| The shelf line uses the ENUM values, not raw source strings | one vocabulary across every source = tighter clusters |
| Re-embed on provenance transition of `description` (absent/stub → source/derived) or of `skills` | provenance gives the trigger for free — no diffing text; the existing backfill loop picks them up |

---

## 9. WHAT THIS UNBLOCKS

| Shelf | What the search side can now do (could not before) |
|---|---|
| deadline | "Closing soon" urgency rail; auto-drop expired jobs from feeds; never let a user write a cover letter for a closed vacancy |
| salary (+currency/period/estimated) | An honest salary filter: annual-GBP comparable across every source; EUR/BRL no longer masquerade as GBP; "advertised" beats an ML guess via `salary_is_estimated`; hourly rates finally survive instead of being clamp-nulled |
| visa_status | Rule #31 done right at catalog level: sponsors spotlighted + badged, `no_sponsorship` honestly labelled, `unknown` visible and unpenalised — the visa dim (6 pts) finally has real input |
| employment_type | Contract/permanent/part-time/internship/apprenticeship as a hard filter and prefilter — before this the user constraint was unanswerable |
| seniority | Entry-level users stop drowning in staff+ roles; seniority dim (8 pts) runs on data instead of title regex |
| workplace_mode | A real remote/hybrid/onsite filter instead of grepping "remote" in a location string; workplace dim (6 pts) fed |
| description (recovered) | The 40-pt skill component and the LLM judge read real ads on far more of the catalog; JOB SOURCE ENRICHMENT stops fabricating from teasers |
| posted_at (fixed) | Recency score honest instead of defaulting to low-confidence None |
| skills / source_tags | Skill matching against the job's own declared stack; better embedding material; skill-gap features get per-job ground truth |
| category | Domain prefilter for retrieval (funnel Stage-1) + dashboard facets |
| provenance (cross-cutting) | Search can rank "states salary" above "salary guessed" WITHOUT hiding either; telemetry can finally answer "is this shelf empty because the extractor broke, the source lacks it, or nobody looked?" — three different work queues |
| the chokepoint | A new source gets every shelf for free on day one; a forgotten mapping is a counted `absent:not_mapped`, not a silent hole discovered months later |
