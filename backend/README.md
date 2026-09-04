<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Job360 Backend

FastAPI backend for Job360. Async job aggregator across 41 sources + scoring +
semantic retrieval. Serves the Next.js dashboard, runs the scheduled pipeline,
and hosts the ARQ worker for notifications.

## Prerequisites

- Python 3.9+ (tested through 3.12)
- Redis only if you run the ARQ worker (optional for CLI / API usage)

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

## ARQ worker (required for notifications)

The worker fires per-user notifications. Without it, `score_and_ingest`
enqueues vanish silently and "configure email channel → wait for new job →
verify email arrives" cannot be tested end-to-end. **Phase −1 manual
verification requires this to be running.**

### Local dev — three commands

From the repo root:

```bash
make redis-up   # start Redis on localhost:6379 via docker-compose.dev.yml
make worker     # run the ARQ worker (blocks — open in a separate terminal)
make redis-down # stop Redis when done
```

`make worker` resolves to `cd backend && arq src.workers.settings.WorkerSettings`
with `REDIS_URL` defaulting to `redis://localhost:6379`. Override with
`REDIS_URL=redis://other-host:6379 make worker` if you've provisioned Redis
elsewhere.

### Production

Same entry point — `arq src.workers.settings.WorkerSettings`. Replace
docker-compose with managed Redis (Upstash, ElastiCache, Render add-on)
and a real process supervisor (systemd, supervisord, container
orchestrator). Phase 3 of `LAUNCH_PLAN.md` covers this.

Tests never touch Redis — they monkeypatch the Apprise dispatcher and call
worker tasks as pure async functions.

## Cross-wiring with the frontend

The dashboard reaches the API via `NEXT_PUBLIC_API_URL` (frontend env, default
`http://localhost:8000`). The API in turn whitelists the browser origin via
`FRONTEND_ORIGIN` (backend env, comma-separated, default
`http://localhost:3000`). Mismatch = CORS preflight failure.

## Further reading

- [`docs/README.md`](../docs/README.md) — full docs index
- [`CLAUDE.md`](../CLAUDE.md) — architecture, hard rules, scoring algorithm
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — branch / commit / PR conventions
