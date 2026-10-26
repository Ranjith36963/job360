import {
  House,
  User,
  ClipboardPaste,
  FolderClock,
  Receipt,
  BarChart3,
  MessageCircleQuestion,
  Users,
  Brain,
  Plug,
  CircleUser,
  type LucideIcon,
} from "lucide-react";

// Nav history: R14 put /receipts back as a first-class link after a new-user
// walk (2026-09-27) found no way to discover it; slice 5 removed Dashboard,
// and the mission sweep removed Channels/notifications. Job360 never sources
// or ranks jobs (VISION rule 4), so there is no catalog to browse either.
//
// Owner, 2026-10-26: Needs you, Home, Jobs in, Applications, Outreach,
// Receipts, Stats, Profile, Memory, Connect your assistant, Account - every one
// a top-level item. Outreach IS the people page (no People sub-item); Memory
// has its own brain icon. There is no Settings item: the old settings pages
// moved to /account and /connect. `children` stays for a future sub-item.
export type NavLink = {
  href: string;
  label: string;
  icon: LucideIcon;
  children?: readonly { href: string; label: string }[];
};

export const NAV_LINKS: readonly NavLink[] = [
  { href: "/needs-you", label: "Needs you", icon: MessageCircleQuestion },
  { href: "/", label: "Home", icon: House },
  { href: "/bring", label: "Jobs in", icon: ClipboardPaste },
  { href: "/applications", label: "Applications", icon: FolderClock },
  { href: "/outreach/people", label: "Outreach", icon: Users },
  { href: "/receipts", label: "Receipts", icon: Receipt },
  { href: "/stats", label: "Stats", icon: BarChart3 },
  { href: "/profile", label: "Profile", icon: User },
  { href: "/profile/memory", label: "Memory", icon: Brain },
  { href: "/connect", label: "Connect your assistant", icon: Plug },
  { href: "/account", label: "Account", icon: CircleUser },
];

/** Every link in drawing order, sub-items right after their parent. */
export const NAV_FLAT: readonly { href: string; label: string; child: boolean }[] = NAV_LINKS.flatMap((l) => [
  { href: l.href, label: l.label, child: false },
  ...(l.children ?? []).map((c) => ({ href: c.href, label: c.label, child: true })),
]);

/** Mirrors PROTECTED_PATHS in src/middleware.ts: routes only a signed-in user
 *  can reach. While the session is still loading, the shell uses this to draw
 *  the sidebar frame straight away instead of a top bar that then jumps. */
const PROTECTED_PREFIXES = [
  "/profile",
  "/bring",
  "/receipts",
  "/applications",
  "/outreach",
  "/stats",
  "/needs-you",
  "/account",
  "/connect",
  "/oauth",
];

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname.startsWith(p));
}

export function isNavActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

/** A link lights up on its own pages, not while a deeper link does (Profile stays dark on
 *  /profile/memory, which is Memory's): two active links at once looks broken. */
export function isParentActive(pathname: string, link: NavLink): boolean {
  if (!isNavActive(pathname, link.href)) return false;
  const deeper = [...NAV_LINKS.map((l) => l.href), ...(link.children ?? []).map((c) => c.href)];
  return !deeper.some((h) => h !== link.href && h.startsWith(link.href + "/") && isNavActive(pathname, h));
}
