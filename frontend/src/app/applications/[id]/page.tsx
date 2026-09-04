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
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return <ApplicationClient applicationId={Number(id)} />;
}
