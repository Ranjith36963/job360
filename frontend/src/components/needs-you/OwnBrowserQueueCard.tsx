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

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3" data-testid="own-browser-queue">
      <h3 className="text-sm font-semibold">Waiting for your own browser</h3>
      <p className="text-sm" data-testid="own-browser-count">{queue.count} waiting for your own browser</p>
      <p className="text-xs text-muted-foreground" data-testid="own-browser-window">{windowText(queue)}</p>
      <div>
        <button type="button" data-testid="own-browser-run-now" disabled={running} onClick={() => void runNow()}
          className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50">
          {RUN_OWN_BROWSER_TASKS}
        </button>
      </div>
      {done && <p className="text-sm" data-testid="own-browser-run-now-done">{RUN_NOW_DONE}</p>}
    </section>
  );
}
