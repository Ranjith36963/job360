"use client";

import { useState } from "react";
import { runOwnBrowserNow, type OwnBrowserQueue } from "@/lib/browser-contract";
import { RUN_NOW_DONE, RUN_OWN_BROWSER_TASKS } from "@/lib/browser-labels";
import { toast } from "@/lib/toast";

/** "Opens Mon 09:00" in the viewer's own clock; "" for a bad date. */
function opensText(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString("en-GB", { weekday: "short" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `Opens ${day} ${time}`;
}

/** Whole minutes until `iso`; 0 when it is past, missing or bad. */
function minutesLeft(iso: string | null | undefined): number {
  const ms = iso ? Date.parse(iso) - Date.now() : NaN;
  return Number.isNaN(ms) ? 0 : Math.max(0, Math.round(ms / 60000));
}

/** When the own-browser window is open. */
export function windowText(q: Pick<OwnBrowserQueue, "open_now" | "opens_at" | "why">): string {
  if (q.open_now) return "Open now";
  const opens = q.opens_at ? opensText(q.opens_at) : "";
  if (opens) return opens;
  return q.why === "any_time" ? "Any time your PC is on" : "Closed";
}

/** Tasks Job360's browser could not finish, waiting for the user's own browser. Shown only when
 *  something waits. Run now opens the queue for an hour; nothing is sent without the per-person click. */
export function OwnBrowserQueueCard({ queue }: { queue: OwnBrowserQueue }) {
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  if (queue.count <= 0) return null;

  async function runNow() {
    setRunning(true);
    try {
      await runOwnBrowserNow();
      setDone(true);
    } catch (err) {
      toast.apiError(err, "Couldn't open the queue");
    } finally {
      setRunning(false);
    }
  }

  // Run now answered, or the server says the window is open on a run-now: the second, small line.
  const until = minutesLeft(queue.run_now_until);
  const note = done ? RUN_NOW_DONE : until > 0 ? `Open for the next ${until} minute${until === 1 ? "" : "s"}.` : "";
  return (
    // One slim strip below the job list (owner 2026-10-27: "put that down there").
    <section className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-muted/30 px-3 py-2" data-testid="own-browser-queue">
      <h3 className="sr-only">Waiting for your own browser</h3>
      <p className="text-sm font-medium" data-testid="own-browser-count">{queue.count} waiting for your own browser</p>
      <p className="text-xs text-muted-foreground" data-testid="own-browser-window">{windowText(queue)}</p>
      <button type="button" data-testid="own-browser-run-now" disabled={running} onClick={() => void runNow()}
        className="ml-auto rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50">
        {RUN_OWN_BROWSER_TASKS}
      </button>
      {note && <p className="basis-full text-xs text-muted-foreground" data-testid={done ? "own-browser-run-now-done" : "own-browser-open-for"}>{note}</p>}
    </section>
  );
}
