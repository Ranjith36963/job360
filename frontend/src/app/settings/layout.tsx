import type { ReactNode } from "react";
import { Settings } from "lucide-react";
import { SettingsNavTabs } from "./_tabs";
import { PageContainer } from "@/components/layout/PageContainer";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <PageContainer>
      <div className="flex items-center gap-3 pb-6 pt-10">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-card">
          <Settings className="h-5 w-5 text-muted-foreground" />
        </div>
        <div>
          <p className="font-heading text-2xl font-normal tracking-tight">Settings</p>
          <p className="text-sm text-muted-foreground">
            Manage your account and connected assistants
          </p>
        </div>
      </div>

      <SettingsNavTabs />

      {children}
    </PageContainer>
  );
}
