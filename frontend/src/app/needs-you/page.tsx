import { PageContainer } from "@/components/layout/PageContainer";
import { NeedsYou } from "@/components/needs-you/NeedsYou";

// Protected by middleware.ts (`/needs-you` is in PROTECTED_PATHS).
export default function NeedsYouPage() {
  return (
    <PageContainer className="flex flex-col gap-6 py-8">
      <div>
        <h1 className="font-heading text-3xl font-normal tracking-tight">Needs you</h1>
        <p className="text-muted-foreground">
          Questions your assistant could not answer without you. Answer once — every assistant
          sees it.
        </p>
      </div>
      <NeedsYou />
    </PageContainer>
  );
}
