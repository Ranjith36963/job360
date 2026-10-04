"use client";

import type { ApplicationEvent } from "@/lib/api";
import { eventLabel } from "@/lib/event-labels";
import { WhoChip } from "@/components/applications/WhoChip";
import { formatDateTime } from "@/lib/format-date";

/** The whole append-only event log, in `occurred_at` order (spec R3/R11). A
 * superseded event (retired by a correcting event, spec R3) is shown struck
 * through rather than hidden — the log itself never drops a row. */
export function Timeline({ events }: { events: ApplicationEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No events yet.</p>;
  }

  return (
    <ol className="flex flex-col">
      {events.map((event) => (
        <li
          key={event.id}
          data-testid="timeline-event"
          className={`border-t border-border py-2.5 text-sm first:border-t-0 first:pt-0 ${event.superseded ? "opacity-50" : ""}`}
        >
          <span className="mb-1 block font-mono text-[11.5px] tabular-nums text-faint">
            {formatDateTime(event.occurred_at)}
          </span>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <WhoChip recordedBy={event.recorded_by} />
            <span className={`font-medium ${event.superseded ? "line-through" : ""}`}>
              {eventLabel(event)}
            </span>
            {event.superseded && (
              <span className="font-mono text-[11px] text-faint">superseded</span>
            )}
          </div>
          {event.detail && (
            <p className="mt-1 font-heading text-[15px] leading-snug text-muted-foreground">
              {event.detail}
            </p>
          )}
          {event.source && (
            <p className="mt-1 text-xs text-muted-foreground">
              {"✉ "}
              {event.source.sender}
              {event.source.subject && ` — “${event.source.subject}”`}
              {event.source.received_at &&
                ` · received ${formatDateTime(event.source.received_at)}`}
            </p>
          )}
          {event.scheduled_at && (
            <p className="mt-1 font-mono text-xs text-foreground">
              Scheduled for {formatDateTime(event.scheduled_at)}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
