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
inference from an empty user field.

**The test for new code:** take any scoring/filter change, empty ONE user
field, and re-rank. If any job's position changes *relative to another job*
because of the emptiness alone, the rule is broken.
