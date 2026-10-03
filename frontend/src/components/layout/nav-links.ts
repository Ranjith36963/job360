import {
  User,
  ClipboardPaste,
  FolderClock,
  Receipt,
  Plug,
  type LucideIcon,
} from "lucide-react";

// R14 (docs/plans/2026-09-04-application-spine) removed /receipts from the
// nav — the URL kept working, but a new-user walk (2026-09-27) found no way
// to discover it at all, so it is back as a first-class link. Slice 5
// (delete-sourcing-era) removed the Dashboard link and the route it pointed
// at, and the mission sweep removed Channels/notifications outright
// (VISION:133 — notifications are pull-not-push) — Job360 never sources or
// ranks jobs (VISION rule 4), so there is no catalog left to browse either.
//
// Agentic UX audit (2026-09-08) — the whole product depends on the user
// connecting their own assistant, but /settings/connect was reachable only
// via the gear icon. It is a first-class destination now, not a settings tab.
export const NAV_LINKS: readonly { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/profile", label: "Profile", icon: User },
  { href: "/bring", label: "Bring a job", icon: ClipboardPaste },
  { href: "/applications", label: "Applications", icon: FolderClock },
  { href: "/receipts", label: "Receipts", icon: Receipt },
  { href: "/settings/connect", label: "Connect your assistant", icon: Plug },
];

/** Mirrors PROTECTED_PATHS in src/middleware.ts: routes only a signed-in user
 *  can reach. While the session is still loading, the shell uses this to draw
 *  the sidebar frame straight away instead of a top bar that then jumps. */
const PROTECTED_PREFIXES = [
  "/profile",
  "/bring",
  "/receipts",
  "/applications",
  "/settings",
  "/admin",
  "/oauth",
];

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname.startsWith(p));
}

export function isNavActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

/** /settings/connect is its own nav entry, so the Settings link must not
 *  also light up for it — two active links at once looks broken. */
export function isSettingsActive(pathname: string): boolean {
  return pathname.startsWith("/settings") && !pathname.startsWith("/settings/connect");
}
