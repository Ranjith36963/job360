import { redirect } from "next/navigation";

// The job list lives on the dashboard (matches, filters, time buckets, search),
// so the redundant "Jobs" nav item was removed. This redirect is kept only as a
// safety net so an old bookmark or a typed "/jobs" URL lands on the dashboard
// instead of 404ing. The individual job detail route /jobs/[id] is unaffected.
//
// NOTE: server component on purpose — no "use client" (that would disable
// generateMetadata, per Next.js 16 App Router rules).
export default function JobsPage() {
  redirect("/dashboard");
}
