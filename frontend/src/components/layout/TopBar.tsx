"use client";

import { usePathname } from "next/navigation";
import { useAuth } from "@/components/layout/AuthProvider";
import { NAV_LINKS, isNavActive, isSettingsActive } from "@/components/layout/nav-links";
import { askResume, usePause } from "@/lib/assistant-state";
import { cn } from "@/lib/utils";

/**
 * "Pause assistants" (owner decision 2026-10-09). Running: one tap pauses, no
 * question. Paused: amber, and a tap opens the Resume confirm in the banner.
 * One button, used by the desktop top bar and the phone header.
 */
export function PauseButton({ className }: { className?: string }) {
  const { paused, pause } = usePause();
  return (
    <button
      type="button"
      data-testid="pause-button"
      aria-pressed={paused}
      onClick={() => (paused ? askResume() : void pause())}
      className={cn(
        "inline-flex min-h-11 items-center gap-2 rounded-lg border px-3 text-[12.5px] font-medium transition-colors md:min-h-0 md:py-[5px]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        paused
          ? "border-warning bg-warning-soft text-warning"
          : "border-border bg-card text-foreground hover:bg-muted",
        className,
      )}
    >
      <span aria-hidden="true" className="inline-flex gap-[3px]">
        <i className="block h-[11px] w-[3px] rounded-[1px] bg-current" />
        <i className="block h-[11px] w-[3px] rounded-[1px] bg-current" />
      </span>
      {paused ? "Assistants paused" : "Pause assistants"}
    </button>
  );
}

/** The crumb: the label of the page the user is on (Settings is not in NAV_LINKS). */
function crumbFor(pathname: string): string {
  if (isSettingsActive(pathname)) return "Settings";
  const hit = [...NAV_LINKS]
    .filter((l) => (l.href === "/" ? pathname === "/" : isNavActive(pathname, l.href)))
    .sort((a, b) => b.href.length - a.href.length)[0];
  return hit?.label ?? "";
}

/** The desktop bar atop the content column (md and up, signed in). Below md the
 * same button lives in the header (Navbar). */
export function TopBar() {
  const pathname = usePathname();
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div
      data-print-hide
      data-testid="top-bar"
      className="hidden items-center justify-between gap-4 border-b border-border px-6 py-3 md:flex lg:px-10"
    >
      <span className="font-mono text-xs text-faint">{crumbFor(pathname)}</span>
      <PauseButton />
    </div>
  );
}
