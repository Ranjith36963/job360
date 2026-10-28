import { PageContainer } from "@/components/layout/PageContainer";
import { NeedsYou } from "@/components/needs-you/NeedsYou";
import { LiveLine } from "@/components/needs-you/LiveLine";
import { RunNowPanel } from "@/components/needs-you/RunNowPanel";

// Protected by middleware.ts (`/needs-you` is in PROTECTED_PATHS).
export default function NeedsYouPage() {
  return (
    // Wide screens use the width, but lines stay readable (owner 2026-10-27: at most about 80rem).
    <PageContainer className="flex max-w-[80rem] flex-col gap-4 py-6">
      <h1 className="font-heading text-3xl font-normal tracking-tight">Needs you</h1>
      <LiveLine />
      {/* The empty space right of the summary holds the Run now triggers (owner 2026-10-27). */}
      <NeedsYou aside={<RunNowPanel />} />
    </PageContainer>
  );
}
