"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getScheduleStatus, type DutyStatus as Row } from "@/lib/api";
import { formatDateTime } from "@/lib/format-date";
import { relativeTime } from "@/lib/utils";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

const HOLD_WORDS: Record<string, string> = {
  queue_full: "your queue is full, so it waits until it drains",
  paused: "you paused your assistant",
  queue_empty: "nothing is waiting in your queue",
  daily_cap_reached: "today's limit is reached",
  weekly_cap_reached: "this week's limit is reached",
  outside_hours: "it is outside your apply hours",
};

const OUTCOME_WORDS: Record<string, string> = { done: "done", partial: "partly done", failed: "failed" };

/** One plain line: what the duty is doing now. Every value is a closed API value. */
export function stateText(row: Row): string {
  const next = row.next_due_at ? formatDateTime(row.next_due_at) : "";
  switch (row.state) {
    case "running":
      return "Running now.";
    case "due":
      return "Due now: it runs at your assistant's next hourly run.";
    case "failing":
      return `Failed ${row.failures_in_row} time${row.failures_in_row === 1 ? "" : "s"} in a row. Tries again${next ? ` ${next}` : " later"}.`;
    case "held":
      return `Waiting: ${HOLD_WORDS[row.hold ?? ""] ?? "it cannot run right now"}.`;
    case "waiting":
      return next ? `Next run ${next}.` : "Waiting for work.";
    default:
      return "Off.";
  }
}

function lastText(row: Row): string {
  if (!row.last_run_at) return "Not run yet.";
  return `Last run ${relativeTime(row.last_run_at)}: ${OUTCOME_WORDS[row.last_outcome ?? "done"] ?? "done"}.`;
}

/**
 * Owner 2026-10-25: Home shows how each of your assistant's jobs is doing - Hunt, Gmail check, People hunt,
 * Apply - with when it last ran, how that ended, and when it runs next. Silent while every job is off or the
 * status cannot load (rule #29: empty stays silent).
 */
export function DutyStatus() {
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => getScheduleStatus())
      .then((out) => {
        if (live) setRows(out.duties);
      })
      .catch(() => undefined); // a missing status is never an error on Home
    return () => {
      live = false;
    };
  }, []);

  const on = rows.filter((r) => r.state !== "off");
  if (on.length === 0) return null;
  return (
    <section aria-labelledby="home-duty-status" className="flex flex-col gap-2" data-testid="duty-status">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="home-duty-status" className={LABEL}>
          Your assistant&apos;s jobs
        </h2>
        <Link href="/settings/connect" className="text-xs underline underline-offset-2">
          Run one now
        </Link>
      </div>
      <ul className="rounded-lg border border-border">
        {on.map((row) => (
          <li
            key={row.duty}
            data-testid={`duty-status-${row.duty}`}
            className="flex flex-col gap-0.5 border-t border-border px-3 py-2 text-sm first:border-t-0 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3"
          >
            <span className="font-medium">{row.label}</span>
            <span className={`text-xs ${row.state === "failing" ? "text-destructive" : "text-muted-foreground"}`}>
              {lastText(row)} {stateText(row)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
