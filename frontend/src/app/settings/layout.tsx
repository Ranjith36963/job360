import type { ReactNode } from "react";
import { Settings } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { SettingsNavTabs } from "./_tabs";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-4xl px-4">
      <PageHeader
        className="pt-10"
        icon={Settings}
        title="Settings"
        subtitle="Manage your notifications and account"
      />

      <SettingsNavTabs />

      {children}
    </div>
  );
}
