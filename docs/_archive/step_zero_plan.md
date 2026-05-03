# Step 0 Pre-Flight — Ralph-Loop-Driven Execution Plan

> **Generator handoff.** This is the repo-visible mirror of the plan approved in plan mode on 2026-04-23.
> Source plan artifact: `C:\Users\Ranjith\.claude\plans\now-i-ve-got-another-crispy-island.md` (local-only, not committed to repo).
> Sibling: `docs/ExecutionOrder.md` (full Steps 0–5 roadmap).

---

## Context

**Why this change is being made.** Job360 has shipped Pillars 1 / 2 / 3 (29 merged batches, `main @ 5fb3c07`, 600p/0f/3s pytest). But a detailed 8-agent audit surfaced ~50 pre-launch gaps that must be closed *before* any later step (engine→API seam surfacing, UI polish, ops hardening, launch readiness) can be verified. These gaps cluster around 4 themes: **(a)** environment configurability (35 env vars exist; `.env.example` undercounted), **(b)** fresh-clone reproducibility (one real `os.makedirs` crash + Windows script parity), **(c)** observability (no `run_uuid`, no `LOG_LEVEL` plumbing, no DB inspection script), and **(d)** onboarding (missing `CONTRIBUTING.md`, per-subproject READMEs, docs index, troubleshooting).

**What prompted it.** The user parked Pillar 3 Batch 4 because its deliverables (freemium caps, source cull, pricing) can't be calibrated without dogfood data from a working end-to-end tool. Step 0 is the foundation that makes dogfood possible: a reproducible bootstrap run, a verifiable baseline, a Windows-parity developer environment, and enough observability for later steps to produce trustworthy data.

**Intended outcome.** After Step 0 completes:
- `python scripts/bootstrap_dev.py` runs cleanly on a fresh clone (Windows + Unix) and proves the end-to-end happy path
- `cd backend && python -m pytest -q` records a green baseline with deterministic timestamps + captured seed
- `.env.example` documents all 35 env vars with grouping + inline signup links
- Pre-commit gate is active; new contributors can't bypass linting
- Docs onboard a new dev in under 30 minutes on either OS
- Every later step (engine→API seam, UI polish, ops) can be verified against a stable baseline

---

## Strategic context — why Ralph Loop for this step

The audit's technical recommendation was "skip Ralph Loop for Step 0; use parallel agents instead" because Step 0 is 12 mostly-independent edits with no sequential dependencies between them.

**The user explicitly overrides this.** The direction is to run Ralph Loop continuously until Step 0 is completely fixed. That framing is actually reasonable once you reframe the loop: Ralph Loop is the **safety harness**, not the parallelism engine. Each iteration of the loop dispatches parallel subagents, runs the verification gate, checks the completion sentinel, and only continues if Step 0 is not yet green. This gives us both parallelism inside an iteration *and* continuous retry-on-failure across iterations — which is exactly what "run until fixed" means.

**Ralph Loop's role here:** outer supervision (retry failing sub-tasks, re-run verification, halt on a promise string).
**Parallel subagents' role:** inner execution (4–6 independent `/implement` calls per iteration).

---

## Scope — what Step 0 covers

### Tier A — Must-fix (12 items, blocking)

1. **Env vars:** complete `.env.example` with all 35 vars, grouped by category (auth / frontend / LLM / job-boards / enrichment / notifications / scoring / salary / flags & ops), inline signup URLs for LLM + keyed APIs
2. **Fresh-clone DB crash:** add `os.makedirs(os.path.dirname(self._path), exist_ok=True)` before `aiosqlite.connect(self._path)` in `backend/src/repositories/database.py:18–19`
3. **Pre-commit activation:** run `pre-commit install` (config at `.pre-commit-config.yaml` already present — gate just not active)
4. **Line-ending safety:** create `.gitattributes` with `*.sh text eol=lf` (prevents CRLF corruption of bash scripts on Windows clone)
5. **Windows setup parity:** create `setup.bat` (PowerShell-friendly) mirroring `setup.sh` — Python 3.9+ check, venv creation with `.venv\Scripts\activate` guidance, data-dir creation, `.env` template copy
6. **Bootstrap script:** `backend/scripts/bootstrap_dev.py` with verified API paths (`/api/auth/register` returns 201 + auto-login cookie, multipart `preferences` as JSON-string, `/search` is async + polls `/search/{run_id}/status`), cookie jar persisted across calls, `fpdf2` inline CV generation (reuse `_make_plain_cv_pdf` pattern from `tests/test_linkedin_github.py`)
7. **Test determinism:** pin `_TEST_NOW` module-level constant in `backend/tests/conftest.py`; replace `datetime.now(timezone.utc).isoformat()` in fixtures with the constant
8. **`run_uuid` correlation:** migration `0010_run_log_observability.sql` adding `run_uuid TEXT UNIQUE`, `per_source_errors TEXT DEFAULT '{}'`, `per_source_duration TEXT DEFAULT '{}'`, `total_duration REAL`, `user_id TEXT` to `run_log`; update `database.log_run()` to accept and store the module-level `_RUN_ID` from `logger.py`
9. **`LOG_LEVEL` env var:** add `os.getenv("LOG_LEVEL", "INFO")` in `backend/src/core/settings.py`; thread through FastAPI lifespan in `backend/src/api/main.py`; document in README
10. **`CONTRIBUTING.md`** at repo root — branch naming (`feature/`, `fix/`, `docs/`), commit convention (imperative + issue reference), PR flow, test-before-merge gate
11. **`frontend/README.md` + `backend/README.md`** — sub-project onboarding: prereqs, install, run, test, cross-wiring (`NEXT_PUBLIC_API_URL` ↔ `FRONTEND_ORIGIN`), FastAPI `/docs` + `/redoc` link
12. **`docs/README.md` index** — separate user-facing docs from internal implementation logs; move stale pillar1/2 progress logs under `docs/_archive/`

### Tier B — Should-fix (10 items, velocity)

- `pytest-xdist` dev dep + `@pytest.mark.fast` marker (~50-test smoke subset)
- `Makefile` with `verify-step-0` target (drives Ralph Loop's gate check)
- `backend/scripts/dump_db.py`, `check_logs.py`, `check_worker.py` inspection tools
- `docs/troubleshooting.md` (port conflicts, SQLite lock, CV parse fail, LLM key missing, Redis on Windows)
- `STATUS.md` refresh (13 days stale)
- Enhanced `migrations runner status` output (table format with applied + pending)
- `pytest-randomly` seed recording in `docs/pytest_baseline_seeds.txt`
- `down()` migration integration test
- `frontend/.env.local.example`
- Log-rotation monitoring helper

### Tier C — Polish (5 items, user-chosen IN SCOPE)

Per user direction, Tier C is part of Step 0 — the loop does not halt until ALL THREE tiers are green.

- Demo GIF or screenshot set in `README.md` showing dashboard + profile flow
- Mypy strict-mode gate (`disallow_untyped_defs=true`) on `backend/src/` with allowlist for optional-deps lazy imports
- Enhanced README API-docs callout — explicit `/docs` + `/redoc` link with screenshot
- Log-rotation alerts helper (script in `backend/scripts/` that warns when `data/logs/` nears cap)
- `frontend/.env.local.example` file (Tier B item elevated if not yet covered)

### Non-scope

- No engine→API seam edits (that's Step 1 / Batch S1)
- No UI surfacing (Step 2)
- No new endpoints (Step 3)
- No CI / Docker / SES (Step 4)

---

## Tool orchestration — what each tool does

### Ralph Loop (outer driver)

**Invocation:** `/ralph-loop` with `completion_promise: "STEP-0-GREEN"` and `max_iterations: 25` (user chose full Tier-A+B+C scope — expect 10–15 iterations realistic; 25 is the safety ceiling).

**Each iteration does:**
1. Check sentinel: does `.claude/step-0-verified.txt` exist? If yes, emit `STEP-0-GREEN` and halt.
2. Run `make verify-step-0` (script lives at repo root Makefile — Tier-B item, created on first iteration).
3. Parse output → identify which Tier-A items are still failing.
4. Dispatch parallel subagents for the failing items.
5. Re-run verification, write/update sentinel if all green.
6. Commit partial progress with a standard conventional-commit prefix.

**Stop criteria (all must hold before emitting `STEP-0-GREEN`):**
- All 12 Tier-A items have landed commits
- All 10 Tier-B items have landed commits
- All 5 Tier-C items have landed commits
- `cd backend && python -m pytest -q --tb=no` returns 600p/0f/3s or better
- `python scripts/bootstrap_dev.py` exits 0 and prints ≥1 feed row
- `npm run dev` (frontend) and `python main.py` (backend) both start without missing-env errors
- `make verify-step-0` (Tier B) exits 0 — this is the single source of truth for "green"
- `mypy backend/src/ --strict` (Tier C) exits 0 or with only the documented allowlist
- Sentinel file `.claude/step-0-verified.txt` contains the commit SHA of the final green commit

### Parallel subagents (inner execution — dispatched per Ralph iteration)

Driven by skill `superpowers:dispatching-parallel-agents`. Each iteration launches up to 6 `/implement` agents in parallel. Agents group by file locality to avoid conflicts:

| Agent | Tier-A item(s) | Files touched | Skill invoked |
|---|---|---|---|
| Agent-Env | 1 | `backend/.env.example` | `/implement` + `superpowers:test-driven-development` |
| Agent-Infra | 2, 4 | `backend/src/repositories/database.py`, `.gitattributes` | `/implement` |
| Agent-Windows | 5 | `setup.bat` | `/implement` |
| Agent-Bootstrap | 6, 7 | `backend/scripts/bootstrap_dev.py`, `backend/tests/conftest.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-Migration | 8, 9 | `backend/migrations/0010_run_log_observability.{up,down}.sql`, `backend/src/repositories/database.py`, `backend/src/api/main.py`, `backend/src/core/settings.py` | `/implement` |
| Agent-Docs | 10, 11, 12 | `CONTRIBUTING.md`, `frontend/README.md`, `backend/README.md`, `docs/README.md` | `/sync` |

**Conflict-avoidance:** Agent-Infra and Agent-Migration both touch `database.py` — they run **sequentially within the same iteration**, not parallel. Everything else is conflict-free.

### Skills (invoked inside agents)

| Skill | Used by | Purpose |
|---|---|---|
| `superpowers:writing-plans` | this plan file | already used — you're reading the output |
| `superpowers:executing-plans` | Ralph Loop iteration orchestrator | picks the next unfinished Tier-A item |
| `superpowers:dispatching-parallel-agents` | each iteration's agent dispatch | batches the 6 agents above |
| `superpowers:test-driven-development` | Agent-Bootstrap, Agent-Migration | write failing test first, then implementation |
| `superpowers:verification-before-completion` | Ralph Loop's gate | cannot emit `STEP-0-GREEN` until `make verify-step-0` passes |
| `superpowers:systematic-debugging` | Agent-Bootstrap if API calls fail | if `/api/profile` returns 422, use this skill to isolate the payload defect |
| `commit` | end of each iteration | create the partial-progress commit |
| `update-config` | iteration 1 | pre-commit install, Makefile creation |
| `less-permission-prompts` | iteration 1 | add the frequent Tier-B tool calls (`pytest`, `pre-commit`, `make`) to `.claude/settings.local.json` allowlist to avoid prompt fatigue |

### MCP servers (nice-to-have inside agents)

| MCP | Used by | Purpose |
|---|---|---|
| Context7 | Agent-Bootstrap | fetch current httpx `AsyncClient` + `files=` multipart docs if the implementation stumbles |
| Context7 | Agent-Migration | SQLite `ALTER TABLE ... ADD COLUMN` best-practices for idempotent migrations |
| Chrome DevTools / Playwright | Ralph gate (iteration N) | headless smoke-test of `http://localhost:3000` to prove frontend boots after env changes |
| IDE diagnostics (`mcp__ide__getDiagnostics`) | every agent post-edit | catch type errors before commit |

### Subagent types (framework-level)

| Subagent type | Usage |
|---|---|
| `Explore` | already used in Phase 1 for the audit; not needed for execution |
| `Plan` | not used — exploration was sufficient for this scoped plan |
| `feature-dev:code-reviewer` | Ralph Loop **final iteration only**: review the accumulated diff before emitting `STEP-0-GREEN` |
| `codex:codex-rescue` | escape hatch — invoke if three Ralph iterations pass without progress on a specific item |
| `coderabbit:code-reviewer` | optional additional review layer for the bootstrap script + migration (safety-critical) |

---

## Critical files to modify (with reuse notes)

| File | Action | Reuse from existing code |
|---|---|---|
| `backend/.env.example` | expand from 13 → 35 vars | mirror section-comment style already present |
| `backend/src/repositories/database.py:18–19` | add `os.makedirs` line | pattern from `backend/src/utils/logger.py:14` (`LOGS_DIR.mkdir(parents=True, exist_ok=True)`) and `backend/src/services/vector_index.py:31` |
| `backend/src/core/settings.py` | add `LOG_LEVEL` getenv | follow existing `os.getenv(..., default)` pattern in same file |
| `backend/src/api/main.py` | wire `LOG_LEVEL` into lifespan | existing `lifespan()` context manager already exists |
| `backend/scripts/bootstrap_dev.py` | new file | reuse `_make_plain_cv_pdf()` pattern from `backend/tests/test_linkedin_github.py` for CV generation; reuse httpx patterns from any existing `backend/tests/test_api*.py` |
| `backend/tests/conftest.py` | pin `_TEST_NOW` | straightforward — no existing frozen-time pattern yet |
| `backend/migrations/0010_run_log_observability.up.sql` + `.down.sql` | new migration | follow `0004_notification_ledger.up.sql` idempotency pattern (`ALTER TABLE ... ADD COLUMN` with `IF NOT EXISTS` where supported or guarded); keep reversible |
| `.gitattributes` | new file at repo root | industry-standard `*.sh text eol=lf` |
| `setup.bat` | new file at repo root | mirror structure of `setup.sh` but Windows-native |
| `CONTRIBUTING.md`, `frontend/README.md`, `backend/README.md`, `docs/README.md` | new files | mirror tone + structure of existing `README.md` |
| `Makefile` | new file at repo root | `verify-step-0` target aggregates the gate checks |

---

## Verification section

Ralph Loop cannot emit the completion promise until this ALL pass.

### Gate command

```bash
make verify-step-0
```

Which runs (in order):

```bash
# Backend
cd backend
python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly
# Expect: 600 passed / 0 failed / 3 skipped (or better)

# Env parity
python scripts/check_env_example.py
# Expect: exit 0 — all 35 vars present in .env.example

# Fresh-clone simulation
rm -rf /tmp/job360_smoke && mkdir /tmp/job360_smoke
cd /tmp/job360_smoke
git clone file://$OLDPWD job360-test
cd job360-test/backend
python -m venv .venv && source .venv/bin/activate && pip install -e .
python -m migrations.runner up
# Expect: exits 0 — no FileNotFoundError

# Bootstrap smoke
cd $OLDPWD/backend
python main.py &  # background
sleep 3
python scripts/bootstrap_dev.py
# Expect: exit 0 with "Bootstrap complete. 5 feed rows." or similar

# Frontend smoke
cd ../frontend
npm run dev &  # background
sleep 5
curl -f http://localhost:3000/
# Expect: 200 OK (HTML shell loads)

# Pre-commit gate
cd ..
pre-commit run --all-files
# Expect: all hooks pass

# Docs inventory
test -f CONTRIBUTING.md && test -f frontend/README.md && test -f backend/README.md && test -f docs/README.md && test -f docs/troubleshooting.md
# Expect: all present
```

### Sentinel write (after gate passes)

```bash
echo "$(git rev-parse HEAD)" > .claude/step-0-verified.txt
# Ralph Loop sees this on next iteration, emits "STEP-0-GREEN", halts.
```

### End-to-end proof

1. Fresh clone on Windows 11 → `setup.bat` works → `python scripts/bootstrap_dev.py` returns feed rows
2. Fresh clone on Unix → `setup.sh` works → same bootstrap result
3. `pytest -q` ≥ 600p/0f/3s on both
4. `docs/pytest_baseline.txt` contains the captured counts + duration + seed

---

## Execution budget

- Ralph Loop: max 25 iterations (expect 10–15 with full A+B+C scope)
- Wall-clock: 2–3 sessions
- Commits: 1 per iteration (partial progress) + 1 final squash or merge commit
- Branch: `step-0-preflight` off `main @ 5fb3c07`
- Worktree: the user will hand this plan to the `generator` worktree to execute
- Scope confirmed by user: **Tier A + B + C (all 27 items)** — the loop does not halt early on Tier-A alone

---

## Acknowledged trade-offs

- **Ralph Loop overhead vs one-shot parallel:** the audit argued Step 0 has no sequential dependencies, so Ralph Loop is architecturally heavier than a single parallel-dispatch batch. Accepted — the loop's safety-harness value (retry on failure, idempotent re-run, clear halt condition) outweighs the overhead for a multi-hour session where a subagent failure would otherwise silently break the chain.
- **Tier-B/C in scope per user direction:** iterations 1–5 target Tier-A, 6–12 target Tier-B, 13–20 target Tier-C. The loop does not emit `STEP-0-GREEN` until all three tiers are done. If Tier-C runs long, iteration budget 20–25 is the hard ceiling — beyond that, unfinished Tier-C items are noted in this doc's companion `docs/step_zero_prompt_leftovers.md` (created at halt) and deferred to Step 1.
- **No Pillar-4 / Batch-4 work in this loop:** explicitly out of scope. Step 0 ends when the gate passes, not when every conceivable improvement has landed.

---

_Plan mirrored to repo 2026-04-23 after plan-mode approval. Generator worktree can read this file directly; no need to reach into `.claude/plans/`._
