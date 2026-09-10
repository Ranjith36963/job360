"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getWhatsNew } from "@/lib/api";
import type { WhatsNewApplication, WhatsNewEvent } from "@/lib/api";
import { eventLabel } from "@/lib/event-labels";
import { relativeTime } from "@/lib/utils";
import { WhoChip } from "@/components/applications/WhoChip";

/** "What's new" — a strip of the caller's most recent activity across every
 * application, mounted above the list on the signed-in home and on
 * `/applications` (fix 4/5, agentic UX audit). Fails SILENTLY: on error or
 * an empty window it renders nothing, since the hermetic e2e specs mock only
 * the routes they need and a missing `/api/whats-new` mock must not break
 * them. */
export function WhatsNewStrip() {
  const { data, isError } = useQuery({
    queryKey: ["whats-new", 10],
    queryFn: () => getWhatsNew({ limit: 10 }),
  });

  if (isError || !data || data.events.length === 0) {
    return null;
  }

  const appById = new Map<number, WhatsNewApplication>(data.applications.map((a) => [a.id, a]));

  // The backend pages `whats_new` forward in RECORDED order (oldest first —
  // that's the correct cursor semantics for polling), so the strip reverses
  // it to show newest-first, the way a human reads "what's new".
  const events: WhatsNewEvent[] = [...data.events].reverse();

  return (
    <section data-testid="whats-new">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        What&apos;s new
      </h2>
      <ul className="flex flex-col gap-2">
        {events.map((event) => {
          const app = appById.get(event.application_id);
          return (
            <li
              key={event.id}
              className="glass-card flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg p-3 text-sm"
            >
              <span className="font-medium">{eventLabel(event)}</span>
              <WhoChip recordedBy={event.recorded_by} />
              {app && (
                <Link
                  href={`/applications/${app.id}`}
                  className="min-w-0 truncate text-primary hover:underline"
                >
                  {app.job_title || "Untitled role"}
                </Link>
              )}
              <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                {relativeTime(event.occurred_at)}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
