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

**What this means in code.** Every layer that consumed a preference — the dim
scorers, the prefilter, the LLM judge, the embeddings — was deleted with the
sourcing era (`backend/tests/test_sourcing_era_deleted.py::test_modules_gone`).
What survives the rule is the input side: preference inputs are **optional and
say so**, nothing blocks on an unfilled one, and no default is silently written
(a written default is indistinguishable from a choice). Extraction obeys the
mirror rule — extract everything offered, invent nothing.

One contradiction found on the 2026-08-07 audit and still open:

- **`needs_visa: bool` cannot say "unset".** `False` conflates "I don't need
  sponsorship" with "I never answered". Every other preference has an empty
  state (empty list / empty string / None) that means "don't care" — this one
  can't express it, so the visa dimension can never be switched off, only
  answered. Schema fix (`Optional[bool]`, default `None`) awaiting owner
  decision.

**The lesson the 2026-08-12 deletion paid for.** A second, hand-typed copy of a
decision (the scorer's own foreign-location penalty, beside Rule 2's gate) is
not a backstop — it is a thing that rots separately. Measured the day it went:
379 of 9,196 rows were paying that penalty and 9 of them were UK jobs docked by
accident ("Belfast, Northern Ireland" matched "ireland").

**The test for new code:** empty ONE user field. If what the user is shown
changes *because of the emptiness alone*, the rule is broken.

---

## Rule 2 — UK-only is a DOOR, not a penalty

*Set by the owner, 2026-08-07: "If I search for a job in UK and I get another
country, that is a product fault. How can we solve it rather than penalty?"*

**The rule.** Job360 is a UK-market product. A job the user cannot take
because it is in another country is a **defect**, not a low-ranking job — it is
refused, never admitted-then-demoted.

**Why not the penalty.** A penalty admits the job and then argues about its
rank, so it still consumes every downstream budget and can still surface when
the other dimensions score well. Measured 2026-08-07: 156 clearly foreign jobs
were live in prod (Shanghai, São Paulo, Lima, Ottawa, München) despite the
penalty existing.

**The gate that enforced this is gone** — it was an ingestion chokepoint on a
catalog we no longer build (rule 4), deleted with the sourcing era
(`backend/tests/test_sourcing_era_deleted.py::test_modules_gone`). What is kept
below is the design lesson it paid for, which applies to any future list.

### THE RULE THIS ENCODES: never hand-enumerate an UNBOUNDED set

The first version listed ~120 **foreign cities** by hand. The owner rejected
it: *"How many will you hard-code like that? What if there is a city out of
this list? Then you missed that."* He is right — foreign cities are unbounded,
so a hand-written **sample** of them rots silently and misses forever.

So the polarity is inverted. **UK places are FINITE** (~52k populated places,
published; settlements do not churn), so the list was compiled from GeoNames at
build time rather than typed. Every future miss is a data refresh, never a code
edit.

**The distinction that matters:** countries (~250) and first-level admin
divisions (~4.5k — US states, Canadian provinces) stay enumerated *on purpose*,
because those are **CLOSED** sets. A COMPLETE closed set is not the same
mistake as a SAMPLE of an open one. Both are built from data, so neither drifts.

**Why the foreign check survives inversion:** positive-only matching has a
fatal hole — `"Cambridge (USA)"` contains "cambridge", a real UK town, so a
pure gazetteer lookup would **admit** it. The country override runs first.

**Ambiguity is COMPUTED, not typed.** Boston, Cambridge and Perth name real
places here and abroad. The collision list was derived at build time by
comparing UK populations against world cities *and* the closed country /
admin-division sets — London survives (London, Ontario is ~4% the size); Boston
does not. Hand-listing collisions would repeat the original sin.

**Ambiguity favours the user:** a dual-site posting ("London / New York") is
kept — the user can take the UK half.

**Dry-run any location rule over real data before shipping it.** Every trap
this rule cost was found that way and none of them by reading the code: a naive
"no UK token → reject" blocked 48% of the catalog; a UK-native source was
misclassified as global on its own domain; "Sydney, Australia" was admitted
because the UK has a hamlet called Sydney; "Indianapolis, IN, USA" survived
because the country data spelled it "United States".

---

## Rule 3 — Visa is a SPOTLIGHT, not a wall

*Set by the owner, 2026-08-07: "If you turn on visa, we emphasize on visa. If
you turn off, we show visa jobs and non-visa jobs. Either way we show both."*

**The rule.** The product serves candidates who need sponsorship and those who
do not. Turning visa ON must never shrink the catalog:

| Toggle | Behaviour |
|---|---|
| **OFF** | every job shows; visa affects nothing |
| **ON** | every job *still* shows — sponsorship is emphasised, never used to hide a job |

**Why never a hard filter.** Visa status is a three-state fact —
**sponsors / no sponsorship / unknown** — and *unknown dominates*. Measured
2026-08-07: with text detection plus LLM enrichment, 42% of the catalog is
decidable; 58% is silent. A hard filter would hide that 58% on the strength of
a sentence the employer simply never wrote — including sponsors we merely
failed to detect. The badge gives the user the fact without the deletion.

**Three states, never a boolean.** `jobs.visa_flag` is a bool, so "this ad says
it will not sponsor" and "this ad never mentions visas" are the same value.
Those are opposite facts for a candidate: a dead end versus a question worth
asking. The detector that gave the third state was deleted with the sourcing era
(`backend/tests/test_sourcing_era_deleted.py::test_modules_gone`); the agent now
supplies the signal on `bring_job` (VISION decision 20) and the column keeps its
old two-state shape until it is replaced.

**If a detector is ever written again, precedence is load-bearing:** "we cannot
offer visa sponsorship" *contains* "visa sponsorship", so refusal must be tested
before offer. A signal that fires on the wrong sentence is worse than no signal
— `tier 2` had to leave the pattern after it matched "Tier 1 and Tier 2 support
representatives".

**This is Rule 1 applied to a filter:** what the user turns on *sharpens* what
they see; it never silently deletes it.

---

## Rule 4 — The user brings the job. We never source, rank or recommend.

*Set by the owner 2026-09-02, confirmed 2026-09-03 (VISION.md, decision 1).*

**The rule.** A job enters Job360 only because the user or their agent brought
it — a pasted ad, a link, or an MCP call. There is no feed, no ranking, no
"jobs for you". Job boards and the user's own AI agent find jobs; we do not.

**What this means in code:** the search pipeline, its sources and the batch
scorer/judge/enrichment were deleted in slice 5 (#483), and
`backend/tests/test_sourcing_era_deleted.py` keeps them deleted. Rules 1–3 above
survive as principles, not as code: they govern the *fit context* we hand the
agent for a job the user brought — an empty preference is still silent, UK and
visa are still spotlights and never walls, applied to one job, not a catalog.

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
