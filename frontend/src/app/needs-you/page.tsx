import { PageContainer } from "@/components/layout/PageContainer";
import { NeedsYou } from "@/components/needs-you/NeedsYou";
import { LiveLine } from "@/components/needs-you/LiveLine";
import { RunNowPanel } from "@/components/needs-you/RunNowPanel";
import { parseJobParam } from "@/lib/back-link";

// Protected by middleware.ts (`/needs-you` is in PROTECTED_PATHS).
// ?job=<id> comes from "Back to Needs you" on an application page; ?show= is read by the page itself.
export default async function NeedsYouPage({ searchParams }: { searchParams: Promise<{ job?: string | string[] }> }) {
  const { job } = await searchParams;
  return (
    // Wide screens use the width, but lines stay readable (owner 2026-10-27: at most about 80rem).
    <PageContainer className="flex max-w-[80rem] flex-col gap-4 py-6">
      <h1 className="font-heading text-3xl font-normal tracking-tight">Needs you</h1>
      <LiveLine />
      {/* The empty space right of the summary holds the Run now triggers (owner 2026-10-27). */}
      <NeedsYou aside={<RunNowPanel />} focusJob={parseJobParam(Array.isArray(job) ? job[0] : job)} />
    </PageContainer>
  );
}
