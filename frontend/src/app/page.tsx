import type { Metadata } from "next";
import { cookies } from "next/headers";
import Landing from "./Landing";
import { Home } from "@/components/home/Home";

// ---------------------------------------------------------------------------
// The web home. A signed-in visitor sees the Progress dashboard (what your
// assistant did, the morning check, the batch queue); a signed-out visitor keeps the marketing
// landing page. /applications stays the full list with its filters.
//
// `/` is deliberately NOT in middleware.ts's PROTECTED_PATHS (unfurl bots
// and landing-cta-auth.spec.ts depend on the public landing staying public)
// — so the split happens HERE, by reading the session cookie's PRESENCE
// (same signal middleware.ts itself checks; the actual auth/authorization
// check for every API call Home makes is the backend's `require_user`, not
// this cookie peek).
// ---------------------------------------------------------------------------

// Signed in, "/" is the Progress dashboard; signed out it is the landing page,
// which keeps the site title from the root layout.
export async function generateMetadata(): Promise<Metadata> {
  const cookieStore = await cookies();
  return cookieStore.get("job360_session")?.value ? { title: "Progress dashboard — Job360" } : {};
}

export default async function HomePage() {
  const cookieStore = await cookies();
  const signedIn = Boolean(cookieStore.get("job360_session")?.value);

  if (!signedIn) {
    return <Landing />;
  }

  return <Home />;
}
