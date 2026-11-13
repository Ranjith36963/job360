// Where an application page was opened from, carried in the link (?from=…&show=…), so "back" returns to
// the same place. Both values come from the address bar, so they are allow-listed here: anything unknown
// falls back to the Applications list. Pure, no React.

import { FILTER_CHIPS, chipLabel } from "@/lib/needs-you-items";

export type Origin = "needs-you" | "outreach" | "home";

const ORIGINS: Record<Origin, { path: string; name: string }> = {
  "needs-you": { path: "/needs-you", name: "Needs you" },
  outreach: { path: "/outreach/people", name: "Outreach" },
  home: { path: "/", name: "Progress dashboard" },
};

const isOrigin = (v: unknown): v is Origin => typeof v === "string" && Object.hasOwn(ORIGINS, v);
const KNOWN_CHIPS = new Set<string>(FILTER_CHIPS.map((c) => c.key));

export type BackTarget = {
  /** Where the link goes. */
  href: string;
  /** The link's words. */
  label: string;
  /** The breadcrumb's trail before the job: [] for a direct visit still shows "Applications". */
  trail: { name: string; href: string }[];
};

type Param = string | string[] | undefined;
const first = (v: Param) => (Array.isArray(v) ? v[0] : v);

/** The back link and breadcrumb trail for application `id`, from the page's search params. */
export function backTarget(id: number, params: { from?: Param; show?: Param } = {}): BackTarget {
  const from = first(params.from);
  if (!isOrigin(from)) {
    return { href: "/applications", label: "All applications", trail: [{ name: "Applications", href: "/applications" }] };
  }
  const { path, name } = ORIGINS[from];
  const showRaw = first(params.show);
  const show = from === "needs-you" && showRaw && KNOWN_CHIPS.has(showRaw) ? showRaw : "all";
  const q = new URLSearchParams();
  if (show !== "all") q.set("show", show);
  if (from === "needs-you" && Number.isInteger(id) && id > 0) q.set("job", String(id));
  const qs = q.toString();
  const href = qs ? `${path}?${qs}` : path;
  const chip = from === "needs-you" && show !== "all" ? chipLabel(show) : "";
  const trail = [{ name, href: path }, ...(chip ? [{ name: chip, href }] : [])];
  return { href, label: `Back to ${name}${chip ? ` · ${chip}` : ""}`, trail };
}

/** `?job=` on the Needs you page: a positive whole number, else null. */
export function parseJobParam(v: string | null | undefined): number | null {
  return v && /^\d{1,9}$/.test(v) && Number(v) > 0 ? Number(v) : null;
}

/** The link to an application page from a page that knows where it is (Outreach, the dashboard). */
export function appHref(id: number, from?: Origin, show?: string): string {
  if (!from) return `/applications/${id}`;
  const q = new URLSearchParams({ from });
  if (show && show !== "all") q.set("show", show);
  return `/applications/${id}?${q.toString()}`;
}

/** Takes `?job=` out of the address bar once it has been used (no reload, no new history entry). */
export function clearJobParam(): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("job")) return;
    url.searchParams.delete("job");
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // The param just stays in the link.
  }
}
