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

**What this means in code.** Every layer this rule was written against —
dimension scorers, the prefilter, the LLM judge, embeddings — was deleted with
the sourcing era (`backend/tests/test_sourcing_era_deleted.py::test_modules_gone`
pins them gone). What survives the rule still governs:

- **Extraction** — the mirror rule: extract everything offered, invent nothing.
  An input the user didn't provide (no LinkedIn, no GitHub) produces empty
  fields, not guesses.
- **Frontend** — preference inputs are optional and say so. Never silently
  write a default the user didn't choose: a written default is
  indistinguishable from a choice.
- **The fit context we hand the agent** for a job the user brought (Rule 4).

One contradiction, still open: **`needs_visa: bool` cannot say "unset".**
`False` conflates "I don't need sponsorship" with "I never answered". Every
other preference has an empty state (empty list / empty string / None) that
means "don't care" — this one can't express it
(`services/profile/models.UserPreferences.needs_visa`). Schema fix
(`Optional[bool]`, default `None`) awaiting owner decision.

---

## Rule 2 — UK-only is a DOOR, not a penalty

*Set by the owner, 2026-08-07: "If I search for a job in UK and I get another
country, that is a product fault. How can we solve it rather than penalty?"*

**The rule.** Job360 is a UK-market product. A job the user cannot take
because it is in another country is a **catalog defect**, not a low-ranking
job — refused at the door, never admitted and then argued about on rank.

*The ingestion gate this rule was written for (`services/uk_gate`, the GeoNames
gazetteer builder and their data files) went with the sourcing era on
2026-09-05 — `backend/tests/test_sourcing_era_deleted.py::test_modules_gone`
pins it gone. Nothing enforces this rule today; it binds the next thing that
touches location.*

### THE RULE THIS ENCODES: never hand-enumerate an UNBOUNDED set

The first version listed ~120 **foreign cities** by hand. The owner rejected
it: *"How many will you hard-code like that? What if there is a city out of
this list? Then you missed that."* He is right — foreign cities are unbounded,
so a hand-written **sample** of them rots silently and misses forever.

So the polarity inverts: enumerate the **finite** side. UK settlements are
finite and published; foreign cities are not. Countries (~250) and first-level
admin divisions (~4.5k) stay enumerated *on purpose* — a COMPLETE closed set is
not the same mistake as a SAMPLE of an open one. Both are built from data, so
neither drifts, and every future miss is a data refresh rather than a code edit.

**Ambiguity is COMPUTED, not typed.** Boston, Cambridge and Perth name real
places here and abroad; hand-listing the collisions would repeat the original
sin. Compute them, and let ambiguity favour the user — a dual-site posting
("London / New York") is kept, because the user can take the UK half.


---

## Rule 3 — Visa is a SPOTLIGHT, not a wall

*Set by the owner, 2026-08-07: "If you turn on visa, we emphasize on visa. If
you turn off, we show visa jobs and non-visa jobs. Either way we show both."*

**The rule.** The product serves candidates who need sponsorship and those who
do not. Visa is a badge and an emphasis, never a hard filter: turning it on
must never shrink what the user can see.

**Why never a hard filter.** Visa status is a three-state fact —
**sponsors / no sponsorship / unknown** — and *unknown dominates*. A hard
filter hides the unknowns on the strength of a sentence the employer simply
never wrote, including sponsors we merely failed to detect. The badge gives
the user the fact without the deletion.

**Three states, never a boolean.** `jobs.visa_flag` is a bool, so "this ad says
it will not sponsor" and "this ad never mentions visas" are the same value.
Those are opposite facts for a candidate: a dead end versus a question worth
asking. The detector that read them (`services/visa_signal`) went with the
sourcing era — a three-state signal is roadmap slice 7, supplied by the agent.

**Precedence is load-bearing** for whatever reads visa text next: "we cannot
offer visa sponsorship" *contains* "visa sponsorship", so refusal must be
tested before offer. A signal that fires on the wrong sentence is worse than no
signal — `tier 2` was dropped after it matched "Tier 1 and Tier 2 support
representatives".

**This is Rule 1 applied to a filter:** what the user turns on *sharpens* what
they see; it never silently deletes it.

---

## Rule 4 — The user brings the job. We never source, rank or recommend.

*Set by the owner 2026-09-02, confirmed 2026-09-03 (VISION.md, decision 1).*

**The rule.** A job enters Job360 only because the user or their agent brought
it — a pasted ad, a link, or an MCP call. There is no feed, no ranking, no
"jobs for you". Job boards and the user's own AI agent find jobs; we do not.

**What this means in code:** the search pipeline, the sources and the batch
scorer/judge/enrichment were deleted 2026-09-05 (slice 5, #483) and must never
come back — `backend/tests/test_sourcing_era_deleted.py` is the guard. Rules
1–3 above still govern the one place matching-like logic survives: the *fit
context* we hand the agent for a job the user brought. An empty preference is
still silent; UK/visa are still spotlights, never walls — applied to one job,
not to a catalog.

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
