import { redirect } from "next/navigation";

// The job list lives on the dashboard (matches, filters, time buckets, search).
// "/jobs" exists only so the "Jobs" nav link resolves instead of 404ing
// (E2E_TEST_REPORT #3). Server-side redirect → no flash of a wrong page, and
// the individual job detail route /jobs/[id] is unaffected.
//
// NOTE: server component on purpose — no "use client" (that would disable
// generateMetadata, per Next.js 16 App Router rules).
export default function JobsPage() {
  redirect("/dashboard");
}
