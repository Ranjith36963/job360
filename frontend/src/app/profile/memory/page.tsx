import { AssistantInstructions } from "@/components/connect/AssistantInstructions";
import { MemoryTab } from "@/components/profile/MemoryTab";
import { ProfileHeader } from "@/components/profile/ProfileHeader";
import { PageContainer } from "@/components/layout/PageContainer";

/** Memory & instructions (owner 2026-10-28): how the assistant should work for the user, in their own
 *  words (moved from Connect), then the facts every form asks for, each with where it came from. */
export default function ProfileMemoryPage() {
  return (
    <PageContainer className="py-8 lg:py-12">
      <ProfileHeader />
      <div className="mb-10">
        <AssistantInstructions />
      </div>
      <MemoryTab />
    </PageContainer>
  );
}
