"use client";

import type { ApplicationEvent } from "@/lib/api";

/** The whole append-only event log, in `occurred_at` order (spec R3/R11). A
 * superseded event (retired by a correcting event, spec R3) is shown struck
 * through rather than hidden — the log itself never drops a row. */
export function Timeline({ events }: { events: ApplicationEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No events yet.</p>;
  }

  return (
    <ol className="flex flex-col gap-3">
      {events.map((event) => (
        <li
          key={event.id}
          className={`glass-card rounded-lg p-3 text-sm ${event.superseded ? "opacity-50" : ""}`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className={`font-medium ${event.superseded ? "line-through" : ""}`}>
              {event.event_type}
            </span>
            <span className="text-xs text-muted-foreground">
              {new Date(event.occurred_at).toLocaleString()}
            </span>
          </div>
          {event.detail && <p className="mt-1 text-muted-foreground">{event.detail}</p>}
          <p className="mt-1 text-xs text-muted-foreground/70">
            recorded by {event.recorded_by}
            {event.superseded && " · superseded"}
          </p>
        </li>
      ))}
    </ol>
  );
}
