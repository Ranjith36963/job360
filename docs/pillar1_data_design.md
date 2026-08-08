# Pillar 1 — Data Design (the user-side shelves)

*Verified against live production code (`main`, commit `496336d`) on 2026-08-08.
The shelf list is generated from the code by `backend/scripts/shelf_manifest.py`,
not hand-typed, so it cannot drift silently — re-run that script to refresh it.*

Pillar 1 is the **user side**: it takes up to four inputs, runs each through two
passes, and places everything into a proper data structure ("shelves") so that
keyword search, semantic search, the LLM judge, and any enrichment can read it
cleanly.

---

## Two questions answered first (plain words)

### Are CV / LinkedIn / GitHub skills merged into one list?

**No — each source keeps its own skill shelf.** They sit side by side and are
never blended:

```
skills                    <- CV only
linkedin_skills           <- LinkedIn only
github_skills_inferred    <- GitHub (from repo structure)
github_llm_skills         <- GitHub (LLM read the README prose)
about_me_inferred_skills  <- from your typed "about me"
additional_skills         <- skills you type yourself
```

The separation is deliberate. It is what lets the system say *"proven in real
GitHub repos"* versus *"listed on a CV"* and weight the two differently
(`skill_tiering` assigns evidence weights per source). If they were one merged
list, that provenance — and the ability to tune it — would be lost.

### What is the merged container called?

Everything extracted from CV + LinkedIn + GitHub lands in **one object called
`CVData`**. The name is a historical misnomer — it began as CV-only and grew to
hold all three inputs. Your typed preferences live in a second object,
`UserPreferences`. Both are wrapped in `UserProfile`.

```
UserProfile
 |-- CVData           <- CV + LinkedIn + GitHub extracted shelves (37)
 +-- UserPreferences  <- what you type (15)
```

In the database, the `user_profiles.cv_data` JSON column is really "everything
we extracted about the person", not just the CV.

---

## How it is placed (the flow)

```
CV ---------\
LinkedIn ----|  each ->  DETERMINISTIC pass + LLM pass  --> merged into ONE CVData
GitHub -----/                                              (skills stay per-source)

Preferences ----> typed by the user, + about_me runs an LLM pass --> UserPreferences
```

- **Deterministic pass** = plain code: structure, exact tokens, ESCO-vocabulary
  matching, raw API fields. No hardcoded skill lists (hard rule #28).
- **LLM pass** = a model reads the prose and returns structured fields.
- The two are **merged** into one `CVData`. The merge fills-or-unions per field;
  it never lets a re-run silently wipe a prior good value (the data-loss bugs
  fixed 2026-08-08 all lived in this merge — see `project_pillar1_complete`).

Every intake is stamped with a snapshot id `SNAP-YYYYMMDD-<user4>-<content8>`
(`backend/src/services/profile/snapshot.py`, migration 0030). The content hash
is over the **input** the user supplied, not the extraction output, so
re-running extraction on the same inputs keeps the same id — a real
re-submission is distinguishable from a re-parse.

**Pass key for the tables:** `D` = deterministic · `L` = LLM · `A` = API fetch ·
`U` = user-typed. A 🔴 marks a shelf that is filled but has no reader — dead
weight, deliberately left, not a gap.

---

## Input 1 — CV → stored in `CVData`

| Shelf | Pass | What it holds |
|---|---|---|
| skills | D + L | CV skills |
| summary | D + L | professional summary |
| job_titles | L | your job titles |
| companies | L | employers |
| cv_positions | L | dated work history `{company, title, dates, location, bullets}` |
| education | L | degrees |
| certifications | L | certifications |
| achievements | L | awards / wins |
| name | L | your name |
| headline | L | headline |
| location | L | location |
| industries | L | your industries |
| experience_text | L | experience prose |
| cv_skills_esco | L | skills mapped to the ESCO ontology |
| raw_text | D | full CV text (kept for offline re-runs) |
| extraction_score | D | extraction quality metric |
| career_domain 🔴 | L | filled, no reader |
| cv_languages 🔴 | L | filled, no reader |
| llm_input_hashes 🔴 | D | LLM-cache key, no reader |

## Input 2 — LinkedIn → `CVData` (linkedin_* fields)

| Shelf | Pass | What it holds |
|---|---|---|
| linkedin_skills | D + L | LinkedIn skills (kept separate from CV skills) |
| linkedin_positions | L | LinkedIn work history |
| linkedin_industry | D | industry |
| linkedin_languages | L | spoken languages |
| linkedin_projects | L | projects |
| linkedin_volunteer | L | volunteer roles |
| linkedin_courses | L | courses |
| linkedin_raw_text | D | full LinkedIn text (kept for re-runs) |

## Input 3 — GitHub → `CVData` (github_* fields)

| Shelf | Pass | What it holds |
|---|---|---|
| github_languages | A / D | languages by bytes (recency-weighted) |
| github_topics | A / D | repo topics |
| github_skills_inferred | D | skills from repo structure |
| github_frameworks | D | frameworks from dependency files |
| github_llm_skills | L | skills the LLM read from README prose |
| github_repos_brief | A | per-repo `{name, language, description, readme_excerpt}` |
| github_bio | A | self-written bio |
| github_profile_readme | A | the `{user}/{user}` profile README |

## Input 4 — Preferences → `UserPreferences`

| Shelf | Pass | What it holds |
|---|---|---|
| target_job_titles | U | roles you're targeting |
| additional_skills | U | skills you add yourself |
| excluded_skills | U | skills to penalise |
| preferred_locations | U | where you want to work |
| industries | U | industries you want |
| negative_keywords | U | title keywords to penalise |
| salary_min | U | pay floor |
| salary_max | U | pay ceiling |
| work_arrangement | U | remote / hybrid / onsite |
| preferred_workplace | U (from work_arrangement) | enum form the scorer reads |
| experience_level | U | your stated level |
| experience_level_inferred | L (derived) | your level read from CV dates when you don't state one |
| needs_visa | U | whether you need visa sponsorship |
| about_me | U | free-text summary |
| about_me_inferred_skills | L | skills the LLM mines from about_me |
| github_username | U | your GitHub handle |

---

## Totals

- **CVData:** 37 shelves (CV + LinkedIn + GitHub)
- **UserPreferences:** 15 shelves
- **Total:** 52 shelves
- **Dead weight (filled, no reader):** 3 — `career_domain`, `cv_languages`,
  `llm_input_hashes`. Left in place on purpose; rendering or "fixing" them would
  add noise, not value.

## Who reads the shelves (the consumers)

`scorer` (keyword + dimension scoring) · `judge` (the LLM matcher) · `semantic`
(embeddings) · `prefilter` (candidate gating) · `search-keywords` (turns the
profile into fetch queries) · `tiering` (per-source skill evidence weighting) ·
`api` (the profile page display). The `read-by` column in
`scripts/shelf_manifest.py` output shows exactly which consumers touch each
shelf; a shelf with no consumer is the dead weight above.

---

*To regenerate this list from current code:*
`cd backend && python scripts/shelf_manifest.py`
