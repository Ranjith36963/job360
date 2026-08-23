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
| career_domain | L | read by the semantic engine (`embeddings.py:208`) and the LLM judge (`llm_matcher.py:207`) |
| cv_languages | L | read by the semantic engine (`embeddings.py:207`) and the LLM judge (`llm_matcher.py:200`) |
| llm_input_hashes | D | cache key; read by `two_pass.py`'s `_already_read()` to skip a paid LLM call on unchanged input |

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

🔴 = a shelf that is filled but nothing reads it — dead weight, deliberately left, not a gap.
None currently qualify: every CVData/UserPreferences shelf has a real reader (see the
correction below for the two that were wrongly marked, and for why the third candidate
isn't dead either).

Correction (2026-08-15): `career_domain` and `cv_languages` were marked 🔴 above but are
NOT dead — verified by grep (`grep -rn "career_domain\|cv_languages" backend/src`) and
confirmed live in `backend/src/services/embeddings.py:207-208` (semantic engine) and
`backend/src/services/llm_matcher.py:200,207` (LLM judge). Both were wired in on
2026-08-09 (see the comment at `embeddings.py:201-205`, which says so directly); this
doc was never updated after that batch shipped.

`llm_input_hashes` was ALSO wrongly marked 🔴 in an earlier pass of this same correction —
that pass only checked `api/routes/profile.py`, where the field is `.pop()`-ed (a discard,
not a read), and stopped there. It missed the field's actual consumer: `two_pass.py`'s
`_already_read()` (`src/services/profile/two_pass.py:213-220`) does
`cv.llm_input_hashes.get(key) == _input_hash(raw)` — a real value comparison that decides
whether `run_two_pass_extraction` skips a paid LLM call because the input hasn't changed
since the last pass. Called from both the per-input skip check (`two_pass.py:184`) and the
cache-hit summary (`two_pass.py:556`). Note for whoever next runs the shelf audit tool over
this file: `readers("llm_input_hashes")` currently returns only `{"api"}` and misses this —
`two_pass.py` is registered in `shelf_audit._WRITER_ROLES` (as `"merge"`) but absent from
`shelf_audit._ROLES`, so the tool never scans it for reads at all. That's a real gap in the
instrument (confirmed: no shelf in the registry reports `"merge"` among its readers), not a
signal that this field is unread — it is the module's core caching logic.
