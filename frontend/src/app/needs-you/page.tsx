import { PageContainer } from "@/components/layout/PageContainer";
import { NeedsYou } from "@/components/needs-you/NeedsYou";
import { LiveLine } from "@/components/needs-you/LiveLine";

// Protected by middleware.ts (`/needs-you` is in PROTECTED_PATHS).
export default function NeedsYouPage() {
  return (
    <PageContainer className="flex flex-col gap-6 py-8">
      <h1 className="font-heading text-3xl font-normal tracking-tight">Needs you</h1>
      <LiveLine />
      <NeedsYou />
    </PageContainer>
  );
}
