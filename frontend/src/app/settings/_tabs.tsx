"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/settings/channels", label: "Channels" },
  { href: "/settings/notifications", label: "Notification Rules" },
  { href: "/settings/account", label: "Account" },
] as const;

export function SettingsNavTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Settings navigation"
      className="mb-8 flex gap-1 border-b border-border/40 pb-px"
    >
      {TABS.map((t) => {
        const isActive =
          pathname === t.href || pathname.startsWith(t.href + "/");
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`rounded-t-md px-4 py-2 text-sm font-medium transition-colors ${
              isActive
                ? "-mb-px border border-border/40 border-b-background bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
