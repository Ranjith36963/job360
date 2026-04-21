# Pillar 2 Progress Log

Mirrors the Pillar 1 format. One section per batch in the execution order fixed
by `docs/pillar2_implementation_plan.md` §7 (2.2 → 2.1 → 2.3 → 2.4 → 2.5 → 2.9 →
2.6 → 2.7 → 2.8 → 2.10).

Generator worktree: `C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\generator` on branch `worktree-generator` rebased onto local `main @ cdf6aaf`.

---

## Environment note — pre-existing test hang on `tests/test_sources.py`

Under Python 3.13 on Windows the 81 tests in `tests/test_sources.py` hang
indefinitely inside asyncio's Windows IOCP selector (`_overlapped.GetQueuedCompletionStatus`)
even with `pytest-timeout`. This predates Pillar 2 work (the same hang reproduces
against baseline `1730bf6`, and was flagged in `memory/project_test_http_leak.md`
under a slightly different guise — "JobSpy hits live Indeed"). It does **not**
block Pillar 2 batches because:

1. Every Pillar 2 batch touches the scoring / enrichment / retrieval layers,
   none of which are imported by `test_sources.py`.
2. The source tests are mocked with `aioresponses` — the hang is in the Python
   3.13 × Windows IOCP × aiohttp-shutdown interaction, not in any production
   code path.

Going forward each batch runs:

```bash
# Broad clean baseline (68x passing, no failures)
python -m pytest tests/ \
  --ignore=tests/test_main.py \
  --ignore=tests/test_sources.py \
  -p no:randomly --timeout=10

# Scoped verification for scoring-adjacent batches (2.2, 2.3, 2.9)
python -m pytest tests/test_scorer.py tests/test_profile.py -p no:randomly
```

`test_main.py` is also excluded per the established Pillar-3 pattern (live HTTP
leak in JobSpy against Indeed/Glassdoor).

---

## Batch 2.2 — Gate-pass scoring — MERGED

**Merged:** `aa13554` on 2026-04-21

**Plan coverage:**
- Plan §4 Batch 2.2 — gate-pass scoring
- Report item(s): #2 (gate-pass eliminates false positives)

**Touches:**
- `backend/src/core/settings.py`: +11 lines (added `MIN_TITLE_GATE` and
  `MIN_SKILL_GATE` as `float(os.getenv(...))` constants defaulting to `0.15`,
  with a comment block explaining the gate semantics).
- `backend/src/services/skill_matcher.py`: +18 lines
  - widened the `src.core.settings` import to pull in the two new constants,
  - added module-level `_gate_suppressed_score(title_pts, skill_pts) -> int | None`
    which returns `max(10, int((title_pts+skill_pts)*0.25))` when either gate
    fails, else `None`,
  - called it from `score_job()` (legacy module-level path) and from
    `JobScorer.score()` (dynamic path) before the linear accumulation of
    location / recency / penalties, so gate-fail jobs cannot be inflated by
    those components.
- `backend/tests/test_scorer.py`: +192 / -24 lines
  - added `TestGatePass` class with 12 gate-aware tests (8 on `JobScorer`,
    4 on the module-level `score_job` path),
  - rewrote 7 pre-existing tests (`test_title_match_contributes_points`,
    `test_location_match_contributes_points`, `test_remote_location_gets_points`,
    `test_more_skills_higher_score`, `test_recency_today_gets_full_points`,
    `test_us_ai_job_scores_lower_than_uk`, `test_score_job_uses_recency_for_job_helper`)
    to use `JobScorer` with a gate-clearing `SearchConfig`. Their original
    invariants (UK > US, today > old, many-skills > few-skills, honest >
    fabricated) are preserved but observable only on the non-suppressed
    linear path — which is Batch 2.2's explicit intent.

**Tests added:** `tests/test_scorer.py` TestGatePass (+12 tests).

**Test delta (scoped to scoring + profile):** 110p → 122p (0 failures, 0 skips).

**Test delta (broad, minus `test_main` / `test_sources`):** 633p/3s → 645p/3s
(+12). No pre-existing test regressed.

**API + IDOR tests (separate run):** 37p / 0f / 0s (unchanged by this batch).

**Deferred from this batch:**
- None. The batch landed exactly as the plan's Touches / Test surface sections
  specified. User-configurable gates (per-profile tuning) are correctly
  deferred to Batch 2.9 per the plan's "Out of scope".

**Post-merge notes:**
- The 7 existing-test rewrites are a direct consequence of the gate's
  semantic intent: without a profile, location/recency alone can no longer
  distinguish jobs, and the old tests encoded the pre-gate bug. Each rewrite
  preserves the original invariant on the JobScorer path where the gate
  passes. This is not scope creep — it is the test-surface evolution named
  in the plan ("Test surface: tests/test_scorer.py — new class TestGatePass").
- `test_score_job_with_patched_keywords_can_pass_gate` uses `monkeypatch` to
  inject non-empty `JOB_TITLES` / `PRIMARY_SKILLS` so the module-level path
  can demonstrate gate-pass. This is the only path inside `score_job()` that
  is observable as non-suppressed under the current (empty-defaults) keyword
  policy.
- Both constants are env-overridable (`MIN_TITLE_GATE=0.10` / `MIN_SKILL_GATE=0.20`
  etc.) so ops can retune without code edits if the 15 % default ever proves
  too aggressive.
- The per-component gate thresholds are absolute, not fractions of the
  user's weighted max. If a future batch introduces user-configurable weights
  (e.g. raising `TITLE_WEIGHT` to 60) the 0.15 fraction would scale with it,
  preserving the intended "15 % of component max" meaning.

---

## Batch 2.1 — Date confidence correction for signal-less sources — MERGED

**Merged:** <pending commit> on 2026-04-21

**Plan coverage:**
- Plan §4 Batch 2.1
- Report item(s): #1 (date accuracy — narrowed scope per 2026-04-20 verification)

**Touches:**
- `backend/src/sources/scrapers/linkedin.py:69`: `date_confidence="low"` → `"fabricated"`
- `backend/src/sources/ats/workable.py:48`: `date_confidence="low"` → `"fabricated"`
- `backend/src/sources/ats/personio.py:85`: `date_confidence="low"` → `"fabricated"`
- `backend/src/sources/ats/pinpoint.py:56`: `date_confidence="low"` → `"fabricated"`
- `backend/src/services/skill_matcher.py`: **no code change** — the
  `recency_score_for_job()` "fabricated" branch already returns 0 (shipped
  in Pillar 3 Batch 1). A regression test was added instead (plan §4 Batch
  2.1 — "Add a regression test if missing").

**Tests added:**
- **New file** `backend/tests/test_source_date_confidence_labels.py` (+8 tests):
  - 4 parametrized tests asserting linkedin/workable/personio/pinpoint emit
    `date_confidence="fabricated"` — and NOT `"low"` — as literal string
    assignments in source files (static grep, no HTTP).
  - 3 parametrized tests asserting nhs_jobs/jooble/greenhouse continue to
    emit `"low"` (the plan's "wrong-field" category, already correct).
  - 1 wiring test asserting `recency_score_for_job()` returns 0 when
    `date_confidence="fabricated"` — the mechanism that turns the label
    change into a visible score penalty downstream.

**Test delta (scoped: scorer + profile + date schema + new labels file):** 135p → 143p (+8 new).

**Test delta (broad, minus `test_main` + `test_sources`):** 682p/3s → 690p/3s (+8).

**Deferred from this batch:**
- Removing the legacy `date_found` column entirely — kept per plan's
  "Out of scope" (defer until frontend/CLI audit).
- The 5-column schema evolution — already shipped in Pillar 3 Batch 1.
- Ghost-detection machine — already shipped in Pillar 3 Batch 1.

**Post-merge notes:**
- Static-grep tests (`test_source_date_confidence_labels.py`) were preferred
  over parametrizing `tests/test_sources.py` because the latter hits the
  pre-existing Windows × Py3.13 × aioresponses IOCP hang documented at the
  top of this file. The static check polices the *label* a source emits,
  which is the correct scope for Batch 2.1 — we are not testing source
  behaviour, only that the instrumented literal is correct.
- These 4 sources still stamp `date_found=datetime.now(...)` which is
  accurate as a *first-seen* timestamp. The `recency_score_for_job` helper
  gates on `date_confidence="fabricated"` before consulting `date_found`,
  so the timestamp itself doesn't leak into scoring — the fabricated flag
  short-circuits to 0. Dropping the `date_found` column entirely is a
  later cleanup (see "Deferred").

---

## Batch 2.3 — Static skill synonym table — MERGED

**Merged:** <pending commit> on 2026-04-21

**Plan coverage:**
- Plan §4 Batch 2.3
- Report item(s): #3 (skill synonym table) + partial-#16 (ESCO activation
  deferred to Batch 2.6)

**Touches:**
- **New file** `backend/src/core/skill_synonyms.py`: +493-entry canonical-form
  dictionary covering tech (languages, frameworks, cloud, DevOps, AI/ML,
  data engineering, mobile, testing, security) and UK-professional domains
  (medical/NHS, finance, legal, HR/PM, marketing) plus general acronyms.
  Exposes `canonicalize_skill(raw) -> str` (LRU-cached),
  `aliases_for(skill) -> tuple[str, ...]` (reverse lookup for the scorer),
  and `total_entries() -> int` (test guard against silent shrinkage).
- `backend/src/services/skill_matcher.py`: +15 lines
  - imports `aliases_for`,
  - adds `_text_contains_skill(text, skill)` which searches the canonical
    form and every known alias, still word-boundary aware,
  - swaps `_text_contains` → `_text_contains_skill` in the 3 skill-matching
    loops (module-level `_skill_score` + `JobScorer._skill_score`).
- `backend/src/services/profile/keyword_generator.py`: +16 lines
  - imports `canonicalize_skill`,
  - adds `_canonicalize_skill_list(skills)` preserving first-occurrence order
    and deduplicating under canonical forms,
  - wraps the primary/secondary/tertiary skill lists in the final
    `SearchConfig(...)` constructor so skills exit the profile pipeline in
    canonical form.

**Tests added:**
- **New file** `backend/tests/test_skill_synonyms.py` (+64 tests):
  - 47 parametrized canonicalization tests (29 tech + 18 UK professional),
  - 6 normalisation-semantics tests (whitespace, empty, unknown,
    idempotence),
  - 3 `aliases_for()` reverse-lookup tests,
  - 4 skill_matcher integration tests (alias text search, word boundary,
    scoring invariance across alias vs canonical job text, profile-side
    alias),
  - 3 keyword_generator integration tests (alias dedup, unknown preservation,
    order),
  - 1 table-size floor guard.

**Tests updated:** 3 in `test_profile.py`, 4 in `test_skill_tiering.py`, and
5 in `test_linkedin_github.py` — all adjusted their string assertions from
case-preserved (`"Python"`) to canonical (`"python"`), reflecting the plan's
intent that skills exit the profile pipeline in canonical form. One
assertion also tracks an alias collapse (`"Spark"` → `"apache spark"`).

**Test delta (broad, minus `test_main` + `test_sources`):** 690p/3s → 754p/3s (+64).

**Deferred from this batch:**
- ESCO embedding scaffold activation — correctly held for Batch 2.6 per
  plan's "Out of scope".
- Embedding-based skill similarity for the long tail — Batch 2.6.
- Auto-growth of the table from usage telemetry — out of scope (no
  telemetry infrastructure yet).

**Post-merge notes:**
- Table size: 493 entries, within the plan's ~500 target. A
  `total_entries() >= 400` floor-guard test catches any future shrinkage.
- Why lower-case canonical forms and not preserve case? Because word-boundary
  regex matching is already case-insensitive via `re.IGNORECASE`; the
  canonical-form string only needs to be consistent for the dedup logic in
  `_canonicalize_skill_list` to work. Lower-case is the most forgiving
  choice for string comparison.
- The `_text_contains_skill` helper is a pure superset of `_text_contains`:
  when called with a skill that has no aliases, `aliases_for(skill)` returns
  just `(canonical_form,)` which is one regex search — identical perf to
  the legacy path. Skills WITH aliases pay O(n_aliases) regex searches per
  skill, bounded by the max alias count on any single canonical form (about
  4 for the current table).
- Behavioural visibility: a user with `"k8s"` in their CV now matches jobs
  describing `"kubernetes"` and vice versa. This is the primary user-facing
  win — more real hits per search.
