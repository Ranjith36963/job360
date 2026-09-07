# Product Design Rules
<!-- doc: LIVING -->

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
job — refuse it at the door rather than admitting it and arguing about its
rank, which still spends every downstream budget and can still surface when
the other dimensions score well. Measured 2026-08-07 under the old −15
penalty: 156 clearly foreign jobs were live in prod.

> **The gate this rule described is gone.** `services/uk_gate.py`, the GeoNames
> gazetteer and its builder were deleted with the sourcing era (slice 5, #483)
> — there is no ingestion to gate, because Job360 no longer sources jobs
> (Rule 4). The rule below is kept for its reasoning, not as a description of
> anything running.

### THE RULE THIS ENCODES: never hand-enumerate an UNBOUNDED set

The first version listed ~120 **foreign cities** by hand. The owner rejected
it: *"How many will you hard-code like that? What if there is a city out of
this list? Then you missed that."* He is right — foreign cities are unbounded,
so a hand-written **sample** of them rots silently and misses forever. Invert
the polarity instead: UK places are FINITE (~52k populated places, published;
settlements do not churn), so every future miss is a data refresh, never a
code edit.

**The distinction that matters:** countries (~250) and first-level admin
divisions (~4.5k — US states, Canadian provinces) stay enumerated *on purpose*,
because those are **CLOSED** sets. A COMPLETE closed set is not the same
mistake as a SAMPLE of an open one. Both are built from data, so neither drifts.

**Ambiguity is COMPUTED, not typed.** Boston, Cambridge and Perth name real
places here and abroad; hand-listing the collisions would repeat the original
sin. Derive them by comparing UK populations against the world-city and closed
country/admin1 sets — London survives (London, Ontario is ~4% the size), Boston
does not. Two corollaries that cost real bugs to learn: a positive-only
gazetteer match **admits** `"Cambridge (USA)"`, so the country check must run
first; and comparing against city primary names alone lets UK hamlets called
New York, California and Canada score as trusted UK places.

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
asking. Anything that reads visa status must model all three.

**Precedence is load-bearing:** "we cannot offer visa sponsorship" *contains*
"visa sponsorship", so refusal must be tested before offer. And a signal that
fires on the wrong sentence is worse than no signal — `tier 2` was removed
from the pattern after it matched "Tier 1 and Tier 2 support representatives".

**Detection is deterministic first, LLM second.** The phrases are formulaic UK
recruitment boilerplate. Reading stored text took visa coverage from **3% to
42% (14×) at zero LLM cost**; enrichment's verdict still wins where it exists.

**This is Rule 1 applied to a filter:** what the user turns on *sharpens the
ranking*; it never silently deletes the catalog.

---

## Rule 4 — The user brings the job. We never source, rank or recommend.

*Set by the owner 2026-09-02, confirmed 2026-09-03 (VISION.md, decision 1).*

**The rule.** A job enters Job360 only because the user or their agent brought
it — a pasted ad, a link, or an MCP call. There is no feed, no ranking, no
"jobs for you". Job boards and the user's own AI agent find jobs; we do not.

**What this means in code:** the search pipeline, the 41 sources and the batch
scorer/judge/enrichment are hidden behind a flag (off) and will be deleted
(VISION.md build order, step 5). Rules 1–3 above still govern the one place
matching-like logic survives: the *fit context* we hand the agent for a job
the user brought. An empty preference is still silent; UK/visa are still
spotlights, never walls — applied to one job, not to a catalog.

---

## Rule 5 — The agent thinks. Job360 remembers.

*Set by the owner 2026-09-03 (VISION.md, decisions 4, 5, 7, 8, 9).*

**The rule.** Do not build what the user's AI agent can already do: judge fit,
write a CV or cover letter, read Gmail, find a recruiter, fill a form. Build
what the agent cannot keep: the candidate's structured context, every version
of every artifact, every event with its author, the receipt of what was sent.

**The test for a new feature:** "Could Claude Code / ChatGPT do this today
with its own tools?" If yes, we expose a *store* tool for the result, not a
*do* tool. Exceptions are explicit and web-only (our tailoring stays as a
fallback button for users with no agent).

**Corollary — one door, typed events.** Anything that changes an application
goes through `record_event` with a fixed event type, free-text detail and
`recorded_by`. Current status is derived from the last status event, never
stored as the only truth. Nothing is deleted or rewritten; a receipt is
append-only.

---

## Rule 6 — Free, pull, consent-first

*Set by the owner 2026-09-02 / 2026-09-03 (VISION.md, decisions 11, 12; pivot memo).*

- **Free** for seekers and recruiters until value is proven. No credits, no
  per-application charge — nothing that rewards volume. No auto-submit.
- **Pull, not push** while there is no worker: the agent asks `whats_new`;
  the web home shows it. Push (email digest, WhatsApp) returns only with the
  worker, on evidence.
- **Recruiters later, consent-first only.** Never sell candidate access
  (Hired and Triplebyte died doing it).
- **Any client connects.** OAuth 2.1 with short-lived tokens is the auth
  shape; personal tokens remain a CLI fallback.
