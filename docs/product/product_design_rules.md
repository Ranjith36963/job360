# Product Design Rules

Owner-set rules that shape every feature. Each rule records WHY it exists and
what obeying it looks like in code, so a future session applies it rather than
rediscovering it. When code contradicts a rule here, the finding goes to the
owner — do not silently "fix" in either direction.

---

## Rule 1 — Filled shelves work harder; empty shelves stay silent

*Set by the owner, 2026-08-07, after the shelf X-ray found four scoring
dimensions dead because their user-side inputs were empty.*

**The rule.** The matcher works like a person searching Indeed or LinkedIn:
it uses what the user gave it, and what they left blank is **"don't care" —
never a penalty, never a zero, never a guess.** Every filled field narrows
and sharpens the match; every empty field switches its dimension off and
lets the filled ones carry the weight.

**Canonical example.** A user types "AI engineer" and nothing else. Indeed
does not punish jobs for missing a salary the user never stated. It matches
on the title alone. Job360 behaves the same for salary range, preferred
locations, remote/hybrid/office, experience level, and about_me.

**What this means in code:**

| Layer | Compliant behaviour |
|---|---|
| Dim scorers (`scoring_dimensions.py`) | Empty user side → a **constant** for every job (neutral half-weight or 0). A constant cannot change ranking order — that is what "silent" means. Never a per-job penalty for a preference that was never stated. |
| Prefilter (`prefilter.py`) | Empty `preferred_locations` / `work_arrangement` / `experience_level` → the stage **passes everything**. A filter you didn't fill is a filter that doesn't exist. |
| LLM judge (`llm_matcher.profile_to_matcher_text`) | Empty prefs are **not mentioned** in the prompt at all — the judge cannot penalise what it never sees. |
| Embeddings (`embeddings.py`) | Empty fields contribute no text. |
| Frontend | Preference inputs are **optional and say so**. Never block a search on unfilled preferences; never silently write a default value the user didn't choose (a written default is indistinguishable from a choice — see the contradiction below). |
| Extraction (Pillar 1) | The mirror rule: extract everything offered, invent nothing. An input the user didn't provide (no LinkedIn, no GitHub) produces empty fields, not guesses. |

**Audited 2026-08-07:** prefilter, judge, embeddings, and all four dim scorers
comply. One contradiction found and reported:

- **`needs_visa: bool` cannot say "unset".** `False` conflates "I don't need
  sponsorship" with "I never answered". Every other preference has an empty
  state (empty list / empty string / None) that means "don't care" — this one
  can't express it, so the visa dimension can never be switched off, only
  answered. Schema fix (`Optional[bool]`, default `None`) awaiting owner
  decision.

**The exception this doc used to carve out is GONE (2026-08-12).** The legacy
scorer's foreign-location penalty (−15) was deleted along with the hand-typed
`FOREIGN_INDICATORS` list behind it — Rule 2 says UK-only is a door, and a
second, rotting copy of that decision in the scorer is not a backstop. Measured
on the live catalog the day it was removed: 379 of 9,196 rows were paying the
−15, and 9 of them were UK jobs docked by accident ("Belfast, Northern
Ireland" matched "ireland"). If a foreign job is found scoring well, that is a
gate bug to fix, never a penalty to reinstate.

**The test for new code:** take any scoring/filter change, empty ONE user
field, and re-rank. If any job's position changes *relative to another job*
because of the emptiness alone, the rule is broken.

---

## Rule 2 — UK-only is a DOOR, not a penalty

*Set by the owner, 2026-08-07: "If I search for a job in UK and I get another
country, that is a product fault. How can we solve it rather than penalty?"*

**The rule.** Job360 is a UK-market product. A job the user cannot take
because it is in another country is a **catalog defect**, not a low-ranking
job. It is refused at ingestion; it never reaches storage, a feed, an
enrichment budget, an embedding, or a candidate-shelf slot.

**Why not the penalty.** The legacy scorer applied −15 for a foreign location
(deleted 2026-08-12). That admits the job and then argues about its rank — so it still consumes
every downstream budget and can still surface when the other dimensions score
well. Measured 2026-08-07: 156 clearly foreign jobs were live in prod
(Shanghai, São Paulo, Lima, Ottawa, München) despite the penalty existing.

**How the gate decides** (`src/services/uk_gate.py`, one chokepoint in
`main.py` — never per-source). Order is load-bearing:

1. A named foreign **country or admin division** → **blocked**, whoever listed it.
2. Remote fenced to another region ("Remote — US only") → **blocked**.
3. An explicit UK country name or a **full UK postcode** → allowed.
4. A hit in the **UK gazetteer** (~52k places) → allowed.
5. Genuine remote → allowed.
6. Otherwise **who said it decides**: a UK-native source keeps it; a global
   source needs evidence (£, right-to-work language, a UK city in the body).

### THE RULE THIS ENCODES: never hand-enumerate an UNBOUNDED set

The first version listed ~120 **foreign cities** by hand. The owner rejected
it: *"How many will you hard-code like that? What if there is a city out of
this list? Then you missed that."* He is right — foreign cities are unbounded,
so a hand-written **sample** of them rots silently and misses forever.

So the polarity is inverted. **UK places are FINITE** (~52k populated places,
published; settlements do not churn), compiled from GeoNames by
`scripts/build_uk_gazetteer.py` into `src/data/uk_gazetteer/`. Every future miss is
a data refresh, never a code edit.

**The distinction that matters:** countries (~250) and first-level admin
divisions (~4.5k — US states, Canadian provinces) stay enumerated *on purpose*,
because those are **CLOSED** sets. A COMPLETE closed set is not the same
mistake as a SAMPLE of an open one. Both are built from data, so neither drifts.

**Why the foreign check survives inversion:** positive-only matching has a
fatal hole — `"Cambridge (USA)"` contains "cambridge", a real UK town, so a
pure gazetteer lookup would **admit** it. The country override runs first.

**Ambiguity is COMPUTED, not typed.** Boston, Cambridge and Perth name real
places here and abroad. `ambiguous.txt` is derived at build time by comparing
UK populations against **world cities *and* the closed country / first-level
admin-division sets** — London survives (London, Ontario is ~4% the size);
Boston does not. Hand-listing collisions would repeat the original sin.

The country/admin1 half was added for issue #330 (2026-08-19) and it is the
whole reason the escape below finally holds. The first version compared against
`cities500` alone — world *city* primary names — and the UK has hamlets called
New York (pop 0), California (830) and Canada (0). None of those three is a
cities500 primary name (GeoNames calls NYC "New York City"; California is a US
state; Canada is a country), so all three scored as *trusted, unambiguous* UK
places and carried 153 of the 190 live foreign rows straight through the
dual-site escape. Countries and admin1 divisions were already downloaded and
already closed sets, so they now feed the same computation at a flat weight —
`FOREIGN_ADMIN_WEIGHT = 20_000` (`backend/scripts/build_uk_gazetteer.py:95,181-186`).
The weight is measured, not chosen by taste: the 84 UK names colliding with a
foreign country or admin1 have exactly one population gap, between Warwick
(37,267) and Portsmouth (47,350), so a 40,000-effective cut-off keeps
Manchester, Southampton and Canterbury while dropping the hamlets. No branch
was added to `check_uk` and no city was typed — the fix is DATA, which is the
rule. Still admitted on purpose: `"London, Ontario"` — a big UK city beside a
foreign region is how both a foreign address and a genuine two-site ad get
written, and `london` never enters `ambiguous.txt`, so the escape still speaks
for it (`uk_gate.py:367-382`; root CLAUDE.md rule #30 records this as the
remaining gap).

**Traps found by dry-running over the live catalog** (do this before shipping
any location rule):
- The naive "no UK token → reject" blocked **48%**, including Telford and
  Northampton — `UK_TERMS` only held 26 cities.
- **devitjobs** was misclassified as global; its endpoint is devitjobs.**uk**.
  That alone was 1,409 wrongly blocked jobs.
- `"Sydney, Australia"` was **allowed**: the UK has a hamlet called Sydney, so
  a bare gazetteer hit triggered the dual-site escape. Dual-site now demands an
  *unambiguous* UK signal.
- `"Indianapolis, IN, USA"` was not blocked — the country data holds "United
  States", not "USA"/"US". ISO2 + ISO3 codes are now included.

Measured after: 799 blocked of 4,544 (18%), **2 potential false drops**.

**Ambiguity favours the user:** a dual-site posting ("London / New York") is
kept — the user can take the UK half.

---

## Rule 3 — Visa is a SPOTLIGHT, not a wall

*Set by the owner, 2026-08-07: "If you turn on visa, we emphasize on visa. If
you turn off, we show visa jobs and non-visa jobs. Either way we show both."*

**The rule.** The product serves candidates who need sponsorship and those who
do not. Turning visa ON must never shrink the catalog:

| Toggle | Behaviour |
|---|---|
| **OFF** | every job shows; visa affects nothing |
| **ON** | every job *still* shows — but sponsors are **guaranteed into the feed**, **ranked up**, and every card carries a badge |

**Why never a hard filter.** Visa status is a three-state fact —
**sponsors / no sponsorship / unknown** — and *unknown dominates*. Measured
2026-08-07: with text detection plus LLM enrichment, 42% of the catalog is
decidable; 58% is silent. A hard filter would hide that 58% on the strength of
a sentence the employer simply never wrote — including sponsors we merely
failed to detect. The badge gives the user the fact without the deletion.

**Three states, never a boolean.** `jobs.visa_flag` is a bool, so "this ad says
it will not sponsor" and "this ad never mentions visas" are the same value.
Those are opposite facts for a candidate: a dead end versus a question worth
asking. Use `services/visa_signal.detect_visa_status()`.

**Precedence is load-bearing:** "we cannot offer visa sponsorship" *contains*
"visa sponsorship", so refusal must be tested before offer. And a signal that
fires on the wrong sentence is worse than no signal — `tier 2` was removed
from the pattern after it matched "Tier 1 and Tier 2 support representatives".

**Detection is deterministic first, LLM second.** The phrases are formulaic UK
recruitment boilerplate. Reading stored text took visa coverage from **3% to
42% (14×) at zero LLM cost**; enrichment's verdict still wins where it exists.

**This is Rule 1 applied to a filter:** what the user turns on *sharpens the
ranking*; it never silently deletes the catalog.
