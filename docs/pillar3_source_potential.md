# Pillar 3 — what every job source ACTUALLY gives us
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

**Probed 2026-08-08 against `main` @ `dc9e546`.** Nine parallel workers called all 46
sources' **live** upstream endpoints, dumped every field returned, measured fill-rates,
and diffed that against the keys our extractors read.

**Rule:** only fields SEEN in a live response are reported. Documentation was never used
as evidence. Where a source could not be probed, it says so.

Companion to `pillar3_source_shelf_audit.md` (what our code takes). **This doc is what's
available.** The gap between them is the work-list.

---

## 1. The headline: this is a LIVENESS problem, not a data-quality problem

We set out to ask "how many shelves does each source fill?" The probe answered a bigger
question: **many sources return nothing at all, and fail silently.**

### Sources returning zero or near-zero (11 of 46)

| Source | Status | Cause |
|---|---|---|
| `aijobs` | 💀 dead | endpoint **404** — `aijobs.net/api/list-jobs/` no longer exists |
| `jobs_ac_uk` | 💀 dead | all 4 feed URLs **404**; feed path discontinued site-wide |
| `biospace` | 💀 dead | all 3 URLs **404**; BioSpace killed job RSS (index now lists only news) |
| `workanywhere` | 💀 blocked | **429** Vercel bot-checkpoint on every path |
| `nhs_jobs` | 💀 0 jobs | **our bug** — wrapper tag renamed `<vacancy>` → `<vacancyDetails>` |
| `nhs_jobs_xml` | 💀 0 jobs | endpoint now serves **HTML, not XML**; `ParseError` swallowed |
| `successfactors` | 💀 0 jobs | **our bug** — hardcoded XML namespace; 0/162 URLs matched |
| `rippling` | 💀 0 jobs | all 5 slugs 404; `ats.rippling.com` root redirects to marketing |
| `personio` | 🟠 1 of 12 | upstream deprecated the customer XML board (all slugs → personio.com) |
| `recruitee` | 🟠 1 of 19 | slug rot |
| `pinpoint` | 🟠 1 of 15 | slug rot |

**Every one fails silently.** A 404 → `_get_json` returns `None` → `continue`. A renamed
wrapper tag → loop body never runs → `return []`. HTML instead of XML → `ParseError`
caught → `return []`. The pipeline reports success; the circuit breaker never trips,
because nothing *errors* — it just returns nothing.

> The live DB corroborates this independently: only **28 of 46** sources have ever
> produced a job row.

### Company-slug rot (ATS category)

| Source | Live companies | Note |
|---|---|---|
| `workday` | **5 of 8** ✅ | healthiest ATS — nvidia 2,000 · intel 623 · astrazeneca 191 · shell 78 |
| `smartrecruiters` | 2 of 10 | 8 return `totalFound: 0` |
| `workable` | 1 of 10 | 9 return `total: 0` |
| `greenhouse` | **3 of 8** | monzo/stripe/cloudflare live (928 jobs); 5 slugs 404 |
| `lever` | **3 of 8** | healx/spotify/palantir live (410 jobs); 4 slugs 404, mistral empty |
| `ashby` | **6 of 8** | 1,458 jobs; anthropic + discord 404 |
| `recruitee` / `pinpoint` / `personio` | 1 of 19 / 15 / 12 | |
| `rippling` / `successfactors` | 0 | |

`gaborsk` in the Workday config is a **typo** for GSK — returns HTTP 422.

---

## 2. Silent bugs — wrong key, wrong shape, wrong namespace (9)

None of these throws an error. `dict.get()` returns `None`, the job is built and stored,
and the shelf looks identical to "the provider doesn't offer this."

| # | Source | Code reads | Live reality | Damage |
|---|---|---|---|---|
| 1 | `devitjobs` | `publishedAt` | **`activeFrom`** (100%) | **2,154 jobs** with no date |
| 2 | `himalayas` | `applicationUrl` / `url` | **`applicationLink`** (100%) | **apply_url empty — users cannot apply** |
| 3 | `jobicy` | `annualSalaryMin/Max` | **`salaryMin/Max`** (23%) | salary always `None` |
| 4 | `landingjobs` | `company_name` / `company_id` | **neither exists** | **company empty on 100%** |
| 5 | `recruitee` | flat `min_salary` | **nested `salary.{min,max}`** | salary always `None` |
| 6 | `workable` | `shortDescription` | **absent from that endpoint** | description always `""` |
| 7 | `pinpoint` | `isinstance(comp, dict)` | `compensation` is a **string** | condition never true → salary `None` |
| 8 | `successfactors` | ns `sitemaps.org/…` | QinetiQ serves **`google.com/schemas/sitemap/0.9`** | **0/162 URLs matched** |
| 9 | `nhs_jobs` | `<vacancy>`, `closingDate`, `advertUrl`, flat `<location>` | **`<vacancyDetails>`, `closeDate`, `url`, nested `<locations><location>`** | total outage |
| 10 | `ashby` | `applicationUrl` (falls back to `jobUrl`) | **`applyUrl`** (100% of 1,458 jobs) | apply link points at the info page, not the form |

### The apply-URL pattern — three sources, all user-facing
`apply_url` is the one shelf a user actually *clicks*. Three sources get it wrong:

| Source | Result |
|---|---|
| `himalayas` | **empty string** — no apply link at all |
| `ashby` | silently falls back to `jobUrl` (description page) instead of `applyUrl` (the form) |
| `lever` | reads `hostedUrl` (info page); `applyUrl` is filled 100% and unused |

`himalayas` is a hard break; `ashby` and `lever` are quality downgrades — an extra click
between the user and the application form on every job from those two.

### Plus one correctness risk
**`nofluffjobs`** returns `salary.currency` at 100% fill — **19,229 of 20,631 jobs are PLN**.
`Job` has no currency field, so PLN numbers are stored in `salary_min/max` and compared
as if GBP. A 15,000 PLN salary reads as £15,000.

---

## 3. Free data we already fetch and discard

### `deadline` — 99% empty catalog-wide, yet SEVEN sources publish it

| Source | Field | Fill |
|---|---|---|
| `himalayas` | `expiryDate` | **100%** |
| `landingjobs` | `expires_at` | **100%** |
| `teaching_vacancies` | `validThrough` | **100%** |
| `weworkremotely` | `expires_at` | **100%** |
| `nhs_jobs` | `closeDate` | **100%** (after the wrapper fix) |
| `workday` | `endDate` | ~50% (in the detail call we already make) |
| `pinpoint` | `deadline_at` | key present, null in sample |

### `experience_level` — filled by only 2 of 46, yet FIVE sources publish it

| Source | Field | Fill |
|---|---|---|
| `jobicy` | `jobLevel` | **100%** |
| `himalayas` | `seniority` | **100%** |
| `nofluffjobs` | `seniority[]` | **100%** (same list call, zero extra cost) |
| `smartrecruiters` | `experienceLevel` | **100%** (in the detail call we already make) |
| `personio` | `seniority` | 100% (but feed 92% dead) |

### `salary`

| Source | Field | Fill | Note |
|---|---|---|---|
| `smartrecruiters` | `compensation.{min,max,currency,period}` | **100%** | **in the detail response we already download** |
| `teaching_vacancies` | `baseSalary` | 89% | free-text, needs a parser |
| `landingjobs` | `gross_salary_low/high` | 22% | never populated in `Job()` |
| `devitjobs` | `contractRateFrom/To` | 17% | day rates — contract roles currently show no salary |
| `remotive` | `salary` (text) | 61% available | parser captures only **1 of 14** (`$20k`, `/hour` formats fail) |

### Other discarded fields

- `remoteok` — `location` filled on **99/100** with real cities; overwritten by literal `"Remote"`.
- `remoteok` — salary uses `0` as its empty sentinel; we store a literal **$0 on 99 of 100 jobs**.
- `landingjobs` — `role_description` (100%, real prose); we store a **tag list** instead.
- `jobicy` — `jobDescription` (100%) available; we use the shorter `jobExcerpt`.
- `realworkfromanywhere` — `<author>` (100%) is the exact company name; we split the title instead.
- `uni_jobs` — `applyOnlineUrl` (92%) is a direct apply form; we link to the listing page.
- `devitjobs` — `redirectJobUrl` (99.6%) is the real apply URL; we guess a slug.
- **`greenhouse` — `company_name` filled on 100% of 928 jobs and never read.** We derive the
  name from the URL slug (`slug.replace("-"," ").title()`) and maintain a hand-written
  **40+-entry `COMPANY_NAME_OVERRIDES` dict** (`companies.py:142-181`) purely to patch what
  the slug mangles (`darktracelimited`→Darktrace, `checkoutcom`→Checkout.com). Reading the
  upstream field would make that entire dict unnecessary.
- `greenhouse` — `first_published` reconfirmed at **100% of 928 jobs** across 3 boards.
- `lever` — `country` is a clean ISO-2 code at **100%**; the UK gate string-matches free-text
  city names instead. `categories.allLocations` / `ashby.secondaryLocations` (57-70%) mean
  multi-office postings collapse to one location and the rest are dropped.
- `workday` / `personio` / `workable` — real ISO dates (`startDate`, `createdAt`, `published`,
  all ~100%) discarded in favour of `"fabricated"` / relative-text parsing.

### Volume left on the table (no pagination)

| Source | We fetch | Available |
|---|---|---|
| `himalayas` | 20 | **99,521** |
| `teaching_vacancies` | 100 | **3,893** |
| `themuse` | 100 | 6,350 |
| `arbeitnow` | page 1 | paginates, `next` never followed |

---

## 4. Honest negatives — shelves genuinely absent upstream

Not every empty shelf is a bug. Confirmed absent in the live payload:

- `hn_jobs` — no location field exists (8 keys total). `location=""` is correct.
- `realworkfromanywhere` — no location/region tag at all.
- `themuse` — no structured salary (prose inside `contents`), and only one date field.
- `weworkremotely` — no salary tag; no structured company tag.
- `pinpoint` — no posted-date field anywhere; `"fabricated"` is honest here.
- `workable` — no salary on the endpoint we call.
- `devitjobs` — no prose description upstream (code already composes one from tech tags).
- **No source of 46 exposes an explicit visa-sponsorship boolean** except `devitjobs`
  (`hasVisaSponsorship`). `himalayas.locationRestrictions` is the nearest adjacent signal.

**Do NOT "fix" these — the field exists but is empty upstream:**
- `greenhouse.application_deadline` — real field, **0% filled across 928 jobs**.
- `lever.salaryRange` — real structured field, **0.2% (1 of 410)**.
- `ashby` salary — grepped the full 747-job OpenAI response: **zero** salary/compensation keys.

---

## 5. What to fix, in order

1. **Restore the dead sources that are OUR bug** — `nhs_jobs` (wrapper tag + 3 renamed
   tags), `successfactors` (XML namespace). Both are total outages with real data waiting.
2. **Fix the 9 silent key/shape bugs** — each is one line. `devitjobs` (2,154 jobs) and
   `himalayas` (unusable apply links) first.
3. **Wire `deadline` from the 5 sources at 100% fill.** Takes the shelf from 1% to
   materially populated — the first time it becomes usable.
4. **Wire `experience_level` from the 4 sources at 100% fill**, including two from
   responses we already download.
5. **Take SmartRecruiters' `compensation`** — free, same response, zero extra network cost.
6. **Add `salary_currency` to `Job`** before trusting NoFluffJobs salary (PLN-as-GBP).
7. **Audit `companies.py` against reality** — roughly 80% of sampled slugs are dead. Volume
   loss here likely exceeds all field-mapping loss combined.
8. **Retire or replace** `aijobs`, `jobs_ac_uk`, `biospace`, `rippling` — upstream is gone.
9. **Make silence loud** — a source returning `[]` must alert as loudly as one that throws.

---

## 6. Method note (and its limits)

Fable reviewed the method and named its blind spot correctly: **a two-point diff
(upstream vs extractor) cannot see an extract→store loss.** It also warned that
single-company sampling generalises badly for ATS sources — borne out here, where fill
rates swing wildly between boards.

Two errors were made and corrected during this audit, both from comparing things measured
at different points:
- A 3-day-old DB snapshot was read as current, hiding five fixes that had shipped since.
- A live test was run against the shared checkout (branch `chore/repo-hygiene`) instead of
  `main`, showing Greenhouse description at 0% when `main` gives **100%** (re-verified).

**Recommended next instrument (Fable's):** a three-point trace — for ~20 fixed job IDs per
source, capture the raw upstream payload, the `Job` object after extraction, and the final
DB row, all from the same run. That localises every loss to a stage instead of inferring it.

Sample sizes are stated per source above; `personio` (n=1) and `pinpoint` (n=4) fill-rates
are not representative — too few live companies remained to sample properly.
