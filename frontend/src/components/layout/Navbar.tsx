"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  LayoutDashboard,
  User,
  Kanban,
  Menu,
  Activity,
  Send,
  Settings,
  LogOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import { useAuth } from "@/components/layout/AuthProvider";

const NAV_LINKS = [
  { href: "/profile", label: "Profile", icon: User },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/pipeline", label: "Pipeline", icon: Kanban },
  { href: "/channels", label: "Channels", icon: Send },
] as const;

export function Navbar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user, loading, logout } = useAuth();

  // NAV_LINKS all point at routes src/middleware.ts guards. Rendering them to a
  // signed-out visitor offered four controls that only ever bounced to /login,
  // while the header carried no way to actually sign in — on the landing page,
  // which is the first thing a new visitor sees.
  //
  // `loading` is its own state on purpose: it starts true, so treating it as
  // "signed out" would flash the marketing CTAs at every returning user before
  // the session resolves. While unknown, the header shows the logo only.
  const signedIn = Boolean(user);
  const signedOut = !loading && !user;

  return (
    <header className="sticky top-0 z-50 border-b border-border/30 bg-background/60 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/30 group-hover:ring-primary/50 transition-all">
            <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <span className="font-heading text-lg font-semibold tracking-tight">
            Job<span className="text-primary">360</span>
          </span>
        </Link>

        {/* Desktop Nav */}
        <nav className="hidden md:flex items-center gap-1" aria-label="Main navigation">
          {signedIn &&
            NAV_LINKS.map(({ href, label, icon: Icon }) => {
            const isActive = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                aria-current={isActive ? "page" : undefined}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Right side — Auth + Theme. The search action lives on the Profile
            page ("Search Latest Jobs") — a nav link here only navigated and
            misled users into thinking it searched. */}
        <div className="hidden md:flex items-center gap-2">
          {signedIn && (
            <Link
              href="/settings"
              aria-current={pathname.startsWith("/settings") ? "page" : undefined}
              aria-label="Settings"
              className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors ${
                pathname.startsWith("/settings")
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              <Settings className="h-4 w-4" aria-hidden="true" />
            </Link>
          )}
          {signedOut && (
            <div className="flex items-center gap-2">
              {/* Not shown on /login and /register themselves — the page it would
                  send you to is the page you are already on. */}
              {!pathname.startsWith("/login") && (
                <Link
                  href="/login"
                  className="rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
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
            </div>
          )}
          {user && (
            <div className="flex items-center gap-2 pl-2 border-l border-border/40">
              {/* Hidden until `lg`. The desktop bar turns on at `md` (768px),
                  but its contents do not fit there: logo + four nav links +
                  the settings gear + a 140px email + logout measured 809px
                  against a 768px viewport, so BOTH /profile and /dashboard
                  scrolled sideways at tablet width. The email is the only
                  part that is purely informational, so it is what yields;
                  logout stays reachable at every size. */}
              <span className="hidden max-w-[140px] truncate text-xs text-muted-foreground lg:inline">
                {user.email}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void logout()}
                aria-label="Log out"
                className="h-9 w-9 text-muted-foreground hover:text-foreground hover:bg-muted/50"
              >
                <LogOut className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          )}
        </div>

        {/* Mobile hamburger */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger
            className="md:hidden inline-flex items-center justify-center h-9 w-9 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            aria-label="Open navigation menu"
            aria-expanded={mobileOpen}
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
            <span className="sr-only">Menu</span>
          </SheetTrigger>
          <SheetContent side="right" className="w-64 bg-background border-border">
            <SheetTitle className="font-heading text-lg font-semibold mb-6">
              Job<span className="text-primary">360</span>
            </SheetTitle>
            <nav className="flex flex-col gap-1" aria-label="Mobile navigation">
              {signedIn &&
                NAV_LINKS.map(({ href, label, icon: Icon }) => {
                const isActive = pathname === href;
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setMobileOpen(false)}
                    aria-current={isActive ? "page" : undefined}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                    {label}
                  </Link>
                );
              })}
              {signedIn && (
                <Link
                  href="/settings"
                  onClick={() => setMobileOpen(false)}
                  aria-current={pathname.startsWith("/settings") ? "page" : undefined}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                    pathname.startsWith("/settings")
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  }`}
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
                    className="flex items-center gap-3 rounded-lg bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground transition-colors"
                  >
                    Get started
                  </Link>
                  <Link
                    href="/login"
                    onClick={() => setMobileOpen(false)}
                    className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
                  >
                    Log in
                  </Link>
                </>
              )}
            </nav>

            {/* Mobile: user email + logout + theme toggle */}
            {user && (
              <div className="mt-6 pt-4 border-t border-border/40 flex flex-col gap-2">
                <p className="px-3 text-xs text-muted-foreground truncate">{user.email}</p>
                <button
                  onClick={() => { setMobileOpen(false); void logout(); }}
                  className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                >
                  <LogOut className="h-4 w-4" aria-hidden="true" />
                  Log out
                </button>
              </div>
            )}
          </SheetContent>
        </Sheet>
      </div>
    </header>
  );
}
