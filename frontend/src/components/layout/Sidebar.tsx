"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Settings } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { useAuth } from "@/components/layout/AuthProvider";
import { NAV_LINKS, isNavActive, isSettingsActive } from "@/components/layout/nav-links";
import { NeedsYouBadge, useOpenAsks } from "@/components/layout/useOpenAsks";
import { cn } from "@/lib/utils";

/** One row of the sidebar. Active = a raised card with a hairline ring. */
export function navItemClass(active: boolean): string {
  return cn(
    "flex items-center gap-2.5 rounded-[7px] px-2.5 py-[7px] text-sm transition-colors",
    "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
    active
      ? "bg-card font-medium text-foreground ring-1 ring-border"
      : "text-muted-foreground hover:bg-card/60 hover:text-foreground"
  );
}

/**
 * Slim left sidebar for signed-in pages (desktop, >= md). Below md the same
 * links live in the top bar's drawer (Navbar.tsx).
 *
 * While the session is still resolving on a guarded route the frame renders
 * with no links, so the page does not jump sideways when the user arrives.
 */
export function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const settingsActive = isSettingsActive(pathname);
  const openAsks = useOpenAsks(Boolean(user), pathname);

  return (
    <aside
      data-testid="app-sidebar"
      className={cn(
        "flex w-[216px] shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar px-3 pb-4 pt-5",
        className
      )}
    >
      <Link href="/" aria-label="job360" className="px-2.5">
        <Logo />
      </Link>

      <nav aria-label="Main navigation" className="flex flex-col gap-px">
        {user &&
          NAV_LINKS.map(({ href, label, icon: Icon }) => {
            const active = isNavActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={navItemClass(active)}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                {label}
                {href === "/needs-you" && <NeedsYouBadge count={openAsks} />}
              </Link>
            );
          })}
      </nav>

      <div className="mt-auto flex flex-col gap-2 border-t border-sidebar-border pt-3">
        {user && (
          <>
            <Link
              href="/settings"
              aria-current={settingsActive ? "page" : undefined}
              className={navItemClass(settingsActive)}
            >
              <Settings className="h-4 w-4 shrink-0" aria-hidden="true" />
              Settings
            </Link>
            <p className="truncate px-2.5 text-xs text-muted-foreground" title={user.email}>
              {user.email}
            </p>
            <button
              type="button"
              onClick={() => void logout()}
              className={cn(navItemClass(false), "w-full text-left")}
            >
              <LogOut className="h-4 w-4 shrink-0" aria-hidden="true" />
              Log out
            </button>
          </>
        )}
        <div className="px-2.5 pt-1">
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}
