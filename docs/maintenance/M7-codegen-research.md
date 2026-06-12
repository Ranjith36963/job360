# M7 — OpenAPI→TS codegen: tool comparison + recommendation

**Status: RESEARCH ONLY — awaiting owner approval before any implementation.**
Researched 2026-06-12 (integrator round, owner-directed). Sources at bottom.

## The problem M7 solves
`frontend/src/lib/types.ts` is **389 lines / 35 hand-mirrored types** of `backend/src/api/models.py`. We've hand-synced it twice already (the `llm_*` matcher fields, the score-dim fields) and it drifts every time the API changes. M7 = generate it from FastAPI's `/openapi.json` so it can never drift.

## Our actual stack (grounds the choice)
- Hand-written fetch client `lib/api.ts` (`request<T>` helper) that **works and we like** — the drift is in the TYPES, not the client.
- TanStack Query v5 present but lightly used (4 query keys).
- **zod NOT installed; react-hook-form NOT installed** — both arrive with M3 (form validation).
- Backend: FastAPI emits OpenAPI 3.1 at `/openapi.json`. Frontend: Next 16 / React 19, npm.

## Candidates (2026 state)

| Tool | What it emits | Runtime deps shipped | Fit to our stack |
|---|---|---|---|
| **openapi-typescript** | **Types only** (a `.d.ts`) | **ZERO** — pure types, 0 bytes shipped | Drop-in: keep our `api.ts`, just import generated types instead of hand-written ones |
| **Hey API** (`@hey-api/openapi-ts`) | Types + SDK client + TanStack Query options + **zod schemas** | A generated runtime SDK/client (ships code) | Powerful, but would replace our hand-written `api.ts`; bigger change |
| **Orval** | Types + custom hooks + zod + mocks | Client + hooks (ships code) | Config-heavy; generates legacy-style hooks; most churn |

**Critical 2026 fact:** the `openapi-fetch` / `openapi-react-query` *runtime* helpers are going into **maintenance mode** — BUT the `openapi-typescript` *types generator* is NOT; maintainers say its maintenance "will increase in 2026" (8.0 roadmap). That caveat does **not** touch us because we don't use those runtime helpers — we have our own client.

## Recommendation: **openapi-typescript** (types-only)

> 1. The real defect is **types drift**, and openapi-typescript fixes exactly that with the **smallest possible change** — regenerate `types.ts`, keep our working hand-written `api.ts` and our direct TanStack Query usage untouched.
> 2. It uniquely satisfies criterion (a) **zero runtime deps** — it emits pure TypeScript types (0 bytes shipped, no client library); criterion (b) build-step is **one CLI line** in CI/predev; criterion (c) maintenance burden is near-zero and the tool's own upkeep is *increasing* in 2026.
> 3. The lone reason to pick **Hey API instead** is the **zod synergy with M3** — but M3's zod is for a few *form-input* shapes, not for runtime-validating 35-field API responses, so the overlap is thin; we can add a focused zod step for M3 without coupling the whole API layer to a heavier generator now.

## The owner's fork (your call)
- **Pick openapi-typescript** (recommended) if you want the minimal drift-killer and to keep `api.ts` as ours.
- **Pick Hey API** if you'd rather adopt a generated SDK + TanStack Query options + API-derived zod as one platform, and are OK replacing the hand-written `api.ts`. This is more upfront churn and ships a runtime client, but more "free" long-term.

## If approved (openapi-typescript) — implementation sketch (NOT done yet)
- `npm i -D openapi-typescript` (devDependency only).
- Script: `openapi-typescript http://localhost:8000/openapi.json -o src/lib/api-types.ts` (or read a committed `openapi.json`).
- Migrate `lib/types.ts` imports to the generated `components["schemas"][...]`; delete the hand-mirrored duplicates.
- CORE-list file (`types.ts`) → full adversarial waves + frontend gate (type-check + lint + vitest) + a live page render in /verify-job360.
- Add a CI check that fails if regenerated types differ from committed (catches future drift automatically).

## Sources
- [Which OpenAPI Codegen Should You Choose? (DEV, 2026)](https://dev.to/nyaomaru/which-openapi-codegen-should-you-choose-openapi-typescript-vs-hey-api-vs-orval-vs-kubb-100p)
- [Typesafe API Code Generation for React in 2026 (Sascha Becker)](https://www.saschb2b.com/blog/typesafe-api-codegen-2026)
- [openapi-typescript 2026 Roadmap (Discussion #2559)](https://github.com/openapi-ts/openapi-typescript/discussions/2559)
- [Hey API — openapi-ts (GitHub)](https://github.com/hey-api/openapi-ts) · [TanStack Query plugin](https://heyapi.dev/openapi-ts/plugins/tanstack-query)
- [openapi-fetch (npm)](https://www.npmjs.com/package/openapi-fetch)
