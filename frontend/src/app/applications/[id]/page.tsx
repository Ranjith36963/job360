import { ApplicationClient } from "./ApplicationClient";

// ---------------------------------------------------------------------------
// The application record (spec R11, R14).
//
// Server component shell: awaits `params` (Next.js 16 — sync access to
// `params` was removed, see frontend/AGENTS.md) and hands the plain numeric
// id to a client child, which does the actual fetch/render. Same
// server-page + client-child split as src/app/oauth/consent/[rid]/page.tsx.
//
// Protected by middleware.ts (`/applications` is in PROTECTED_PATHS).
// ---------------------------------------------------------------------------

export default async function ApplicationPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ from?: string | string[]; show?: string | string[] }>;
}) {
  const { id } = await params;
  // Where the user came from (?from=&show=); ApplicationClient allow-lists both.
  const { from, show } = await searchParams;

  return <ApplicationClient applicationId={Number(id)} from={from} show={show} />;
}
