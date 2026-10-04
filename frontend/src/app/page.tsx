import { cookies } from "next/headers";
import Landing from "./Landing";
import { Home as SignedInHome } from "@/components/home/Home";
import { PageContainer } from "@/components/layout/PageContainer";

// ---------------------------------------------------------------------------
// R14 (docs/plans/2026-09-04-application-spine/spec.md) — the web home is
// YOUR APPLICATIONS. A signed-in visitor sees their home (redesign slice 2:
// what the assistant wrote, what needs them, the applications ledger, the
// counts); a signed-out visitor keeps the existing marketing landing page.
//
// `/` is deliberately NOT in middleware.ts's PROTECTED_PATHS (unfurl bots
// and landing-cta-auth.spec.ts depend on the public landing staying public)
// — so the split happens HERE, by reading the session cookie's PRESENCE
// (same signal middleware.ts itself checks; the actual auth/authorization
// check for every API call this page's children make is the backend's
// `require_user`, not this cookie peek).
//
// This file stays a Server Component; the interactive home lives in the
// client component it renders.
// ---------------------------------------------------------------------------

export default async function Home() {
  const cookieStore = await cookies();
  const signedIn = Boolean(cookieStore.get("job360_session")?.value);

  if (!signedIn) {
    return <Landing />;
  }

  return (
    <PageContainer>
      <SignedInHome />
    </PageContainer>
  );
}
