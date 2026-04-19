# Pillar 3 — Batch 3.5.4 Test Cleanup Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`. Steps use checkbox syntax.

**Goal:** Drive the pytest baseline from `24f/578p/3s` to **0f / +24p / 3s** before Batch 4 launch prep. All 24 failures are test-only fixes per `docs/plans/batch-3.5.4-investigation.md` — no production code changes, no feature work, no new test coverage.

**Architecture:** Three Bucket-A subcategories (see investigation doc). One new `conftest.py` fixture for A1; test-constant edits for A2; per-test SearchConfig injection for A3.

**Tech stack:** existing — pytest, httpx, AsyncClient + ASGITransport, `migrations.runner`, `src.services.channels.crypto`.

---

## POST-BATCH-3.5.3 BASELINE

```
Command:    cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:     24 failed, 578 passed, 3 skipped in 252.11s
Log:        /tmp/pytest_baseline_3_5_4.log
Random-order (seed 12345): same 24 failures (no hidden flakes)
Log:        /tmp/pytest_random_3_5_4.log
```

**Acceptance gate (STEP 6):** 0 unexpected failures × 3 consecutive runs + 1 random-order run.

---

## Scope-out list (explicit — this is a cleanup batch)

- **No new feature tests.** "To cover the new fixture" is scope creep.
- **No new coverage.** Existing tests are re-enabled; no new assertions beyond what the original tests intended.
- **No production code changes.** Every fix is in `tests/` or `conftest.py`.
- **No `@pytest.mark.xfail(strict=True)`** — investigation found zero Bucket-C production bugs. All 24 fix cleanly.
- **No pytest-randomly in default CI.** Dev tool only — documented rationale below.
- **No test-file reorganisation.** Fix tests in place; deleting/renaming is scope creep.

---

## File-level plan

### Modified files

| Path | Change |
|---|---|
| `backend/tests/conftest.py` | Add `authenticated_async_context` fixture (tmp DB + migrations + register user + env patching) |
| `backend/tests/test_api.py` | 6 tests → use new fixture |
| `backend/tests/test_cron.py` | `PROJECT_ROOT = parent.parent.parent` (repo root, not `backend/`) |
| `backend/tests/test_setup.py` | same |
| `backend/tests/test_sources.py` | 7 parser tests → pass a `SearchConfig` with `job_titles` / `search_queries` |
| `backend/tests/test_time_buckets.py` | 3 `extract_matched_skills` tests → pass explicit `primary`/`secondary`/`tertiary` kwargs |
| `backend/pyproject.toml` | Add `pytest-randomly>=4.0` to `[project.optional-dependencies].dev` (dev only, NOT in CI invocation) |

### No created / deleted files.

---

## Phase A — Plan + investigation committed

**Commit:** `docs(pillar3): Batch 3.5.4 cleanup plan + investigation`

- [ ] Step A1: Write investigation + plan (done)
- [ ] Step A2: Commit

---

## Phase B — A2 path fixes (cron + setup)

Smallest blast radius — pure path-constant edit. Start here to prove the pattern works.

- [ ] **B-Step 1:** `backend/tests/test_cron.py:9` — `PROJECT_ROOT = Path(__file__).resolve().parent.parent` → `parent.parent.parent`.
- [ ] **B-Step 2:** `backend/tests/test_setup.py:8` — same change.
- [ ] **B-Step 3:** Run:
  ```
  pytest tests/test_cron.py tests/test_setup.py -q
  ```
  Expected: all 8 previously-failing + whatever passed before → all pass (modulo Windows-bash-skip semantics already in place).
- [ ] **B-Step 4:** Commit `test(cleanup): fix PROJECT_ROOT path in test_cron + test_setup (post phase-1 refactor)`

---

## Phase C — A3 SearchConfig injection (sources + time_buckets)

- [ ] **C-Step 1:** Define a test helper `_sc_with_ai_defaults()` at the top of `test_sources.py` that returns a `SearchConfig` with `job_titles=["AI Engineer", "ML Engineer"]`, `search_queries=["AI engineer"]`, `relevance_keywords=["python","machine learning"]`. Use the existing `_make_search_config` helper at L53 if already sufficient.
- [ ] **C-Step 2:** For each failing parser test, pass `search_config=sc` into the source constructor:
  - `test_reed_parses_response` (L93)
  - `test_adzuna_parses_response` (L122)
  - `test_jooble_parses_response`
  - `test_google_jobs_parses_response`
  - `test_workday_parses_response`
  - `test_careerjet_parses_response` (L1030)
  - `test_jobspy_parses_dataframe`
- [ ] **C-Step 3:** For `test_time_buckets.py` 3 matched_skills tests, pass `primary=["Python","PyTorch","LangChain"]`, `secondary=["Docker","TensorFlow"]`, `tertiary=["CI/CD","Kubernetes"]` explicitly.
- [ ] **C-Step 4:** Run:
  ```
  pytest tests/test_sources.py tests/test_time_buckets.py -q
  ```
  Expected: all 10 pass; all pre-existing green tests stay green.
- [ ] **C-Step 5:** Commit `test(cleanup): inject SearchConfig in source-parser + matched-skills tests`

---

## Phase D — A1 authenticated_client fixture + test_api.py

- [ ] **D-Step 1:** Add `authenticated_async_context` to `backend/tests/conftest.py`. Returns an `@asynccontextmanager` factory so async tests use it via `async with authenticated_async_context() as client:`. Internally:
  1. Create tmp DB via `JobDatabase.init_db()` + `runner.up()`.
  2. Patch `DB_PATH` on `core.settings`, `api.dependencies`, `api.auth_deps`, `api.routes.auth`, `api.routes.channels` (+ any other module holding a captured DB_PATH).
  3. Reset `dependencies._db` singleton.
  4. Set `SESSION_SECRET` + `crypto.set_test_key(Fernet.generate_key()...)`.
  5. Register a user via sync `TestClient` (simpler cookie capture) + extract the `job360_session` cookie.
  6. Wrap `app.router.lifespan_context = _noop_lifespan` so ASGITransport doesn't init the real lifespan.
  7. Yield an `AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)` from the async cm.
- [ ] **D-Step 2:** Update 6 test_api.py tests to use the fixture. Each test body becomes:
  ```python
  async def test_jobs_list_empty(authenticated_async_context):
      async with authenticated_async_context() as client:
          resp = await client.get("/api/jobs")
      assert resp.status_code == 200
      assert resp.json()["total"] == 0
  ```
- [ ] **D-Step 3:** Run:
  ```
  pytest tests/test_api.py -q
  ```
  Expected: 6 previously-failing + all originally-passing → all green.
- [ ] **D-Step 4:** Commit `test(cleanup): authenticated_async_context fixture + rehab test_api per-user tests`

---

## Phase E — pytest-randomly dev-dep (policy-only)

- [ ] **E-Step 1:** Add to `backend/pyproject.toml` `[project.optional-dependencies].dev`:
  ```toml
  "pytest-randomly>=4.0",
  ```
- [ ] **E-Step 2:** Do NOT add `-p randomly` to any CI invocation. Document the rationale in CLAUDE.md or the plan doc (below): use during cleanup batches to surface hidden flakes, not in default CI until a 0f baseline holds across N seeds.
- [ ] **E-Step 3:** Commit `chore(pyproject): add pytest-randomly as dev dep (cleanup tool, not CI)`

---

## STEP 6 — Verify before completion (critical, no reviewer)

- [ ] **Three-run stability:**
  ```
  for i in 1 2 3; do
    python -m pytest tests/ --ignore=tests/test_main.py -q 2>&1 | tail -1
  done
  ```
  All three lines must show `0 failed`.

- [ ] **Random-order run** (`-p randomly --randomly-seed=last`): 0 failed.

- [ ] **Completion report artefacts:**
  1. Investigation table (from this doc)
  2. Bucket A fixes file:line
  3. Bucket B: none
  4. Bucket C: none
  5. Bucket D: none
  6. Three-run proof
  7. Random-order proof
  8. Pytest delta (before/after/xfail=0/skip=3)

---

## STEP 7 — Handoff

- [ ] `git push -u origin pillar3/batch-3.5.4`
- [ ] Report final SHA. STOP.

---

## Self-review

**Spec coverage.**
- STEP 0 baseline — locked at 24f/578p/3s + random-order seed 12345 proved zero additional flakes.
- STEP 1 investigation gate — `docs/plans/batch-3.5.4-investigation.md` with per-test classification.
- STEP 2 Bucket A — Phases B (A2), C (A3), D (A1) cover all 24.
- STEP 3 Bucket B — zero flakes found; skip phase.
- STEP 4 pytest-randomly — Phase E adds dev-only dep.
- STEP 5 "no new tests" — honoured by design.
- STEP 6 verification — three-run + random-order + artefact list.
- STEP 7 push — at the end.

**Placeholder scan.** Line numbers for test edits are inferred from grep output (e.g. `L93`, `L122`); confirmed during implementation.

**Scope honesty.** Zero production code changes. Zero new feature tests. The deliverable is "0f green baseline", per the batch prompt.

---

_Last updated: 2026-04-19_
