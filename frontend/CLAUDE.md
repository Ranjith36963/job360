@AGENTS.md

# frontend/ — Claude Code pointer
<!-- doc: LIVING | last-verified: 2026-08-21 by /sync -->

> **Thin pointer, not the source of truth.** The 31 hard rules, scoring/engine
> guidance, DB schema, and phase history live in the **root [`../CLAUDE.md`](../CLAUDE.md)** —
> read that first. This file adds frontend-local essentials so they're at hand when
> you work in this directory. Keep it thin; do not duplicate the root.

## How to talk to me (STRICT — always follow)

**Explain everything in simple, plain English.** Short sentences, easy words, no
jargon (if a technical word is needed, say what it means in one short line). No walls
of text — say what happened, what I did, what's next.

## What this is

The Job360 web app: **Next.js 16.3.0 (App Router) + React 19.2.8 + Tailwind 4 +
shadcn 4**. Talks to the FastAPI backend on `:8000`. State via **TanStack Query 5**;
forms via **react-hook-form 7 + zod 4**; Kanban drag via **@dnd-kit**; charts via
**recharts**; toasts via **sonner**. Auth is cookie-session — guarded in `src/middleware.ts`.

## Owner rule #29 — empty preferences stay SILENT (never default, never require)

Preference inputs (salary range, locations, remote/hybrid/office, experience
level, about_me) are OPTIONAL and must read as optional. Never block a search
on an unfilled preference, and never write a default value the user didn't
choose — a silently-written default is indistinguishable from a real choice
and turns "don't care" into a fake constraint. The backend treats empty as
"dimension off"; the UI must not manufacture emptiness away. Details:
`../docs/product/product_design_rules.md` (root CLAUDE.md rule #29).

## ⚠️ Next.js 16 — this is NOT the Next.js your training data knows (root rule #22)

Training data for Next.js 14–15 is **wrong** here. Before any App Router pattern, read
`node_modules/next/dist/docs/` and query Context7 (`/vercel/next.js`). Hard-won traps:

- **Request APIs are async Promises — sync access was fully REMOVED in 16.** `params`,
  `searchParams`, `cookies()`, `headers()`, `draftMode()` must all be `await`ed.
  - Server component: `export default async function Page({ params }: { params: Promise<{ id: string }> }) { const { id } = await params }`
  - Route handler: `export async function GET(req, { params }: { params: Promise<{ id: string }> }) { const { id } = await params }`
  - Client component: get the value with React's `use(params)` hook, not `await`.
  - See `src/app/jobs/[id]/page.tsx` for the real pattern in this repo.
- **`"use client"` on a `page.tsx` silently disables `generateMetadata`.** If a page
  needs both metadata and interactivity: keep `page.tsx` a server component, push the
  interactive parts into a child client component (this repo's pattern:
  `jobs/[id]/page.tsx` server + `jobs/[id]/JobDetailClient.tsx` client).
- A codemod exists for the async migration, but **don't trust auto-fixes blindly** —
  verify against the running app (see "Verify", below).

## Commands (run from `frontend/`)

```bash
npm run dev              # localhost:3000
npm run build            # production build (catches type + RSC errors CI catches)
npm run type-check       # tsc --noEmit
npm run lint             # eslint (CI gate)
npm run test:unit        # vitest
npm run test:e2e         # playwright
npm run check:types-drift # regenerate api-types from backend OpenAPI + fail if drifted
```

## The API-types drift guard (don't fight it — regenerate)

`src/lib/api-types.ts` is **generated** from the backend's OpenAPI schema, NOT
hand-edited. The commit gate (`scripts/agent-gate.sh`) runs `check:types-drift` and
**blocks the commit** if the checked-in types don't match the backend. After any
backend route/response change: run `npm run gen:types`, commit the regenerated
`api-types.ts` + `openapi.json` together. Hand-editing them will fail the gate.

## Where things are

- `src/app/` — App Router pages: `(auth)/`, `dashboard/`, `jobs/[id]/`, `pipeline/`,
  `profile/`, `settings/{layout,channels,notifications,account}/`, `notifications/`.
- `src/components/` — `ui/` (shadcn primitives), `jobs/`, `pipeline/` (KanbanBoard),
  `profile/`, `layout/`, `providers/`.
- `src/lib/` — `api.ts` (fetch wrapper), `api-types.ts` (generated), `queryKeys.ts`
  (TanStack Query keys), `api-error.ts`, `types.ts`, `utils.ts`.
- `src/middleware.ts` — session-cookie auth guard (redirects unauthed → login).

## Verify, don't assume (root rule + verify-job360 skill)

A clean `npm run build` does NOT prove the UI works. After touching pages, API calls,
or auth, run the **`/verify-job360`** skill (browser flavor) — it drives a real browser
with Playwright, checks the console for errors, and walks the journey. "Compiles" ≠ "works".

See root [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the deep technical reference.
