# PLAN 4 — Rescue the stranded profile-extraction quality fixes (worktree-tp-final)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rank: 4 of 5.**
Why: ~2,300 lines of finished, tested profile-extraction quality work (fuzzy dedup of certs/education/skills, LLM-provider hardening with timeouts + smart 429 backoff, prompt-steered CV extraction, GitHub dependency-noise removal) sit unmerged on `worktree-tp-final`. Profile extraction quality is the core of the product — every score depends on it. The branch decays: main moved 91 commits past its fork point already. The catch: **a plain `git merge` conflicts (9 markers, 5 files)** because main independently re-implemented four of the branch's own commits. The rescue is a selective cherry-pick, not a merge.

**Goal:** the ~11 genuinely novel commits from `worktree-tp-final` land on main; the 4 commits main already re-implemented are skipped; full suite green.

**Architecture:** Cherry-pick oldest-first onto a fresh branch from main, with explicit skip list, per-pick targeted tests, and a fallback to manual porting when a pick conflicts too widely.

**Tech Stack:** git cherry-pick, pytest.

---

## Verified facts (checked 2026-07-07 — the commit analysis below is the heart of this plan)

- `worktree-tp-final` is 18 commits ahead of main; merge-base is `61d1971`; main has 91 commits since, INCLUDING the SQLite→Postgres migration (`8d240f8`) and — critically — **independent re-implementations of four of the branch's commits**:

| Branch commit (SKIP — already on main as…) | Main's twin |
|---|---|
| `467d27d` symmetric two-pass extraction | `62f1505` |
| `041857a` merge Stage 1 + Stage 2 | `e7dbe67` |
| `ff5b5d5` 4 dedicated input routes | `db2c518` |
| `a0d3767` remove dead reextract_and_rescore | `e7d4f52` |

Also skip `ae6ada0` (test patch for the Stage-1/2 merge) and `e5432d7` (import cleanup after that patch) — they only make sense on top of the skipped refactor; main's twins carry their own test updates.

- **PICK list, in this exact order (oldest first):**

| # | SHA | Subject |
|---|---|---|
| 1 | `aae8b53` | docs(pillar1): extraction blockers + quality audit with live evidence |
| 2 | `752a604` | feat(llm): harden provider layer - timeouts, smart 429 backoff, rate-limit taxonomy |
| 3 | `e31febc` | feat(cv): robust deterministic skill-heading detection for prose/non-standard CVs |
| 4 | `2e1b23a` | feat(github): runtime-only manifest deps - drop dev-tooling noise (rule #28 safe) |
| 5 | `e517703` | test: update review-fix test for runtime-only manifest deps |
| 6 | `44b976f` | feat(cv): prompt-steer LLM extraction — categories/acronyms + skip vague duties |
| 7 | `dec740b` | feat(llm): OpenAI gpt-4o-mini as deterministic PRIMARY provider — ⚠ see edge case #1 |
| 8 | `58e0345` | docs(pillar1): deep honest extraction diagnosis (OpenAI primary, 5 CVs) |
| 9 | `c3ecb8e` | fix(pillar1): close 3 extraction gaps TDD — CV prose, GitHub language, LinkedIn leak |
| 10 | `0e6e1c2` | fix(pillar1): skills were polluted with prose/dates/headers + empty skill tiers |
| 11 | `6081bfc` | fix(pillar1): profile-page trust audit — GitHub noise, wiped username, polluted roles, fake education count |
| 12 | `37a330d` | fix(pillar1): de-fragment + dedup certifications/education + collapse spacing-variant skills |
| 13 | `9355f86` | fix(pillar1): general fuzzy + acronym dedup for certs/education/skills (all profiles) |

- Safety copies: tag `tp-final-safe` == branch tip `9355f86` == external worktree `D:\dev\job360-tprun`. Nothing can be lost as long as nobody deletes the tag. NEVER force-delete the tag or branch during this work.
- Conflicting files in a naive merge (expect the same hotspots during picks): `backend/src/api/routes/profile.py`, `backend/src/services/profile/{github_enricher,linkedin_parser,llm_provider,two_pass}.py`.
- Branch's test surface: `test_cv_prompt_steering.py` (NEW file), plus edits to `conftest.py`, `test_cv_schema.py`, `test_github_deps.py`, `test_linkedin_github.py`, `test_llm_provider.py`, `test_profile.py`, `test_profile_upload.py`, `test_profile_versions_endpoint.py`, `test_review_fixes.py`, `test_skill_tiering.py`, `test_two_pass.py`.
- Main-side churn to respect during conflict resolution: `e1cbb30` set **Cerebras-first LLM order for the ENGINES (E2/E3/E4)** and fixed Chroma/HF writable dirs; `b359a20` added profile-extraction summary logging.
- Hard rule #28 (CLAUDE.md) applies to every pick: ZERO hardcoded skill/keyword lists in `src/services/profile/` — extraction is ESCO-data + LLM only. Commit `2e1b23a` was explicitly written to be rule-#28-safe; verify the others don't reintroduce lists.

## Files that will change

All under `backend/`: `src/api/routes/profile.py`, `src/core/settings.py`, `src/services/profile/{cv_parser,dep_file_parser,github_enricher,linkedin_parser,llm_provider,preferences,schemas,skill_tiering,two_pass}.py`, the 11 test files listed above + new `tests/test_cv_prompt_steering.py`, plus `docs/PILLAR1_DEEP_DIAGNOSIS.md` and `docs/PILLAR1_EXTRACTION_AUDIT.md`.

---

### Task 0: Preflight

- [ ] **Step 0.1:**

```bash
git fetch origin main
git checkout -b rescue/pillar1-extraction origin/main
git status --porcelain          # must be empty
git tag -l "tp*"                # must show tp-final-safe — the recovery anchor
git log --oneline -1 tp-final-safe   # must be 9355f86
```

- [ ] **Step 0.2: Baseline green.** Run the suite ONCE before touching anything so you can distinguish inherited failures from ones you cause:

```bash
cd backend && python -m pytest -q -p no:randomly
```

Record the pass/fail/skip counts. (Windows note: a SECOND full-suite run in the same shell can crash psycopg with exit 139 — environmental, use a fresh shell per full run.)

### Task 1: Cherry-pick loop (repeat for each of the 13 PICK commits, in table order)

For commit N:

- [ ] **Step 1.N.a: Pick it:**

```bash
git cherry-pick <SHA>
```

- [ ] **Step 1.N.b: If it conflicts**, count the conflicted files (`git status --porcelain | grep -c "^UU"`):
  - **≤ 3 conflicted files:** resolve using these decision rules:
    1. **Structure from main, semantics from the branch.** Main's shape (Postgres shim imports, the 4-input route layout from `db2c518`, the merged Stage-1/2 extraction flow from `e7dbe67`) WINS on structure. The branch's ADDITIONS (new functions like fuzzy/acronym dedup helpers, new prompt text, new timeout/backoff logic, new schema fields) get carried into that shape.
    2. If the branch side edits a function that no longer exists on main (renamed/moved by the refactor), find where its responsibility lives now (`git log -S "<function name>" main -- backend/src/services/profile/` helps) and apply the change there.
    3. Never resolve by deleting a test — port it.
  - **> 3 conflicted files:** `git cherry-pick --abort`, then port MANUALLY: `git show <SHA>` to read the full diff, re-apply its intent file-by-file onto the current tree, commit with the original subject plus suffix ` (ported)`. This is expected mainly for #9–#13 (the pillar1 fixes) if they touch the refactored files heavily.

- [ ] **Step 1.N.c: Targeted tests after EVERY pick** (fast feedback beats one big bang at the end):

```bash
cd backend && python -m pytest tests/test_profile.py tests/test_two_pass.py tests/test_llm_provider.py tests/test_skill_tiering.py tests/test_profile_upload.py -q -p no:randomly
```

Expected: green after each pick. If red: fix before the next pick — a broken intermediate state makes later conflicts unreadable.

- [ ] **Step 1.N.d (picks #4, #6, #9–#13 only): rule #28 scan** — the pick must not (re)introduce hardcoded skill lists:

```bash
cd backend && grep -rn "_SKILL_TERMS\|_TO_SKILL\|SKILL_MAP\|_DENYLIST" src/services/profile/ || echo CLEAN
```

Expected: `CLEAN` (or only hits that existed at Step 0.2 baseline — compare).

### Task 2: Reconcile the provider-order question (after pick #7, before continuing)

Commit `dec740b` makes OpenAI `gpt-4o-mini` the PRIMARY provider **for CV/profile extraction**. Main's `e1cbb30` made Cerebras first **for the engines (E2/E3/E4 enrichment/semantic/judge)**. These are different call chains and BOTH should survive:

- [ ] **Step 2.1:** Open `backend/src/services/profile/llm_provider.py` after pick #7 and confirm the provider order it changes is the PROFILE-extraction chain.
- [ ] **Step 2.2:** Grep for where engines pick providers (`cd backend && grep -rn "cerebras" src/services/ --include=*.py -il`) and confirm engine-side ordering still matches main's `e1cbb30` behavior (Cerebras first). If pick #7 clobbered engine ordering, restore main's engine ordering while keeping OpenAI-primary for extraction.
- [ ] **Step 2.3:** `python -m pytest tests/test_llm_provider.py -v -p no:randomly` — the branch updated this file (+122 lines) to match the new order; after reconciliation it must pass.
- [ ] **Step 2.4: OPENAI_API_KEY handling:** pick #7 likely reads an `OPENAI_API_KEY`. Check `.env.example` documents it and `backend/scripts/check_env_example.py` passes; the provider must SKIP gracefully (fall through the chain) when the key is empty — verify a test covers that; if not, add one modeled on the existing empty-key tests in `test_llm_provider.py`.

### Task 3: Full verification

- [ ] **Step 3.1: Full suite, fresh shell, one run:**

```bash
cd backend && python -m pytest -q -p no:randomly
```

Expected: pass count ≥ Step 0.2 baseline + new tests (the branch adds ~37 tests in `test_cv_prompt_steering.py` alone); 0 failures.

- [ ] **Step 3.2: Live behavior check (this is extraction — tests can't prove quality).** Start the backend (`cd backend && python main.py`) + frontend (`cd frontend && npm run dev`), log in, upload a CV (a real one from `backend/data/` fixtures or `frontend/tests/synthetic/sample-cv.pdf` if present), and verify on the profile page:
  - skills contain NO prose fragments, dates, or section headers;
  - certifications/education lists have NO near-duplicate entries (e.g. "AWS Certified Solutions Architect" vs "AWS certified solutions architect – associate" collapsed);
  - GitHub-derived skills (if a GitHub username is set) contain no dev-tooling noise (eslint/prettier-type entries).
  Screenshot or note what you saw — this is the acceptance evidence.

- [ ] **Step 3.3: Frontend sanity:** `cd frontend && npm run type-check && npm run lint` (extraction changes are backend-only; this guards against accidental API-shape drift).

### Task 4: Ship

- [ ] **Step 4.1:**

```bash
git push -u origin rescue/pillar1-extraction
gh pr create --title "fix(pillar1): land the stranded extraction-quality work from worktree-tp-final" --body "Selective cherry-pick of the 13 novel commits from worktree-tp-final (tag tp-final-safe). Skipped 467d27d/041857a/ae6ada0/e5432d7/ff5b5d5/a0d3767 — main re-implemented those as 62f1505/e7dbe67/db2c518/e7d4f52. Provider-order reconciliation: OpenAI-primary for extraction, Cerebras-first preserved for engines. Live profile-page verification: <paste Step 3.2 evidence>."
```

- [ ] **Step 4.2: AFTER the PR merges (not before):** clean up the stranded copies — `git branch -d worktree-tp-final` (will refuse if unmerged — that refusal is a safety check, investigate before forcing), remove the external worktree (`git worktree remove D:/dev/job360-tprun`), KEEP the tag `tp-final-safe` permanently as the historical anchor.

---

## Edge cases a weaker model would miss

1. **The provider-order collision (Task 2) is the subtlest trap.** Two "make X the first LLM provider" decisions exist for two DIFFERENT call chains (extraction vs engines). Blindly taking either side of a conflict in `llm_provider.py` silently breaks the other chain — and tests may not catch engine-side ordering because engine flags default OFF.
2. **Don't `git merge worktree-tp-final`.** It conflicts on 9 hunks AND would re-apply refactors main already has, producing duplicate logic. The skip list is not optional.
3. **Cherry-picking pre-Postgres commits onto post-Postgres main:** the branch forked before `8d240f8`. If a pick's diff context mentions `aiosqlite` or sqlite paths, the resolution is main's Postgres-shim form (`from src.repositories import pg as aiosqlite` is the established alias pattern — code that imports `aiosqlite` via that alias is CORRECT, don't "fix" it).
4. **Rule #28 is a hard rule, not a preference.** If any pick reintroduces a hand-typed skill map (grep in Step 1.N.d), strip that part and route through ESCO/LLM — even if it makes a branch test fail; adapt the test to the data-driven path.
5. **Order matters within the pick list.** #5 (`e517703`) is the test for #4; #8 documents #7. Picking out of order creates avoidable conflicts.
6. **Docs picks (#1, #8) may conflict on nothing but still matter** — they contain the measured evidence (5-CV diagnosis) that justifies the code. Don't drop them as "just docs."
7. **`conftest.py` edits from the branch** (+18 lines) may collide with Postgres-era conftest changes (per-test schemas, sqlite3 shim registration). Resolution rule: keep ALL of main's Postgres fixtures; add the branch's new fixtures alongside.
8. **Pillar 2 is owner-reserved.** These picks touch `services/profile/` (Pillar 1 — allowed). If any resolution tempts you into `skill_matcher.py`, `scoring_dimensions.py`, `llm_matcher.py`, `retrieval.py`, `embeddings.py`, `vector_index.py`, or `job_enrichment.py` — STOP, that's out of bounds.
9. **Windows full-suite double-run crash (exit 139)** — environmental psycopg issue; fresh shell per full run; don't chase it.

## Acceptance criteria

- [ ] `git log --oneline origin/main..rescue/pillar1-extraction` shows ~13 commits matching the PICK table (subjects may carry "(ported)").
- [ ] Full backend suite: 0 failures; pass count strictly greater than the Step 0.2 baseline (new tests landed).
- [ ] `tests/test_cv_prompt_steering.py` exists and passes.
- [ ] rule-#28 grep (Step 1.N.d) reports CLEAN.
- [ ] Cerebras-first engine ordering verified intact (Task 2.2) AND extraction chain is OpenAI-primary (Task 2.1).
- [ ] Live check (Step 3.2): profile page shows deduped certs/education and prose-free skills — evidence pasted in the PR.
- [ ] Tag `tp-final-safe` still exists after all cleanup.

## STOP conditions

- More than 3 consecutive picks need full manual porting — the branch has drifted further than analyzed; stop and report with the list of what landed and what remains (partial rescue is still valuable — ship what's green).
- Any resolution would require changing Pillar-2 files beyond mechanical import fixes.
- The Step 0.2 baseline suite is NOT green before you start — fix or report that first; never rescue onto a broken base.
