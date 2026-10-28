"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { toast } from "sonner";
import { listApplications, recordApplicationEvent } from "@/lib/api";
import { ApiError, apiErrorMessage } from "@/lib/api-error";
import {
  filterByBrowser,
  kindTag,
  mergeQueue,
  originLabel,
  ownBrowserExtras,
  preflightProblems,
  queueSummaryLine,
  withExtras,
} from "@/lib/batch-queue";
import type { OwnTask, QueueBrowserFilter, QueueSummary } from "@/lib/batch-queue";
import { decideOwnBrowserSend, getOwnBrowserQueue, type OwnBrowserQueue } from "@/lib/browser-contract";
import { onChanges } from "@/lib/live-changes";
import { QueueBrowserChip, QueueBrowserFilterRow } from "./QueueBrowser";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const TAG = "mr-1.5 rounded-full border border-border px-2 py-0.5 font-mono text-[10.5px] text-muted-foreground";
const BTN =
  "flex-none rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50";

type Browser = { kind?: string; label?: string; reason?: string } | null;

type QueueRow = {
  id: number;
  origin?: string;
  job_title: string;
  job_company?: string | null;
  preflight?: Record<string, unknown> | null;
  browser?: Browser;
  /** An own-browser task that was never batch-queued: nothing to Remove. */
  extra?: boolean;
};

type OutreachRow = {
  task_id: string;
  outreach_kind: string;
  title: string;
  contact_id: number;
  application_id?: number | null;
  message_id: number;
  state: string;
  browser?: Browser;
};

/** Where a person's card lives: on their job's page when they have one, else the People page. */
function personHref(o: OutreachRow): string {
  return typeof o.application_id === "number"
    ? `/applications/${o.application_id}#person-${o.contact_id}`
    : "/outreach/people";
}

/** One queue (owner, 2026-10-26): the jobs your assistant applies to AND the LinkedIn sends you said yes to,
 *  in the order you said yes, each with the browser that runs it. Names are untrusted: React text only.
 *  It also lists the own-browser queue (owner, 2026-10-27), merged in once each; its Run now button
 *  sits on Needs you (RunNowPanel): a trigger, not a permission. */
export function BatchQueueCard() {
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [outreach, setOutreach] = useState<OutreachRow[]>([]);
  const [order, setOrder] = useState<string[] | null>(null);
  const [summary, setSummary] = useState<QueueSummary | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Client-side over the loaded rows (the card loads the first 50); the counts come from the whole queue.
  const [browserFilter, setBrowserFilter] = useState<QueueBrowserFilter>("all");
  // The own-browser queue (null: failed or not loaded): its tasks join the list once each.
  const [own, setOwn] = useState<OwnBrowserQueue | null>(null);

  const load = useCallback(async () => {
    void getOwnBrowserQueue().then((q) => q && setOwn(q)); // a failed read keeps the last good tasks
    try {
      const res = await listApplications({ queued: true, limit: 50 });
      setRows(res.applications as QueueRow[]);
      setOutreach((res.outreach ?? []) as OutreachRow[]);
      setOrder(res.queue_order ?? null);
      setSummary((res.queue_summary as QueueSummary | null | undefined) ?? null);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    void load();
  }, [load]);

  // Live: re-read when the assistant moves anything (the queue spans jobs and people); a failed read keeps the rows.
  useEffect(() => onChanges(() => void load()), [load]);

  async function remove(id: number) {
    setBusy(`application:${id}`);
    try {
      await recordApplicationEvent(id, { event_type: "unqueued", payload: { reason: "user_removed" } });
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not remove this from the batch."));
    } finally {
      setBusy(null);
    }
  }

  async function dontSend(o: OutreachRow) {
    setBusy(o.task_id);
    try {
      await decideOwnBrowserSend(o.contact_id, o.message_id, "decline");
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("The draft changed since you looked. Look again.");
        await load();
      } else toast.error(apiErrorMessage(err, "Couldn't save that."));
    } finally {
      setBusy(null);
    }
  }

  // Every own-browser task once: the ones the batch list lacks join it (dedupe by application and contact id).
  const extras = ownBrowserExtras(rows ?? [], outreach, own?.tasks as OwnTask[] | undefined);
  const items = rows === null ? [] : mergeQueue<QueueRow, OutreachRow>([...rows, ...extras.apps], [...outreach, ...extras.outreach], order);
  const place = new Map(items.map((it, i) => [it.key, i + 1]));
  const filterSummary = withExtras(summary, extras.apps.length, extras.outreach.length);
  return (
    <section
      aria-labelledby="batch-queue"
      data-testid="batch-queue"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h3 id="batch-queue" className={LABEL}>
        Queue
      </h3>
      {error && rows === null ? (
        <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
          <span>Could not load your batch.</span>
          <button
            type="button"
            data-testid="batch-queue-retry"
            onClick={() => void load()}
            className="text-xs font-medium underline-offset-2 hover:underline"
          >
            Try again
          </button>
        </div>
      ) : rows === null ? (
        <p className="text-sm text-muted-foreground" data-testid="batch-queue-loading">
          Loading…
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="batch-queue-empty">
          Your batch is empty. Bring a job and leave Add to batch ticked, or let your AI hunt: the jobs it finds come here.
        </p>
      ) : (
        <>
          {queueSummaryLine(summary) && (
            <p className="font-mono text-xs text-muted-foreground" data-testid="batch-queue-summary">
              {queueSummaryLine(summary)}
            </p>
          )}
          <QueueBrowserFilterRow summary={filterSummary} total={items.length} value={browserFilter} onChange={setBrowserFilter} />
          <ul className="flex flex-col" data-testid="batch-queue-list">
            {filterByBrowser(items, browserFilter).map((it) => {
              const pos = <span className="mr-1.5 font-mono text-xs text-faint">#{place.get(it.key)}</span>;
              const li = "flex items-start justify-between gap-3 border-t border-border py-2.5 text-sm first:border-t-0";
              if (it.kind === "outreach") {
                const o = it.row;
                return (
                  <li key={it.key} data-testid={`batch-outreach-${o.contact_id}`} className={li}>
                    <div className="min-w-0 flex-1">
                      <span data-testid={`batch-kind-${it.key}`} className={TAG}>{kindTag(o.outreach_kind)}</span>
                      <Link href={personHref(o)} className="break-words font-medium underline-offset-2 hover:underline">
                        {pos}
                        {o.title}
                      </Link>
                      <QueueBrowserChip id={`contact-${o.contact_id}`} browser={o.browser} />
                      {o.state === "in_progress" && (
                        <p className="mt-1 text-xs text-muted-foreground">Sending now.</p>
                      )}
                    </div>
                    {o.state === "queued" && (
                      <button
                        type="button"
                        data-testid={`batch-dont-send-${o.contact_id}`}
                        disabled={busy === it.key}
                        onClick={() => void dontSend(o)}
                        className={BTN}
                      >
                        {busy === it.key ? "Saving…" : "Don't send"}
                      </button>
                    )}
                  </li>
                );
              }
              const r = it.row;
              const problems = preflightProblems(r.preflight);
              const name = [r.job_title || "Untitled role", r.job_company].filter(Boolean).join(" · ");
              return (
                <li key={it.key} data-testid={`batch-row-${r.id}`} className={li}>
                  <div className="min-w-0 flex-1">
                    <span data-testid={`batch-kind-${it.key}`} className={TAG}>{kindTag("application")}</span>
                    <Link href={`/applications/${r.id}`} className="break-words font-medium underline-offset-2 hover:underline">
                      {pos}
                      {name}
                    </Link>
                    {originLabel(r.origin) && (
                      <span
                        data-testid={`batch-row-origin-${r.id}`}
                        className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[10.5px] text-brand"
                      >
                        {originLabel(r.origin)}
                      </span>
                    )}
                    <QueueBrowserChip id={r.id} browser={r.browser} />
                    {problems.length > 0 && (
                      <ul data-testid={`batch-row-problems-${r.id}`} className="mt-1 flex flex-col gap-0.5 text-xs text-warning">
                        {problems.map((p) => (
                          <li key={p}>{p}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {!r.extra && (
                    <button
                      type="button"
                      data-testid={`batch-remove-${r.id}`}
                      disabled={busy === it.key}
                      onClick={() => void remove(r.id)}
                      className={BTN}
                    >
                      {busy === it.key ? "Removing…" : "Remove"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
