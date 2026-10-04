"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/settings/account", label: "Account" },
  { href: "/settings/connect", label: "Connect your assistant" },
] as const;

export function SettingsNavTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Settings navigation"
      className="mb-8 flex gap-1 border-b border-border pb-px"
    >
      {TABS.map((t) => {
        const isActive =
          pathname === t.href || pathname.startsWith(t.href + "/");
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={isActive ? "page" : undefined}
            className={`rounded-t-md px-4 py-2 text-sm font-medium transition-colors ${
              isActive
                ? "-mb-px border border-border border-b-background bg-card text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
