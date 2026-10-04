"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Menu, Settings, LogOut } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import { useAuth } from "@/components/layout/AuthProvider";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import {
  NAV_LINKS,
  isNavActive,
  isProtectedPath,
  isSettingsActive,
} from "@/components/layout/nav-links";
import { NeedsYouBadge, useOpenAsks } from "@/components/layout/useOpenAsks";
import { cn } from "@/lib/utils";

/**
 * The top bar.
 *
 * - Signed out: logo, Log in, Get started, theme toggle (md and up); a drawer
 *   with the same on a phone.
 * - Signed in: only on narrow screens (< md), where the sidebar is gone — logo
 *   plus a drawer holding the nav links, Settings, account and theme toggle.
 *   From md up the Sidebar (Sidebar.tsx) takes over and this bar hides.
 */
export function Navbar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user, loading, logout } = useAuth();

  // NAV_LINKS all point at routes src/middleware.ts guards. Rendering them to a
  // signed-out visitor offered controls that only ever bounced to /login,
  // while the header carried no way to actually sign in — on the landing page,
  // which is the first thing a new visitor sees.
  //
  // `loading` is its own state on purpose: it starts true, so treating it as
  // "signed out" would flash the marketing CTAs at every returning user before
  // the session resolves. While unknown, the header shows the logo only.
  const signedIn = Boolean(user);
  const signedOut = !loading && !user;
  // The sidebar owns desktop whenever we are (or are about to be) signed in.
  const sidebarOnDesktop = signedIn || (loading && isProtectedPath(pathname));
  const settingsActive = isSettingsActive(pathname);
  const openAsks = useOpenAsks(signedIn, pathname);

  const drawerLink = (active: boolean) =>
    cn(
      "flex min-h-11 items-center gap-3 rounded-[7px] px-3 py-2.5 text-sm font-medium transition-colors",
      active
        ? "bg-card text-foreground ring-1 ring-border"
        : "text-muted-foreground hover:bg-card/60 hover:text-foreground"
    );

  return (
    <header
      data-print-hide
      className={cn(
        "sticky top-0 z-50 border-b border-border bg-background/90 backdrop-blur-md",
        sidebarOnDesktop && "md:hidden"
      )}
    >
      <div className="flex h-14 items-center justify-between px-4 sm:px-6 lg:px-10">
        <Link href="/" aria-label="job360" className="flex items-center">
          <Logo />
        </Link>

        {/* Desktop, signed out */}
        <div className="hidden items-center gap-2 md:flex">
          {signedOut && (
            <>
              {/* Not shown on /login and /register themselves — the page it would
                  send you to is the page you are already on. */}
              {!pathname.startsWith("/login") && (
                <Link
                  href="/login"
                  className="rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  Log in
                </Link>
              )}
              {/* A styled Link, not <Button asChild> — this repo's Button
                  (src/components/ui/button.tsx) has no asChild prop. */}
              {!pathname.startsWith("/register") && (
                <Link
                  href="/register"
                  className="inline-flex h-9 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
                >
                  Get started
                </Link>
              )}
              <ThemeToggle className="ml-1" />
            </>
          )}
        </div>

        {/* Drawer — hidden while the session is still resolving, because it
            would have nothing to put in it. */}
        {!loading && (
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger
              className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
              aria-label="Open navigation menu"
              aria-expanded={mobileOpen}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
              <span className="sr-only">Menu</span>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 border-border bg-sidebar p-4">
              <SheetTitle className="mb-4 px-3">
                <Logo />
              </SheetTitle>
              <nav className="flex flex-col gap-1" aria-label="Mobile navigation">
                {signedIn &&
                  NAV_LINKS.map(({ href, label, icon: Icon }) => {
                    const active = isNavActive(pathname, href);
                    return (
                      <Link
                        key={href}
                        href={href}
                        onClick={() => setMobileOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={drawerLink(active)}
                      >
                        <Icon className="h-4 w-4" aria-hidden="true" />
                        {label}
                        {href === "/needs-you" && <NeedsYouBadge count={openAsks} />}
                      </Link>
                    );
                  })}
                {signedIn && (
                  <Link
                    href="/settings"
                    onClick={() => setMobileOpen(false)}
                    aria-current={settingsActive ? "page" : undefined}
                    className={drawerLink(settingsActive)}
                  >
                    <Settings className="h-4 w-4" aria-hidden="true" />
                    Settings
                  </Link>
                )}
                {/* Without this the signed-out drawer opened completely empty. */}
                {signedOut && (
                  <>
                    <Link
                      href="/register"
                      onClick={() => setMobileOpen(false)}
                      className="flex min-h-11 items-center gap-3 rounded-[7px] bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground transition-colors"
                    >
                      Get started
                    </Link>
                    <Link
                      href="/login"
                      onClick={() => setMobileOpen(false)}
                      className={drawerLink(false)}
                    >
                      Log in
                    </Link>
                  </>
                )}
              </nav>

              <div className="mt-auto flex flex-col gap-2 border-t border-border pt-4">
                {user && (
                  <>
                    <p className="truncate px-3 text-xs text-muted-foreground">{user.email}</p>
                    <button
                      type="button"
                      onClick={() => {
                        setMobileOpen(false);
                        void logout();
                      }}
                      className={cn(drawerLink(false), "w-full text-left")}
                    >
                      <LogOut className="h-4 w-4" aria-hidden="true" />
                      Log out
                    </button>
                  </>
                )}
                <div className="px-3">
                  <ThemeToggle />
                </div>
              </div>
            </SheetContent>
          </Sheet>
        )}
      </div>
    </header>
  );
}
