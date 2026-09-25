import type { ReactNode } from "react";
import { Settings } from "lucide-react";
import { SettingsNavTabs } from "./_tabs";
import { PageContainer } from "@/components/layout/PageContainer";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <PageContainer>
      <div className="flex items-center gap-3 pb-6 pt-10">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/20">
          <Settings className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">
            Manage your account and connected agents
          </p>
        </div>
      </div>

      <SettingsNavTabs />

      {children}
    </PageContainer>
  );
}
