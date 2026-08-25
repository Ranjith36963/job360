# Raw Feedback Loop — turning hardcoded lists into self-growing data
<!-- doc: PLAN -->

> **The idea in one line:** Stop hand-typing the skill/company/domain/location lists. Instead, let the LLM (which already reads every CV and job) **remember what it learns** and grow those lists automatically — a data flywheel that raises the bar with every use, so we never hit the tech/UK ceiling.

---

## 1. The problem this solves

Job360 has ~6 areas of **hardcoded domain data** — hand-typed lists that decide who matches what. They are all **live and running** on every search:

| File | What it hardcodes | Bias baked in |
|---|---|---|
| `core/skill_synonyms.py` | 528 skill nicknames → real names (k8s→kubernetes) | tech-heavy |
| `services/skill_matcher.py` | ~50 foreign-country markers + 18 UK cities + remote words | UK-only |
| `services/domain_classifier.py` | ~150 keywords across 5 domains | only 5 domains |
| `core/keywords.py` | UK locations + visa phrases | UK-only |
| `core/companies.py` | ~250 company IDs to scrape | fixed employer set |
| `services/scoring_dimensions.py` + weights | scoring weights/penalties | fixed opinion of "what matters" |

**Why they are a limitation (not a bug — a ceiling):**
- **Can't scale with breadth** — 528 tech skills won't serve nurses/lawyers/chefs; you can't hand-type the whole world.
- **Brittle** — a new skill/company/domain = a code edit + redeploy.
- **Biased** — tech + UK are baked in; fine today, a wall the day we expand.
- **Rots** — companies get acquired, skills get renamed; someone babysits the lists forever.

**Important distinction:** *plumbing* hardcoding (API URLs, regexes, prompts, model names, token TTLs) is **correct and should stay** — it's how the machine works, not data. This doc is only about the **domain-knowledge** hardcoding above.

**Rule #28 already banned hardcoded skill lists in _extraction_** (`profile/` is clean ✅). This doc extends that spirit to **scoring + sourcing**.

---

## 2. The idea — an LLM-in-the-loop data flywheel

The LLM already runs on every CV (extraction, enrichment) and every top job (the judge). It's **already looking at the data — it just isn't saving what it learns.**

The loop:

```
LLM reads a CV / job
      │
      ▼
Discovers something new  (a skill, alias, company, domain, location)
      │
      ▼
GATE  ── validate ──►  reject junk  (hallucination / low confidence)
      │  promote if it clears the bar
      ▼
Add to a living DATASET (DB table)  ──►  used by scoring on the next run
      │
      └──────────── coverage compounds: more use → richer data → better matches → repeat
```

Result: the "hardcoded" lists become **living datasets** that grow themselves. The tech/UK ceiling dissolves because the LLM adds whatever domain it actually sees.

### ⚠️ The one thing that makes or breaks it: the GATE
A **raw** loop that blindly adds everything becomes a garbage pile — LLM hallucinations pollute the data, bad data makes worse matches, and the flywheel spins **backward** (self-poisoning). The fix is a **gated** loop:

> **LLM proposes → validate (ESCO match / confidence / seen-N-times / dedup) → promote to the dataset only if it clears the bar.**

That gate is the difference between *self-improving* and *self-poisoning*. **ESCO can be the validator** for skills (does the proposed skill match a real ESCO entry?).

---

## 3. Where the loop applies (area by area)

| # | Replaces | Hook (where the LLM already runs) | What grows | Gate |
|---|---|---|---|---|
| **1. Skills vocabulary** ⭐ | `skill_synonyms.py` | CV parse + job enrichment (E2) | `skills_vocab` table: skill + aliases + canonical | ESCO match **or** seen ≥ N times |
| **2. Domain map** | `domain_classifier.py` | CV/job classification | `domains` table: domain + keywords | seen across ≥ N CVs/jobs |
| **3. Company list** | `companies.py` | job ingestion (every job names a company) | `companies` table + auto-detected ATS | ATS endpoint verified live |
| **4. Location / geo** | UK/foreign lists in `skill_matcher.py` | job enrichment (reads location) | `locations` table: place → UK/foreign/remote | LLM confidence + dedup |
| **5. Scoring weights** | weights in `skill_matcher`/`scoring_dimensions` | **user actions** (liked/applied/skipped) | tuned weights | slow nudge + bounded range |

Note: **#5 is a different loop** — it learns from **user behaviour**, not LLM discovery. The signal (`user_actions`: liked/applied/skipped) is **already captured**. It nudges weights toward what people actually click. It's the riskiest (bad tuning hurts everyone), so it's built **last**.

---

## 4. Why this is doable for us (we already have the hard parts)

- **The discovery engine already runs.** The LLM reads every CV (extraction + E2 enrichment) and judges top jobs (E4). We add a *"remember what you learned"* step after it — not a new engine.
- **The feedback signal already exists.** `user_actions` records liked/applied/skipped per user.
- **A validator already exists.** ESCO (13,900 skills across all professions) is the natural gate for #1.
- **The DB is ready.** We're on Postgres now; adding `skills_vocab` / `companies` / etc. tables is trivial + auto-migrates on boot.

So this is **additive** — a save-step + a gate — not a rewrite.

---

## 5. Build sequence (value fast, safe first)

1. **#1 Skills vocabulary (PILOT).** Highest impact, cleanest gate (ESCO validates). One table, one hook, one gate. Proves the flywheel. Directly attacks the tech/UK ceiling.
2. **#3 Company list.** Practical + high-supply impact; discover employers from job postings, verify their ATS, add without redeploy.
3. **#2 Domain map** and **#4 Location/geo.** Same pattern, LLM-gated.
4. **#5 Weight-learning (last).** From `user_actions`. Bounded, slow, reversible — because bad weight-tuning is the easiest to get wrong.

### Pilot detail — #1 Self-growing skills vocabulary
- **Table:** `skills_vocab(id, canonical_skill, aliases JSONB, esco_uri, source, confidence, seen_count, first_seen_at, last_seen_at)`
- **Hook:** in CV extraction + `enrich_batch` (E2), after the LLM returns skills.
- **Flow:** for each LLM skill → normalize → is it in `skills_vocab`? If yes, bump `seen_count`. If no → **gate:** does it match an ESCO entry (via the ESCO index)? OR has it been seen ≥ N times across users? If it clears → insert. Else hold in a "pending" state until it does.
- **Read path:** the scorer's `aliases_for()` / `canonicalize_skill()` read from `skills_vocab` instead of the static dict (with the static dict as a seed / fallback).
- **Result:** every CV upload makes the vocabulary richer; new AI frameworks, and eventually non-tech skills, appear automatically.

---

## 6. Risks & guardrails

- **Self-poisoning** — the #1 risk. Mitigate with the gate (ESCO / confidence / seen-N-times / dedup). Never promote on a single unvalidated LLM output.
- **Cost** — the LLM already runs; the save-step is cheap. No extra LLM calls for #1–#4.
- **Drift / bad weights (#5)** — bound the range, nudge slowly, keep it reversible, and A/B it (the repo already has an eval harness).
- **Pillar 2 is scoring** — these loops touch scoring/sourcing (skill_synonyms, companies, weights). Change deliberately, keep the static lists as seed/fallback, and verify against the eval report.
- **Keep plumbing hardcoded** — do NOT "data-drive" URLs, regexes, prompts, model names. Those stay in code.

---

## 7. One-line summary

> **The LLM already reads everything — make it *remember*.** Add a gated save-step after each LLM pass so the skill/company/domain/location lists grow themselves, with ESCO + user actions as the quality gates. Start with the skills vocabulary; keep the static lists as the seed. That's how the tech/UK ceiling turns into a floor that keeps rising.
