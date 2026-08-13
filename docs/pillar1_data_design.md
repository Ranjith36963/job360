## Your two questions, simple words first

**Are CV / LinkedIn / GitHub skills merged into one list?**
**No — each source keeps its own skill shelf.** Proven in the live model:

```
skills                    ← CV only
linkedin_skills           ← LinkedIn only
github_skills_inferred    ← GitHub (structural)
github_llm_skills         ← GitHub (LLM read the README)
about_me_inferred_skills  ← from your typed "about me"
additional_skills         ← skills you type yourself
```
They sit **side by side, never blended.** That separation is deliberate — it's what lets the system say *"proven in real GitHub repos"* vs *"listed on a CV"* and weight them differently.

**What's the merged container called?**
Everything from CV + LinkedIn + GitHub lands in **one object called `CVData`** (a slightly odd name — it holds all three, not just the CV). Your typed preferences live in a second object, **`UserPreferences`**. Both are wrapped in **`UserProfile`**.

```
UserProfile
 ├── CVData          ← CV + LinkedIn + GitHub extracted shelves (37)
 └── UserPreferences ← what you type (15)
```

---

## The 52 shelves, by input and pass

**Pass key:** `D` = deterministic (plain code) · `L` = LLM · `A` = API fetch · `U` = you type it

### Input 1 — CV → stored in `CVData`
| Shelf | Pass | What it holds |
|---|---|---|
| skills | D + L | CV skills |
| summary | D + L | professional summary |
| job_titles | L | your titles |
| companies | L | employers |
| **cv_positions** | L | dated work history {company·title·dates·bullets} |
| education | L | degrees |
| certifications | L | certs |
| achievements | L | awards/wins |
| name · headline · location | L | identity |
| industries | L | your industries |
| experience_text | L | experience prose |
| cv_skills_esco | L | skills mapped to the ESCO ontology |
| raw_text | D | full CV text (for re-runs) |
| extraction_score | D | quality metric |
| career_domain 🔴 | L | *filled, no reader* |
| cv_languages 🔴 | L | *filled, no reader* |
| llm_input_hashes 🔴 | D | *cache key, no reader* |

### Input 2 — LinkedIn → `CVData` (linkedin_*)
| Shelf | Pass | What it holds |
|---|---|---|
| linkedin_skills | D + L | LinkedIn skills (kept separate) |
| linkedin_positions | L | LinkedIn work history |
| linkedin_industry | D | industry |
| linkedin_languages | L | spoken languages |
| linkedin_projects | L | projects |
| linkedin_volunteer | L | volunteer roles |
| linkedin_courses | L | courses |
| linkedin_raw_text | D | full text (for re-runs) |

### Input 3 — GitHub → `CVData` (github_*)
| Shelf | Pass | What it holds |
|---|---|---|
| github_languages | A/D | languages by bytes |
| github_topics | A/D | repo topics |
| github_skills_inferred | D | skills from structure |
| github_frameworks | D | frameworks from dep files |
| **github_llm_skills** | L | skills the LLM read from READMEs |
| github_repos_brief | A | per-repo {name·lang·README} |
| github_bio · github_profile_readme | A | self-written text |

### Input 4 — Preferences → `UserPreferences`
| Shelf | Pass | What it holds |
|---|---|---|
| target_job_titles · additional_skills · excluded_skills | U | targeting |
| preferred_locations · industries · negative_keywords | U | filters |
| salary_min · salary_max | U | pay range |
| work_arrangement | U | remote/hybrid/onsite. `preferred_workplace` is a read-only property derived from it, not a second stored field (2026-08-13). |
| experience_level · **needs_visa** | U | level + visa |
| about_me | U | free text |
| **about_me_inferred_skills** | L | skills the LLM mines from about_me |
| **experience_level_inferred** | L (derived) | your level read from CV dates |
| github_username | U | handle |

## How it's placed (the flow)
```
CV ─┐
LinkedIn ─┤  each →  DETERMINISTIC pass + LLM pass  ─→ merged into ONE CVData
GitHub ─┘                                              (skills stay per-source)
Preferences ──→ typed, + about_me runs an LLM pass ─→ UserPreferences
```

`★ Insight ─────────────────────────────────────`
The container name `CVData` is a historical misnomer — it started as CV-only and grew to hold LinkedIn + GitHub too. Worth knowing when you read the code: `cv_data` in the DB is really "everything we extracted about the person."
`─────────────────────────────────────────────────`

🔴 = the 3 shelves that are filled but nothing reads them — dead weight, deliberately left, not gaps.
