# Job360 Frontend
<!-- doc: LIVING | last-verified: 2026-08-08 by /sync -->

Next.js 16 + React 19 dashboard for Job360. Talks to the FastAPI backend for
job listings, profile, pipeline, and search.

## Prerequisites

- Node 20+ (LTS)
- npm (bundled with Node). pnpm or yarn also work — lockfile is npm.

## Install

```bash
cd frontend
npm install
```

## Environment

This repo ships a committed `.env.local` with the dev default. If yours is
missing, create it manually:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Point it at whatever host and port the FastAPI backend is running on.
`NEXT_PUBLIC_*` vars are inlined into the client bundle at build time, so a
rebuild is needed after changing them.

## Run

```bash
npm run dev           # dev server on http://localhost:3000
npm run build         # production build
npm start             # serve the production build
npm run lint          # eslint
```

## Tests

```bash
npm run test:unit              # vitest unit tests (component + lib)
npm run test:e2e               # Playwright E2E (@playwright/test)
npm run test:coverage          # vitest with v8 coverage
npm run type-check             # tsc --noEmit (no emit, type-only)
```

Unit tests live alongside their subject files (`*.test.tsx` next to
`*.tsx`). E2E specs live in `tests/e2e/` and run against a started dev
server. Step-3 close-out invariant: ≥44 unit tests, ≥5 E2E specs.

## Cross-wiring with the backend

1. The backend must be running on the URL in `NEXT_PUBLIC_API_URL`
   (default `http://localhost:8000`). See
   [`../backend/README.md`](../backend/README.md).
2. The backend's `FRONTEND_ORIGIN` env var must include this frontend's
   origin (default `http://localhost:3000`). Without it, the browser blocks
   the API calls at the CORS preflight.

## Further reading

- [`../backend/README.md`](../backend/README.md) — API, CLI, worker, migrations
- [`../docs/README.md`](../docs/README.md) — full docs index
