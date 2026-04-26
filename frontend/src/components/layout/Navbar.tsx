"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  LayoutDashboard,
  User,
  Kanban,
  Search,
  Menu,
  Activity,
  Briefcase,
  Settings,
  LogOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import { useAuth } from "@/components/layout/AuthProvider";
import { ThemeToggle } from "@/components/layout/ThemeProvider";

const NAV_LINKS = [
  { href: "/profile", label: "Profile", icon: User },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/jobs", label: "Jobs", icon: Briefcase },
  { href: "/pipeline", label: "Pipeline", icon: Kanban },
  { href: "/settings/channels", label: "Channels", icon: Settings },
] as const;

export function Navbar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user, logout } = useAuth();

  return (
    <header className="sticky top-0 z-50 border-b border-border/30 bg-background/60 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/30 group-hover:ring-primary/50 transition-all">
            <Activity className="h-4 w-4 text-primary" />
          </div>
          <span className="font-heading text-lg font-semibold tracking-tight">
            Job<span className="text-primary">360</span>
          </span>
        </Link>

        {/* Desktop Nav */}
        <nav className="hidden md:flex items-center gap-1">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => {
            const isActive = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Right side — Search + Auth + Theme */}
        <div className="hidden md:flex items-center gap-2">
          <Link href="/dashboard">
            <Button
              size="sm"
              className="gap-2 bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
            >
              <Search className="h-3.5 w-3.5" />
              Search Latest Jobs
            </Button>
          </Link>

          <ThemeToggle />

          {user && (
            <div className="flex items-center gap-2 pl-2 border-l border-border/40">
              <span className="text-xs text-muted-foreground max-w-[140px] truncate">
                {user.email}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void logout()}
                aria-label="Log out"
                className="h-9 w-9 text-muted-foreground hover:text-foreground hover:bg-muted/50"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>

        {/* Mobile hamburger */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger className="md:hidden inline-flex items-center justify-center h-9 w-9 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors">
            <Menu className="h-5 w-5" />
            <span className="sr-only">Menu</span>
          </SheetTrigger>
          <SheetContent side="right" className="w-64 bg-background border-border">
            <SheetTitle className="font-heading text-lg font-semibold mb-6">
              Job<span className="text-primary">360</span>
            </SheetTitle>
            <nav className="flex flex-col gap-1">
              {NAV_LINKS.map(({ href, label, icon: Icon }) => {
                const isActive = pathname === href;
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setMobileOpen(false)}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </Link>
                );
              })}
            </nav>

            {/* Mobile: user email + logout + theme toggle */}
            {user && (
              <div className="mt-6 pt-4 border-t border-border/40 flex flex-col gap-2">
                <p className="px-3 text-xs text-muted-foreground truncate">{user.email}</p>
                <button
                  onClick={() => { setMobileOpen(false); void logout(); }}
                  className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                >
                  <LogOut className="h-4 w-4" />
                  Log out
                </button>
              </div>
            )}

            <div className="mt-4 px-3">
              <ThemeToggle />
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </header>
  );
}
