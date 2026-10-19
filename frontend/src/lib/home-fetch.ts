// ---------------------------------------------------------------------------
// Fetch helpers for Home: follow the pages, cap the work.
// ---------------------------------------------------------------------------

import { listApplications, whatsNew } from "@/lib/api";
import type { ApplicationSummary, WhatsNewEvent, WhatsNewResponse } from "@/lib/api";
import { MAX_WHATS_NEW_PAGES } from "@/lib/home";

type WhatsNewApplication = WhatsNewResponse["applications"][number];

export type WhatsNewPages = {
  events: WhatsNewEvent[];
  applications: WhatsNewApplication[];
  /** `now` from the FIRST page — the stamp to store as the next last-visit. */
  now: string;
  /** Still more after the page cap, so counts must show as "N+". */
  truncated: boolean;
};

/** Read `whats-new` from `since`, following `next_since`/`next_after_id` while
 * `truncated`, for at most `maxPages` pages. Events stay oldest first. */
export async function fetchWhatsNewPages(
  since: string,
  maxPages: number = MAX_WHATS_NEW_PAGES
): Promise<WhatsNewPages> {
  const events: WhatsNewEvent[] = [];
  const apps = new Map<number, WhatsNewApplication>();
  let cursor: { since: string; afterId?: number } = { since };
  let now = "";
  let truncated = false;
  for (let page = 0; page < maxPages; page++) {
    const res = await whatsNew({ since: cursor.since, after_id: cursor.afterId, limit: 200 });
    if (page === 0) now = res.now;
    events.push(...res.events);
    for (const a of res.applications) apps.set(a.id, a);
    truncated = res.truncated;
    if (!res.truncated) break;
    cursor = { since: res.next_since, afterId: res.next_after_id ?? undefined };
  }
  return { events, applications: [...apps.values()], now, truncated };
}

/** Every application, paged to `total` (the route caps a page at 200). */
export async function fetchAllApplications(): Promise<{
  applications: ApplicationSummary[];
  total: number;
}> {
  const PAGE = 200;
  const out: ApplicationSummary[] = [];
  let total = 0;
  for (let guard = 0; guard < 50; guard++) {
    const res = await listApplications({ limit: PAGE, offset: out.length });
    total = res.total;
    out.push(...res.applications);
    if (res.applications.length === 0 || out.length >= total) break;
  }
  return { applications: out, total };
}
