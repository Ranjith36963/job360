<!-- doc: LIVING | last-verified: 2026-10-20 by /sync -->
# Job360 Backend

FastAPI backend for Job360 — the memory layer for the seeker's own AI agent
(`../docs/product/VISION.md`): profile extraction, bring-a-job, application
receipts, the CV tailor (renders and versions what the agent writes — decision
28, no LLM of ours) and the MCP server at `/api/mcp`.
The legacy search-and-score pipeline (job sources, scoring, semantic retrieval)
was deleted 2026-09-05 (roadmap slice 5, #483); git history is the only record
(`backend/tests/test_sourcing_era_deleted.py::test_archive_deleted`).

## Prerequisites

- Python 3.10+ (tested through 3.12; `mcp` needs 3.10)

## Install

```bash
# Unix / macOS
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Windows (PowerShell / cmd)
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

For dev tooling, install the dev extra — read
`[project.optional-dependencies].dev` in `pyproject.toml` for what it gives you:

```bash
pip install -e ".[dev]"
```

CI and the prod image do not resolve `pyproject.toml` themselves: they install
the hashed locks (`requirements.lock.txt` for prod, `requirements-dev.lock.txt`
for CI, `requirements-browser.lock.txt` for the browser image) with
`pip install --require-hashes -r <lock>`, then the app with `--no-deps`. To get
CI's exact set on Linux, run `pip install --require-hashes -r requirements-dev.lock.txt`
and then `pip install --no-deps -e .`. After any dependency edit in
`pyproject.toml`, run `python scripts/lock_deps.py` (it needs uv) and commit the
three locks. `python ../scripts/check_py_lock.py` says whether they match.

`pre-commit` is **not** in that extra even though `CONTRIBUTING.md` makes
`pre-commit run --all-files` a merge gate. Install it yourself
(`pip install pre-commit`, or pipx) before the first commit.

## Environment

Copy the example env from the **repo root** (not `backend/`):

```bash
cp ../.env.example ../.env     # Unix
copy ..\.env.example ..\.env   # Windows
```

Edit `../.env` for your API keys and `FRONTEND_ORIGIN` (read in `api.main`, not
`core.settings`). Most other knobs are in `core/settings.py`, not all.
`core.settings._REQUIRED_PROD_VARS` is the set prod refuses to boot without.

## Run the API

```bash
python main.py
```

FastAPI boots on `http://localhost:8000`. Interactive API docs:

- Swagger UI — http://localhost:8000/docs
- ReDoc — http://localhost:8000/redoc

Production-style:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## CLI

```bash
python -m src.cli setup-profile --cv path/to/cv.pdf --linkedin li.pdf --github user
python -m src.cli api                          # same as `python main.py`
```

`run` / `status` / `view` / `sources` / `rescore-backfill` were deleted with the
sourcing era (slice 5, #483) — there is nothing left to search, score or list.

## Tests

Must pass from `backend/`:

```bash
python -m pytest -q -p no:randomly
```

Invariant: full suite passes, 0 failing. No test total is written down here —
measure it with `python -m pytest --collect-only -q -p no:randomly | tail -1`.

## Database migrations

Forward-only schema migrations live in `backend/migrations/`:

```bash
python -m migrations.runner up         # apply pending migrations
python -m migrations.runner status     # show applied/pending
python -m migrations.runner down       # reverse last migration
```

The API also auto-applies on boot via `lifespan`.

## Further reading

- [`docs/README.md`](../docs/README.md) — full docs index
- [`CLAUDE.md`](../CLAUDE.md) — mission, hard rules
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — directory tree, DB schema
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — branch / commit / PR conventions
