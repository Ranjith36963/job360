"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getDutyTriggers, runDuty, type Duty, type DutyTrigger } from "@/lib/api";
import { minutesLeft, windowText } from "@/lib/batch-queue";
import { getOwnBrowserQueue, runOwnBrowserNow, type OwnBrowserQueue } from "@/lib/browser-contract";
import { RUN_NOW_DONE, RUN_OWN_BROWSER_TASKS } from "@/lib/browser-labels";
import { onChanges } from "@/lib/live-changes";
import { toast } from "@/lib/toast";

/** Gmail first (owner 2026-10-27: "a button to trigger Gmail here"), then the other scheduled duties. */
const ORDER: Duty[] = ["inbox", "hunt", "reach", "batch"];
const WHAT: Record<Duty, string> = {
  inbox: "Check my Gmail for replies",
  hunt: "Look for new jobs",
  reach: "Find people for my new applications",
  batch: "Apply to the jobs I said yes to",
};
const BUTTON: Record<Duty, string> = { inbox: "Check Gmail now", hunt: "Hunt now", reach: "Find people now", batch: "Apply now" };
/** Why a start did not happen, in plain words (the closed reasons of POST /assistant-duty-triggers/{duty}/run). */
export const NOT_STARTED: Record<string, string> = {
  not_set: "Save a start link first.",
  cooldown: "Started a moment ago. Try again in a minute.",
  hourly_cap: "Started many times this hour already. The hourly run will catch up.",
  rate_limited: "Your assistant asked Job360 to wait. Try later.",
};

const QUIET =
  "rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";
const BTN =
  "flex-none rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";

/**
 * Run now (owner 2026-10-27): triggers, not permissions. Each runs on its schedule anyway; a press
 * only starts it early, and never says yes to anything (check_submit still decides every submit).
 * It sits beside the Needs you summary (NeedsYou's `aside`). Live: re-reads when the assistant moves anything.
 */
export function RunNowPanel() {
  const [own, setOwn] = useState<OwnBrowserQueue | null>(null);
  const [duties, setDuties] = useState<DutyTrigger[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [ran, setRan] = useState(false);

  const load = useCallback(async () => {
    // A failed read (null) keeps the last good queue, so the button never blinks away on a blip.
    void getOwnBrowserQueue().then((q) => q && setOwn(q));
    try {
      setDuties((await getDutyTriggers()).duties);
    } catch {
      setDuties((d) => d ?? []);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first read; later ones come from onChanges
    void load();
  }, [load]);
  useEffect(() => onChanges(() => void load()), [load]);

  async function runOwn() {
    setBusy("own");
    try {
      await runOwnBrowserNow();
      toast.success("Your own-browser tasks are starting");
      setRan(true);
      const q = await getOwnBrowserQueue();
      if (q) setOwn(q);
    } catch (err) {
      toast.apiError(err, "Couldn't open the queue");
    } finally {
      setBusy(null);
    }
  }

  async function run(row: DutyTrigger) {
    setBusy(row.duty);
    try {
      const out = await runDuty(row.duty);
      if (out.fired) toast.success(`${row.label} started.`);
      else toast.info(NOT_STARTED[out.reason ?? ""] ?? out.detail ?? "It did not start.");
      await load();
    } catch (err) {
      toast.apiError(err, "Could not start it.");
    } finally {
      setBusy(null);
    }
  }

  const waitN = own?.count ?? 0;
  const until = minutesLeft(own?.run_now_until);
  const ownNote = ran ? RUN_NOW_DONE : until > 0 ? `Open for the next ${until} minute${until === 1 ? "" : "s"}.` : "";
  const rows = ORDER.map((d) => duties?.find((r) => r.duty === d)).filter((r): r is DutyTrigger => !!r);
  const unlinked = rows.filter((r) => !r.set);

  return (
    <section
      aria-labelledby="run-now"
      data-testid="run-now-panel"
      className="flex w-full flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-card lg:w-[30rem] lg:flex-none"
    >
      <h2 id="run-now" className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        Run now
      </h2>
      <p className="text-xs text-muted-foreground">These run on your schedule anyway. Press one to start it early.</p>
      {(waitN > 0 || ran) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-2" data-testid="own-browser-queue">
          <h3 className="sr-only">Waiting for your own browser</h3>
          <p className="text-sm font-medium" data-testid="own-browser-count">
            {waitN} waiting for your own browser
          </p>
          {own && <p className="text-xs text-muted-foreground" data-testid="own-browser-window">{windowText(own)}</p>}
          <button type="button" data-testid="own-browser-run-now" disabled={busy === "own"} onClick={() => void runOwn()} className={`ml-auto ${BTN}`}>
            {RUN_OWN_BROWSER_TASKS}
          </button>
          {ownNote && (
            <p className="basis-full text-xs text-muted-foreground" data-testid={ran ? "own-browser-run-now-done" : "own-browser-open-for"}>
              {ownNote}
            </p>
          )}
        </div>
      )}
      {rows.some((r) => r.set) && (
        <ul className="flex flex-wrap gap-2 border-t border-border pt-2">
          {rows.filter((r) => r.set).map((r) => (
            <li key={r.duty} data-testid={`run-now-${r.duty}`}>
              <button
                type="button"
                data-testid={`run-now-${r.duty}-btn`}
                title={WHAT[r.duty]}
                aria-label={`${BUTTON[r.duty]}: ${WHAT[r.duty]}`}
                disabled={busy === r.duty}
                onClick={() => void run(r)}
                className={QUIET}
              >
                {busy === r.duty ? "Starting…" : BUTTON[r.duty]}
              </button>
            </li>
          ))}
        </ul>
      )}
      {unlinked.length > 0 && (
        <p className="text-xs text-muted-foreground" data-testid="run-now-unlinked">
          {unlinked.map((r) => r.label).join(", ")}: no start link yet.{" "}
          <Link href="/connect" className="font-medium text-foreground underline-offset-2 hover:underline">
            Add one in Connect your assistant
          </Link>
        </p>
      )}
    </section>
  );
}
