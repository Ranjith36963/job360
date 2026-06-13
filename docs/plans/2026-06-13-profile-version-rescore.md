# Profile-Version Re-score & Refresh-Delta — Implementation Plan

> **For agentic workers:** TDD, task-by-task. Each task: failing test first, minimal
> impl, gate (`bash scripts/agent-gate.sh`), commit. Steps use `- [ ]`.
> Owner authorized lifting the Pillar-2 M2 reservation for THIS feature on 2026-06-13.

**Goal:** A user's stored job scores/verdicts are a snapshot taken under a specific
*profile version*. When the profile's content changes, that user's whole feed is
re-scored and re-judged against the new profile (Mode 1). When the profile is unchanged,
an ordinary search only scores the newly-fetched jobs and leaves existing scores and
verdicts untouched (Mode 2).

**Architecture:** Add `user_feed.profile_version` (the `user_profile_versions.id` that
produced the row). A profile *content* change (last two snapshots differ) triggers a
background `rescore_user_feed(user_id)` that clears the user's LLM verdicts, re-scores the
30-day catalog against the new profile (reusing the `jobs.py` recompute pattern), upserts
feed rows stamped with the new version, and re-runs the matcher (which now re-judges
because verdicts were cleared). Ordinary searches stamp the current version on the rows
they write; the matcher's existing `skip_existing` lock keeps Mode 2 from re-judging.

**Tech Stack:** Python 3.9, FastAPI, aiosqlite, existing `JobScorer` + `llm_matcher`.

**Invariants respected:** rules #10/#17 (no `user_id` on `jobs`/`job_enrichment` — version
stamp lives on `user_feed`), #18/#19/#20 (flags default off; per-user scorer needs both
kwargs — reuse run_search's exact wiring), #16 (lazy heavy imports), Python-3.9 (no
module-level `X | Y`). MATCHER re-judge only fires when `MATCHER_ENABLED` is on.

---

## File structure

- `migrations/0018_user_feed_profile_version.{up,down}.sql` — new column.
- `src/services/profile/storage.py` — add `current_profile_version_id`,
  `profile_content_changed_since_previous`.
- `src/services/feed.py` — `upsert_feed_row` gains `profile_version`; new
  `clear_scores_for_rescore` is NOT here (verdict clear lives in llm_matcher).
- `src/services/llm_matcher.py` — add `clear_user_verdicts(conn, user_id)`.
- `src/services/rescore.py` — NEW. `rescore_user_feed(user_id)` (Mode 1 core) +
  `score_catalog_row(scorer, row)` shared scoring helper.
- `src/repositories/database.py` — add read-only `get_catalog_jobs_for_rescore()`.
- `src/main.py` — feed-write loop stamps `profile_version`.
- `src/api/routes/profile.py` — after each save, trigger background re-score on real change.

---

## Task 1: Migration 0018 — `user_feed.profile_version`

**Files:** Create `migrations/0018_user_feed_profile_version.up.sql` + `.down.sql`;
Test: `tests/test_migrations.py` (or extend the existing migration round-trip test).

- [ ] Failing test: applying migrations leaves `user_feed` with a `profile_version` column
  (PRAGMA table_info); down removes it. Mirror how 0017 is tested.
- [ ] up.sql: `ALTER TABLE user_feed ADD COLUMN profile_version INTEGER;` (NULL allowed —
  legacy + as-yet-unscored rows are NULL). down.sql: documented SQLite limitation note +
  table rebuild OR the project's existing down convention for ADD COLUMN (match 0017.down).
- [ ] Gate, commit.

## Task 2: Profile version helpers (`storage.py`)

- [ ] `current_profile_version_id(user_id) -> Optional[int]`: `SELECT MAX(id) FROM
  user_profile_versions WHERE user_id=?`. None if no rows.
- [ ] `profile_content_changed_since_previous(user_id) -> bool`: load the two most recent
  snapshots' `(cv_data, preferences)`; return True if they differ, True if only one exists
  (first profile), False if none. (The latest row is the just-saved one.)
- [ ] Tests: two identical snapshots → False; two different → True; one → True; none → False.
- [ ] Gate, commit.

## Task 3: Stamp `profile_version` on feed writes (Mode 2 plumbing)

- [ ] `FeedService.upsert_feed_row(..., profile_version: Optional[int] = None)` writes the
  column (both INSERT and the ON CONFLICT UPDATE). Default None keeps old callers working.
- [ ] `main.py` feed-write loop: fetch the user's `current_profile_version_id` once before
  the loop, pass it to each `upsert_feed_row`.
- [ ] Tests: upsert with a version stamps the column; without leaves it NULL; dashboard read
  unaffected (existing feed tests still green).
- [ ] Gate, commit.

## Task 4: `clear_user_verdicts` (`llm_matcher.py`)

- [ ] `async def clear_user_verdicts(conn, user_id) -> int`: `UPDATE user_feed SET
  llm_fit_score=NULL, llm_verdict=NULL, llm_reason=NULL, llm_matched_at=NULL WHERE
  user_id=?`; return rowcount. Mirrors `save_verdict` style.
- [ ] Tests: save a verdict → `has_verdict` True → clear → `has_verdict` False, all four
  columns NULL, returns count.
- [ ] Gate, commit.

## Task 5: Catalog loader + shared scoring helper

- [ ] `database.get_catalog_jobs_for_rescore(limit=5000) -> list[dict]`: read-only SELECT of
  the scoring-relevant columns (`id,title,company,apply_url,source,date_found,location,
  description,salary_min,salary_max,posted_at,date_confidence`) from `jobs`, newest first,
  bounded by `limit`. (Catalog is already 30-day-purged.)
- [ ] `src/services/rescore.py::score_catalog_row(scorer, row) -> ScoreBreakdown`:
  reconstruct a `Job` from the row exactly as `jobs.py:519-531` does, return
  `scorer.score(job)`. (Single source of truth for "score a stored row".)
- [ ] Tests: helper reproduces a known score for a fixed row+profile; loader returns rows
  with the expected keys and respects `limit`.
- [ ] Gate, commit.

## Task 6: `rescore_user_feed` (Mode 1 core) — `src/services/rescore.py`

- [ ] `async def rescore_user_feed(user_id, db_path=None) -> dict`:
  1. `load_profile(user_id)`; if not complete → return `{"rescored":0,"reason":"no_profile"}`.
  2. Open its own `JobDatabase` (run_search pattern). `version = current_profile_version_id`.
  3. `await clear_user_verdicts(conn, user_id)` (so the matcher re-judges).
  4. Build the per-user `JobScorer` exactly as run_search does (search_config +
     user_preferences + enrichment_lookup; enrichment only if `ENRICHMENT_ENABLED`).
  5. Load catalog via `get_catalog_jobs_for_rescore`. For each row: `score_catalog_row`;
     if `breakdown.match_score > 0` OR the user already has a feed row for it →
     `upsert_feed_row(score, bucket, profile_version=version)`.
  6. If `MATCHER_ENABLED`: build lightweight Job objects (with `.id`, `.match_score`) for the
     scored set and call `_run_matcher_stage`-equivalent (reuse via import) to re-judge the
     shortlist.
  7. Return counts `{"rescored": n, "version": version}`. Log start/end with counts.
- [ ] Tests (MATCHER off): fixed catalog of 3 jobs + a senior profile → re-score writes
  feed rows stamped with the version, scores match `score_catalog_row`. Flip to a junior
  profile (new version) → scores change AND a previously-sub-floor job now appears
  (new-job-surfaces proof). Verdict-clear path tested separately with a saved verdict.
- [ ] Gate (CORE files touched → I run adversarial review), commit.

## Task 7: Trigger on real profile change (`api/routes/profile.py`)

- [ ] After each `save_profile(...)` call site (CV/prefs, linkedin, github): if
  `profile_content_changed_since_previous(user.id)` → schedule
  `asyncio.create_task(rescore_user_feed(user.id))` (mirror search.py:79; don't block the
  HTTP response). Wrap so a scheduling failure never breaks the save.
- [ ] Tests: route test — a content-changing POST schedules re-score (patch
  `rescore_user_feed`, assert awaited/created); a no-op save does NOT; the save itself still
  succeeds either way.
- [ ] Gate, commit.

## Task 8: Docs everywhere

- [ ] `CLAUDE.md`: phase summary entry + matcher section "re-judge" follow-on marked done +
  the two-mode model in plain words. `ARCHITECTURE.md`: data-flow note + `user_feed` schema
  row. `STATUS.md`: current phase. `docs/IMPLEMENTATION_LOG.md`: batch entry (migration 0018,
  rescore service, trigger, measured proof). `BACKLOG.md`: #8 → DONE. `MISSIONS.md`: M2 done.
- [ ] Memory `feedback_pillar2_hands_off.md`: note the owner lifted M2 for this build.
- [ ] No code → no gate needed for docs-only; commit.

## Task 9: Verify + evals (live — integrator/Opus)

- [ ] Backend verify: demo user + `User_info` CV. (a) Search with `MATCHER_ENABLED=true` →
  feed rows stamped with version, verdicts present. (b) Change the profile (different CV /
  preferences) → confirm via DB: verdicts cleared then re-judged, scores changed, version
  bumped on rows, ≥1 new job surfaced. (c) Re-open profile with NO change → confirm NO
  re-score (version stable, verdicts untouched).
- [ ] Eval: senior-profile vs junior-profile flip measurably re-ranks the same catalog
  (intern roles fall, senior roles rise). Journal the before/after top-5 + counts.
