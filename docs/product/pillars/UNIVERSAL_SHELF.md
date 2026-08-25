# The Universal Shelf — Catalog Field Contract
<!-- doc: LIVING -->

> **Audience.** Read this to understand what fields every job in the shared CATALOG must carry, how each field gets filled, and how we always know *how* it was filled. This is the design the owner asked for: *"You build the shelves, and the catalog is nothing but a combination of N shelves. The shelves should be the right amount of shelves that are relevant, so we do better search, match and score for the users."*
>
> **Scope.** CATALOG only. A SHELF is one field on a job we can fill and later match on. The UNIVERSAL SHELF is the fixed set every job carries, whatever its source. JOB SOURCE ENRICHMENT is an LLM reading a job ad to extract facts about the JOB (identical for all users — catalog work). This doc does not design search or matching; §9 only names what search gains.
>
> **Evidence base.** Field availability per source comes from the live source harvest of 2026-08-16 (all four batches: keyed 7, ATS 10, free 11, feeds 11 — every source probed with a real `SearchConfig`, real HTTP 200 responses). Code facts were re-verified against this branch (`fix/pillar3-source-repairs`) on 2026-08-16 with `file:line` cites. Prod stats carry their own dates.

**Baseline, live-measured 2026-08-16 (task harvest):** 41 registry keys → 40 fetchers → 39 sources returning jobs; one run fetches 16,116 jobs. 16 of 39 sources store a fake/stub description. 24% of the live catalog has NO description at all. Only 5 of today's 14 shelves affect anything downstream. There are ~42 hand-written `Job(...)` constructions, no normalisation layer, and no provenance.

---

## 1. The shelf list

Discipline rule: a shelf earns its place only if (a) at least one real source can fill it and (b) a named consumer reads it. A shelf nobody fills or nothing consumes is dead weight — see the rejected list below.

The three owner-named shelves are **DEADLINE**, **SALARY**, **VISA SPONSORSHIP**. Four shelves (salary, seniority, visa, workplace) already have paid-for consumers waiting: the multi-dim scorer weights SALARY 10 / SENIORITY 8 / VISA 6 / WORKPLACE 6 points (`core/settings.py:195-198`) — today those dims run mostly on empty inputs.

### Identity block (not matchable shelves, but every row needs them)

| Field | Type | Why | Status today |
|---|---|---|---|
| `source` | text | Which fetcher produced the row | exists |
| `apply_url` | text | Where the user applies; part of dedup | exists — upgrade candidates: reed detail `externalUrl` (real employer link), google `source_link`/`apply_options[]`, indeed `job_url_direct` (harvest 2026-08-16) |
| `first_seen_at` / `last_seen_at` / `staleness_state` | timestamps + enum | Lifecycle + ghost detection | exists (`models.py:50-52`) |
| (recommended) `source_job_id` | text | Stable upstream id (reed `jobId`, jsearch `job_id`, google `job_id`, ashby `id`…) — stronger dedup than `(company,title)` | does not exist; harvest flagged google dedup as weaker without it |

### The 13 universal shelves

| # | Shelf | Type (catalog column) | Why it matters for matching a person to a job | Consumer that reads it | Status today (2026-08-16) |
|---|---|---|---|---|---|
| 1 | **title** | text | Primary role match — Title component is 40 of 100 legacy points | `JobScorer` title component | exists, filled |
| 2 | **company** | text | Identity, dedup key half, display trust | dedup (`normalized_key`), UI | exists, filled |
| 3 | **location** | text (raw) | UK door (rule #30) + location score 10 pts | `uk_gate.check_uk`, location component | exists; known leak: aijobs_ai drops the card's own location text so a "United States" job slipped the gate (harvest 2026-08-16) |
| 4 | **description** | text | Raw material for skill match (40 pts), JOB SOURCE ENRICHMENT, and embeddings. Everything downstream eats this. | skill matcher, enrichment, embeddings | exists; 24% of catalog empty, 16/39 sources stub it (harvest 2026-08-16); prod 2026-08-07: 30% of active jobs <200 chars and enriched-field coverage tracks description length almost perfectly (`settings.py:234-236`) |
| 5 | **posted_at** (+ `date_confidence`, `date_posted_raw`) | timestamp + enum | Recency score 10 pts; honesty about date trust | recency component, UI | exists (5-column date model, `models.py:35-41`); free fixes owed: climatebase `activation_date` (100% fill, code hardcodes None), eightykhours reads nonexistent `date_published` instead of `posted_at` |
| 6 | **deadline** (+ `deadline_source`) | date + enum | OWNER-NAMED. "Closing soon" urgency, expired-job removal — an application after the deadline is wasted user effort | UI, feed ordering (unblocks §9) | exists, partially fed (8 sources read it; 4+ more carry it unread) |
| 7 | **salary** = `salary_min`, `salary_max`, + NEW `salary_currency`, `salary_period`, `salary_is_estimated`, + derived `salary_min_gbp_annual`/`salary_max_gbp_annual` | numeric ×2, text ×2, bool, numeric ×2 | OWNER-NAMED. Salary dim is 10 pts; salary filter is a top-3 job-board filter. Without currency+period the numbers are lies: landingjobs live 2026-08-16 = 0/50 jobs GBP (46 EUR, 3 BRL, 1 USD) yet all stored as GBP; careerjet stores hourly and annual in the same field | salary dim (`salary.py`), display (`api/routes/jobs.py:43-49`) | min/max exist; currency/period/estimated DO NOT — the model clamp `models.py:91-95` silently assumes GBP-annual |
| 8 | **visa_status** | text enum: `sponsors` / `no_sponsorship` / `unknown` | OWNER-NAMED. Rule #31: visa is a SPOTLIGHT, not a wall — sponsors ranked up + badged, catalog never shrinks. The bool `visa_flag` conflates "says no" with "never mentioned" | visa dim (6 pts), badge UI | 3-state exists only at read time (`visa_signal.py:33-36`); catalog column is still the conflating bool |
| 9 | **employment_type** | text enum: `full_time` / `part_time` / `contract` / `internship` / `temporary` / `apprenticeship` / `freelance` (from `job_enrichment_schema.py:53-61`) | Contract vs permanent is a hard user constraint, not a preference. Widest free coverage of any missing shelf: ~25 sources already send it | new search filter + prefilter (§9) | NO column exists at all (harvest note, 2026-08-16) — every source's value is thrown away |
| 10 | **seniority** | text enum: `intern`…`director` (`job_enrichment_schema.py:77-85`) | Seniority dim is 8 pts; entry-level users drown in senior roles and vice versa | seniority dim, prefilter | free-text `experience_level` exists, filled only from title regex (`main.py:706`); 6+ sources' structured values unread |
| 11 | **workplace_mode** | text enum: `remote` / `onsite` / `hybrid` (`job_enrichment_schema.py:64-68`) | Remote/hybrid/onsite is a hard constraint for most users; today it's inferred from the word "remote" in a location string | workplace dim (6 pts), filter | NO column; ~14 sources send it (ashby, workable, pinpoint, climatebase 100% fill, indeed, …) |
| 12 | **skills** = `source_tags` (catalog) + LLM `required_skills`/`preferred_skills` (in `job_enrichment`) | JSON list + enrichment columns | Direct fodder for the 40-pt skill component and embeddings; source tags are the job's own vocabulary, no guessing | skill matcher via `enrichment_lookup`, embeddings | NO catalog column; arbeitnow tags 93% fill, remotive 100%, nofluffjobs `tiles.values[]` 100% of 21,795 postings — all discarded today (harvest 2026-08-16) |
| 13 | **category** | text enum (16-way, `job_enrichment_schema.py:33-50`) | Domain facet ("IT vs teaching vs healthcare") for retrieval prefilter and dashboard facets; free deriver already exists (`services/domain_classifier.py`) | retrieval prefilter, facets | in LLM contract only; adzuna `category.label`, smartrecruiters `industry`/`function`, teaching `occupationalCategory` unread |

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

All field names below are from the live harvest of 2026-08-16 unless cited otherwise.

### DEADLINE (owner-named)

| Layer | Real fields |
|---|---|
| Source — read today | pinpoint `deadline_at` · recruitee `close_at` · workday detail `endDate` · himalayas `expiryDate` · landingjobs `expires_at` · weworkremotely `expires_at` · teaching_vacancies `validThrough` · nhs_jobs `closeDate` |
| Source — in payload, unread (FREE) | reed `expirationDate` · gov_apprenticeships `closingDate` (3/3 sample) · greenhouse `application_deadline` (non-null on a live Monzo job) · linkedin JSON-LD `validThrough` (detail page already fetched) |
| Source — extra request | nofluffjobs detail `expiresAt` (`GET /api/posting/{id}`, id in hand) · eightykhours `closes_at` (schema-present, null in 26-hit sample) |
| Free derivation | `services/deadline.extract_deadline(description)` — already runs for every job lacking a structured deadline (`main.py:708-716`), sets `deadline_source='description'` |
| JOB SOURCE ENRICHMENT | **NONE, deliberately.** `JobEnrichment` has no deadline field and must not get one: the regex covers prose dates, and an LLM-guessed date is exactly the fabrication rule #29 bans. Chain ends at derived. |
| ABSENT | `not_stated` — most boards have no deadline concept. `models.py:43`: "None means no deadline listed. NEVER fabricated." |

### SALARY (owner-named)

| Layer | Real fields |
|---|---|
| Source — numbers read today | smartrecruiters `compensation.min/max/currency/period` (only source reading currency+period) · pinpoint `compensation_minimum/maximum` · recruitee `salary.min/max` · remoteok `salary_min/max` (USD by convention, unstated in payload) · devitjobs `annualSalaryFrom/To` (+ `contractRate*` fallback) · jobicy `salaryMin/Max` · himalayas `minSalary/maxSalary` · landingjobs `gross_salary_low/high` · nofluffjobs `salary.from/to` (currency-guarded — the only guard in the free batch) · climatebase `salary_from/to` (3/100 fill) · indeed `min_amount/max_amount` · adzuna `salary_min/max` (3/3 fill) · reed `minimumSalary/maximumSalary` · careerjet `salary_min/max` · google `detected_extensions.salary` (regex) · jsearch `job_min_salary/job_max_salary` |
| Source — unit/currency SIDECARS, unread (FREE — these are the mislabel bugs) | jobicy `salaryCurrency`+`salaryPeriod` (of 3 filled: 1 USD, 2 GBP) · himalayas `currency`+`salaryPeriod` (USD seen stored as GBP) · landingjobs `currency_code` (**0/50 GBP live** — every stored salary from this source mislabeled) · careerjet `salary_type` ('Y'/'H' both seen — hourly and annual currently indistinguishable) + `salary_currency_code` · indeed `currency`+`interval`+`salary_source` · nofluffjobs `salary.period` ('Month' seen — latent gap behind the currency guard) · pinpoint `compensation_currency`/`compensation_frequency` · recruitee `salary.period/currency` · reed detail `salaryType` ('per day' with null amount — rate unit without a number) · adzuna `salary_is_predicted` (real vs ML-guessed, '0'/'1' both seen) |
| Source — unlockable | ashby `compensation.summaryComponents[].{minValue,maxValue,currencyCode,interval}` — requires only `?includeCompensation=true` on the existing request (confirmed populated live on openai: $257K–$335K structured) · linkedin JSON-LD `baseSalary.value.{minValue,maxValue,currency,unitText}` (clean numerics, page already fetched — £65,000–£75,000/YEAR seen) · eightykhours `salary_limit` (clean numeric) + `salary` string · gov_apprenticeships `wage.wageAdditionalInformation` free text — the ONLY number carrier; code reads `wage.wageAmount` which does not exist in the payload (3/3 samples), so gov salary is null 100% today |
| Free derivation | `normalize_salary()` (`services/salary.py:37`) annualises via `_FREQUENCY_ANNUAL` and converts via `core/fx.to_gbp` (18 hardcoded rates, 2026-Q1, `fx.py:14-33`); `is_known_currency` guards display (`api/routes/jobs.py:43-49`); remotive's regex parser (correctly skips hourly); nhs regex (broken: splits on '.', decimal hourly rates never parse — fix in gate) |
| JOB SOURCE ENRICHMENT | `JobEnrichment.salary` = `SalaryBand{min,max,currency,frequency}` (`job_enrichment_schema.py:108-119`) |
| ABSENT | `not_stated` — ~70% of the UK corpus omits pay entirely (`job_enrichment_schema.py:110`) |

**Gate rule for salary:** annualise + currency-tag FIRST, then sanity-clamp. The current clamp (`models.py:91-95`, <10k or >500k → None, GBP-annual assumed) destroys every honest hourly/daily/monthly figure (nhs £30.27/h, nofluffjobs 3,600/Month) — with `salary_period` known, the gate converts before clamping and keeps `salary_is_estimated` from adzuna so a real advertised figure outranks an ML guess.

### VISA SPONSORSHIP (owner-named)

| Layer | Real fields |
|---|---|
| Source | devitjobs `hasVisaSponsorship` (real bool, read today) · eightykhours `visas[]` (e.g. `['not us','not uk']`, unread) |
| Free derivation | `visa_signal.detect_visa_status(description, title)` → 3-state, refusal beats offer (`visa_signal.py:70-93`) — already exists and already accepts the LLM verdict as an override |
| JOB SOURCE ENRICHMENT | `JobEnrichment.visa_sponsorship` (yes/no/unknown) — `detect_visa_status(..., enrichment_value=...)` already gives it precedence (`visa_signal.py:78-87`) |
| ABSENT | IS the third state: `unknown`. Rule #31 — never shrink the catalog, never penalise unknown; sponsors get the spotlight, unknown stays visible unbadged. |

### EMPLOYMENT_TYPE (widest free coverage of any missing shelf)

| Layer | Real fields |
|---|---|
| Source — keyed | adzuna `contract_time`+`contract_type` · jsearch `job_employment_type`/`job_employment_types[]` · reed detail `contractType`/`partTime`/`fullTime` · google `detected_extensions.schedule_type` (same dict we already read `posted_at` from) |
| Source — ATS | lever `categories.commitment` · ashby `employmentType` · workable `type` · pinpoint `employment_type(_text)` · recruitee `employment_type_code` · workday LIST `timeType` (in the list response, before any detail call — costs nothing) · personio `employmentType`+`schedule` · smartrecruiters `typeOfEmployment.label` (rides the already-made detail call) · successfactors JSON-LD `employmentType[]` (needs the page fetch) |
| Source — free/feeds | arbeitnow `job_types[]` (82% fill) · jobicy `jobType[]` · himalayas `employmentType` · remotive `job_type` (100% fill: full_time/part_time/freelance) · landingjobs `type` · nhs `type` (100% fill) · weworkremotely `type` (100/100: 96 Full-Time, 4 Contract) · teaching_vacancies `employmentType[]` (100% fill; can be multi: PART_TIME+TEMPORARY) · climatebase `job_types` (96% fill) · eightykhours `tags_role_type` · aijobs_ai badge = `_card_texts()[1]`, computed then thrown away · indeed `job_type` (empty on n=1 — unverified fill) · linkedin JSON-LD `employmentType` |
| Free derivation | none — no guessing from titles |
| JOB SOURCE ENRICHMENT | `employment_type` enum (`job_enrichment_schema.py:53-61`) |
| ABSENT | `not_stated` / `source_lacks_field`. Multi-valued reality (teaching: 4% carry two): store the primary value; raw list preserved in provenance. |

### SENIORITY

| Layer | Real fields |
|---|---|
| Source — read today | smartrecruiters `experienceLevel.label` · personio `seniority` · jobicy `jobLevel` · themuse `levels[0].name` · nofluffjobs `seniority[0]` · devitjobs `expLevel` |
| Source — unread (FREE) | recruitee `experience_code` ('entry_level' — literally the shelf, nothing reads it) · gov_apprenticeships `apprenticeshipLevel` + `course.level` · eightykhours `tags_exp_required` ('Mid (5-9 years experience)') · linkedin JSON-LD `experienceRequirements.monthsOfExperience` + `educationRequirements.credentialCategory` · uni_jobs `category` (Studentships→Professorships role-tier, 100% fill of 198) |
| Source — extra request | jsearch detail `seniority_level` |
| Free derivation | `detect_experience_level(job.title)` — already runs (`main.py:706`) |
| JOB SOURCE ENRICHMENT | `seniority` (7-enum) + `experience_level` (entry/mid/senior) + `experience_min_years` (`job_enrichment_schema.py:77-92,157-160`) |
| ABSENT | `not_stated` — legacy free-text `experience_level` column stays until consumers migrate to the enum |

### WORKPLACE_MODE

| Layer | Real fields |
|---|---|
| Source | ashby `isRemote`+`workplaceType` · lever `workplaceType` · workable `remote`+`workplace` · pinpoint `workplace_type(_text)` · smartrecruiters `location.remote/hybrid` · jsearch `job_is_remote` · arbeitnow `remote` · findwork `remote` · landingjobs `remote` · climatebase `remote_preferences` (100% fill) · indeed `is_remote`+`work_from_home_type` · nofluffjobs `fullyRemote` · devitjobs `remoteType`/`workplace` · himalayas `locationRestrictions[]`/`timezoneRestrictions[]` |
| Free derivation | 'remote' token in the location string (today's only signal — weak; keep as fallback) |
| JOB SOURCE ENRICHMENT | `workplace_type` enum (`job_enrichment_schema.py:64-68`) |
| ABSENT | `not_stated` |

### DESCRIPTION (recovery, not extraction — the LLM reads this shelf, it never writes it)

| Layer | Real fields |
|---|---|
| Source — same response, unread (FREE) | himalayas `description` (avg 7,299 chars vs the `excerpt` we store at avg 187 — bigger on 20/20 samples) · pinpoint `key_responsibilities` (3,381) + `skills_knowledge_expertise` (1,998) beside the 1,625-char `description` we read · lever `lists[].content` (~5,200 chars already downloaded — task brief) · landingjobs `main_requirements`+`nice_to_have` · nofluffjobs `tiles.values[]` (100% fill on all 21,795 postings; today NO `description=` kwarg is passed at all — every row ships empty) |
| Source — extra request (budget-cap pattern already exists in smartrecruiters/workday) | reed detail `jobDescription` (453-char teaser → ~4,700 full) · workable detail GET `.../jobs/{shortcode}` (description 2,237 + requirements 2,134 + benefits 1,884 — list endpoint genuinely has none) · nofluffjobs detail `requirements.description` · successfactors job-page JSON-LD `description` (8,876 chars; today description=title for all ~1,800 jobs/run) · nhs detail page (list gives a ~180-char excerpt) · climatebase detail page (list has no description field) |
| Free derivation | the existing sweep: `workers/tasks.py::_backfill_thin_descriptions`, `DESCRIPTION_BACKFILL_PER_TICK=50` per 30-min cron tick (`settings.py:229-240`) |
| JOB SOURCE ENRICHMENT | never |
| ABSENT | `stub` (description==title or <600 chars — blocks the LLM pass for this job, §6) or `source_lacks_field` (hn_jobs, devitjobs structurally have none) |

### Remaining shelves, compact

| Shelf | Source fields | Derivation | LLM | Notes |
|---|---|---|---|---|
| title | every source; google/jsearch also carry canonical variants | `html.unescape` (`models.py:87`) | `title_canonical` (enrichment, stays there) | filled everywhere |
| company | every source except landingjobs (API has no company field — documented no-op) and hn/aijobs sponsored cards | `_clean_company` (`models.py:97-104`) | — | absent = 'Unknown' today; keep |
| location | every source; structured upgrades sitting free: jsearch `job_city/state/country`, workday detail `country.alpha2Code`, ashby `address.postalAddress`, linkedin JSON-LD `jobLocation`, eightykhours `tags_city/tags_country` (code currently reads nonexistent `locations` key → every job defaults to 'Remote') · aijobs_ai `_card_texts()[2]` thrown away → confirmed US-job leak past the UK gate | `uk_gate.check_uk` (the door, rule #30) | — (retired from LLM contract 2026-08: `locations` 0% populated over 3,119 rows, `job_enrichment_schema.py:12-19`) | fix the two reader bugs; keep raw string as the shelf |
| posted_at | climatebase `activation_date` (100% fill, hardcoded None today) · eightykhours `posted_at` epoch (code reads nonexistent `date_published`) · reed detail `datePosted` · all sources' existing date fields | `normalize_posted_at` + 5-column confidence model | never (fabrication risk) | exists, works |
| skills | arbeitnow `tags[]` (93%) · remotive `tags[]` (100%) · remoteok `tags[]` · findwork `keywords[]` · themuse `tags[]`/`categories[]` · devitjobs `technologies[]` (read, composed) · nofluffjobs `tiles.values[]` · linkedin JSON-LD `skills` · jsearch detail `required_technologies`/`preferred_technologies`/`soft_skills` | normalise via `core/skill_synonyms.py` (retained carve-out, rule #28 — scoring vocab, reads no CV) | `required_skills`/`preferred_skills` ≤30 each | source tags land in `jobs.source_tags`; LLM lists stay in `job_enrichment` |
| category | adzuna `category.label` · smartrecruiters `industry.label`+`function.label` · teaching_vacancies `occupationalCategory` (100% fill) · themuse `categories[]` · uni_jobs `category` | `services/domain_classifier.py` (exists) | `category` 16-enum | write-back to `jobs.category` |

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

**Values live in typed columns; provenance lives in JSONB.** The shelf VALUES (salary_min, deadline, employment_type…) stay real, indexable columns on `jobs` — filterable and cheap. Only the how-it-got-there metadata goes to JSONB. New columns (migration `0031`): `employment_type`, `workplace_mode`, `seniority`, `salary_currency`, `salary_period`, `salary_is_estimated`, `salary_min_gbp_annual`, `salary_max_gbp_annual`, `visa_status`, `category`, `source_tags`, `shelf_provenance` (NOT NULL DEFAULT '{}').

**Where LLM values land:** `job_enrichment` remains the LLM layer's append store (versioned raw output, idempotent per `job_id` — `job_enrichment.py:3-6`). The enrichment sweep then WRITES BACK through the gate (§5) into still-NULL `jobs` columns with `how:"llm"`. Rejected alternative — virtual shelves via `COALESCE(jobs.col, enrichment.col)` at read time — would force every consumer (filters, serializers, embeddings) to replicate the join+merge logic, which is exactly the per-source scatter this design exists to kill. (Read-time merge already crept in once: `api/routes/jobs.py:43-49` reads enrichment salary directly.)

---

## 4. ABSENT is a real value

Rule #29 for the catalog side: an empty shelf must never become a guess, a zero, or a penalty. Absent is stored, typed, and explained.

**Storage:** value column = NULL, provenance = `{"how": "absent", "why": <reason>}`.

| `why` | Meaning | Who can change it |
|---|---|---|
| `not_mapped` | Nobody looked — the source handed nothing and no derivation ran (the gate's default stamp for a fresh gap) | free recovery work, next gate pass |
| `source_lacks_field` | We looked — this source's schema structurally has no such field (e.g. findwork salary, arbeitnow deadline: confirmed absent in full key dumps 2026-08-16) | only a richer fetch (detail call) or the LLM reading the ad |
| `stub` | LLM pass was BLOCKED: description is a stub/<600 chars — answering would be fabrication | description recovery, then re-queue |
| `not_stated` | A real description was read (by detector or LLM) and the ad genuinely does not say | nothing — this is a final fact about the job |
| `implausible` | A value ARRIVED and the gate refused it — today only salary, when the annualised GBP band falls outside £10k–£500k. The original figures are kept in `raw`, so the refusal is auditable | a better unit/currency mapping upstream, or a change to the plausibility band |

**What consumers MUST do with absent** (the catalog-side mirror of rule #29):

| Consumer | Rule on NULL shelf |
|---|---|
| Dim scorers | Return the neutral constant — `salary_score` already treats no-signal as the neutral band (`services/salary.py:7-9`); never a per-job zero |
| Prefilters | Pass the job through. A filter "salary ≥ £50k" excludes NULL-salary jobs ONLY if the user explicitly ticks "only show jobs that state pay" — default keeps them, ranked by everything else |
| Visa consumers | `unknown` = visible, unbadged, unpenalised (rule #31 — refusal is tested before offer) |
| Deadline consumers | NULL ≠ expired. Never sort NULL to "closing soon" or "expired"; UI shows the existing fallback |
| JOB SOURCE ENRICHMENT | Never asked to fill a shelf from a `stub` job; prompt already mandates explicit `unknown` over invention (`job_enrichment.py:42-46`) |
| Frontend | Show "—" / omit the chip. Never render 0, never a red state |
| Telemetry | Per-shelf, per-source fill-rate split BY `why` — the empty-shelf-three-causes rule: broken extractor vs merge-dropped vs no front door look identical in a bare NULL; `why` separates them |

---

## 5. The chokepoint — one door, every job

Today ~42 hand-written `Job(...)` constructions each fill whatever they happen to have; source #41 can silently forget a shelf and nothing notices. The fix is one function every job passes through, on a path no source can skip.

### Design

`src/services/shelf_gate.py` — `fill_shelves(job: Job) -> Job` (sync, no I/O, no await):

1. **Sources become dumb mappers.** A source's only duty is copying upstream keys onto `Job` fields (including the new ones: `employment_type`, `salary_currency`, …). No policy, no normalisation, no defaults in source files.
2. **The gate owns all policy:** enum-normalise employment/workplace/seniority strings ('Full time', 'FULLTIME', 'permanent' → `full_time` — a CLOSED set, so enumeration is legal under rule #30's bounded-set law, raw value preserved in provenance); annualise+currency-tag salary via `normalize_salary`/`fx` then clamp (moving the unit-blind clamp out of `models.py:91-95`); run `detect_visa_status`; run `extract_deadline` (absorbing the existing pass at `main.py:708-716`); detect stub descriptions (`description == title` or <600 chars → `absent:stub`).
3. **Stamp provenance for EVERY shelf, always** — filled or absent. The invariant is not "every shelf filled" (impossible); it is "every shelf ACCOUNTED FOR."
4. Two entry points, same core: `fill_shelves(job)` at ingest; `apply_enrichment(job_row, enrichment)` in the sweep write-back — so `how:"llm"` rows get identical normalisation and never overwrite `source`/`derived` fills.

### Where it sits — WIRED 2026-08-16 (step 2)

Inside `_score_dedup_and_filter()` (`src/main.py:681`) — the single synchronous stage every run already passes through (score → deadline-extract → dedup → store, threaded off the loop since PR #123). The gate runs FIRST in that function, before scoring, so the scorer reads normalised shelves; the existing deadline loop (`main.py:708-716`) moves into the gate. No source can bypass it because sources don't call it — the orchestrator does, downstream of all of them. **Done:** `main.py` now calls `fill_shelves(job)` for every raw job before scoring; the deadline loop is gone from `main.py` and lives in `shelf_gate._fill_deadline`; the unit-blind clamp is gone from `models.Job.__post_init__` and lives in `shelf_gate._fill_salary`, which annualises + converts to GBP FIRST (`salary.normalize_salary` / `core.fx`) and writes the derived `salary_min_gbp_annual` / `salary_max_gbp_annual` pair. `SCORER_VERSION` 7 → 8 so the freeze in `services/feed.py` does not make it inert on existing rows. Source #41 forgetting a shelf now produces a counted `absent:not_mapped`, not a silent hole.

### Enforcement — `tests/test_universal_shelf.py`

| Test | Asserts |
|---|---|
| `test_gate_accounts_for_every_shelf` | `fill_shelves(Job(minimal))` → `set(job.shelf_provenance) == set(UNIVERSAL_SHELF)` exactly. `UNIVERSAL_SHELF` is a frozen tuple in `models.py` — the single source of truth the gate, migration, and tests all import. Add a shelf to the tuple without teaching the gate → this test fails. |
| `test_pipeline_round_trip` | Fake source → `run_search` → stored row: full provenance present AND one non-default value round-trips (value-presence, not schema-presence — rule #21, pattern `test_database.py::test_dim_columns_round_trip`) |
| `test_absent_is_typed` | A job with no salary stores NULL + `{"how":"absent"}` — never 0, never a guess |
| `test_llm_blocked_on_stub` | A stub-description job is never handed to `enrich_job` |
| `test_a_stub_description_is_never_sent_to_the_llm` | Step 3, end to end: the sweep's mocked LLM records every PROMPT, and the stub job's text appears in none of them — proof it was never SENT, not merely that its answer was discarded |
| `test_an_llm_value_never_overwrites_a_source_filled_shelf` | Trust order §2 holds under a real write: source values survive, and the empty shelf beside them still gets filled |
| `test_provenance_says_llm_only_for_shelves_the_llm_filled` | A shelf the model left `unknown` keeps `absent` — `how:"llm"` is never stamped on a shelf the LLM did not fill |
| `test_the_budget_cap_stops_the_sweep_and_says_so_loudly` / `test_the_spend_cap_bites_before_the_job_cap_when_it_is_tighter` | Both ceilings are hard stops on CALLS, and hitting one logs at ERROR |
| `test_the_spend_is_written_to_run_log` | One `run_log` row per sweep with the real spend — "what did last night cost?" is answerable |
| `test_pass1_fills_shelves_without_spending_anything` / `test_pass1_never_removes_a_value` | The free pass really is free (zero LLM calls, $0) and only ever ADDS |
| `test_re_running_the_gate_does_not_relabel_an_llm_fill_as_source` | Provenance cannot be laundered by a second sweep |
| `test_with_the_flags_off_the_nightly_path_never_sweeps` | Rule #18 — with `ENGINE2_ENABLED` and `ENRICHMENT_ENABLED` both off the sweep is not called at all |

Plus a per-shelf × per-source fill-rate export in `services/metrics_exporter.py` split by `why` — the CI canary that counts like the consumer counts. New guard declares its drill in `scripts/drill_registry.py` (project law).

---

## 6. Dependency order — what MUST come first

The owner wants shelves and catalog built in parallel. Most of it can be; two edges are hard.

| Step | What | Depends on | Parallel? |
|---|---|---|---|
| 1 | **The frame**: migration 0031 + `UNIVERSAL_SHELF` tuple + `shelf_gate.py` + provenance + tests | nothing | FIRST, alone. Small (days). Building recoveries before the frame just re-creates 42 scattered conventions with more fields. |
| 2a | **Free source-field recoveries** (Appendix A) — per-source mapper edits riding through the gate | 1 | yes — parallelisable per source batch |
| 2b | **Text recovery** — stub/teaser/empty descriptions: same-response fields + detail-call budget pattern + the existing `_backfill_thin_descriptions` sweep | 1 | yes — parallel with 2a |
| 3 | **JOB SOURCE ENRICHMENT at scale** (LLM pass) — **BUILT 2026-08-17**, `src/services/shelf_enrichment.py` | 1 + per-job 2b | LAST per job. Global work can overlap: a job whose text is already real can be enriched while another still awaits recovery. The ordering is per-job, enforced by the gate's `stub` block — not a calendar phase. |

**Step 3 as shipped — the two-pass sweep** (`services/shelf_enrichment.py`, wired into `workers/tasks.refresh_catalog` after the nightly fetch, behind `ENGINE2_ENABLED OR ENRICHMENT_ENABLED`):

| Pass | Cost | What it does |
|---|---|---|
| **1** | **$0** | Re-runs `shelf_gate.fill_shelves` over rows ALREADY stored (visa text detector, deadline extractor, enum normaliser, annualise-then-clamp salary) — every row written before the gate existed never saw it. Also writes back `job_enrichment` rows already paid for whose facts never reached the `jobs` columns (§3 "Where LLM values land"). **Only ever ADDS**: a re-derivation that yields NULL never deletes a stored value. |
| **2** | LLM | Only on rows that survive three filters: description is not a stub (`is_stub_description` — the fabrication block, §6), ≥ `SHELF_ENRICHMENT_MIN_ABSENT_SHELVES` consumer shelves honestly absent, no existing `job_enrichment` row (unless `force`). Writes through `shelf_gate.apply_enrichment`, which fills ONLY honestly-absent shelves and stamps `how:"llm"` for exactly what it filled. |

Two rules the code enforces that this doc previously only asserted: re-running the gate on a stored row **cannot relaunder** a `how:"llm"` fill into `how:"source"`, and an LLM `category` of `other` is **refused** — `JobCategory` is the one contract enum with no `unknown` member, so a model that cannot classify is forced into `other` and that value carries no information (rule #29).

**Measured, and the answer is a warning: pass 1 fills NOTHING on a post-step-2 catalog.** Simulated read-only over the 2,915-job `shelf_after_verify` snapshot (2026-08-17): **0 jobs improved, 0 shelves filled**, every per-shelf fill rate identical before and after. That is not a bug — it is the gate working. Every row in that snapshot already went through `fill_shelves` at ingest (step 2), `job_enrichment` holds 0 rows, and 0 rows have empty provenance, so the free pass has nothing left to recover. **Pass 1 earns its keep on exactly two populations, neither of which exists in that snapshot:** rows stored BEFORE the gate was wired, and rows whose already-paid-for `job_enrichment` facts never reached the `jobs` columns (prod holds thousands of the latter). The consequence to state plainly: on a freshly-gated catalog, the flat `visa_status` 1.6% / `seniority` 7.5% / `category` 7.5% numbers can only be moved by **pass 2** — the free pass cannot reach them, because the free detectors have already had their turn.

**Text recovery MUST precede LLM shelf-filling, per job. Two independent proofs:**

1. **Fabrication is cached.** A confident LLM answer extracted from reed's 453-char teaser or successfactors' description==title is a fabrication — and `enrich_job` is idempotent (second call on an enriched `job_id` is a no-op unless `force=True`, `job_enrichment.py:3-6`), so the wrong enum is PERMANENT until someone force-re-runs. Recover text first and the poison never enters.
2. **Prod already measured the correlation.** 1,311 active jobs (30% of catalog) carry <200 chars of description, and coverage of every enriched field (workplace/seniority/visa) "tracks description length almost perfectly" (`settings.py:234-236`, measured 2026-08-07). Enriching thin jobs buys `unknown`s at full token price.

---

## 7. Cost of JOB SOURCE ENRICHMENT at ~6,000 new jobs per run

**Model:** the enrichment chain is OpenAI-primary — `llm_extract_validated` → `gpt-4o-mini` (paid, deterministic), then free-tier Gemini `gemini-3.7-flash` → Groq → Cerebras fallbacks (`settings.py:61,69`; `llm_provider.py:311,330,477-480`). Cost math below prices the primary; fallbacks only fire on OpenAI failure.

**Price** (OpenAI list price, web-verified 2026-08-16 — no price constant exists in the codebase): **$0.150 / 1M input tokens, $0.600 / 1M output tokens**; Batch API −50% on both. Source: openrouter.ai/openai/gpt-4o-mini, pricepertoken.com (checked 2026-08-16). Model is env-overridable (`OPENAI_MODEL`), so re-price with: cost/job = (in_tokens × in_price + out_tokens × out_price).

**Tokens per job** (from the real prompt, `job_enrichment.py:49-93`): fixed system+contract ≈ 400 tok; title/company/location ≈ 30; description capped at 4,000 chars ≈ 1,000 tok → **~1,400 in**; 16-field JSON out ≈ **~200 out**. Per job ≈ $0.00033.

| Strategy | LLM calls / run | Cost / run | Cost / 1,000 jobs | Notes |
|---|---|---|---|---|
| One-pass (LLM reads every new job) | 6,000 | **~$1.98** | ~$0.33 | Simple; wastes calls on thin text (buys cached fabrications) and on facts already structured upstream |
| **Two-pass (recommended)** | ~3,600 | **~$1.19** | ~$0.33 (on the enriched subset) | Pass 1 free: gate fills from source fields + detectors. Pass 2 LLM ONLY where (a) description is real (skips the ~30% thin/stub — prod 2026-08-07) and (b) ≥1 consumer shelf still NULL (skips ~10% fully-source-filled ATS jobs — estimate from harvest fill rates) |
| Two-pass + Batch API (−50%, async fits the sweep cron) | ~3,600 | **~$0.59** | — | Sweep is already asynchronous; batch latency is free here |

**Recommendation: two-pass, batched.** Not mainly for the ~$1.40/run saved — pass 1 IS the fabrication guard (§6) and the provenance stays honest (`source` beats `llm` in the trust order). Numbers are estimates on stated assumptions; re-measure after the first real sweep.

**Budget reality check:** today's default enriches at most **20 jobs per run** (`ENRICHMENT_MAX_JOBS=20`, `settings.py:151`) with `ENRICHMENT_ENABLED` defaulting off (`job_enrichment.py:33`, rule #18). Filling 6,000/run is a deliberate budget raise and belongs in the worker's enrichment sweep cron — never the search hot path (the event loop has been frozen by catalog-scale work before; PR #123).

**Shipped budget (2026-08-17).** The step-3 sweep has TWO hard ceilings, both from settings, both checked BEFORE each call, and it stops at whichever bites first: `SHELF_ENRICHMENT_MAX_JOBS` (default 500) and `SHELF_ENRICHMENT_MAX_SPEND_USD` (default $1.00). A job cap alone cannot bound cost — the same 500 jobs cost several times more when the ads are long — so the spend cap is the real rail. Hitting either logs at ERROR with how many eligible jobs went unread; a cap that trims silently is a cap nobody can act on (same lesson as `MAX_REFRESH_INGEST_IDS`). Prices are `LLM_INPUT_USD_PER_1M` / `LLM_OUTPUT_USD_PER_1M`, env-overridable for the same reason `OPENAI_MODEL` is — a stale hardcoded price is a silent lie. Input tokens are measured from the real prompt at ~4 chars/token (≈15% conservative against the tiktoken dry run, i.e. the cap trips early not late); output is the fixed ~200-token JSON shape, which cannot be measured without making the call.

**And it is now ANSWERABLE.** Migration `0032` adds `run_log.enrichment_stats`; every sweep writes one row (`run_uuid LIKE 'shelf-enrichment-%'`) carrying jobs read, tokens in/out, estimated USD and whether a cap bit. Before this, nothing in the system could answer *"what did last night cost?"*.

**Measured eligibility, live catalog 2026-08-17 (2,915 jobs):** 2,826 eligible (96.9%), 89 blocked as stubs (3.1%), 0 already enriched, 0 with all six shelves filled. Absence is CORRELATED — 99.6% of eligible jobs are missing 2+ shelves, 78% are missing 4+ — so raising `SHELF_ENRICHMENT_MIN_ABSENT_SHELVES` from 1 to 4 saves only ~17% of spend while dropping 15% of the jobs. The stub block, not the shelf threshold, is where the money-losing mistake was.

---

## 8. Embeddings — what text represents a job

Catalog-side only. Infra exists: `job_embeddings` (no user_id — rule #17), `SEMANTIC_ENABLED` default off (`settings.py:221`), convergence backfill `EMBED_BACKFILL_PER_RUN=300` (`settings.py:227`). The enrichment `requirements_summary` field (≤250 chars) was designed as embedding input (`job_enrichment_schema.py:162-163`).

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
| The shelf line uses the ENUM values, not raw source strings | one vocabulary across 39 sources = tighter clusters |
| Re-embed on provenance transition of `description` (absent/stub → source/derived) or of `skills` | provenance gives the trigger for free — no diffing text; the existing backfill loop picks them up |

---

## 9. WHAT THIS UNBLOCKS

| Shelf | What the search side can now do (could not before) |
|---|---|
| deadline | "Closing soon" urgency rail; auto-drop expired jobs from feeds; never let a user write a cover letter for a closed vacancy |
| salary (+currency/period/estimated) | An honest salary filter: annual-GBP comparable across 39 sources; EUR/BRL no longer masquerade as GBP (landingjobs 0/50 GBP, 2026-08-16); "advertised" beats "Adzuna ML guess" via `salary_is_estimated`; hourly NHS rates finally survive instead of being clamp-nulled |
| visa_status | Rule #31 done right at catalog level: sponsors spotlighted + badged, `no_sponsorship` honestly labelled, `unknown` visible and unpenalised — the visa dim (6 pts) finally has real input |
| employment_type | Contract/permanent/part-time/internship/apprenticeship as a hard filter and prefilter — today this user constraint is unanswerable |
| seniority | Entry-level users stop drowning in staff+ roles; seniority dim (8 pts) runs on data instead of title regex |
| workplace_mode | A real remote/hybrid/onsite filter instead of grepping "remote" in a location string; workplace dim (6 pts) fed |
| description (recovered) | The 40-pt skill component and the LLM judge read real ads on ~40% more of the catalog; JOB SOURCE ENRICHMENT stops fabricating from teasers |
| posted_at (fixed) | Recency score honest for climatebase/eightykhours instead of defaulting to low-confidence None |
| skills / source_tags | Skill matching against the job's own declared stack; better embedding material; skill-gap features get per-job ground truth |
| category | Domain prefilter for retrieval (funnel Stage-1) + dashboard facets |
| provenance (cross-cutting) | Search can rank "states salary" above "salary guessed" WITHOUT hiding either; telemetry can finally answer "is this shelf empty because the extractor broke, the source lacks it, or nobody looked?" — three different work queues |
| the chokepoint | Source #42 gets every shelf for free on day one; a forgotten mapping is a counted `absent:not_mapped`, not a silent hole discovered months later |

---

## Appendix A — free recoveries already in hand (payload we download today and throw away)

Ranked by impact; all live-verified 2026-08-16 (task harvest). "FREE" = zero extra HTTP requests.

| # | Source · field | Shelf | Evidence |
|---|---|---|---|
| 1 | linkedin detail-page JSON-LD: `baseSalary` (clean min/max/currency/unitText) + `employmentType` + `validThrough` + `experienceRequirements.monthsOfExperience` | salary + employment_type + DEADLINE + seniority | ALL THREE owner shelves in one already-fetched object (page fetched today for description only, 30/run cap); £65,000–£75,000/YEAR seen live |
| 2 | himalayas `description` (vs stored `excerpt`) | description | avg 7,299 vs 187 chars, bigger on 20/20 samples — ~20-50× richness, same response |
| 3 | nofluffjobs `tiles.values[]` | description + skills | 100% fill on ALL 21,795 postings fetched; description currently EMPTY on every row from this source |
| 4 | ashby `?includeCompensation=true` → `compensation.summaryComponents` | salary | structured min/max/currency/interval, confirmed populated (openai board, $257K–$335K); one query param, same request count |
| 5 | deadline trio: reed `expirationDate` · gov_apprenticeships `closingDate` · greenhouse `application_deadline` | DEADLINE | all three sit in responses already fetched, all three 100% unread; gov 3/3 fill, greenhouse confirmed non-null live (Monzo) |
| 6 | landingjobs `currency_code` (+ jobicy/himalayas/careerjet/indeed currency+period sidecars) | salary integrity | landingjobs 0/50 jobs GBP — every stored salary from it mislabeled today |
| 7 | employment-type sweep: workday LIST `timeType`, adzuna `contract_time/type`, remotive `job_type` (100%), weworkremotely `type` (100%), teaching `employmentType` (100%), arbeitnow `job_types` (82%), +≈18 more | employment_type | the widest single-shelf free harvest; no column exists yet to receive it |
| 8 | recruitee `experience_code` + gov `apprenticeshipLevel` + eightykhours `tags_exp_required` + uni_jobs `category` | seniority | ready-made seniority values, all unread |
| 9 | climatebase `activation_date` (100% fill) + eightykhours `posted_at` (code reads a key that doesn't exist) | posted_at | free real dates currently stored as None/low-confidence |
| 10 | pinpoint `key_responsibilities` + `skills_knowledge_expertise` (5,379 chars) · lever `lists[].content` (~5,200) · landingjobs `main_requirements`+`nice_to_have` | description | same-response prose sections never concatenated |

*Not free but named for completeness (existing budget-cap pattern applies): workable detail GET (~6,255 chars, every description empty today), successfactors job-page JSON-LD (description=title on ~1,800 jobs/run), reed detail (453→4,700 chars), nofluffjobs detail `expiresAt`, nhs detail page.*
