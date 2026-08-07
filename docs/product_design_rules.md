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

**Deliberate exception (documented, not a violation):** the legacy scorer's
foreign-location penalty (−15) applies even with no location preference. That
is UK-market product scope — every source is UK/remote by design — not an
inference from an empty user field. **Superseded in practice by Rule 2**: the
UK gate now refuses foreign jobs at ingestion, so the penalty should rarely
have anything left to fire on. It stays as a belt-and-braces backstop for rows
that predate the gate; if it is ever found penalising a job the gate admitted,
that is a gate bug to fix, not a penalty to tune.

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

**Why not the penalty.** The legacy scorer applies −15 for a foreign location.
That admits the job and then argues about its rank — so it still consumes
every downstream budget and can still surface when the other dimensions score
well. Measured 2026-08-07: 156 clearly foreign jobs were live in prod
(Shanghai, São Paulo, Lima, Ottawa, München) despite the penalty existing.

**How the gate decides** (`src/services/uk_gate.py`, one chokepoint in
`main.py` — never per-source):

1. A named foreign country/city → **blocked**, whoever listed it.
2. Remote fenced to another region ("Remote — US only") → **blocked**.
3. A UK token, or genuine remote → **allowed**.
4. Otherwise **who said it decides**: a UK-native source (Reed, NHS,
   jobs.ac.uk, teaching_vacancies, devitjobs.**uk**) is UK by construction, so
   an unrecognised town is a UK town; a global source (Greenhouse, Workday,
   RemoteOK) needs evidence — a £ sign, UK right-to-work language, or a UK
   city named in the ad.

Unknown sources default to **strict**: a new source opts *into* trust.

**The trap this design exists to avoid.** `UK_TERMS` holds 26 cities; Cardiff,
Newcastle and Brighton are absent. A dry run of the naive rule ("no UK token →
reject") blocked **2,436 jobs (48%)** including Telford, Fareham and
Northampton. Never ship a location rule without dry-running it over the live
catalog first and eyeballing what it drops.

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
