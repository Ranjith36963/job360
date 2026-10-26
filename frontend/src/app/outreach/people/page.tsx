import type { Metadata } from "next";
import { PageContainer } from "@/components/layout/PageContainer";
import { PeopleClient } from "./PeopleClient";

export const metadata: Metadata = { title: "People" };

// Protected by middleware.ts (`/people` and `/outreach` are in PROTECTED_PATHS).
export default function PeoplePage() {
  return (
    <PageContainer className="flex flex-col gap-6 py-8">
      <div>
        <h1 className="font-heading text-3xl font-normal tracking-tight">People</h1>
        <p className="text-muted-foreground">
          Everyone you or your assistant added, across every job. Your assistant finds people and
          sends messages; Job360 keeps the record. Nothing is sent from this page.
        </p>
      </div>
      <PeopleClient />
    </PageContainer>
  );
}
