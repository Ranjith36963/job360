<!-- doc: LIVING -->
<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

<!-- Job360 addition. Deliberately OUTSIDE the block above: `next dev` rewrites
     everything between BEGIN and END, so anything added in there is erased. -->

## Before any App Router work: Context7 first

Consult Context7 for the Next.js docs BEFORE reading `node_modules/next/dist/docs/`
and before writing code (root rule #22). Training data for 14-15 is wrong for 16:
`params` is a Promise, and `metadata` / `generateMetadata` are Server-Component
only — a `page.tsx` carrying `"use client"` cannot provide them. Keep the page a
Server Component and push interactivity into a child Client Component.
