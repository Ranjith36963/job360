# backend/ — Claude Code pointer
<!-- doc: LIVING | last-verified: 2026-09-03 by the mission sweep -->

> **This is a thin pointer, not the source of truth.** The mission is
> [`../docs/product/VISION.md`](../docs/product/VISION.md) (agent thinks, Job360
> remembers; never source or rank). The load-bearing guidance (hard rules,
> lazy-import rules, DB schema) lives in the **root [`../CLAUDE.md`](../CLAUDE.md)** —
> read that first. This file only adds backend-local essentials so they're at hand
> when you're working in this directory. Keep it thin; do not duplicate the root.

## What this is

The Job360 backend: Python 3.10+ (`mcp` needs it; CI and prod run 3.12), FastAPI, Postgres via psycopg3 (`pg.py` — an aiosqlite-shaped shim). No worker: the ARQ code in `src/workers/` is dead since the worker + Redis services were deleted 2026-09-02.
**Product path:** root [`../CLAUDE.md`](../CLAUDE.md) § Architecture — one copy, not two. Everything under `src/sources/`, `src/main.py`, `services/skill_matcher.py`, `services/enrichment/` is the sourcing era, slated for deletion (roadmap slice 5, #483). Never add to it.
Entry points: `main.py` (uvicorn) and `python -m src.cli`. Runtime data (gitignored)
lives in `data/` (`exports/`, `reports/`, `logs/`, `chroma/`, and the legacy `user_profile.json` that
`storage.py` migrates once then deletes). There is **no `data/jobs.db`** — the store is
Postgres; `DB_PATH` is only a connection selector (`src/core/settings.py:15-20`,
`src/repositories/pg.py:732-737`).

## Owner rule #29 — empty user fields stay SILENT

Like Indeed/LinkedIn: match on what the user filled. An empty preference
(salary, locations, workplace, experience level, about_me) = "don't care" —
never a penalty, never a per-job zero, never a guess. Dim scorers return a
constant for an empty user side; prefilter passes everything; the judge prompt
omits unset prefs. Details + audit: `../docs/product/product_design_rules.md` (root
CLAUDE.md rule #29).

## Commands (run from `backend/`)

```bash
# Canonical pre-commit test run — defer to the runtime collected count, not a doc figure
python -m pytest -q -p no:randomly   # 2 `live` deselected; test_main.py included. Measure the count, never quote it.

python -m pytest tests/test_scorer.py::test_name -v   # single test
python -m ruff check .                                # lint (CI gate)
python -m src.cli run                                 # full pipeline
python main.py                                        # FastAPI on :8000
python -m migrations.runner up                        # apply migrations (non-API contexts)
```

`test_main.py` is now part of the canonical run. The M8 batch stubbed JobSpy
(`fetch_jobs → []` via autouse fixture) and patched `load_profile`, making it
fully offline (18 `def test_` as of 2026-08-24 — measure, don't quote). Do NOT
add `--ignore=tests/test_main.py` back.

## Backend test-infra notes (hard-won; don't relearn)

- **The suite is fast and fully green** as of 2026-06-06. It is *not* a hang — if a
  run seems to hang it's almost always a real bug, not the documented-old behavior.
- `conftest.py` mocks `asyncio.sleep` to instant (retry/backoff sleeps otherwise sum
  to ~37 min). Tests asserting on **real** elapsed time must use `@pytest.mark.real_sleep`.
- `conftest.py` closes the lazily-created `dependencies._db` singleton per test —
  an open async DB connection blocks process exit (non-daemon worker thread).
- **DB_PATH gotcha:** modules that do `from src.core.settings import DB_PATH` bind the
  *value* at import time. Test fixtures must redirect `DB_PATH` on **every** importer
  (the fixtures loop over `sys.modules`), not just `settings`, or queries hit the real DB.
- Always mock HTTP with `aioresponses` (root rule #4). The suite must run offline.

## Where things are

- `src/api/routes/bring.py`, `receipts.py`, `tailor.py`, `src/api/mcp_server.py` — the product path
- `src/main.py` — legacy orchestrator + `SOURCE_REGISTRY` (41) + `_build_sources()` (sourcing era, never extend)
- `src/cli.py` — Click CLI · `src/api/` — FastAPI app + routes · `src/services/` — profile extraction + legacy engine
- `src/repositories/database.py` — Postgres via psycopg3 (aiosqlite-shaped shim) · `migrations/` — forward/reverse SQL pairs
- `scripts/` — backend Python helpers (run `python scripts/X.py`); see root `CONTRIBUTING.md`

See root [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the deep technical reference.
