import { cookies } from "next/headers";
import Link from "next/link";
import Landing from "./Landing";
import { ApplicationList } from "@/components/applications/ApplicationList";

// ---------------------------------------------------------------------------
// R14 (docs/plans/2026-09-04-application-spine/spec.md) — the web home is
// YOUR APPLICATIONS. A signed-in visitor sees their applications home; a
// signed-out visitor keeps the existing marketing landing page.
//
// `/` is deliberately NOT in middleware.ts's PROTECTED_PATHS (unfurl bots
// and landing-cta-auth.spec.ts depend on the public landing staying public)
// — so the split happens HERE, by reading the session cookie's PRESENCE
// (same signal middleware.ts itself checks; the actual auth/authorization
// check for every API call this page's children make is the backend's
// `require_user`, not this cookie peek).
// ---------------------------------------------------------------------------

export default async function Home() {
  const cookieStore = await cookies();
  const signedIn = Boolean(cookieStore.get("job360_session")?.value);

  if (!signedIn) {
    return <Landing />;
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:py-12">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold sm:text-3xl">Your applications</h1>
          <p className="text-muted-foreground">
            Every job you&apos;ve brought, its status, and its whole history.
          </p>
        </div>
        <Link
          href="/bring"
          className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
        >
          Bring a job
        </Link>
      </div>
      <ApplicationList />
    </div>
  );
}
