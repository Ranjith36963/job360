# Job360 Frontend
<!-- doc: LIVING | last-verified: 2026-10-21 by the daily truth check -->

Next.js 16 + React 19 web app for Job360. A thin screen over the FastAPI
backend's routes. Job360 never sources or ranks jobs — the seeker's own agent
finds the job; Job360 remembers it.

## Prerequisites

- Node 22 (LTS; the Docker image and CI both run 22)
- npm (bundled with Node). pnpm or yarn also work — lockfile is npm.

## Install

```bash
cd frontend
npm install
```

## Environment

`.env*` is gitignored (`frontend/.gitignore`), so a fresh clone has no
`.env.local`. Copy `.env.local.example` and read its comments before setting
anything — they say which vars must stay UNSET for local dev, and what breaks
when they are not.

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
`*.tsx`). Counts are not written down here — measure them.

## Cross-wiring with the backend

Browser calls go to this app's own origin and are proxied; the target is
`BACKEND_ORIGIN` in `next.config.ts`. The backend's own browser-origin
allow-list is read in `api.main`.

## Further reading

- [`../backend/README.md`](../backend/README.md) — API, CLI, migrations
- [`../docs/README.md`](../docs/README.md) — full docs index
