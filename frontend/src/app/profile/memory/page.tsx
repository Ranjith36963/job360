import { MemoryTab } from "@/components/profile/MemoryTab";
import { ProfileHeader } from "@/components/profile/ProfileHeader";
import { PageContainer } from "@/components/layout/PageContainer";

/** Memory: the facts every form asks for, each with where it came from. */
export default function ProfileMemoryPage() {
  return (
    <PageContainer className="py-8 lg:py-12">
      <ProfileHeader />
      <MemoryTab />
    </PageContainer>
  );
}
