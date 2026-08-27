# Country is a PARAMETER, not a hardcode — verification, challenge, plan

**Date:** 2026-08-27
**Status:** Plan. Nothing built. Nothing merged.
**Verified against:** `origin/main` @ `e58cceb` — **not** `9b6cfba`. Main moved
under the request; `0032_universal_shelf` landed 2026-08-25 and changes the
answer materially (see C5).
**Live catalog dry-run:** all 18,459 prod rows, pulled 2026-08-27 via
`railway run -s Postgres` on `DATABASE_PUBLIC_URL`. Rule #30 requires this
before any location rule ships; the numbers below are measured, not modelled.

---

## 1. What was confirmed

| Claim | Verdict | Evidence |
|---|---|---|
| Three UK-hooked concepts: location, sponsorship, currency | **True**, but each scoped differently than stated | §2 |
| `jobs` has no `country` column | **True — confirmed in prod**, not just in the repo | `information_schema` query over the live DB returned `country_cols []` |
| The 8 named location sites exist | **True**, line numbers still valid at `e58cceb` | `main.py:1167`, `adzuna.py:43`, `jsearch.py:80`, `indeed.py:46`, `landingjobs.py:42`, `lever.py:72`, `successfactors.py:265`, `src/data/uk_gazetteer/` |
| `uk_gate.py` is hard-won; do not delete | **True and understated** | `uk_gate.py:311-345` documents the 373-row dual-site bug (2026-08-12), the Sydney/California/New York hamlet collisions (#330, 135 rows), and two refinements dry-run over 14,378 rows |
| Gazetteer is buildable per-country from the same script | **True and cheap** | `build_uk_gazetteer.py:28` pulls GeoNames `GB.zip`; every country ships as `<CC>.zip` from the same endpoint |
| Scoring / extraction / dedup / ghosting / notifications are country-agnostic | **True** | no country literals in those modules |
| The 7 source-level hardcodes violate rule #30 | **True** | rule #30 says refused at one chokepoint, never per-source |

---

## 2. Five corrections — the findings were directionally right and materially incomplete

### C1 — It is **12** outbound hardcodes, not 7, in **three** categories

**(a) Sends a UK country/location in the outbound request — 12 sites, 11 files.**
Named in the brief: `adzuna.py:43` (`/gb/` in the URL path), `jsearch.py:80`
(`"country": "uk"`), `indeed.py:45-46` (`location="London, UK"`,
`country_indeed="UK"`).
**Missed:** `careerjet.py:37`, `google_jobs.py:57` **and** `:68` (`"gl": "uk"`),
`jooble.py:37`, `themuse.py:27`, `linkedin.py:151`, `climatebase.py:56`
(`"l": "United Kingdom"`), `jobicy.py:22` (`"geo": "uk"`), `findwork.py:50`
(`for loc in ("uk", "london")`).

**(b) Filters inbound results against a UK code set — 2 sites.**
`landingjobs.py:15,42` (`_UK_CODES`), `lever.py:72` (`country == "GB"`).

**(c) Structurally national — cannot be parameterised at all. 7 sources.**
`reed` (`reed.co.uk`), `nhs_jobs` (`jobs.nhs.uk`), `teaching_vacancies`
(`teaching-vacancies.service.gov.uk`), `gov_apprenticeships`
(`apprenticeships.education.gov.uk`), `devitjobs` (`devitjobs.uk`), `uni_jobs`
(UK university RSS list), `bcs_jobs` (UK chartered institute).

Category (c) is the one the brief has no answer for. **You cannot parameterise a
domain name.** These need a declared `COUNTRIES` class attribute on
`BaseJobSource`, and `_build_sources()` must skip a source whose countries do
not intersect the enabled set. That is a **sixth surface** added to rule
#8/#13's five — the add-source contract changes, and
`.claude/skills/add-source/SKILL.md` must change with it.

### C2 — `_is_uk_or_remote` is 40 files and 89 call sites, not 7 — and that is good news

`grep -rn "_is_uk_or_remote" src/sources/` → 89 hits across 40 files. But it is
**one function**, `sources/base.py:39`, delegating to
`uk_gate.names_foreign_place`. Parameterise that single function and all 40
files follow with zero edits. **Do not touch the 89 call sites.** Its docstring
(`base.py:40-70`) records why it only ever accepts a bare location string —
four callers pass a description, and `foreign_admin.txt` contains `LI`, `BR`,
`TD`, `TR`, `HR`, so `</li>` in HTML reads as Liechtenstein. Guarded by
`tests/test_fetch_filter_never_overreaches.py`.

### C3 — `test_design_rules.py` does **not** pin rule #30

It contains six tests, all rule #29 (`test_empty_salary_pref_is_silent`,
`…_experience_level_…`, `…_workplace_…`, `test_no_visa_need_is_silent`,
`test_empty_location_prefs_prefilter_passes_everything`,
`test_filled_side_still_discriminates`). Zero mention of `check_uk` or the
gazetteer.

Rule #30's real guards: `tests/test_uk_gate.py`,
`tests/test_fetch_filter_never_overreaches.py`, `tests/test_shipped_data.py`,
`tests/test_sources.py`, `tests/test_prefilter_wiring.py`,
`tests/test_scorer.py`. **70 test files** mention UK at all. The three-file
"CLAUDE.md + docs + test_design_rules" update in the brief names the wrong
third file.

### C4 — Visa is ~80% country-neutral, and the real gap is language, not country

`visa_signal.py` holds 15 regex alternatives. **Three** are UK-specific:
`skilled worker visa`, `certificate of sponsorship`,
`right to work in the uk … required`. The other twelve —
`no sponsorship`, `cannot offer sponsorship`, `unable to sponsor`,
`not able to sponsor`, `sponsorship is not available`, `without sponsorship`,
`must already have the right to work`, `sponsorship is available`,
`we can sponsor`, `licensed sponsor` — are generic English recruitment
boilerplate that works verbatim in US, Irish, Australian and Canadian ads.

So "meaningless in other countries" is wrong. **The module is English-only.**
In Germany or France it returns `UNKNOWN` for every row — silently. The
parameter is a per-country *phrase pack + language*, not a rewrite, and the
`_NO`-before-`_YES` precedence (documented at `visa_signal.py:41-44`) must be
preserved in every pack.

### C5 — Currency is further along than the brief says, and more dangerous

`0032_universal_shelf.up.sql` landed **2026-08-25**, after the `9b6cfba`
snapshot the brief was written against. It already added to `jobs`:
`salary_currency`, `salary_period`, `salary_is_estimated`,
`salary_min_gbp_annual`, `salary_max_gbp_annual`, `shelf_provenance` (JSONB).

So the raw currency **is** preserved and GBP is a *derived* column. `fx.py` is
not a hardcoded base unit in the damaging sense — it is a derived view.

**But measured in prod today: `salary_currency` is filled on 194 of 18,459 rows
— 1.05%.** (`GBP` 193, `USD` 1, `NULL` 18,265.) The shelf only fills forward.
And `fx.to_gbp()` treats `None` as already-GBP (`fx.py:41`).

> A €80,000 Berlin job arriving with `salary_currency IS NULL` is stored,
> ranked and displayed as **£80,000**. Not an estimate — a wrong number.

That makes currency a **Phase 2 blocker, not a Phase 4 nicety.** The catalog
cannot admit a second country until `salary_currency` is NOT NULL on ingest.

Two further currency surfaces the brief does not name:
- The API exposes **field names** `salary_min_gbp` / `salary_max_gbp`, baked
  into `JobDetailClient.tsx:312-318` and into `FilterPanel.tsx:308` ("Salary
  Range (annual GBP)", `placeholder="Min £"`).
- `PreferencesForm.tsx:584` — "I need visa sponsorship to work in the UK".

Those field names become **MCP structured-output field names** the moment the
MCP server ships. Rename before, not after.

---

## 3. The design challenge — "classifying gate" is half right and half dangerous

### The right half

Yes: the gazetteer's intelligence should produce a country, and "UK" should
become data rather than a branch. Yes: other countries are new data files from
the same builder. That part is correct and cheap.

### The wrong half

**`detect_country()` returning *a* country code cannot express what 27.6% of the
live catalog is, and "nothing is refused" discards a refusal that was never
about country.**

Dry-run over all 18,459 live prod rows (2026-08-27):

```
check_uk today                                   a single-code classifier could place
  46.8%  uk_gazetteer                               72.4%  GB_named             (13,361)
  25.0%  uk_location                                18.9%  UNPLACEABLE_text      (3,490)
   9.6%  remote                       <-- no country 4.6%  AMBIGUOUS both ways     (845)
   5.7%  unverified_location_on_global_source        3.2%  FOREIGN_named           (588)
   4.7%  foreign_location                            0.9%  NO_LOCATION             (175)
   3.7%  uk_native_source             <-- from SOURCE
   3.0%  dual_site_includes_uk        <-- TWO countries
   0.9%  no_location_on_global_source
   0.5%  ambiguous_place_name_unconfirmed
```

Three measured facts break the single-code design:

**1. 9.6% — 1,766 rows — are `remote`. A remote job has no country.**
Return `GB` and you have lied about 1,766 rows. Return `NULL` and a feed filter
of `WHERE country = :user_country` deletes all 1,766 from every feed. That is
rule #29 wearing a new costume: an *unknown* being treated as a *penalty*.

**2. 3.0% — 558 rows — are `dual_site_includes_uk`. "London / New York" is two
countries.** One code cannot hold it. Choose `GB` and the job vanishes from a
US feed; choose `US` and you regress the 373-row fix that `uk_gate.py:311` was
built for.

**3. 7.2% — 1,318 rows — are refused for being UNPLACEABLE, not foreign.**
`unverified_location_on_global_source` (5.7%) +
`no_location_on_global_source` (0.9%) + `ambiguous_place_name_unconfirmed`
(0.5%) all mean "we cannot tell where this is and the source is not trusted".
Only 4.7% is `foreign_location`. A gate that refuses nothing admits 1,318
unplaceable rows per cycle into an O(n²) dedup, all with `country IS NULL` —
so they surface in every country's feed, or in none. Neither is a product.

### The counter-proposal: **split the gate, do not convert it**

The gate currently fuses two questions. Separate them and both parameterise
cleanly, with nothing lost.

```
detect_location(location, source, description, title) -> LocationVerdict
    countries : frozenset[str]   # empty = unknown; >1 = genuine dual-site
    scope     : DOMESTIC | REMOTE_GLOBAL | REMOTE_FENCED
    reason    : str              # KEEP today's reason strings verbatim
```
A pure classifier. No refusal, no policy, no I/O — so it can be dry-run over the
whole catalog (as above) without touching the door.

```
is_eligible(verdict, enabled_countries) -> GateVerdict
```
The door. Still exactly one chokepoint, still `main.py:1167`. Refuses when
`verdict.countries` is disjoint from `enabled_countries`, **and keeps today's
quality refusals** for unplaceable rows on untrusted sources.

Why this is better than "classify, refuse nothing":

- **Rule #30's actual content survives.** It becomes "country is a PARAMETER,
  refused at ONE chokepoint" — precisely the rewrite asked for — without
  having to also claim "nothing is refused", which the numbers do not support.
- **`REMOTE_GLOBAL` is a first-class answer.** A remote job is not "unknown
  country", it is "every country". The 1,766 rows keep working in every feed
  instead of being deleted by a NULL.
- **`countries` as a set keeps the dual-site fix intact** — the 558 rows carry
  `{GB, US}` and appear in both feeds. That is strictly better than today.
- **Phase 1 becomes provable, not hoped-for.** With
  `enabled_countries = {"GB"}`, `is_eligible ∘ detect_location` must reproduce
  today's `check_uk` verdict **row for row over all 18,459 live rows**. That is
  a real regression gate. "Tests stay green" is not.

### And it belongs on the Universal Shelf, not inside the gate

`services/shelf_gate.py` + `migrations/0032` (both 2026-08-25) already provide
exactly the machinery this needs, and the brief predates them:

- `fill_shelves(job)` is **already** the one chokepoint that fills every
  normalised per-job fact, called from `main.py::_score_dedup_and_filter`
  *before* scoring, dedup **and** the door.
- `UNIVERSAL_SHELF` (`models.py:17`) is a 13-name tuple that is the single
  source of truth; `test_universal_shelf.py::test_gate_accounts_for_every_shelf`
  fails loudly if a shelf is added without teaching `shelf_gate` to fill it.
- `shelf_provenance` (JSONB) already records `source | derived | llm | absent`
  per shelf.

**So `country` becomes shelf #14.** Then "GB because the gazetteer matched"
vs "GB because `reed` is a UK-native source" vs "nobody could tell" is recorded
for free, in a column that already exists, with a drift test that already
exists. The door at `main.py:1167` reads `job.country` instead of calling
`check_uk` itself.

`uk_gate.py` is **not deleted and not renamed in Phase 1.** It becomes the GB
matcher that `detect_location` consults. Its comments are the record of four
measured production incidents; they move with the code, intact.

---

## 4. Phasing — reordered, with reasons

### Phase 1 — country becomes a parameter, GB stays the default. Zero behaviour change.
*Agreed, keep it first. It is the honest repair of the rule #30 violation.*

1. `core/settings.py`: `ENABLED_COUNTRIES` (default `{"GB"}`),
   `DEFAULT_COUNTRY = "GB"`, `BASE_CURRENCY = "GBP"`. Parameters, from env.
2. `BaseJobSource.COUNTRIES: frozenset[str] = frozenset()` (empty = global).
   The 7 structurally-national sources declare `{"GB"}`. `_build_sources()`
   skips a source disjoint from `ENABLED_COUNTRIES`. **This is the sixth
   surface** — update `.claude/skills/add-source/SKILL.md` and the counts at
   `test_cli.py:52`, `test_api.py:43,56,158,163`.
3. Fix the 12 outbound hardcodes to read the parameter. Per-source country
   *query* stays (an API needs one); the per-source *refusal* goes.
4. `sources/base.py:39` `_is_uk_or_remote` → `_is_in_scope`, reading the
   parameter. 89 call sites untouched.
5. **Proof gate:** replay all 18,459 live rows; verdicts must match today's
   `check_uk` exactly. Committed as a script under `scripts/`, not a one-off.

### Phase 2 — `jobs.country` + `detect_location` + **currency correctness**
*Currency moves here from Phase 4. C5 is the reason: 98.9% NULL currency plus
one non-GB job is a wrong number in a user's face.*

1. Migration: `jobs.country TEXT` (nullable) + `jobs.country_scope TEXT`.
   Add `"country"` to `UNIVERSAL_SHELF`; teach `shelf_gate._fill_country`.
2. `detect_location` + `is_eligible`, split out of `check_uk`. Reasons kept.
3. **Make `salary_currency` NOT NULL on ingest** — default `BASE_CURRENCY`
   while `ENABLED_COUNTRIES == {"GB"}`, derived from `country` after. Backfill
   the 18,265 NULL rows to `GBP` *while that is still true*.
4. Rename `salary_min_gbp` / `salary_max_gbp` → `salary_min_base` /
   `salary_max_base` + `salary_base_currency`, in the API, the generated
   api-types, and `JobDetailClient.tsx` / `FilterPanel.tsx`. **Before MCP
   ships** — after, it is a breaking connector change.
5. Backfill `country` from the gazetteer. Per
   `postmortem_event_loop_blocking.md`: batched, off the event loop, tested at
   production scale — a 5-row test cannot see blocking.

### Phase 3 — per-user country preference; filtering moves to the feed
*Agreed. One trap to name.*

`ENABLED_COUNTRIES` (a deployment parameter) and `user.preferred_countries`
(a user preference) are **two different things — do not collapse them.**
Rule #29 says an empty *preference* means "don't care" → all enabled
countries. It does **not** mean a UK user who never opened preferences starts
seeing Warsaw. The deployment stays `{GB}` until you say otherwise; the empty
preference means "everything the deployment offers", which is still GB.

`services/prefilter.py:86-137` is a **fourth UK hook the brief does not name** —
it imports `_UK_SELF` and `_gazetteer()` to decide whether a stated location
preference is *resolvable*. It must become country-aware or it will silently
pass everything for any non-GB preference.

### Phase 4 — sponsorship per country
Per-country phrase packs behind `detect_visa_status(..., country=)`, keeping
`_NO`-before-`_YES` precedence. Note the honest bound: **English-only** until
someone writes a non-English pack. Say so in the UI rather than showing
`UNKNOWN` for an entire country.

### Phase 5 — turn on a second country
Build `<CC>.zip` through `build_uk_gazetteer.py` (renamed `build_gazetteer.py`,
country as an argument). **The ambiguity computation must be generalised**:
`ambiguous.txt` today is GB-relative (a UK name colliding with a bigger
non-GB place). With two gazetteers it becomes pairwise and
population-weighted. This is the genuinely hard data work, and it is the part
the brief undercounts.

---

## 5. Rule #30's rewrite

Current: *"UK-only is a DOOR, not a penalty; never hand-enumerate an UNBOUNDED
set."*

Proposed: *"Country is a PARAMETER, refused at ONE chokepoint; never
hand-enumerate an UNBOUNDED set."*

The unbounded/bounded half is untouched — it is the durable half. What changes
is that the door's *policy* is now an input (`ENABLED_COUNTRIES`) rather than a
constant, and the per-source refusals that violate the "one chokepoint" clause
are removed.

Three files change together — but the third is **not** `test_design_rules.py`
(see C3):
1. `CLAUDE.md` — the rule #30 index line.
2. `docs/product/product_design_rules.md` §Rule 2 (line 63) and its
   "never hand-enumerate an UNBOUNDED set" section (line 90).
3. `backend/tests/test_uk_gate.py` — the real pin, plus
   `test_fetch_filter_never_overreaches.py`, `test_sources.py`,
   `test_prefilter_wiring.py`, `test_shipped_data.py`.

---

## 6. MCP compatibility

`docs/plans/2026-08-26-mcp-server-design.md` (branch
`worktree-feat+mcp-server-design`) contains **zero** UK / country / region /
location references — only `job360.uk` as a hostname. It is already
region-neutral. Three constraints on this work:

- Its jobs tool is backed by `GET /jobs` with the existing `limit`/`offset`
  (`jobs.py:543-544`). Any country filter must be a `country` parameter taking
  **ISO-3166 alpha-2 codes** — never a `uk_only` boolean, never a "UK" default
  written into a tool description or schema.
- §9 leans on the types-drift check, and §10 item 3 records that it is not in
  `ci.yml` yet. Every route field added here needs api-types regenerated or
  that check goes red.
- The `salary_min_gbp` → `salary_min_base` rename (Phase 2 step 4) must land
  **before** the MCP server, because those become connector-visible field names.

---

## 7. Coverage bounds — what this plan has NOT verified

- The dry-run replays `check_uk(location, source)` **without** description or
  title. The live door passes both. So `uk_evidence_in_ad`,
  `ambiguous_name_with_uk_evidence` and the body-based
  `remote_restricted_to_other_region` branches are **under-counted** here. The
  Phase 1 proof gate must dump descriptions too.
- No frontend sweep beyond the three files named in C5. There are 20 frontend
  files matching `/uk|gbp|£/`; most are tests with `"London, UK"` fixtures,
  but that was not audited row by row.
- Notification templates, email digests and the tailored-CV prompts were not
  read for country or currency literals.
- No estimate of what a second country does to source *yield*. Adzuna `/de/`
  returning German-language ads meets an English-only keyword matcher and an
  English-only visa detector. **Phase 5 is a product question, not just a data
  question**, and this plan does not answer it.
