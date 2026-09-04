<!-- doc: LIVING | last-verified: 2026-09-03 by the mission sweep -->
# Job360 Backend

FastAPI backend for Job360 — the memory layer for the seeker's own AI agent
(`../docs/product/VISION.md`): profile extraction, bring-a-job, application
receipts, the CV tailor (web fallback) and the MCP server at `/api/mcp`.
The legacy search pipeline (41 sources + scoring + semantic retrieval) is still
in the tree and runs only on demand; it is slated for deletion (roadmap slice 5).
Notifications (email via Resend + webhook) are sent synchronously from the API
process — the ARQ worker and Redis services were deleted 2026-09-02, and there
are no background jobs.

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

For dev tooling (pytest, ruff, pre-commit), install the dev extra:

```bash
pip install -e ".[dev]"
```

## Environment

Copy the example env from the **repo root** (not `backend/`):

```bash
cp ../.env.example ../.env     # Unix
copy ..\.env.example ..\.env   # Windows
```

Edit `../.env` to set your API keys, webhook URLs, and `FRONTEND_ORIGIN`.
Free sources (33 of 41) work without any keys. See [`CLAUDE.md`](../CLAUDE.md)
for the full env-var table.

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

## Run the pipeline (CLI)

```bash
python -m src.cli run                          # all 41 sources
python -m src.cli run --source arbeitnow       # single source
python -m src.cli run --dry-run --log-level DEBUG
python -m src.cli status                       # last-run summary
python -m src.cli sources                      # list all 41 sources
python -m src.cli view --hours 24 --min-score 50
python -m src.cli setup-profile --cv path/to/cv.pdf
```

## Tests

Must pass from `backend/`:

```bash
python -m pytest -q -p no:randomly
```

Invariant: full suite passes, 0 failing, across **218** `test_*.py` files (2 `live`
deselected offline). The collected count is deliberately not written down — run
`python -m pytest --collect-only -q -p no:randomly | tail -1` for it. Any total
committed to a doc is unguarded (`scripts/doc_sync_check.py` declines to check it
on purpose: it needs Postgres, and parametrization makes a cheap check flaky) and
rots silently — this line carried a stale one. The
`-p no:randomly` flag keeps the default order deterministic (pytest-randomly is
installed but opt-in).

## Database migrations

Forward-only schema migrations live in `backend/migrations/`:

```bash
python -m migrations.runner up         # apply pending migrations
python -m migrations.runner status     # show applied/pending
python -m migrations.runner down       # reverse last migration
```

The API also auto-applies on boot via `lifespan`.

## Worker (deleted 2026-09-02)

The ARQ worker and Redis services were deleted. Notifications now send
synchronously from the API process — there is nothing to run separately.

## Cross-wiring with the frontend

The dashboard reaches the API via `NEXT_PUBLIC_API_URL` (frontend env, default
`http://localhost:8000`). The API in turn whitelists the browser origin via
`FRONTEND_ORIGIN` (backend env, comma-separated, default
`http://localhost:3000`). Mismatch = CORS preflight failure.

## Further reading

- [`docs/README.md`](../docs/README.md) — full docs index
- [`CLAUDE.md`](../CLAUDE.md) — architecture, hard rules, scoring algorithm
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — branch / commit / PR conventions
