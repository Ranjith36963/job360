# PLAN 1 — Fix the red CI gate + merge the live synthetic smoke test

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rank: 1 of 5 — do this FIRST.**
Why first: GitHub Actions is enabled and running, but the per-commit gate (`ci.yml`) fails at the ruff lint step on every recent push to `main`. A red gate means every merge (including plans 2–5) lands unverified, and nobody notices new breakage because red is the normal color. This plan makes the gate trustworthy again and merges an already-written live-production smoke test that is sitting unmerged on a branch.

**Goal:** `ci.yml` green on `main`; `ci-offline.yml` either green or retired; `synthetic-live.yml` (from branch `worktree-feat-live-smoke`) merged and running.

**Architecture:** No production code changes expected — this is lint fixes, workflow YAML fixes, and one clean branch merge.

**Tech Stack:** GitHub Actions, ruff, pytest, Playwright (already-written script).

---

## Verified facts (do not re-derive; checked 2026-07-07)

- Repo: `Ranjith36963/job360`. Actions IS enabled (`gh api repos/Ranjith36963/job360/actions/permissions` → `"enabled":true`).
- 4 workflows on `main`: `ci.yml` (push/PR — backend job with Postgres 16 + Redis 7 service containers, runs `ruff check` then pytest then non-blocking mypy; frontend job passes), `ci-offline.yml` (push/PR/daily — runs full pytest WITHOUT a Postgres service container), `live-e2e.yml` (nightly `pytest -m live`), `uptime.yml` (10-min ping — the only green one).
- `ci.yml` backend job currently fails at step "Lint (ruff)", exit 1, before tests run. Frontend job passes.
- `ci-offline.yml` was written BEFORE the SQLite→Postgres migration. The backend test suite now requires a real Postgres (`backend/src/repositories/pg.py`, default DSN `postgresql://job360:job360dev@localhost:5433/job360`). Its two backend jobs (`offline-suite`, `loop2-e2e-spotlight`) have no Postgres service container — that is almost certainly why its scheduled runs fail. Its third job (`frontend-e2e`, Playwright UI specs) is unique — `ci.yml` does NOT run Playwright.
- Branch `worktree-feat-live-smoke` is exactly 1 commit ahead of `main` (`0338864`), adds 3 files (`.github/workflows/synthetic-live.yml`, `frontend/tests/synthetic/live-smoke.mjs`, `frontend/tests/synthetic/sample-cv.pdf`), **0 conflicts** — branch point equals current main tip `fc8ce20`.
- The smoke script's defaults: `SMOKE_BASE_URL` → `https://frontend-production-c608f.up.railway.app`, `SMOKE_API_URL` → `https://backend-production-80e8e.up.railway.app`. Public corners (landing/login/register/privacy/terms/contact) run with no secrets. Authed corners need repo secret `SMOKE_SESSION` (a valid `job360_session` cookie value from a verified synthetic account) — that part is a human step.

## Files to touch

- Modify: whatever `backend/` files ruff flags (unknown until run — likely `backend/src/**/*.py`, `backend/tests/**/*.py`)
- Modify: `.github/workflows/ci-offline.yml`
- Merge in (no edits): `.github/workflows/synthetic-live.yml`, `frontend/tests/synthetic/live-smoke.mjs`, `frontend/tests/synthetic/sample-cv.pdf`
- Possibly modify: `.github/workflows/live-e2e.yml` (triage outcome dependent)

## Environment prerequisites (all commands from repo root unless stated)

- Local Postgres for the test suite must be reachable at `postgresql://job360:job360dev@localhost:5433/job360` (or set `DATABASE_URL`). If `python -m pytest` errors with connection-refused, start the dev Postgres first (check `docker ps`; the project's dev DB runs on port 5433).
- Windows quirk: running the FULL backend suite twice back-to-back in one shell can crash psycopg natively (exit 139). This is environmental, not a real failure. Run the full suite ONCE per shell; if you see exit 139 on a second run, open a fresh shell and re-run once.

---

### Task 0: Preflight

- [ ] **Step 0.1: Branch + clean tree**

```bash
git fetch origin main
git checkout -b fix/ci-red-gate origin/main
git status --porcelain   # must be empty
```

Expected: new branch from origin/main, empty status. If status is not empty or origin/main has diverged from what you expect, STOP and report — do not stash or rebase silently.

### Task 1: Reproduce and fix the ruff failure

- [ ] **Step 1.1: See exactly what CI runs.** Open `.github/workflows/ci.yml`, find the backend job's "Lint (ruff)" step, and copy its exact command and working directory. (Expected shape: `ruff check .` or `ruff check src/ tests/` with `working-directory: backend`.)

- [ ] **Step 1.2: Reproduce locally with the same command:**

```bash
cd backend
python -m ruff check .    # substitute the exact command from Step 1.1
```

Expected: FAILS with a list of violations. Copy the full list into your notes. If it passes locally, your ruff version differs from CI — run `python -m ruff --version`, compare against the version CI installs (it comes from `pip install -e ".[dev]"`, so check the `ruff` pin in `backend/pyproject.toml` dev extras), and `pip install -e ".[dev]"` to sync before proceeding.

- [ ] **Step 1.3: Auto-fix the safe ones:**

```bash
python -m ruff check . --fix
git diff
```

**Review EVERY change `--fix` made before accepting it.** See the edge cases section — two ruff auto-fixes are dangerous in this repo.

- [ ] **Step 1.4: Fix the remaining violations by hand.** Decision rules, in order of preference:
  1. Fix the code (rename unused variable to `_`, remove dead code, split long line).
  2. If a violation is a deliberate pattern (e.g., an import inside a function — see edge cases), add a targeted `# noqa: <RULE>` on that one line with a short trailing comment saying why.
  3. NEVER blanket-disable a rule in `pyproject.toml` and NEVER add file-level `# ruff: noqa`. If you believe a rule itself is wrong for this repo, STOP and report instead.

- [ ] **Step 1.5: Verify lint is clean and tests still pass:**

```bash
python -m ruff check .        # expected: "All checks passed!"
python -m pytest -q -p no:randomly
```

Expected: 0 ruff violations; pytest ~1,600+ passed, 0 failed (exact count varies; 3 skips on Windows are normal).

- [ ] **Step 1.6: Commit**

```bash
git add -A
git commit -m "fix(ci): clear ruff violations blocking the backend lint gate"
```

### Task 2: Fix or retire `ci-offline.yml`

- [ ] **Step 2.1: Confirm the diagnosis.** Fetch the log of the latest failed run:

```bash
gh run list --workflow ci-offline.yml --limit 3
gh run view <latest-failed-run-id> --log-failed | head -80
```

Expected finding: backend jobs fail with a Postgres connection error (connection refused / could not translate host). If instead the failure is something else entirely (e.g., a dependency install error), fix THAT and skip Step 2.2.

- [ ] **Step 2.2: Give the two backend jobs a database.** Open `.github/workflows/ci.yml` and copy — verbatim — the backend job's `services:` block AND any `env:` entries that set `DATABASE_URL`/`REDIS_URL` for the test steps. Paste both into `.github/workflows/ci-offline.yml`, into BOTH the `offline-suite` job and the `loop2-e2e-spotlight` job (the `frontend-e2e` job needs no database). Do not invent your own service config — `ci.yml`'s is known-good for this suite.

- [ ] **Step 2.3: Reduce redundancy.** `ci.yml` already runs the full backend suite on every push/PR. Edit `ci-offline.yml`'s `on:` block to remove `push:` and `pull_request:`, keeping only:

```yaml
on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch: {}
```

This keeps the daily deep signal (including Playwright UI specs, which ci.yml lacks) without running the same pytest suite twice on every push.

- [ ] **Step 2.4: Commit**

```bash
git add .github/workflows/ci-offline.yml
git commit -m "fix(ci): give ci-offline backend jobs a Postgres service; run on schedule only"
```

### Task 3: Triage `live-e2e.yml` (bounded — max 30 minutes)

- [ ] **Step 3.1: Read the failure:**

```bash
gh run list --workflow live-e2e.yml --limit 3
gh run view <latest-failed-run-id> --log-failed | head -100
```

Decision rules:
- **Import/collection error or infra error** (module not found, DB connection, fixture error): fix it — it's a real regression.
- **Individual live-source assertion failures** (a job board changed its HTML/API): do NOT chase them in this plan. These tests hit real external sites and are flaky by design. Leave the workflow as-is and note the failing sources in the PR description.
- If unsure which case you're in, treat it as the second case and note it.

- [ ] **Step 3.2: Commit if you changed anything**

```bash
git add -A && git commit -m "fix(ci): repair live-e2e collection error"   # only if applicable
```

### Task 4: Merge the live synthetic smoke test

- [ ] **Step 4.1: Merge the branch (verified 0 conflicts):**

```bash
git merge worktree-feat-live-smoke --no-edit
```

Expected: clean merge adding exactly 3 files: `.github/workflows/synthetic-live.yml`, `frontend/tests/synthetic/live-smoke.mjs`, `frontend/tests/synthetic/sample-cv.pdf`. If there ARE conflicts (main moved since this plan was written), STOP and report — do not resolve blind.

- [ ] **Step 4.2: Set the repo variables the workflow reads** (public corners work without secrets):

```bash
gh variable set SMOKE_BASE_URL --body "https://frontend-production-c608f.up.railway.app"
gh variable set SMOKE_API_URL --body "https://backend-production-80e8e.up.railway.app"
```

- [ ] **Step 4.3: HUMAN STEP (note in PR, do not block on it):** the `SMOKE_SESSION` secret needs a `job360_session` cookie from a verified synthetic prod account (log in on prod, copy the cookie from browser devtools, then `gh secret set SMOKE_SESSION`). Until set, authed corners are skipped by design — public corners still run.

### Task 5: Ship and verify green

- [ ] **Step 5.1: Push and open the PR:**

```bash
git push -u origin fix/ci-red-gate
gh pr create --title "fix(ci): green gate — ruff clean, ci-offline Postgres service, merge synthetic-live smoke" --body "Fixes the red ci.yml lint gate, repairs ci-offline's missing Postgres service, merges worktree-feat-live-smoke. live-e2e triage notes: <fill in from Task 3>."
```

- [ ] **Step 5.2: Watch the PR checks:**

```bash
gh pr checks --watch
```

Expected: `ci.yml` backend AND frontend jobs green on this PR.

- [ ] **Step 5.3: After merge, manually fire the new/repaired scheduled workflows once:**

```bash
gh workflow run synthetic-live.yml
gh workflow run ci-offline.yml
# wait a few minutes, then:
gh run list --limit 5
```

Expected: synthetic-live green (public corners), ci-offline green.

---

## Edge cases a weaker model would miss

1. **ruff `--fix` can delete "unused" imports that are load-bearing.** This repo lazy-imports heavy deps (`apprise`, `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`) INSIDE functions on purpose (CLAUDE.md rules #11/#16). Never "fix" an import-related violation by moving an import to module top level, and never let `--fix` remove an import that a conditional/lazy path uses. Review the `git diff` after `--fix` line by line.
2. **ruff version drift**: CI installs ruff via `backend/pyproject.toml` dev extras. If your local ruff is newer/older, you'll fix a different violation set than CI sees. Sync versions before fixing (Step 1.2).
3. **`ci-offline.yml` is not "offline" anymore.** The name predates the Postgres migration. Don't try to make the suite pass without a database (e.g., by skipping tests) — the correct fix is adding the service container. Do not rename the file either (workflow history/badges reference it); a comment in the YAML noting the name is historical is fine.
4. **Two different `main.py` files exist**: `backend/src/api/main.py` (FastAPI app) and `backend/src/main.py` (pipeline orchestrator). If ruff flags either, make sure you're editing the file it actually pointed at.
5. **Don't fix live-e2e by deleting/skipping live tests.** They're deselected from the offline suite already (`-m live` marker). Their nightly red is a signal for the owner about real upstream breakage, not noise to silence.
6. **The smoke workflow only schedules from the default branch.** Merging the PR is what activates `synthetic-live.yml`'s cron — testing it on the feature branch requires `workflow_dispatch` AFTER merge, not before.
7. **Windows double-run psycopg crash** (exit 139 on the second consecutive full-suite run in one shell) is environmental. Don't debug it; fresh shell, run once.

## Acceptance criteria (verify each)

- [ ] `cd backend && python -m ruff check .` → "All checks passed!" locally.
- [ ] `cd backend && python -m pytest -q -p no:randomly` → 0 failures locally (one run).
- [ ] PR checks: `ci.yml` fully green on the PR (`gh pr checks`).
- [ ] After merge: `gh run list --workflow ci.yml --limit 1` on main → success.
- [ ] After merge: `gh workflow run ci-offline.yml` → green run.
- [ ] After merge: `gh api repos/Ranjith36963/job360/actions/workflows` lists `synthetic-live.yml`; `gh workflow run synthetic-live.yml` → green (public corners).
- [ ] `ci-offline.yml` no longer triggers on push/PR (check its `on:` block on main).

## STOP conditions (report instead of proceeding)

- origin/main has moved such that `worktree-feat-live-smoke` no longer merges clean.
- A ruff violation can only be fixed by restructuring scoring/search-engine code under `backend/src/services/` (Pillar 2 is owner-reserved — report, don't touch `skill_matcher.py`, `scoring_dimensions.py`, `retrieval.py`, `embeddings.py`, `vector_index.py`, `llm_matcher.py`, `job_enrichment.py` beyond mechanical one-line lint fixes).
- The pytest suite has real (non-environmental) failures unrelated to your changes.
