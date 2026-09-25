"use client";

import { ApplicationList } from "@/components/applications/ApplicationList";
import { PageContainer } from "@/components/layout/PageContainer";

// spec R14 — the applications list. Protected by middleware.ts
// (`/applications` is in PROTECTED_PATHS). No metadata/SSR needs here, so a
// plain client page.
export default function ApplicationsPage() {
  return (
    <PageContainer className="flex flex-col gap-6 py-8">
      <div>
        <h1 className="font-heading text-2xl font-bold">Your applications</h1>
        <p className="text-muted-foreground">
          Every job you&apos;ve brought, its status, and its whole history.
        </p>
      </div>
      <ApplicationList />
    </PageContainer>
  );
}
